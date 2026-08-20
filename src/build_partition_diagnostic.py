#!/usr/bin/env python
"""Quantify conventional-network separation of the conflicting family partitions."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

import numpy as np

from .common import STOP, project_root, utc_now, write_json
from .coverage import write_rows
from .external_catalog import read_csv_rows


PARTITION_SEQUENCES = ("M00413", "M00414")


def _is_true(value: Any) -> bool:
    return value is True or str(value).strip().lower() == "true"


def _auc(
    positive_values: Sequence[float],
    negative_values: Sequence[float],
    higher_is_positive: bool,
) -> float | None:
    if not positive_values or not negative_values:
        return None
    favorable = 0.0
    total = 0
    for positive in positive_values:
        for negative in negative_values:
            difference = positive - negative
            if not higher_is_positive:
                difference = -difference
            favorable += 1.0 if difference > 0.0 else (0.5 if difference == 0.0 else 0.0)
            total += 1
    return favorable / total


def main() -> None:
    root = project_root()
    crosswalk = read_csv_rows(
        root / "outputs" / "michel_validation" / "exact_id_crosswalk.csv"
    )
    mapping = {
        str(row["event_id"]): str(row["michel_sequence_id"])
        for row in crosswalk
        if _is_true(row["michel_exact_id_match"])
        and str(row["michel_sequence_id"]) in PARTITION_SEQUENCES
    }
    features = read_csv_rows(
        root
        / "outputs"
        / "incremental_value"
        / "network_baseline"
        / "historical_pair_features.csv"
    )
    rows: List[Dict[str, Any]] = []
    for feature in features:
        first = str(feature["reference_event_id"])
        second = str(feature["comparison_event_id"])
        if first not in mapping or second not in mapping:
            continue
        first_sequence = mapping[first]
        second_sequence = mapping[second]
        relation = (
            "within_{}".format(first_sequence)
            if first_sequence == second_sequence
            else "between_M00413_M00414"
        )
        rows.append(
            {
                "reference_event_id": first,
                "comparison_event_id": second,
                "reference_michel_sequence_id": first_sequence,
                "comparison_michel_sequence_id": second_sequence,
                "partition_relation": relation,
                "median_correlation": float(feature["median_correlation"]),
                "differential_lag_rms_s": float(
                    feature["differential_lag_rms_s"]
                ),
                "usable_component_count": int(
                    feature["usable_component_count"]
                ),
                "usable_station_count": int(
                    feature["usable_station_count"]
                ),
                "status": feature["status"],
            }
        )

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["status"] == "PASS":
            grouped[row["partition_relation"]].append(row)
    summaries: List[Dict[str, Any]] = []
    for relation in (
        "within_M00413",
        "within_M00414",
        "between_M00413_M00414",
    ):
        selected = grouped.get(relation, [])
        correlations = np.asarray(
            [row["median_correlation"] for row in selected], dtype=float
        )
        lags = np.asarray(
            [row["differential_lag_rms_s"] for row in selected], dtype=float
        )
        summaries.append(
            {
                "partition_relation": relation,
                "pair_count": len(selected),
                "median_correlation": (
                    float(np.median(correlations))
                    if correlations.size
                    else ""
                ),
                "minimum_correlation": (
                    float(np.min(correlations))
                    if correlations.size
                    else ""
                ),
                "maximum_correlation": (
                    float(np.max(correlations))
                    if correlations.size
                    else ""
                ),
                "median_differential_lag_rms_s": (
                    float(np.median(lags)) if lags.size else ""
                ),
                "minimum_differential_lag_rms_s": (
                    float(np.min(lags)) if lags.size else ""
                ),
                "maximum_differential_lag_rms_s": (
                    float(np.max(lags)) if lags.size else ""
                ),
            }
        )

    within = (
        grouped.get("within_M00413", [])
        + grouped.get("within_M00414", [])
    )
    between = grouped.get("between_M00413_M00414", [])
    correlation_auc = _auc(
        [row["median_correlation"] for row in within],
        [row["median_correlation"] for row in between],
        higher_is_positive=True,
    )
    lag_auc = _auc(
        [row["differential_lag_rms_s"] for row in within],
        [row["differential_lag_rms_s"] for row in between],
        higher_is_positive=False,
    )

    output = root / "outputs" / "michel_validation" / "partition_diagnostic"
    write_rows(output / "network_pair_partition_features.csv", rows)
    write_rows(output / "network_partition_summaries.csv", summaries)
    status = {
        "status": STOP,
        "conventional_partition_separation_claim": STOP,
        "reason": "diagnostic pair distributions overlap and labels are not event-independent",
        "mapped_event_count": len(mapping),
        "mapped_event_ids": sorted(mapping),
        "within_pair_count": len(within),
        "between_pair_count": len(between),
        "correlation_pair_auc_within_vs_between": correlation_auc,
        "differential_lag_pair_auc_within_vs_between": lag_auc,
        "uncertainty_status": "event_block_bootstrap_not_identifiable_with_two_M00413_historical_events",
        "allowed_interpretation": "comparator diagnostic motivating a held-out DAS spatial-resolution test",
        "generated_utc": utc_now(),
    }
    write_json(output / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
