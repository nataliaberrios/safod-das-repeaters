"""Pure schemas and frozen candidate mechanics for held-out DAS v2.

The functions here contain no filesystem or waveform access.  They adapt the
released development detector to one registered interval, retain every v1-null
threshold candidate, and apply the disclosed four-of-ten v2 support gate.
"""

from __future__ import annotations

import copy
import math
from typing import Any, Dict, List, Mapping, Sequence

from .common import iso_utc, parse_utc


CHUNK_FIELDS = [
    "chunk_id",
    "core_start_utc",
    "core_end_utc",
    "read_start_utc",
    "read_end_utc",
    "raw_sample_count",
    "target_core_sample_count",
    "sampled_channel_count",
    "source_file_count",
    "source_files",
    "raw_missing_fraction",
    "maximum_raw_sample_interval_s",
    "maximum_timestamp_uniformity_residual_s",
    "unit",
    "status",
]

CHANNEL_FIELDS = [
    "column_index",
    "locus_index",
    "filtered_rms_phase_rad",
    "finite",
    "nonzero",
    "usable",
    "reason",
    "amplitude_based_selection_used",
]

BLOCK_FIELDS = [
    "block_id",
    "column_start",
    "column_end",
    "locus_start",
    "locus_end",
    "sampled_channel_count",
    "usable_channel_count",
    "median_filtered_rms_phase_rad",
    "minimum_usable_channels",
    "status",
]

NULL_FIELDS = ["replicate", "maximum_score"]

BASE_V1_CANDIDATE_FIELDS = [
    "interval_id",
    "candidate_id",
    "trigger_time",
    "trigger_epoch_s",
    "coincidence_score",
    "threshold",
    "coincidence_block_count",
    "block_support_count_at_declared_ratio",
    "block_characteristic_support_threshold",
    "usable_block_count",
    "total_registered_block_count",
    "minimum_required_strong_block_count",
    "passes_frozen_v2_support_gate",
    "candidate_generation_label",
    "network_candidate_time_fields_read",
    "catalog_event_time_fields_read",
    "family_label_fields_read",
    "family_assignment",
]

V2_CANDIDATE_FIELDS = [
    "interval_id",
    "candidate_id",
    "parent_v1_candidate_id",
    "trigger_time",
    "trigger_epoch_s",
    "coincidence_score",
    "v1_interval_null_threshold",
    "strong_block_characteristic_ratio",
    "strong_block_count",
    "usable_block_count",
    "total_registered_block_count",
    "strong_block_fraction_of_registered",
    "minimum_required_strong_block_count",
    "v1_score_rank_within_interval",
    "candidate_generation_label",
    "network_candidate_time_fields_read",
    "catalog_event_time_fields_read",
    "family_label_fields_read",
    "family_assignment",
]

INTERVAL_STATUS_FIELDS = [
    "interval_id",
    "interval_start_utc",
    "interval_end_utc",
    "interval_status",
    "selected_manifest_record_count",
    "unique_hdf5_files_read",
    "hdf5_chunk_file_use_count",
    "sampled_channel_count",
    "usable_sampled_channel_count",
    "usable_block_count",
    "chunk_count",
    "score_sample_count",
    "detection_threshold",
    "observed_maximum_score",
    "base_v1_candidate_count",
    "v2_candidate_count",
    "chunk_QC_sha256",
    "channel_QC_sha256",
    "block_QC_sha256",
    "null_maxima_sha256",
    "base_v1_candidate_sha256",
    "v2_candidate_sha256",
    "full_score_cache_sha256",
]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ValueError("value is not an explicit boolean: {!r}".format(value))


def interval_v1_view(
    v1: Mapping[str, Any],
    registration: Mapping[str, Any],
    interval: Mapping[str, Any],
) -> Dict[str, Any]:
    """Change only interval bounds and declared held-out role."""

    view = copy.deepcopy(dict(v1))
    view["interval"] = {
        "start_utc": str(interval["interval_start_utc"]),
        "end_utc": str(interval["interval_end_utc"]),
        "duration_s": float(interval["interval_duration_s"]),
        "filter_padding_s": float(interval["filter_padding_s"]),
        "role": "heldout_DAS_only",
        "heldout_access": "REMOTE_RUNNER_RELEASE_REQUIRED",
    }
    for field in (
        "path",
        "sha256",
        "path_rewrites",
        "primary_configuration",
        "maximum_internal_gap_s",
    ):
        if view["manifest"][field] != registration["manifest"][field]:
            raise RuntimeError("held-out DAS manifest settings changed")
    return view


