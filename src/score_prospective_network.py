#!/usr/bin/env python
"""Score prospective archive candidates with the frozen network-only verifier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from .common import (
    CONDITIONAL,
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
    pair_feature_rows,
)


def _historical_event(row: Dict[str, str]) -> Dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "origin_time": row.get("waveform_origin_time") or row["origin_time"],
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "depth_km": float(row["depth_km"]),
        "magnitude": float(row["magnitude"]),
    }


def _candidate_event(row: Dict[str, str]) -> Dict[str, Any]:
    return {
        "event_id": str(row["event_id"]),
        "origin_time": row["origin_time"],
        "latitude": float(row["latitude"]),
        "longitude": float(row["longitude"]),
        "depth_km": float(row["depth_km"]),
        "magnitude": float(row["magnitude"]),
    }


def _verify_model_products(
    model: Dict[str, Any], model_path: Path, population_path: Path
) -> None:
    if model.get("freeze_status") != "FROZEN_FOR_PROSPECTIVE_SCORING":
        raise RuntimeError("network model is not frozen")
    if sha256_file(population_path) != model["training_population_sha256"]:
        raise RuntimeError("historical training population changed after freeze")
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
    if model.get("config_sha256") != config["_config_sha256"]:
        raise RuntimeError("current configuration differs from frozen model")

    population_path = (
        root
        / "outputs"
        / "external_validation"
        / "published_validation_population.csv"
    )
    _verify_model_products(model, model_path, population_path)
    historical_population = read_csv_rows(population_path)
    target_sequence = str(model["target_sequence_id"])
    target_rows = [
        row
        for row in historical_population
        if str(row["sequence_id"]) == target_sequence
    ]

    candidate_path = (
        root / "outputs" / "incremental_value" / "prospective_candidates.csv"
    )
    candidates = read_csv_rows(candidate_path)
    cache_root = root / "cached_event_windows" / "network_array"
    anchor_streams: Dict[str, Any] = {}
    anchor_events: Dict[str, Dict[str, Any]] = {}
    for row in target_rows:
        event = _historical_event(row)
        stream, _ = download_event(
            event,
            config,
            cache_root,
            access_role="historical_exact_id_training",
            force=False,
        )
        if stream:
            anchor_events[event["event_id"]] = event
            anchor_streams[event["event_id"]] = stream

    candidate_streams: Dict[str, Any] = {}
    candidate_events: Dict[str, Dict[str, Any]] = {}
    availability: List[Dict[str, Any]] = []
    for row in candidates:
        event = _candidate_event(row)
        event_id = str(event["event_id"])
        candidate_events[event_id] = event
        stream, source_rows = download_event(
            event,
            config,
            cache_root,
            access_role="prospective_scoring",
            frozen_model_path=model_path,
            force=args.refresh_waveforms,
        )
        availability.extend(source_rows)
        if stream:
            candidate_streams[event_id] = stream

    comparison_config = {"hrsn": dict(config["network_array"])}
    metrics: List[Dict[str, Any]] = []
    summaries: List[Dict[str, Any]] = []
    for candidate_id in sorted(candidate_streams):
        for anchor_id in sorted(anchor_streams):
            pair_metrics, summary = compare_events(
                candidate_events[candidate_id],
                anchor_events[anchor_id],
                candidate_streams[candidate_id],
                anchor_streams[anchor_id],
                comparison_config,
                pair_name="prospective_{}_target_anchor_{}".format(
                    candidate_id, anchor_id
                ),
            )
            summaries.append(
                {
                    "candidate_event_id": candidate_id,
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
    combined_population: List[Dict[str, Any]] = [
        {
            "event_id": row["event_id"],
            "origin_time": row["origin_time"],
            "sequence_id": row["sequence_id"],
        }
        for row in target_rows
    ]
    combined_population.extend(
        {
            "event_id": row["event_id"],
            "origin_time": row["origin_time"],
            "sequence_id": "PROSPECTIVE_UNLABELED",
        }
        for row in candidates
    )
    score_rows = aggregate_target_scores(
        combined_population,
        features,
        list(anchor_streams) + list(candidate_streams),
        config["network_verifier"],
    )
    candidate_ids = {str(row["event_id"]) for row in candidates}
    candidate_scores = [
        row for row in score_rows if str(row["event_id"]) in candidate_ids
    ]
    decisions = apply_frozen_thresholds(candidate_scores, model)

    output = root / "outputs" / "incremental_value" / "prospective_network"
    write_rows(output / "source_availability.csv", availability)
    write_rows(output / "candidate_anchor_summaries.csv", summaries)
    if metrics:
        write_metrics(output / "candidate_anchor_metrics.csv", metrics)
    if features:
        write_rows(output / "candidate_anchor_features.csv", features)
    write_rows(output / "candidate_scores.csv", candidate_scores)
    write_rows(output / "frozen_network_decisions.csv", decisions)

    predicted_target = [
        row["event_id"]
        for row in decisions
        if row["frozen_decision"] == "target_family"
    ]
    abstained = [
        row["event_id"]
        for row in decisions
        if row["frozen_decision"] == "abstain"
    ]
    status = {
        "status": CONDITIONAL if decisions else STOP,
        "stage": "prospective_network_only_scoring_complete",
        "network_model_path": str(model_path),
        "network_model_sha256": sha256_file(model_path),
        "network_model_freeze_status": model["freeze_status"],
        "candidate_population_path": str(candidate_path),
        "candidate_population_sha256": sha256_file(candidate_path),
        "candidate_count": len(candidates),
        "candidate_with_any_network_waveform_count": len(candidate_streams),
        "candidate_scored_count": len(decisions) - len(abstained),
        "candidate_abstain_count": len(abstained),
        "network_predicted_target_count": len(predicted_target),
        "network_predicted_target_event_ids": predicted_target,
        "network_abstain_event_ids": abstained,
        "blind_to_das_waveforms": True,
        "das_waveform_files_opened_by_this_stage": 0,
        "interpretation": "frozen network predictions only; no prospective family truth labels yet",
        "prospective_validation_status": "AWAITING_INDEPENDENT_DAS_AND_ADJUDICATION",
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    write_json(output / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
