"""Access guards for the independent DAS-only development scan.

This module validates the pre-waveform registration and selects input files
from manifest times and acquisition metadata only. It never opens an HDF5 file
or imports a network candidate table.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .archive_population import ManifestRecord
from .common import parse_utc


def validate_das_development_registration(
    parent: Mapping[str, Any],
    registration: Mapping[str, Any],
    union_status: Mapping[str, Any],
) -> Tuple[float, float, float, float]:
    """Validate the registered stage and return interval/request bounds."""

    if str(registration["registration_state"]) != (
        "SPECIFIED_BEFORE_ANY_RAW_HDF5_DATASET_ACCESS"
    ):
        raise PermissionError("DAS registration was not frozen before access")
    if str(parent["_config_sha256"]) != str(
        registration["parent_config_sha256"]
    ):
        raise RuntimeError("DAS registration parent checksum mismatch")

    declared = registration["interval"]
    parent_interval = parent["development_interval"]
    if (
        str(declared["start_utc"]) != str(parent_interval["start_utc"])
        or str(declared["end_utc"]) != str(parent_interval["end_utc"])
        or str(declared["role"]) != "nonblind_development_only"
        or str(declared["heldout_access"]) != "FORBIDDEN"
    ):
        raise PermissionError("DAS access is not the parent development interval")
    start_s = parse_utc(str(declared["start_utc"])).timestamp()
    end_s = parse_utc(str(declared["end_utc"])).timestamp()
    duration_s = end_s - start_s
    if not math.isclose(
        duration_s,
        float(declared["duration_s"]),
        rel_tol=0.0,
        abs_tol=1.0e-6,
    ):
        raise ValueError("DAS development duration is inconsistent")
    padding_s = float(declared["filter_padding_s"])
    if padding_s < 0.0:
        raise ValueError("filter padding must be nonnegative")

    gate = registration["network_union_gate"]
    if str(union_status["next_stage_gate"]) != str(
        gate["required_next_stage_gate"]
    ):
        raise PermissionError("network union has not released DAS development")
    if int(union_status["network_union_stage_das_waveforms_opened"]) != 0:
        raise PermissionError("network stage reports premature DAS access")
    if int(union_status["heldout_intervals_opened"]) != 0:
        raise PermissionError("network stage reports held-out access")

    manifest = registration["manifest"]
    parent_archive = parent["archive"]
    for key in (
        "path",
        "path_rewrites",
        "primary_configuration",
        "maximum_internal_gap_s",
    ):
        parent_key = "manifest" if key == "path" else key
        if manifest[key] != parent_archive[parent_key]:
            raise RuntimeError(
                "DAS manifest setting differs from frozen parent: {}".format(
                    key
                )
            )
    if str(manifest["selection_role"]) != (
        "time_and_configuration_only_no_catalog_fields"
    ):
        raise PermissionError("manifest selection is not catalog blind")

    guard = registration["independence_guard"]
    forbidden = set(map(str, guard["candidate_generation_forbidden_inputs"]))
    required_forbidden = {
        "outputs/development_network/candidate_detections.csv",
        "outputs/development_network/generic_candidate_detections.csv",
        "outputs/development_network/network_candidate_union_time_only.csv",
        "outputs/development_network/network_candidate_union_adjudicated.csv",
        "outputs/incremental_value/ncedc_archive_catalog.csv",
        "outputs/incremental_value/heldout_intervals.csv",
        "network_or_catalog_event_times",
    }
    if not required_forbidden.issubset(forbidden):
        raise PermissionError("DAS independence guard is incomplete")
    if str(guard["comparison_order"]) != (
        "materialize_and_checksum_DAS_candidate_table_before_opening_any_"
        "forbidden_input"
    ):
        raise PermissionError("DAS comparison order is not sealed")

    channel = registration["channel_sampling"]
    columns = list(
        range(
            int(channel["column_start"]),
            int(channel["column_stop"]),
            int(channel["column_stride"]),
        )
    )
    if len(columns) != int(channel["expected_sampled_channel_count"]):
        raise ValueError("registered sampled-channel count is inconsistent")
    block_width = int(channel["block_width_columns"])
    if block_width <= 0:
        raise ValueError("DAS block width must be positive")
    block_counts = Counter(column // block_width for column in columns)
    if len(block_counts) != int(channel["expected_block_count"]):
        raise ValueError("registered DAS block count is inconsistent")
    if set(block_counts.values()) != {
        int(channel["expected_sampled_channels_per_block"])
    }:
        raise ValueError("registered channels per DAS block are inconsistent")

    preprocessing = registration["preprocessing"]
    raw_rate = float(manifest["primary_configuration"]["sample_rate_hz"])
    target_rate = float(preprocessing["target_sample_rate_hz"])
    score_rate = float(preprocessing["score_sample_rate_hz"])
    if (
        raw_rate <= 0.0
        or target_rate <= 0.0
        or score_rate <= 0.0
        or not math.isclose(
            raw_rate / target_rate,
            round(raw_rate / target_rate),
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
        or not math.isclose(
            target_rate / score_rate,
            round(target_rate / score_rate),
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
    ):
        raise ValueError("DAS sample rates must be positive integer ratios")
    low, high = map(float, preprocessing["primary_band_hz"])
    if not 0.0 < low < high < 0.5 * target_rate:
        raise ValueError("DAS primary band is incompatible with target rate")

    trigger = registration["generic_array_trigger"]
    if float(preprocessing["chunk_filter_overlap_s"]) < float(
        trigger["lta_window_s"]
    ):
        raise ValueError("chunk overlap is shorter than the LTA")
    if not 1 <= int(trigger["block_coincidence_count"]) <= len(block_counts):
        raise ValueError("invalid DAS block coincidence count")
    null = registration["null_calibration"]
    if int(null["replicate_count"]) < 19:
        raise ValueError("DAS null bank is too small")
    if 2.0 * float(null["minimum_absolute_shift_s"]) >= duration_s:
        raise ValueError("DAS null shifts are incompatible with interval")

    return start_s, end_s, start_s - padding_s, end_s + padding_s


def select_registered_manifest_records(
    records: Sequence[ManifestRecord],
    request_start_s: float,
    request_end_s: float,
    maximum_gap_s: float,
) -> Tuple[List[ManifestRecord], Dict[str, Any]]:
    """Select and validate manifest coverage without opening HDF5 files."""

    if request_end_s <= request_start_s:
        raise ValueError("invalid DAS manifest request interval")
    selected = sorted(
        (
            record
            for record in records
            if record.end_s > request_start_s
            and record.start_s < request_end_s
        ),
        key=lambda record: (record.start_s, record.end_s, record.path),
    )
    if not selected:
        raise FileNotFoundError("no manifest record intersects DAS request")
    if len({record.path for record in selected}) != len(selected):
        raise RuntimeError("duplicate DAS paths occur in manifest selection")

    coverage_start = float(selected[0].start_s)
    coverage_end = float(selected[0].end_s)
    maximum_observed_gap_s = 0.0
    for record in selected[1:]:
        gap_s = max(0.0, float(record.start_s) - coverage_end)
        maximum_observed_gap_s = max(maximum_observed_gap_s, gap_s)
        coverage_end = max(coverage_end, float(record.end_s))
    if coverage_start > request_start_s:
        raise RuntimeError("manifest does not cover padded DAS request start")
    if coverage_end < request_end_s:
        raise RuntimeError("manifest does not cover padded DAS request end")
    if maximum_observed_gap_s > float(maximum_gap_s):
        raise RuntimeError(
            "manifest gap exceeds registered maximum: {}".format(
                maximum_observed_gap_s
            )
        )

    summary = {
        "selected_record_count": len(selected),
        "request_start_epoch_s": float(request_start_s),
        "request_end_epoch_s": float(request_end_s),
        "coverage_start_epoch_s": coverage_start,
        "coverage_end_epoch_s": coverage_end,
        "maximum_manifest_gap_s": maximum_observed_gap_s,
    }
    return selected, summary
