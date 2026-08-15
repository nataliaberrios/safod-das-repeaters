#!/usr/bin/env python
"""Run the published-family conventional-HRSN leave-one-event-out baseline."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List

from .common import STOP, load_config, project_root, utc_now, write_json
from .coverage import write_rows
from .external_catalog import read_csv_rows
from .hrsn import compare_events, download_event, load_stream, write_metrics
from .network_family import (
    classification_summary,
    leave_one_event_out_classification,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--refresh-waveforms", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    root = project_root()
    settings = config["external_validation"]
    population = read_csv_rows(
        root
        / "outputs"
        / "external_validation"
        / "published_validation_population.csv"
    )
    cache = root / "cached_event_windows" / "hrsn"
    streams: Dict[str, Any] = {}
    events: Dict[str, Dict[str, Any]] = {}
    availability: List[Dict[str, Any]] = []
    for row in population:
        event_id = str(row["event_id"])
        event = {
            "event_id": event_id,
            "origin_time": row.get("waveform_origin_time") or row["origin_time"],
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "depth_km": float(row["depth_km"]),
            "magnitude": float(row["magnitude"]),
        }
        events[event_id] = event
        try:
            path = download_event(
                event, config, cache, force=args.refresh_waveforms
            )
            streams[event_id] = load_stream(path)
            availability.append(
                {
                    "event_id": event_id,
                    "sequence_id": row["sequence_id"],
                    "origin_time": event["origin_time"],
                    "status": "available",
                    "trace_count": len(streams[event_id]),
                    "error": "",
                }
            )
        except Exception as exc:
            availability.append(
                {
                    "event_id": event_id,
                    "sequence_id": row["sequence_id"],
                    "origin_time": event["origin_time"],
                    "status": "unavailable",
                    "trace_count": 0,
                    "error": "{}: {}".format(type(exc).__name__, exc),
                }
            )

    population_by_id = {str(row["event_id"]): row for row in population}
    pair_summaries: List[Dict[str, Any]] = []
    pair_metrics: List[Dict[str, Any]] = []
    for first_id, second_id in combinations(sorted(streams), 2):
        metrics, summary = compare_events(
            events[first_id],
            events[second_id],
            streams[first_id],
            streams[second_id],
            config,
            pair_name="network_family_{}_{}".format(first_id, second_id),
        )
        first_family = population_by_id[first_id]["sequence_id"]
        second_family = population_by_id[second_id]["sequence_id"]
        summary_row = {
            "pair_name": summary["pair_name"],
            "reference_event_id": first_id,
            "comparison_event_id": second_id,
            "reference_sequence_id": first_family,
            "comparison_sequence_id": second_family,
            "same_published_family": first_family == second_family,
            "overall_median_correlation": summary["overall_median_correlation"],
            "weakest_band_median_correlation": summary[
                "weakest_band_median_correlation"
            ],
            "minimum_components_per_band": summary[
                "minimum_components_per_band"
            ],
            "minimum_stations_per_band": summary["minimum_stations_per_band"],
            "decision": summary["decision"],
            "status": summary["status"],
        }
        pair_summaries.append(summary_row)
        for metric in metrics:
            metric["reference_sequence_id"] = first_family
            metric["comparison_sequence_id"] = second_family
            metric["same_published_family"] = first_family == second_family
        pair_metrics.extend(metrics)

    score_rows, classifications = leave_one_event_out_classification(
        population,
        pair_summaries,
        list(streams),
        int(settings["network_family_maximum_anchors"]),
        float(settings["network_family_minimum_score"]),
        float(settings["network_family_minimum_margin"]),
    )
    summary = classification_summary(
        classifications, settings["target_sequence_id"]
    )
    status = {
        "status": STOP,
        "classification_gate": STOP,
        "continuous_detection_status": "NOT_RUN",
        "reason": "the HRSN correlation-only diagnostic classified no events under the frozen margin; continuous network detection, differential relocation, and bootstrap uncertainty are not complete",
        "method": "median of frozen top-k multi-anchor HRSN correlations with an explicit abstention margin",
        "scope": "waveform-correlation family-assignment diagnostic; not the final best-network baseline",
        "labeled_events": summary["labeled_events"],
        "waveform_available_events": summary["waveform_available_events"],
        "waveform_unavailable_events": summary["labeled_events"]
        - summary["waveform_available_events"],
        "classified_events": summary["classified_events"],
        "macro_f1_with_abstention_as_error": summary[
            "macro_f1_with_abstention_as_error"
        ],
        "target_recall": summary["target_recall"],
        "target_precision": summary["target_precision"],
        "neighbor_to_target_overmerge_count": summary[
            "neighbor_to_target_overmerge_count"
        ],
        "neighbor_to_target_overmerge_rate": summary[
            "neighbor_to_target_overmerge_rate"
        ],
        "top_family_accuracy_ignoring_abstention_policy": summary[
            "top_family_accuracy_ignoring_abstention_policy"
        ],
        "top_target_recall_ignoring_abstention_policy": summary[
            "top_target_recall_ignoring_abstention_policy"
        ],
        "top_target_precision_ignoring_abstention_policy": summary[
            "top_target_precision_ignoring_abstention_policy"
        ],
        "per_family": summary["per_family"],
        "thresholds": {
            "maximum_anchors_per_family": settings[
                "network_family_maximum_anchors"
            ],
            "minimum_score": settings["network_family_minimum_score"],
            "minimum_margin": settings["network_family_minimum_margin"],
        },
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    output = root / "outputs" / "external_validation"
    write_rows(output / "network_waveform_availability.csv", availability)
    write_rows(output / "network_pair_summaries.csv", pair_summaries)
    if pair_metrics:
        write_metrics(output / "network_pair_metrics.csv", pair_metrics)
    write_rows(output / "network_family_scores.csv", score_rows)
    write_rows(output / "network_family_classification.csv", classifications)
    write_json(output / "network_family_status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
