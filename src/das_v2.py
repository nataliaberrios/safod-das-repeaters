"""Frozen version-2 spatial-coherence rule for SAFOD DAS candidates.

Version 2 is a transparently development-tuned subset of version 1.  It keeps
the v1 peak, null threshold, timing, preprocessing, and channel definitions and
adds only the registered minimum number of blocks at the existing ratio of 2.
No function in this module reads network, catalog, family, or held-out data.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence


DEVELOPMENT_CANDIDATE_FIELDS = [
    "candidate_id",
    "parent_v1_candidate_id",
    "trigger_time",
    "trigger_epoch_s",
    "coincidence_score",
    "v1_interval_null_threshold",
    "strong_block_characteristic_ratio",
    "strong_block_count",
    "total_registered_block_count",
    "strong_block_fraction",
    "minimum_required_strong_block_count",
    "v1_score_rank",
    "candidate_generation_label",
    "network_candidate_time_fields_read",
    "catalog_event_time_fields_read",
    "heldout_waveform_fields_read",
    "family_assignment",
]


def validate_v2_inheritance(
    v2: Mapping[str, Any], v1: Mapping[str, Any]
) -> None:
    """Prove that v2 changes only the disclosed block-support gate."""

    if str(v2["registration_state"]) != (
        "DEVELOPMENT_TUNED_AFTER_V1_COMPARISON_BEFORE_ANY_"
        "HELDOUT_WAVEFORM_ACCESS"
    ):
        raise PermissionError("v2 was not registered before held-out access")
    disclosure = v2["development_tuning_disclosure"]
    if str(disclosure["interpretation"]) != (
        "v2_is_posthoc_development_tuning_not_independent_validation"
    ):
        raise PermissionError("v2 tuning disclosure is incomplete")
    if bool(disclosure["threshold_sweep_used_to_select_gate"]):
        raise PermissionError("v2 registration reports a threshold sweep")

    rule = v2["v2_candidate_rule"]
    trigger = v1["generic_array_trigger"]
    null = v1["null_calibration"]
    if not math.isclose(
        float(rule["strong_block_characteristic_ratio"]),
        float(trigger["block_support_ratio"]),
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise RuntimeError("v2 strong-block ratio differs from v1")
    if int(rule["minimum_strong_block_count"]) != int(
        trigger["block_coincidence_count"]
    ):
        raise RuntimeError("v2 support count differs from v1 coincidence count")
    if not math.isclose(
        float(rule["candidate_minimum_separation_s"]),
        float(trigger["candidate_minimum_separation_s"]),
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise RuntimeError("v2 candidate separation differs from v1")
    comparisons = (
        ("base_null_random_seed", "random_seed", int),
        ("base_null_replicate_count", "replicate_count", int),
        (
            "base_null_familywise_quantile",
            "familywise_threshold_quantile",
            float,
        ),
    )
    for v2_field, v1_field, cast in comparisons:
        if cast(rule[v2_field]) != cast(null[v1_field]):
            raise RuntimeError(
                "v2 null setting {} differs from v1".format(v2_field)
            )
    if str(rule["base_score_threshold_repair"]) != "FORBIDDEN":
        raise PermissionError("v2 permits score-threshold repair")
    if str(rule["preprocessing"]) != "inherit_v1_exactly":
        raise PermissionError("v2 preprocessing is not inherited")
    if str(rule["channel_sampling"]) != "inherit_v1_exactly":
        raise PermissionError("v2 channel sampling is not inherited")
    if str(rule["channel_QC"]) != "inherit_v1_exactly":
        raise PermissionError("v2 channel QC is not inherited")
    if str(rule["amplitude_based_channel_selection"]) != "FORBIDDEN":
        raise PermissionError("v2 permits amplitude-based channel selection")
    if int(rule["total_registered_block_count"]) != int(
        v1["channel_sampling"]["expected_block_count"]
    ):
        raise RuntimeError("v2 total block count differs from v1")


def select_v2_candidates(
    v1_rows: Sequence[Mapping[str, Any]],
    registration: Mapping[str, Any],
    identifier_prefix: str = "das_v2_dev",
) -> List[Dict[str, Any]]:
    """Apply the frozen support gate to already materialized v1 candidates."""

    rule = registration["v2_candidate_rule"]
    ratio = float(rule["strong_block_characteristic_ratio"])
    minimum_count = int(rule["minimum_strong_block_count"])
    total_count = int(rule["total_registered_block_count"])
    identifiers = [str(row["candidate_id"]) for row in v1_rows]
    if any(not value for value in identifiers):
        raise ValueError("v1 candidate identifier is empty")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("v1 candidate identifiers are not unique")

    rows = sorted(
        v1_rows,
        key=lambda row: (
            float(row["trigger_epoch_s"]),
            str(row["candidate_id"]),
        ),
    )
    epochs = [float(row["trigger_epoch_s"]) for row in rows]
    if any(not math.isfinite(value) for value in epochs):
        raise ValueError("v1 candidate time is not finite")
    if any(first >= second for first, second in zip(epochs, epochs[1:])):
        raise ValueError("v1 candidate times are not strictly chronological")

    score_order = sorted(
        rows,
        key=lambda row: (
            -float(row["coincidence_score"]),
            str(row["candidate_id"]),
        ),
    )
    score_ranks = {
        str(row["candidate_id"]): index
        for index, row in enumerate(score_order, start=1)
    }
    retained: List[Dict[str, Any]] = []
    for source in rows:
        score = float(source["coincidence_score"])
        threshold = float(source["threshold"])
        support_ratio = float(
            source["block_characteristic_support_threshold"]
        )
        support_count = int(
            source["block_support_count_at_declared_ratio"]
        )
        coincidence_count = int(source["coincidence_block_count"])
        if not math.isfinite(score) or not math.isfinite(threshold):
            raise ValueError("v1 candidate score or threshold is not finite")
        if score < threshold:
            raise ValueError("v1 raw table contains a sub-threshold candidate")
        if not math.isclose(
            support_ratio, ratio, rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise RuntimeError("v1 candidate support ratio changed")
        if coincidence_count != minimum_count:
            raise RuntimeError("v1 candidate coincidence count changed")
        if not 0 <= support_count <= total_count:
            raise ValueError("v1 candidate block support count is invalid")
        for field in (
            "network_candidate_time_fields_read",
            "catalog_event_time_fields_read",
            "heldout_interval_fields_read",
        ):
            if int(source[field]) != 0:
                raise PermissionError("v1 candidate contains leaked input")
        if str(source["family_assignment"]) != "not_assigned":
            raise PermissionError("v1 candidate has a family assignment")
        if support_count < minimum_count:
            continue
        retained.append(
            {
                "candidate_id": "{}_{:04d}".format(
                    identifier_prefix, len(retained) + 1
                ),
                "parent_v1_candidate_id": str(source["candidate_id"]),
                "trigger_time": str(source["trigger_time"]),
                "trigger_epoch_s": float(source["trigger_epoch_s"]),
                "coincidence_score": score,
                "v1_interval_null_threshold": threshold,
                "strong_block_characteristic_ratio": ratio,
                "strong_block_count": support_count,
                "total_registered_block_count": total_count,
                "strong_block_fraction": support_count / float(total_count),
                "minimum_required_strong_block_count": minimum_count,
                "v1_score_rank": score_ranks[str(source["candidate_id"])],
                "candidate_generation_label": (
                    "development_tuned_v2_replay_not_independent_validation"
                ),
                "network_candidate_time_fields_read": 0,
                "catalog_event_time_fields_read": 0,
                "heldout_waveform_fields_read": 0,
                "family_assignment": "not_assigned",
            }
        )
    return retained