def finalize_base_candidates(
    raw_rows: Sequence[Mapping[str, Any]],
    interval_id: str,
    usable_block_count: int,
    registration: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Rename and retain every v1-null-threshold candidate."""

    rule = registration["detector_inheritance"]
    ratio = float(rule["DAS_v2_strong_block_characteristic_ratio"])
    minimum = int(rule["DAS_v2_minimum_strong_block_count"])
    total = int(rule["DAS_v2_total_registered_block_count"])
    usable = int(usable_block_count)
    if not minimum <= usable <= total:
        raise ValueError("usable DAS block count cannot support the v2 gate")
    rows = sorted(
        raw_rows,
        key=lambda row: (float(row["trigger_epoch_s"]), str(row["candidate_id"])),
    )
    epochs = [float(row["trigger_epoch_s"]) for row in rows]
    if any(not math.isfinite(value) for value in epochs):
        raise ValueError("base DAS candidate time is not finite")
    if any(first >= second for first, second in zip(epochs, epochs[1:])):
        raise ValueError("base DAS candidate times are not strictly chronological")

    finalized: List[Dict[str, Any]] = []
    for index, source in enumerate(rows, start=1):
        score = float(source["coincidence_score"])
        threshold = float(source["threshold"])
        support_ratio = float(source["block_characteristic_support_threshold"])
        support_count = int(source["block_support_count_at_declared_ratio"])
        if not math.isfinite(score) or not math.isfinite(threshold):
            raise ValueError("base DAS score or threshold is not finite")
        if score + 1.0e-12 < threshold:
            raise ValueError("sub-threshold DAS row entered the base table")
        if not math.isclose(support_ratio, ratio, rel_tol=0.0, abs_tol=1.0e-12):
            raise RuntimeError("base DAS support ratio differs from v2 registration")
        if int(source["coincidence_block_count"]) != minimum:
            raise RuntimeError("base DAS coincidence count changed")
        if not 0 <= support_count <= usable:
            raise ValueError("base DAS support count exceeds usable blocks")
        for field in (
            "network_candidate_time_fields_read",
            "catalog_event_time_fields_read",
        ):
            if int(source[field]) != 0:
                raise PermissionError("comparison time leaked into DAS generation")
        if str(source["family_assignment"]) != "not_assigned":
            raise PermissionError("base DAS row has a family assignment")
        finalized.append(
            {
                "interval_id": str(interval_id),
                "candidate_id": "das_v1_{}_{:04d}".format(interval_id, index),
                "trigger_time": iso_utc(float(source["trigger_epoch_s"])),
                "trigger_epoch_s": float(source["trigger_epoch_s"]),
                "coincidence_score": score,
                "threshold": threshold,
                "coincidence_block_count": minimum,
                "block_support_count_at_declared_ratio": support_count,
                "block_characteristic_support_threshold": ratio,
                "usable_block_count": usable,
                "total_registered_block_count": total,
                "minimum_required_strong_block_count": minimum,
                "passes_frozen_v2_support_gate": support_count >= minimum,
                "candidate_generation_label": (
                    "heldout_DAS_base_v1_candidate_arrival_not_origin_or_family_truth"
                ),
                "network_candidate_time_fields_read": 0,
                "catalog_event_time_fields_read": 0,
                "family_label_fields_read": 0,
                "family_assignment": "not_assigned",
            }
        )
    return finalized


def apply_frozen_v2_gate(
    base_rows: Sequence[Mapping[str, Any]],
    interval_id: str,
    registration: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Apply exactly the registered four-of-ten rule without a sweep."""

    rule = registration["detector_inheritance"]
    ratio = float(rule["DAS_v2_strong_block_characteristic_ratio"])
    minimum = int(rule["DAS_v2_minimum_strong_block_count"])
    total = int(rule["DAS_v2_total_registered_block_count"])
    score_order = sorted(
        base_rows,
        key=lambda row: (-float(row["coincidence_score"]), str(row["candidate_id"])),
    )
    score_ranks = {
        str(row["candidate_id"]): index
        for index, row in enumerate(score_order, start=1)
    }
    chronological = sorted(
        base_rows,
        key=lambda row: (float(row["trigger_epoch_s"]), str(row["candidate_id"])),
    )
    retained: List[Dict[str, Any]] = []
    for source in chronological:
        if str(source["interval_id"]) != str(interval_id):
            raise PermissionError("base DAS row crossed an interval")
        support_count = int(source["block_support_count_at_declared_ratio"])
        if _as_bool(source["passes_frozen_v2_support_gate"]) != (
            support_count >= minimum
        ):
            raise RuntimeError("stored v2 support disposition changed")
        if support_count < minimum:
            continue
        retained.append(
            {
                "interval_id": str(interval_id),
                "candidate_id": "das_v2_{}_{:04d}".format(
                    interval_id, len(retained) + 1
                ),
                "parent_v1_candidate_id": str(source["candidate_id"]),
                "trigger_time": str(source["trigger_time"]),
                "trigger_epoch_s": float(source["trigger_epoch_s"]),
                "coincidence_score": float(source["coincidence_score"]),
                "v1_interval_null_threshold": float(source["threshold"]),
                "strong_block_characteristic_ratio": ratio,
                "strong_block_count": support_count,
                "usable_block_count": int(source["usable_block_count"]),
                "total_registered_block_count": total,
                "strong_block_fraction_of_registered": support_count / float(total),
                "minimum_required_strong_block_count": minimum,
                "v1_score_rank_within_interval": score_ranks[
                    str(source["candidate_id"])
                ],
                "candidate_generation_label": (
                    "heldout_DAS_v2_candidate_arrival_not_origin_or_family_truth"
                ),
                "network_candidate_time_fields_read": 0,
                "catalog_event_time_fields_read": 0,
                "family_label_fields_read": 0,
                "family_assignment": "not_assigned",
            }
        )
    return retained


def validate_candidate_tables(
    base_rows: Sequence[Mapping[str, Any]],
    v2_rows: Sequence[Mapping[str, Any]],
    interval: Mapping[str, Any],
    registration: Mapping[str, Any],
    threshold: float,
) -> None:
    """Validate exact IDs, bounds, threshold, leakage, and v2 membership."""

    interval_id = str(interval["interval_id"])
    start_s = parse_utc(str(interval["interval_start_utc"])).timestamp()
    end_s = parse_utc(str(interval["interval_end_utc"])).timestamp()
    base_ids = []
    for row in base_rows:
        if str(row["interval_id"]) != interval_id:
            raise PermissionError("base DAS candidate crossed an interval")
        base_ids.append(str(row["candidate_id"]))
        epoch_s = float(row["trigger_epoch_s"])
        if not start_s <= epoch_s < end_s:
            raise RuntimeError("base DAS candidate lies outside its interval")
        if not math.isclose(
            float(row["threshold"]), float(threshold), rel_tol=0.0, abs_tol=1.0e-12
        ):
            raise PermissionError("base DAS interval threshold changed")
        if float(row["coincidence_score"]) + 1.0e-12 < float(threshold):
            raise RuntimeError("base DAS candidate is below threshold")
        for field in (
            "network_candidate_time_fields_read",
            "catalog_event_time_fields_read",
            "family_label_fields_read",
        ):
            if int(row[field]) != 0:
                raise PermissionError("comparison/family field leaked into base DAS")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("base DAS candidate has a family assignment")
    expected_base_ids = [
        "das_v1_{}_{:04d}".format(interval_id, index)
        for index in range(1, len(base_rows) + 1)
    ]
    if base_ids != expected_base_ids:
        raise RuntimeError("base DAS candidate identifiers or order changed")

    expected_v2 = apply_frozen_v2_gate(base_rows, interval_id, registration)
    if len(v2_rows) != len(expected_v2):
        raise RuntimeError("held-out DAS v2 membership count changed")
    numeric_fields = {
        "trigger_epoch_s",
        "coincidence_score",
        "v1_interval_null_threshold",
        "strong_block_characteristic_ratio",
        "strong_block_count",
        "usable_block_count",
        "total_registered_block_count",
        "strong_block_fraction_of_registered",
        "minimum_required_strong_block_count",
        "v1_score_rank_within_interval",
        "network_candidate_time_fields_read",
        "catalog_event_time_fields_read",
        "family_label_fields_read",
    }
    for observed, expected in zip(v2_rows, expected_v2):
        for field in V2_CANDIDATE_FIELDS:
            if field in numeric_fields:
                if not math.isclose(
                    float(observed[field]),
                    float(expected[field]),
                    rel_tol=0.0,
                    abs_tol=1.0e-12,
                ):
                    raise RuntimeError("held-out DAS v2 numeric field changed")
            elif str(observed[field]) != str(expected[field]):
                raise RuntimeError("held-out DAS v2 text field changed")
