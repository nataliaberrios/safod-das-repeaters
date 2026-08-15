"""Target-family verifier built from correlation and network differential lags.

For each event pair, the median lag common to the whole network is removed.
The remaining interstation lag scatter carries relative-source-location
information that a single waveform-correlation number discards. Thresholds are
fit only to historical exact-ID published labels and then frozen.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from .common import PASS, STOP


def _station_id(metric: Mapping[str, Any]) -> str:
    fields = str(metric["trace_id"]).split(".")
    if len(fields) >= 2:
        return ".".join(fields[:2])
    return str(metric["station"])


def pair_feature_rows(
    metrics: Sequence[Mapping[str, Any]], settings: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    """Reduce component metrics to one fixed-band feature row per event pair."""

    band = settings["model_band_hz"]
    grouped: Dict[Tuple[str, str], List[Mapping[str, Any]]] = defaultdict(list)
    for row in metrics:
        if not bool(row["usable"]):
            continue
        if (
            not math.isclose(
                float(row["band_low_hz"]),
                float(band[0]),
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
            or not math.isclose(
                float(row["band_high_hz"]),
                float(band[1]),
                rel_tol=0.0,
                abs_tol=1.0e-9,
            )
        ):
            continue
        pair = tuple(
            sorted(
                [
                    str(row["reference_event_id"]),
                    str(row["comparison_event_id"]),
                ]
            )
        )
        grouped[pair].append(row)

    features: List[Dict[str, Any]] = []
    for pair in sorted(grouped):
        rows = grouped[pair]
        correlations = np.asarray(
            [float(row["correlation"]) for row in rows], dtype=float
        )
        station_lags: Dict[str, List[float]] = defaultdict(list)
        for row in rows:
            station_lags[_station_id(row)].append(float(row["lag_s"]))
        station_medians = np.asarray(
            [
                float(np.median(station_lags[station]))
                for station in sorted(station_lags)
            ],
            dtype=float,
        )
        common_lag_s = (
            float(np.median(station_medians))
            if station_medians.size
            else float("nan")
        )
        residuals = station_medians - common_lag_s
        lag_rms_s = (
            float(np.sqrt(np.mean(residuals ** 2)))
            if residuals.size
            else float("nan")
        )
        lag_mad_s = (
            float(np.median(np.abs(residuals)))
            if residuals.size
            else float("nan")
        )
        lag_span_s = (
            float(np.percentile(station_medians, 90) - np.percentile(station_medians, 10))
            if station_medians.size >= 2
            else float("nan")
        )
        station_count = len(station_lags)
        component_count = len(rows)
        enough_data = (
            component_count >= int(settings["minimum_pair_components"])
            and station_count >= int(settings["minimum_pair_stations"])
        )
        features.append(
            {
                "reference_event_id": pair[0],
                "comparison_event_id": pair[1],
                "band_low_hz": float(band[0]),
                "band_high_hz": float(band[1]),
                "usable_component_count": component_count,
                "usable_station_count": station_count,
                "median_correlation": float(np.median(correlations)),
                "p10_correlation": float(np.percentile(correlations, 10)),
                "common_lag_s": common_lag_s,
                "differential_lag_rms_s": lag_rms_s,
                "differential_lag_mad_s": lag_mad_s,
                "station_lag_p10_p90_span_s": lag_span_s,
                "status": PASS if enough_data else STOP,
                "lag_definition": "station_median_peak_lag_minus_network_station_median",
            }
        )
    return features


def _pair_lookup(
    features: Sequence[Mapping[str, Any]],
) -> Dict[frozenset, Mapping[str, Any]]:
    return {
        frozenset(
            [
                str(row["reference_event_id"]),
                str(row["comparison_event_id"]),
            ]
        ): row
        for row in features
        if str(row["reference_event_id"]) != str(row["comparison_event_id"])
    }


def aggregate_target_scores(
    population: Sequence[Mapping[str, Any]],
    pair_features: Sequence[Mapping[str, Any]],
    available_event_ids: Sequence[str],
    settings: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Score each historical event against leave-one-event-out target anchors."""

    available = set(map(str, available_event_ids))
    target_sequence = str(settings["target_sequence_id"])
    target_anchors = sorted(
        str(row["event_id"])
        for row in population
        if str(row["sequence_id"]) == target_sequence
        and str(row["event_id"]) in available
    )
    lookup = _pair_lookup(pair_features)
    rows: List[Dict[str, Any]] = []
    for event in population:
        event_id = str(event["event_id"])
        if event_id not in available:
            rows.append(
                {
                    "event_id": event_id,
                    "origin_time": event["origin_time"],
                    "published_sequence_id": event["sequence_id"],
                    "is_published_target": str(event["sequence_id"])
                    == target_sequence,
                    "eligible_target_anchor_count": 0,
                    "target_anchors_used": 0,
                    "median_target_correlation": "",
                    "median_target_differential_lag_rms_s": "",
                    "score_status": STOP,
                    "score_reason": "event_waveform_unavailable",
                }
            )
            continue
        eligible: List[Tuple[str, float, float]] = []
        for anchor_id in target_anchors:
            if anchor_id == event_id:
                continue
            pair = lookup.get(frozenset([event_id, anchor_id]))
            if not pair or pair.get("status") != PASS:
                continue
            correlation = float(pair["median_correlation"])
            lag_rms = float(pair["differential_lag_rms_s"])
            if math.isfinite(correlation) and math.isfinite(lag_rms):
                eligible.append((anchor_id, correlation, lag_rms))
        eligible.sort(key=lambda item: item[1], reverse=True)
        selected = eligible[: int(settings["maximum_target_anchors"])]
        enough = len(selected) >= int(settings["minimum_target_anchors"])
        rows.append(
            {
                "event_id": event_id,
                "origin_time": event["origin_time"],
                "published_sequence_id": event["sequence_id"],
                "is_published_target": str(event["sequence_id"])
                == target_sequence,
                "eligible_target_anchor_count": len(eligible),
                "target_anchors_used": len(selected),
                "target_anchor_event_ids": ";".join(
                    item[0] for item in selected
                ),
                "median_target_correlation": (
                    float(np.median([item[1] for item in selected]))
                    if selected
                    else ""
                ),
                "median_target_differential_lag_rms_s": (
                    float(np.median([item[2] for item in selected]))
                    if selected
                    else ""
                ),
                "score_status": PASS if enough else STOP,
                "score_reason": (
                    "eligible"
                    if enough
                    else "fewer_than_minimum_target_anchors"
                ),
            }
        )
    return rows


