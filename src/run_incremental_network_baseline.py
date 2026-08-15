#!/usr/bin/env python
"""Train and freeze the strongest historical conventional-network verifier."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, List

from .common import (
    CONDITIONAL,
    PASS,
    STOP,
    load_config,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .coverage import write_rows
from .external_catalog import read_csv_rows
from .hrsn import compare_events, write_metrics
from .network_array import download_event
from .network_baseline import (
    aggregate_target_scores,
    apply_frozen_thresholds,
    fit_frozen_thresholds,
    freeze_gate,
    pair_feature_rows,
)


def _event_from_population(row: Dict[str, str]) -> Dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "origin_time": row.get("waveform_origin_time") or row["origin_time"],
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "depth_km": float(row["depth_km"]),
        "magnitude": float(row["magnitude"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "incremental_value.json",
    )
    parser.add_argument("--refresh-waveforms", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    root = project_root()
    population_path = (
        root
        / "outputs"
        / "external_validation"
        / "published_validation_population.csv"
    )
    population = read_csv_rows(population_path)
    if config["network_array"]["training_population"] != (
        "published_exact_id_families_only"
    ):
        raise ValueError("historical training population rule is not frozen")

    cache_root = root / "cached_event_windows" / "network_array"
    events: Dict[str, Dict[str, Any]] = {}
    streams: Dict[str, Any] = {}
    availability: List[Dict[str, Any]] = []
    population_by_id = {str(row["event_id"]): row for row in population}
    for row in population:
        event = _event_from_population(row)
        event_id = str(event["event_id"])
        events[event_id] = event
        stream, source_rows = download_event(
            event,
            config,
            cache_root,
            access_role="historical_exact_id_training",
            force=args.refresh_waveforms,
        )
        for source_row in source_rows:
            source_row["sequence_id"] = row["sequence_id"]
            source_row["validation_role"] = row["validation_role"]
        availability.extend(source_rows)
        if stream:
            streams[event_id] = stream

    comparison_config = {"hrsn": dict(config["network_array"])}
    pair_metrics: List[Dict[str, Any]] = []
    pair_summaries: List[Dict[str, Any]] = []
    for first_id, second_id in combinations(sorted(streams), 2):
        metrics, summary = compare_events(
            events[first_id],
            events[second_id],
            streams[first_id],
            streams[second_id],
            comparison_config,
            pair_name="full_network_{}_{}".format(first_id, second_id),
        )
        first_family = population_by_id[first_id]["sequence_id"]
        second_family = population_by_id[second_id]["sequence_id"]
        pair_summaries.append(
            {
                "pair_name": summary["pair_name"],
                "reference_event_id": first_id,
                "comparison_event_id": second_id,
                "reference_sequence_id": first_family,
                "comparison_sequence_id": second_family,
                "same_published_family": first_family == second_family,
                "usable_metric_count": summary["usable_metric_count"],
                "minimum_components_per_supported_band": summary[
                    "minimum_components_per_band"
                ],
                "minimum_stations_per_supported_band": summary[
                    "minimum_stations_per_band"
                ],
                "overall_median_correlation": summary[
                    "overall_median_correlation"
                ],
                "weakest_supported_band_median_correlation": summary[
                    "weakest_band_median_correlation"
                ],
                "status": summary["status"],
            }
        )
        for metric in metrics:
            metric["reference_sequence_id"] = first_family
            metric["comparison_sequence_id"] = second_family
            metric["same_published_family"] = first_family == second_family
        pair_metrics.extend(metrics)

    verifier = config["network_verifier"]
    features = pair_feature_rows(pair_metrics, verifier)
    score_rows = aggregate_target_scores(
        population,
        features,
        list(streams),
        verifier,
    )
    gate = freeze_gate(score_rows, verifier)

    output = root / "outputs" / "incremental_value" / "network_baseline"
    write_rows(output / "historical_source_availability.csv", availability)
    write_rows(output / "historical_pair_summaries.csv", pair_summaries)
    if pair_metrics:
        write_metrics(output / "historical_pair_metrics.csv", pair_metrics)
    if features:
        write_rows(output / "historical_pair_features.csv", features)
    write_rows(output / "historical_target_scores.csv", score_rows)

    model: Dict[str, Any] = {
        "freeze_status": "NOT_FROZEN",
        "scientific_status": STOP,
        "reason": "historical population gate did not pass",
        "config_sha256": config["_config_sha256"],
        "training_population_path": str(population_path),
        "training_population_sha256": sha256_file(population_path),
        "training_population_rule": config["network_array"][
            "training_population"
        ],
        "target_sequence_id": verifier["target_sequence_id"],
        "model_band_hz": verifier["model_band_hz"],
        "feature_definition": {
            "correlation": "median normalized positive-peak correlation over target anchors",
            "differential_lag": "median target-anchor network station-lag RMS after common-shift removal",
            "instrument_response_removed": False,
            "amplitude_used": False,
        },
        "freeze_gate": gate,
        "historical_available_event_ids": sorted(streams),
        "target_anchor_event_ids": sorted(
            event_id
            for event_id in streams
            if population_by_id[event_id]["sequence_id"]
            == verifier["target_sequence_id"]
        ),
        "network_sources": config["network_array"]["sources"],
        "threshold_fit_rule": verifier["threshold_fit"],
        "selection_order": verifier["selection_order"],
        "generated_utc": utc_now(),
    }
    decisions: List[Dict[str, Any]] = []
    if gate["status"] == PASS:
        thresholds = fit_frozen_thresholds(score_rows)
        model.update(thresholds)
        model["freeze_status"] = "FROZEN_FOR_PROSPECTIVE_SCORING"
        model["scientific_status"] = CONDITIONAL
        model["reason"] = (
            "model mechanics are frozen; apparent historical fit is not prospective validation"
        )
        decisions = apply_frozen_thresholds(score_rows, model)
        write_rows(output / "historical_frozen_decisions.csv", decisions)

    metric_paths = [
        output / "historical_pair_metrics.csv",
        output / "historical_pair_features.csv",
        output / "historical_target_scores.csv",
    ]
    model["training_product_sha256"] = {
        path.name: sha256_file(path) for path in metric_paths if path.exists()
    }
    model_path = output / "network_model_frozen.json"
    write_json(model_path, model)

    source_counts = Counter(
        (row["source_name"], row["status"]) for row in availability
    )
    status = {
        "status": CONDITIONAL if gate["status"] == PASS else STOP,
        "model_freeze_status": model["freeze_status"],
        "model_path": str(model_path),
        "candidate_waveform_access": (
            "RELEASED_FOR_BLIND_NETWORK_SCORING"
            if gate["status"] == PASS
            else "EMBARGOED"
        ),
        "labeled_population_events": len(population),
        "events_with_any_network_waveform": len(streams),
        "historical_target_events_eligible": gate[
            "eligible_historical_target_events"
        ],
        "historical_neighbor_events_eligible": gate[
            "eligible_historical_neighbor_events"
        ],
        "source_availability_counts": {
            "{}:{}".format(source, state): count
            for (source, state), count in sorted(source_counts.items())
        },
        "historical_apparent_balanced_accuracy": model.get(
            "balanced_accuracy"
        ),
        "historical_apparent_target_precision": model.get(
            "target_precision"
        ),
        "historical_apparent_target_recall": model.get("target_recall"),
        "prospective_validation_status": "NOT_RUN",
        "continuous_detection_status": "NOT_RUN",
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    write_json(output / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
