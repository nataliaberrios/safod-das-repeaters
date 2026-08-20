"""Fail-closed guards for the independent held-out DAS replay.

This module validates frozen scalar rules and stage-status summaries.  It does
not import a waveform reader or parse network, catalog-association, or family
tables.  Manifest selection uses only acquisition times and configuration.
"""

from __future__ import annotations

import math
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import PASS, parse_utc, sha256_file
from .das_v2 import validate_v2_inheritance


def _assert_equal(observed: Any, expected: Any, label: str) -> None:
    if isinstance(observed, (int, float)) and isinstance(
        expected, (int, float)
    ):
        if not math.isclose(
            float(observed),
            float(expected),
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise RuntimeError("{} changed".format(label))
    elif observed != expected:
        raise RuntimeError("{} changed".format(label))


def validate_frozen_inputs(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Path]:
    """Verify byte hashes without semantically reading forbidden tables."""

    paths: Dict[str, Path] = {}
    for name, declaration in registration["frozen_inputs"].items():
        raw = Path(str(declaration["path"]))
        path = raw if raw.is_absolute() else project / raw
        if not path.is_file():
            raise FileNotFoundError(
                "missing held-out DAS frozen input: {}".format(path)
            )
        observed = sha256_file(path)
        expected = str(declaration["sha256"])
        if observed != expected:
            raise RuntimeError(
                "held-out DAS frozen input changed for {}: {} != {}".format(
                    name, observed, expected
                )
            )
        paths[str(name)] = path
    return paths


def validate_release_anchor(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Any]:
    """Require the remotely verified private catalog checkpoint as ancestor."""

    release = registration["release_anchor"]
    if str(release["repository_visibility"]) != "private":
        raise PermissionError("held-out DAS registration is not private")
    if not bool(release["remote_branch_sha_verified_equal_before_registration"]):
        raise PermissionError("catalog checkpoint remote SHA was not verified")
    anchor = str(release["catalog_audit_checkpoint_commit_sha"])
    if len(anchor) != 40 or any(
        character not in "0123456789abcdef" for character in anchor
    ):
        raise ValueError("catalog checkpoint anchor is not a full Git SHA")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "cat-file", "-e", anchor + "^{commit}"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", anchor, head],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise PermissionError("catalog checkpoint is not an ancestor of HEAD")
    return {
        "registered_catalog_audit_checkpoint_commit_sha": anchor,
        "local_HEAD_at_registration": head,
        "release_anchor_is_ancestor_of_HEAD": True,
        "remote_branch_sha_verified_equal_before_registration": True,
    }


def validate_heldout_intervals(
    rows: Sequence[Mapping[str, Any]], registration: Mapping[str, Any]
) -> Tuple[float, List[str]]:
    """Validate the immutable 12-hour metadata ledger without DAS access."""

    expected = registration["heldout_population"]
    if len(rows) != int(expected["interval_count"]):
        raise RuntimeError("held-out DAS interval count changed")
    identifiers = [str(row["interval_id"]) for row in rows]
    if identifiers != list(map(str, expected["interval_ids"])):
        raise RuntimeError("held-out DAS interval identities or order changed")
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError("held-out DAS interval identifiers are not unique")

    total_s = 0.0
    previous_end = float("-inf")
    duration_s = float(expected["interval_duration_s"])
    for row in rows:
        if str(row["analysis_status"]) != str(
            expected["required_analysis_status_at_registration"]
        ):
            raise PermissionError("a held-out DAS interval is no longer sealed")
        if str(row["selection_inputs"]) != str(
            expected["required_selection_inputs"]
        ):
            raise PermissionError("held-out interval selection inputs changed")
        if int(row["selection_seed"]) != int(
            expected["required_selection_seed"]
        ):
            raise PermissionError("held-out interval selection seed changed")
        start_s = parse_utc(str(row["start_utc"])).timestamp()
        end_s = parse_utc(str(row["end_utc"])).timestamp()
        if not math.isclose(
            float(row["duration_s"]),
            duration_s,
            rel_tol=0.0,
            abs_tol=1.0e-6,
        ):
            raise RuntimeError("held-out DAS interval duration changed")
        if not math.isclose(
            end_s - start_s,
            duration_s,
            rel_tol=0.0,
            abs_tol=1.0e-6,
        ):
            raise RuntimeError("held-out DAS timestamps changed")
        if start_s < previous_end:
            raise RuntimeError("held-out DAS intervals overlap or are unordered")
        previous_end = end_s
        total_s += duration_s
    expected_total_s = float(expected["total_duration_h"]) * 3600.0
    if not math.isclose(
        total_s, expected_total_s, rel_tol=0.0, abs_tol=1.0e-6
    ):
        raise RuntimeError("held-out DAS total duration changed")
    return total_s / 3600.0, identifiers


def validate_detector_and_stage_gates(
    registration: Mapping[str, Any],
    parent: Mapping[str, Any],
    v1: Mapping[str, Any],
    v2: Mapping[str, Any],
    v2_status: Mapping[str, Any],
    network_status: Mapping[str, Any],
    catalog_status: Mapping[str, Any],
    population_status: Mapping[str, Any],
) -> None:
    """Prove exact detector inheritance and the network/catalog ordering gate."""

    expected_state = (
        "SPECIFIED_AFTER_REMOTE_CATALOG_CHECKPOINT_BEFORE_ANY_"
        "HELDOUT_DAS_HDF5_ACCESS"
    )
    if str(registration["registration_state"]) != expected_state:
        raise PermissionError("held-out DAS replay was not preregistered")
    release = registration["release_anchor"]
    if str(release["repository_visibility"]) != "private":
        raise PermissionError("held-out DAS release anchor is not private")
    if not bool(release["remote_branch_sha_verified_equal_before_registration"]):
        raise PermissionError("catalog checkpoint remote SHA is unverified")

    _assert_equal(
        parent["_config_sha256"],
        registration["frozen_inputs"]["parent_config"]["sha256"],
        "parent config hash",
    )
    validate_v2_inheritance(v2, v1)
    if str(v2_status["status"]) != PASS:
        raise PermissionError("DAS v2 development replay did not pass")
    if str(v2_status["development_replay_regression_status"]) != (
        "PASS_DISCLOSED_2_OF_65"
    ):
        raise PermissionError("DAS v2 development disclosure changed")
    if str(v2_status["das_v2_config_sha256"]) != str(
        registration["frozen_inputs"]["DAS_v2_config"]["sha256"]
    ):
        raise RuntimeError("DAS v2 status/config checksum mismatch")
    for field in (
        "heldout_DAS_HDF5_files_opened",
        "heldout_DAS_HDF5_datasets_opened",
        "heldout_network_waveform_files_opened",
        "family_assignments_made",
    ):
        if int(v2_status[field]) != 0:
            raise PermissionError("DAS v2 development access ledger changed")

    manifest = registration["manifest"]
    source_manifest = v1["manifest"]
    for field in (
        "path",
        "sha256",
        "path_rewrites",
        "primary_configuration",
        "maximum_internal_gap_s",
    ):
        _assert_equal(manifest[field], source_manifest[field], "manifest " + field)
    if str(manifest["selection_role"]) != (
        "interval_and_manifest_acquisition_metadata_only"
    ):
        raise PermissionError("held-out DAS manifest selection is not blind")
    if str(manifest["HDF5_header_or_dataset_access_during_registration"]) != (
        "FORBIDDEN"
    ):
        raise PermissionError("DAS registration permits HDF5 inspection")

    inherited = registration["detector_inheritance"]
    for field in (
        "DAS_v1_preprocessing",
        "DAS_v1_channel_sampling",
        "DAS_v1_channel_QC",
        "DAS_v1_characteristic_function",
        "DAS_v1_null_method",
    ):
        if not str(inherited[field]).startswith("inherit_config_das_development"):
            raise PermissionError("DAS detector inheritance is incomplete")
    _assert_equal(
        inherited["DAS_v1_null_random_seed"],
        v1["null_calibration"]["random_seed"],
        "DAS null seed",
    )
    _assert_equal(
        inherited["DAS_v1_null_replicate_count"],
        v1["null_calibration"]["replicate_count"],
        "DAS null replicate count",
    )
    _assert_equal(
        inherited["DAS_v1_null_familywise_quantile"],
        v1["null_calibration"]["familywise_threshold_quantile"],
        "DAS null quantile",
    )
    _assert_equal(
        inherited["minimum_candidate_separation_s"],
        v1["generic_array_trigger"]["candidate_minimum_separation_s"],
        "DAS candidate separation",
    )
    _assert_equal(
        inherited["DAS_v2_strong_block_characteristic_ratio"],
        v2["v2_candidate_rule"]["strong_block_characteristic_ratio"],
        "DAS v2 support ratio",
    )
    _assert_equal(
        inherited["DAS_v2_minimum_strong_block_count"],
        v2["v2_candidate_rule"]["minimum_strong_block_count"],
        "DAS v2 support count",
    )
    _assert_equal(
        inherited["DAS_v2_total_registered_block_count"],
        v2["v2_candidate_rule"]["total_registered_block_count"],
        "DAS v2 total block count",
    )
    _assert_equal(
        registration["heldout_population"]["filter_padding_s"],
        v1["interval"]["filter_padding_s"],
        "DAS filter padding",
    )
    if str(inherited["threshold_recalibration_or_repair_after_review"]) != (
        "FORBIDDEN"
    ):
        raise PermissionError("held-out DAS threshold repair is permitted")
    if str(inherited["DAS_v2_threshold_or_support_sweep"]) != "FORBIDDEN":
        raise PermissionError("held-out DAS v2 sweep is permitted")
    if str(inherited["family_assignment"]) != "FORBIDDEN":
        raise PermissionError("held-out DAS candidate family assignment is permitted")

    materialization = registration["candidate_materialization"]
    for field in (
        "waveform_window_selection_from_network_or_catalog_times",
        "base_candidate_deletion_after_review",
        "v2_candidate_deletion_after_review",
        "family_assignment",
    ):
        if not str(materialization[field]).startswith("FORBIDDEN"):
            raise PermissionError("held-out DAS materialization guard changed")
    if str(materialization["interval_failure_policy"]) != (
        "STOP_complete_freeze_requires_12_of_12_PASS"
    ):
        raise PermissionError("partial held-out DAS freeze is permitted")

    guards = registration["independence_guards"]
    allowed = set(map(str, guards["candidate_generation_allowed_inputs"]))
    if any(
        "network" in value.lower()
        or "catalog" in value.lower()
        or "family" in value.lower()
        for value in allowed
    ):
        raise PermissionError("DAS candidate-generation allowlist leaks comparison data")
    required_forbidden = {
        "outputs/heldout_v2/network/network_union_time_only.csv",
        "outputs/heldout_v2/network_catalog_audit/network_union_adjudicated.csv",
        "outputs/heldout_v2/network_catalog_audit/network_evaluation_units.csv",
        "outputs/incremental_value/ncedc_archive_catalog.csv",
        "network_or_catalog_candidate_times",
        "family_labels",
    }
    forbidden = set(map(str, guards["candidate_generation_forbidden_inputs"]))
    if not required_forbidden.issubset(forbidden):
        raise PermissionError("held-out DAS forbidden-input list is incomplete")
    for field in (
        "network_candidate_time_fields_read_before_DAS_freeze",
        "catalog_event_time_fields_read_before_DAS_freeze",
        "family_label_rows_read_before_DAS_freeze",
    ):
        if int(guards[field]) != 0:
            raise PermissionError("held-out DAS independence ledger is nonzero")
    if str(guards["comparison_order"]) != (
        "materialize_and_checksum_complete_DAS_candidate_tables_before_"
        "opening_any_forbidden_input"
    ):
        raise PermissionError("held-out DAS comparison order changed")

    if str(network_status["status"]) != PASS:
        raise PermissionError("held-out network union did not pass")
    if not bool(network_status["network_union_complete_and_frozen"]):
        raise PermissionError("held-out network union is not frozen")
    if str(network_status["DAS_access_gate"]) != (
        "PASS_COMPLETE_HELDOUT_NETWORK_UNION_IS_FROZEN_"
        "REGISTER_DAS_REPLAY_BEFORE_WAVEFORM_ACCESS"
    ):
        raise PermissionError("held-out network status does not release registration")
    for field in (
        "heldout_DAS_HDF5_files_opened",
        "heldout_DAS_HDF5_datasets_opened",
        "heldout_family_label_rows_opened",
        "candidate_family_assignments_made",
    ):
        if int(network_status[field]) != 0:
            raise PermissionError("network-stage DAS/family ledger changed")

    if str(catalog_status["status"]) != PASS:
        raise PermissionError("held-out catalog audit did not pass")
    if str(catalog_status["next_stage_gate"]) != (
        "PASS_REGISTER_INDEPENDENT_HELDOUT_DAS_REPLAY"
    ):
        raise PermissionError("catalog audit does not release DAS registration")
    if bool(catalog_status["DAS_candidate_generation_read_network_or_catalog_times"]):
        raise PermissionError("catalog stage reports leaked DAS-generation times")
    if not bool(catalog_status["all_raw_union_rows_retained"]):
        raise PermissionError("catalog audit did not retain the raw network union")
    for field in (
        "heldout_DAS_HDF5_files_opened",
        "heldout_DAS_HDF5_datasets_opened",
        "heldout_family_label_rows_opened",
        "family_assignments_made",
    ):
        if int(catalog_status[field]) != 0:
            raise PermissionError("catalog-stage DAS/family ledger changed")

    if int(population_status["heldout_interval_count"]) != int(
        registration["heldout_population"]["interval_count"]
    ):
        raise RuntimeError("population held-out interval count changed")
    _assert_equal(
        population_status["heldout_total_hours"],
        registration["heldout_population"]["total_duration_h"],
        "population held-out duration",
    )
    if str(population_status["heldout_selection_input"]) != str(
        registration["heldout_population"]["required_selection_inputs"]
    ):
        raise PermissionError("population held-out selection is not manifest-only")

    ledger = registration["registration_access_ledger"]
    for field in (
        "network_candidate_table_rows_opened",
        "catalog_association_table_rows_opened",
        "network_or_catalog_candidate_time_fields_read",
        "heldout_DAS_HDF5_files_opened",
        "heldout_DAS_HDF5_headers_opened",
        "heldout_DAS_HDF5_datasets_opened",
        "heldout_family_label_rows_opened",
    ):
        if int(ledger[field]) != 0:
            raise PermissionError("registration access ledger is nonzero: " + field)


def select_manifest_records(
    records: Sequence[Any],
    request_start_s: float,
    request_end_s: float,
    maximum_gap_s: float,
) -> Tuple[List[Any], Dict[str, Any]]:
    """Select manifest rows by time only, without opening an HDF5 file."""

    if request_end_s <= request_start_s:
        raise ValueError("invalid held-out DAS manifest request interval")
    selected = sorted(
        (
            record
            for record in records
            if float(record.end_s) > request_start_s
            and float(record.start_s) < request_end_s
        ),
        key=lambda record: (
            float(record.start_s),
            float(record.end_s),
            str(record.path),
        ),
    )
    if not selected:
        raise FileNotFoundError("no manifest row intersects held-out DAS request")
    paths = [str(record.path) for record in selected]
    if len(set(paths)) != len(paths):
        raise RuntimeError("duplicate DAS path in one interval selection")

    coverage_start = float(selected[0].start_s)
    coverage_end = float(selected[0].end_s)
    maximum_observed_gap_s = 0.0
    for record in selected[1:]:
        gap_s = max(0.0, float(record.start_s) - coverage_end)
        maximum_observed_gap_s = max(maximum_observed_gap_s, gap_s)
        coverage_end = max(coverage_end, float(record.end_s))
    if coverage_start > request_start_s:
        raise RuntimeError("manifest does not cover padded held-out DAS start")
    if coverage_end < request_end_s:
        raise RuntimeError("manifest does not cover padded held-out DAS end")
    if maximum_observed_gap_s > float(maximum_gap_s):
        raise RuntimeError("held-out DAS manifest gap exceeds registered maximum")
    return selected, {
        "selected_record_count": len(selected),
        "request_start_epoch_s": float(request_start_s),
        "request_end_epoch_s": float(request_end_s),
        "coverage_start_epoch_s": coverage_start,
        "coverage_end_epoch_s": coverage_end,
        "maximum_manifest_gap_s": maximum_observed_gap_s,
    }