def _threshold_grid(values: Sequence[float]) -> List[float]:
    unique = sorted(set(float(value) for value in values))
    if not unique:
        return []
    scale = max(1.0, max(abs(value) for value in unique))
    epsilon = np.finfo(float).eps * scale * 16.0
    thresholds = [unique[0] - epsilon]
    thresholds.extend(
        (first + second) / 2.0
        for first, second in zip(unique[:-1], unique[1:])
    )
    thresholds.append(unique[-1] + epsilon)
    return thresholds


def _decision_metrics(
    rows: Sequence[Mapping[str, Any]],
    correlation_threshold: float,
    lag_threshold_s: float,
) -> Dict[str, Any]:
    target_rows = [row for row in rows if bool(row["is_published_target"])]
    neighbor_rows = [
        row for row in rows if not bool(row["is_published_target"])
    ]

    def predicts_target(row: Mapping[str, Any]) -> bool:
        return (
            float(row["median_target_correlation"]) >= correlation_threshold
            and float(row["median_target_differential_lag_rms_s"])
            <= lag_threshold_s
        )

    true_positive = sum(predicts_target(row) for row in target_rows)
    false_negative = len(target_rows) - true_positive
    false_positive = sum(predicts_target(row) for row in neighbor_rows)
    true_negative = len(neighbor_rows) - false_positive
    recall = true_positive / len(target_rows) if target_rows else 0.0
    specificity = true_negative / len(neighbor_rows) if neighbor_rows else 0.0
    precision = (
        true_positive / (true_positive + false_positive)
        if true_positive + false_positive
        else 0.0
    )
    return {
        "true_positive": true_positive,
        "false_negative": false_negative,
        "false_positive": false_positive,
        "true_negative": true_negative,
        "target_recall": recall,
        "target_precision": precision,
        "neighbor_specificity": specificity,
        "balanced_accuracy": 0.5 * (recall + specificity),
    }


