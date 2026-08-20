#!/usr/bin/env python
"""Apply the frozen network model to post-2014 Michel exact-ID continuations."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from .common import CONDITIONAL, STOP, load_config, project_root, sha256_file, utc_now, write_json
from .coverage import write_rows
from .external_catalog import read_csv_rows
from .hrsn import compare_events, write_metrics
from .network_array import download_event
from .network_baseline import aggregate_target_scores, apply_frozen_thresholds, pair_feature_rows


def _event(row: Dict[str, str], origin_field: str = "origin_time") -> Dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "origin_time": row.get("waveform_origin_time") or row[origin_field],
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "depth_km": float(row["depth_km"]),
        "magnitude": float(row["magnitude"]),
    }


def _verify_frozen_products(
    model: Dict[str, Any], model_path: Path, population_path: Path
) -> None:
    if model.get("freeze_status") != "FROZEN_FOR_PROSPECTIVE_SCORING":
        raise RuntimeError("network model is not frozen")
    if sha256_file(population_path) != model["training_population_sha256"]:
        raise RuntimeError("historical population changed after model freeze")
    for filename, expected in model["training_product_sha256"].items():
        path = model_path.parent / filename
        if not path.exists() or sha256_file(path) != expected:
            raise RuntimeError(
                "frozen training product changed: {}".format(filename)
            )


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
    baseline = root / "outputs" / "incremental_value" / "network_baseline"
    model_path = baseline / "network_model_frozen.json"
    with model_path.open("r", encoding="utf-8") as handle:
        model = json.load(handle)
    if model["config_sha256"] != config["_config_sha256"]:
        raise RuntimeError("current configuration differs from frozen model")

    population_path = (
        root
        / "outputs"
        / "external_validation"
        / "published_validation_population.csv"
    )
    _verify_frozen_products(model, model_path, population_path)
    population = read_csv_rows(population_path)
    target_rows = [
        row
        for row in population
        if row["sequence_id"] == model["target_sequence_id"]
    ]
    continuation_path = (
        root
        / "outputs"
        / "michel_validation"
        / "post_2014_continuations.csv"
    )
    continuations = read_csv_rows(continuation_path)

    cache_root = root / "cached_event_windows" / "network_array"
    anchor_events: Dict[str, Dict[str, Any]] = {}
    anchor_streams: Dict[str, Any] = {}
    for row in target_rows:
        event = _event(row)
        stream, _ = download_event(
            event,
            config,
            cache_root,
            access_role="historical_exact_id_training",
        )
        if stream:
            anchor_events[event["event_id"]] = event
            anchor_streams[event["event_id"]] = stream

    validation_events: Dict[str, Dict[str, Any]] = {}
    validation_streams: Dict[str, Any] = {}
    availability: List[Dict[str, Any]] = []
    continuation_by_id = {
        str(row["event_id"]): row for row in continuations
    }
    for row in continuations:
        event = _event(row)
        event_id = str(event["event_id"])
        validation_events[event_id] = event
        stream, source_rows = download_event(
            event,
            config,
            cache_root,
            access_role="external_exact_id_validation",
            frozen_model_path=model_path,
            force=args.refresh_waveforms,
        )
        for source_row in source_rows:
            source_row["michel_sequence_id"] = row[
                "michel_sequence_id"
            ]
        availability.extend(source_rows)
        if stream:
            validation_streams[event_id] = stream

    comparison_config = {"hrsn": dict(config["network_array"])}
    metrics: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    for event_id in sorted(validation_streams):
        for anchor_id in sorted(anchor_streams):
            pair_metrics, summary = compare_events(
                validation_events[event_id],
                anchor_events[anchor_id],
                validation_streams[event_id],
                anchor_streams[anchor_id],
                comparison_config,
                pair_name="michel_continuation_{}_anchor_{}".format(
                    event_id, anchor_id
                ),
            )
            summaries.append(
                {
                    "event_id": event_id,
                    "michel_sequence_id": continuation_by_id[event_id][
                        "michel_sequence_id"
                    ],
                    "target_anchor_event_id": anchor_id,
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
                    "status": summary["status"],
                }
            )
            metrics.extend(pair_metrics)

    features = pair_feature_rows(metrics, config["network_verifier"])
    combined: List[Dict[str, Any]] = [
        {
            "event_id": row["event_id"],
            "origin_time": row["origin_time"],
            "sequence_id": row["sequence_id"],
        }
        for row in target_rows
    ]
    combined.extend(
        {
            "event_id": row["event_id"],
            "origin_time": row["origin_time"],
            "sequence_id": row["michel_sequence_id"],
        }
        for row in continuations
    )
    scores = aggregate_target_scores(
        combined,
        features,
        list(anchor_streams) + list(validation_streams),
        config["network_verifier"],
    )
    continuation_ids = {str(row["event_id"]) for row in continuations}
    validation_scores = [
        row for row in scores if str(row["event_id"]) in continuation_ids
    ]
    decisions = apply_frozen_thresholds(validation_scores, model)
    for row in decisions:
        row["michel_sequence_id"] = continuation_by_id[
            str(row["event_id"])
        ]["michel_sequence_id"]
        row["truth_interpretation"] = (
            "published_Michel_label_but_cross_catalog_partition_conflicts"
        )

    output = root / "outputs" / "michel_validation" / "frozen_network"
    write_rows(output / "source_availability.csv", availability)
    write_rows(output / "anchor_summaries.csv", summaries)
    if metrics:
        write_metrics(output / "anchor_metrics.csv", metrics)
    if features:
        write_rows(output / "anchor_features.csv", features)
    write_rows(output / "scores.csv", validation_scores)
    write_rows(output / "decisions.csv", decisions)

    predicted_target = [
        str(row["event_id"])
        for row in decisions
        if row["frozen_decision"] == "target_family"
    ]
    status = {
        "status": CONDITIONAL if decisions else STOP,
        "stage": "post_freeze_external_exact_id_diagnostic",
        "event_count": len(continuations),
        "scored_event_count": len(decisions),
        "frozen_model_predicted_target_count": len(predicted_target),
        "frozen_model_predicted_target_event_ids": predicted_target,
        "michel_sequence_ids": sorted(
            {str(row["michel_sequence_id"]) for row in continuations}
        ),
        "accuracy_not_reported_reason": "Michel and Waldhauser-Schaff family partitions conflict on exact shared events",
        "network_model_sha256": sha256_file(model_path),
        "continuation_population_sha256": sha256_file(continuation_path),
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    write_json(output / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
