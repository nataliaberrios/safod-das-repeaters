#!/usr/bin/env python
"""Score the named 2026 deep-DAS candidate and hard control network-only."""

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


def _verify_model(
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
    parser.add_argument(
        "--pilot-config",
        type=Path,
        default=project_root() / "config" / "pilot.json",
    )
    parser.add_argument("--refresh-waveforms", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    pilot = load_config(args.pilot_config)
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
    _verify_model(model, model_path, population_path)
    population = read_csv_rows(population_path)
    target_rows = [
        row
        for row in population
        if row["sequence_id"] == model["target_sequence_id"]
    ]

    named_roles = {
        "deep_template": "prospective_deep_das_candidate",
        "hard_spatial_control": "prospective_same_fiber_hard_control",
    }
    named_events: Dict[str, Dict[str, Any]] = {}
    event_roles: Dict[str, str] = {}
    for name, role in named_roles.items():
        event = dict(pilot["events"][name])
        event["event_id"] = str(event["event_id"])
        named_events[event["event_id"]] = event
        event_roles[event["event_id"]] = role

    cache_root = root / "cached_event_windows" / "network_array"
    anchor_events: Dict[str, Dict[str, Any]] = {}
    anchor_streams: Dict[str, Any] = {}
    for row in target_rows:
        event = {
            "event_id": str(row["event_id"]),
            "origin_time": row.get("waveform_origin_time") or row["origin_time"],
            "latitude": float(row["latitude"]),
            "longitude": float(row["longitude"]),
            "depth_km": float(row["depth_km"]),
            "magnitude": float(row["magnitude"]),
        }
        stream, _ = download_event(
            event,
            config,
            cache_root,
            access_role="historical_exact_id_training",
        )
        if stream:
            anchor_events[event["event_id"]] = event
            anchor_streams[event["event_id"]] = stream

    named_streams: Dict[str, Any] = {}
    availability: List[Dict[str, Any]] = []
    for event_id, event in named_events.items():
        stream, source_rows = download_event(
            event,
            config,
            cache_root,
            access_role="prospective_scoring",
            frozen_model_path=model_path,
            force=args.refresh_waveforms,
        )
        for row in source_rows:
            row["named_role"] = event_roles[event_id]
        availability.extend(source_rows)
        if stream:
            named_streams[event_id] = stream

    comparison_config = {"hrsn": dict(config["network_array"])}
    metrics: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    for event_id in sorted(named_streams):
        for anchor_id in sorted(anchor_streams):
            pair_metrics, summary = compare_events(
                named_events[event_id],
                anchor_events[anchor_id],
                named_streams[event_id],
                anchor_streams[anchor_id],
                comparison_config,
                pair_name="deep_named_{}_anchor_{}".format(
                    event_id, anchor_id
                ),
            )
            summaries.append(
                {
                    "event_id": event_id,
                    "named_role": event_roles[event_id],
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
            "event_id": event_id,
            "origin_time": event["origin_time"],
            "sequence_id": event_roles[event_id],
        }
        for event_id, event in named_events.items()
    )
    scores = aggregate_target_scores(
        combined,
        features,
        list(anchor_streams) + list(named_streams),
        config["network_verifier"],
    )
    named_ids = set(named_events)
    named_scores = [
        row for row in scores if str(row["event_id"]) in named_ids
    ]
    decisions = apply_frozen_thresholds(named_scores, model)
    for row in decisions:
        row["named_role"] = event_roles[str(row["event_id"])]
        row["catalog_partition_warning"] = (
            "Waldhauser-Schaff and Michel exact-ID family labels conflict"
        )

    output = (
        root / "outputs" / "incremental_value" / "deep_named_network"
    )
    write_rows(output / "source_availability.csv", availability)
    write_rows(output / "anchor_summaries.csv", summaries)
    if metrics:
        write_metrics(output / "anchor_metrics.csv", metrics)
    if features:
        write_rows(output / "anchor_features.csv", features)
    write_rows(output / "scores.csv", named_scores)
    write_rows(output / "decisions.csv", decisions)

    decision_by_id = {
        str(row["event_id"]): row["frozen_decision"] for row in decisions
    }
    status = {
        "status": CONDITIONAL if decisions else STOP,
        "stage": "named_2026_network_only_scoring_complete",
        "event_decisions": decision_by_id,
        "deep_candidate_event_id": str(
            pilot["events"]["deep_template"]["event_id"]
        ),
        "hard_control_event_id": str(
            pilot["events"]["hard_spatial_control"]["event_id"]
        ),
        "das_waveforms_opened_by_this_stage": 0,
        "network_model_sha256": sha256_file(model_path),
        "catalog_partition_warning": "classification name is provisional because exact-ID catalogs disagree",
        "config_sha256": config["_config_sha256"],
        "pilot_config_sha256": pilot["_config_sha256"],
        "generated_utc": utc_now(),
    }
    write_json(output / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