def fit_frozen_thresholds(
    score_rows: Sequence[Mapping[str, Any]]
) -> Dict[str, Any]:
    """Fit deterministic two-gate thresholds on eligible historical labels."""

    eligible = [row for row in score_rows if row["score_status"] == PASS]
    correlation_values = [
        float(row["median_target_correlation"]) for row in eligible
    ]
    lag_values = [
        float(row["median_target_differential_lag_rms_s"])
        for row in eligible
    ]
    candidates: List[Dict[str, Any]] = []
    for correlation_threshold in _threshold_grid(correlation_values):
        for lag_threshold_s in _threshold_grid(lag_values):
            metrics = _decision_metrics(
                eligible, correlation_threshold, lag_threshold_s
            )
            candidates.append(
                {
                    "correlation_threshold": correlation_threshold,
                    "differential_lag_rms_threshold_s": lag_threshold_s,
                    **metrics,
                }
            )
    if not candidates:
        raise ValueError("no eligible historical scores for threshold fitting")
    best = max(
        candidates,
        key=lambda row: (
            row["balanced_accuracy"],
            row["target_precision"],
            row["target_recall"],
            row["correlation_threshold"],
            -row["differential_lag_rms_threshold_s"],
        ),
    )
    best["candidate_threshold_pair_count"] = len(candidates)
    best["fit_population_event_count"] = len(eligible)
    best["fit_interpretation"] = (
        "apparent historical training performance; prospective validation required"
    )
    return best


def apply_frozen_thresholds(
    score_rows: Sequence[Mapping[str, Any]],
    model: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Apply a frozen target-family decision with explicit abstention."""

    correlation_threshold = float(model["correlation_threshold"])
    lag_threshold = float(model["differential_lag_rms_threshold_s"])
    rows: List[Dict[str, Any]] = []
    for score in score_rows:
        row = dict(score)
        if score["score_status"] != PASS:
            decision = "abstain"
            reason = str(score["score_reason"])
        else:
            correlation_pass = (
                float(score["median_target_correlation"])
                >= correlation_threshold
            )
            lag_pass = (
                float(score["median_target_differential_lag_rms_s"])
                <= lag_threshold
            )
            decision = (
                "target_family" if correlation_pass and lag_pass else "not_target_family"
            )
            reason = "both_gates_pass" if decision == "target_family" else (
                "correlation_gate_fail"
                if not correlation_pass
                else "differential_lag_gate_fail"
            )
        row["frozen_decision"] = decision
        row["decision_reason"] = reason
        row["correlation_threshold"] = correlation_threshold
        row["differential_lag_rms_threshold_s"] = lag_threshold
        rows.append(row)
    return rows


def freeze_gate(
    score_rows: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    eligible = [row for row in score_rows if row["score_status"] == PASS]
    target_count = sum(bool(row["is_published_target"]) for row in eligible)
    neighbor_count = len(eligible) - target_count
    passes = (
        target_count >= int(settings["minimum_historical_target_events"])
        and neighbor_count
        >= int(settings["minimum_historical_neighbor_events"])
    )
    return {
        "status": PASS if passes else STOP,
        "eligible_historical_target_events": target_count,
        "eligible_historical_neighbor_events": neighbor_count,
        "minimum_historical_target_events": int(
            settings["minimum_historical_target_events"]
        ),
        "minimum_historical_neighbor_events": int(
            settings["minimum_historical_neighbor_events"]
        ),
    }
