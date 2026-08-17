#!/usr/bin/env python
"""Register post-union catalog annotation without parsing catalog event rows."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import urllib.parse
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import (
    PASS,
    iso_utc,
    load_config,
    parse_utc,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)


QUERY_MANIFEST_FIELDS = [
    "interval_id",
    "interval_start_utc",
    "interval_end_utc",
    "query_start_utc",
    "query_end_utc",
    "event_service",
    "catalog",
    "bounds_json",
    "request_url",
    "cache_path",
    "provenance_path",
    "association_role",
    "catalog_event_rows_opened_at_registration",
]


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


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
    """Hash frozen inputs; hashing the catalog is not event-row parsing."""

    paths: Dict[str, Path] = {}
    for name, declaration in registration["frozen_inputs"].items():
        raw = Path(str(declaration["path"]))
        path = raw if raw.is_absolute() else project / raw
        if not path.is_file():
            raise FileNotFoundError(
                "missing catalog-audit frozen input: {}".format(path)
            )
        observed = sha256_file(path)
        expected = str(declaration["sha256"])
        if observed != expected:
            raise RuntimeError(
                "catalog-audit frozen input changed for {}: {} != {}".format(
                    name, observed, expected
                )
            )
        paths[str(name)] = path
    return paths


def validate_release_anchor(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Any]:
    release = registration["release_anchor"]
    if str(release["repository_visibility"]) != "private":
        raise PermissionError("catalog registration is not anchored to a private repo")
    if not bool(release["remote_branch_sha_verified_equal_before_registration"]):
        raise PermissionError("network union remote checkpoint was not verified")
    anchor = str(release["network_union_freeze_commit_sha"])
    if len(anchor) != 40 or any(
        character not in "0123456789abcdef" for character in anchor
    ):
        raise ValueError("network union release anchor is not a full Git SHA")
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
        raise PermissionError("network union release anchor is not an ancestor")
    return {
        "registered_network_union_commit_sha": anchor,
        "local_HEAD_at_registration": head,
        "release_anchor_is_ancestor_of_HEAD": True,
        "remote_branch_sha_verified_equal_before_registration": True,
    }


def validate_catalog_rules(
    registration: Mapping[str, Any],
    parent: Mapping[str, Any],
    development: Mapping[str, Any],
    network_registration: Mapping[str, Any],
) -> None:
    """Prove all held-out rules inherit frozen development semantics."""

    expected_state = (
        "SPECIFIED_AFTER_REMOTE_NETWORK_UNION_FREEZE_BEFORE_ANY_"
        "POST_UNION_CATALOG_EVENT_ROW_ACCESS"
    )
    if str(registration["registration_state"]) != expected_state:
        raise PermissionError("catalog audit was not preregistered")

    target = registration["target_region_catalog"]
    network_target = network_registration["post_union_catalog_adjudication"]
    _assert_equal(
        target["path"],
        network_target["target_region_catalog_path"],
        "target catalog path",
    )
    _assert_equal(
        target["sha256"],
        network_target["target_region_catalog_sha256"],
        "target catalog hash",
    )
    _assert_equal(
        target["template_origin_association"]["maximum_absolute_residual_s"],
        development["repeater_template_bank"][
            "catalog_association_tolerance_s"
        ],
        "template catalog tolerance",
    )
    _assert_equal(
        target["generic_trigger_association"]["maximum_absolute_residual_s"],
        development["generic_network_trigger"][
            "catalog_association_tolerance_s"
        ],
        "generic catalog tolerance",
    )

    neighborhood = registration["family_neighborhood_annotation"]
    source_neighborhood = parent["family_neighborhood"]
    for field in ("reference_latitude", "reference_longitude", "reference_depth_km"):
        _assert_equal(
            neighborhood[field], source_neighborhood[field], field
        )
    _assert_equal(
        neighborhood["maximum_horizontal_distance_km"] * 1000.0,
        source_neighborhood["maximum_horizontal_distance_m"],
        "family-neighborhood horizontal distance",
    )
    _assert_equal(
        neighborhood["maximum_depth_difference_km"],
        source_neighborhood["maximum_depth_difference_km"],
        "family-neighborhood depth distance",
    )

    broader = registration["broader_regional_catalog"]
    source_broader = development["background_catalog"]
    for field in (
        "event_service",
        "catalog",
        "origin_time_padding_before_s",
        "origin_time_padding_after_s",
        "bounds",
        "array_reference_depth_km",
        "minimum_plausible_phase_velocity_km_s",
        "maximum_plausible_phase_velocity_km_s",
        "nominal_ranking_phase_velocity_km_s",
        "early_arrival_slack_s",
        "late_arrival_slack_s",
    ):
        _assert_equal(broader[field], source_broader[field], "broader " + field)
    _assert_equal(
        broader["array_reference_latitude"],
        source_neighborhood["reference_latitude"],
        "array reference latitude",
    )
    _assert_equal(
        broader["array_reference_longitude"],
        source_neighborhood["reference_longitude"],
        "array reference longitude",
    )

    immutable = registration["immutable_network_union"]
    adjudication = registration["adjudication"]
    guards = registration["independence_guards"]
    if str(immutable["candidate_deletion"]) != "FORBIDDEN":
        raise PermissionError("catalog audit permits candidate deletion")
    if not bool(immutable["retain_every_raw_union_row"]):
        raise PermissionError("catalog audit can omit raw union rows")
    if str(adjudication["family_assignment"]) != "FORBIDDEN":
        raise PermissionError("catalog audit permits family assignment")
    if bool(guards["DAS_candidate_generation_may_read_catalog_audit_outputs"]):
        raise PermissionError("DAS candidate generation can read catalog outputs")
    if bool(guards["DAS_candidate_generation_may_read_network_candidate_times"]):
        raise PermissionError("DAS candidate generation can read network times")


def validate_network_freeze(
    registration: Mapping[str, Any],
    network_status: Mapping[str, Any],
    union_rows: Sequence[Mapping[str, Any]],
    interval_rows: Sequence[Mapping[str, Any]],
    intervals: Sequence[Mapping[str, Any]],
) -> Tuple[List[str], Counter[str]]:
    """Validate the exact immutable union, without a catalog or waveform read."""

    immutable = registration["immutable_network_union"]
    if str(network_status["status"]) != PASS:
        raise PermissionError("network union did not finish with PASS")
    if not bool(network_status["network_union_complete_and_frozen"]):
        raise PermissionError("network union is not frozen")
    if str(network_status["time_only_union_sha256"]) != str(
        registration["frozen_inputs"]["network_time_only_union"]["sha256"]
    ):
        raise RuntimeError("network union status/hash mismatch")
    for field in (
        "heldout_catalog_event_rows_opened",
        "heldout_DAS_HDF5_files_opened",
        "heldout_DAS_HDF5_datasets_opened",
        "heldout_family_label_rows_opened",
        "candidate_family_assignments_made",
    ):
        if int(network_status[field]) != 0:
            raise PermissionError("network freeze access ledger changed: " + field)
    if not bool(network_status["all_interval_candidate_tables_checksums_verified_before_union"]):
        raise PermissionError("interval candidate tables were not verified")
    if bool(network_status["threshold_recalibration_performed"]):
        raise PermissionError("held-out network thresholds were recalibrated")
    if bool(network_status["candidate_deletion_after_review_performed"]):
        raise PermissionError("held-out network candidates were deleted")

    interval_ids = [str(row["interval_id"]) for row in intervals]
    if len(interval_ids) != int(immutable["interval_count"]):
        raise RuntimeError("held-out interval count changed")
    if len(set(interval_ids)) != len(interval_ids):
        raise RuntimeError("held-out interval IDs are not unique")
    interval_bounds = {
        str(row["interval_id"]): (
            parse_utc(str(row["start_utc"])).timestamp(),
            parse_utc(str(row["end_utc"])).timestamp(),
        )
        for row in intervals
    }
    if len(interval_rows) != len(interval_ids):
        raise RuntimeError("network interval status row count changed")
    if [str(row["interval_id"]) for row in interval_rows] != interval_ids:
        raise RuntimeError("network interval status ordering changed")
    if any(str(row["interval_status"]) != PASS for row in interval_rows):
        raise PermissionError("not every network interval passed")

    if len(union_rows) != int(immutable["candidate_count"]):
        raise RuntimeError("network union candidate count changed")
    identifiers = [str(row["union_candidate_id"]) for row in union_rows]
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError("network union candidate IDs are not unique")
    counts: Counter[str] = Counter()
    previous_key: Tuple[int, float, str] | None = None
    order = {identifier: index for index, identifier in enumerate(interval_ids)}
    for row in union_rows:
        interval_id = str(row["interval_id"])
        if interval_id not in interval_bounds:
            raise RuntimeError("union row has an unknown interval")
        if int(row["catalog_fields_used_in_grouping"]) != 0:
            raise PermissionError("catalog field leaked into union grouping")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("network union contains a family assignment")
        membership = str(row["branch_membership"])
        if membership not in {
            "template_bank",
            "generic_trigger",
            "template_bank+generic_trigger",
        }:
            raise RuntimeError("unexpected branch membership")
        counts[membership] += 1
        epoch_s = float(row["representative_epoch_s"])
        start_s, end_s = interval_bounds[interval_id]
        if not start_s <= epoch_s < end_s:
            raise RuntimeError("union candidate lies outside its interval")
        if membership == "template_bank":
            if str(row["representative_time_basis"]) != "template_origin":
                raise RuntimeError("template candidate time semantics changed")
            if not str(row["template_candidate_id"]):
                raise RuntimeError("template member is missing")
        elif membership == "generic_trigger":
            if str(row["representative_time_basis"]) != (
                "generic_trigger_arrival"
            ):
                raise RuntimeError("generic candidate time semantics changed")
            if not str(row["generic_candidate_id"]):
                raise RuntimeError("generic member is missing")
        key = (order[interval_id], epoch_s, str(row["union_candidate_id"]))
        if previous_key is not None and key < previous_key:
            raise RuntimeError("network union row order changed")
        previous_key = key

    _assert_equal(
        counts["template_bank"],
        immutable["template_only_count"],
        "template-only count",
    )
    _assert_equal(
        counts["generic_trigger"],
        immutable["generic_only_count"],
        "generic-only count",
    )
    _assert_equal(
        counts["template_bank+generic_trigger"],
        immutable["cross_branch_count"],
        "cross-branch count",
    )
    return interval_ids, counts


def broader_query_url(
    registration: Mapping[str, Any], interval: Mapping[str, Any]
) -> Tuple[str, str, str]:
    settings = registration["broader_regional_catalog"]
    start_s = parse_utc(str(interval["start_utc"])).timestamp() - float(
        settings["origin_time_padding_before_s"]
    )
    end_s = parse_utc(str(interval["end_utc"])).timestamp() + float(
        settings["origin_time_padding_after_s"]
    )
    query_start = iso_utc(start_s)
    query_end = iso_utc(end_s)
    parameters: List[Tuple[str, Any]] = [
        ("starttime", query_start),
        ("endtime", query_end),
        ("format", "text"),
        ("orderby", "time"),
        ("catalog", settings["catalog"]),
    ]
    parameters.extend(settings["bounds"].items())
    url = str(settings["event_service"]) + "?" + urllib.parse.urlencode(
        parameters
    )
    return query_start, query_end, url


def build_query_manifest(
    registration: Mapping[str, Any], intervals: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    """Materialize URLs only; this function performs no network request."""

    settings = registration["broader_regional_catalog"]
    cache_root = Path(str(settings["cache_directory"]))
    rows: List[Dict[str, Any]] = []
    for interval in intervals:
        interval_id = str(interval["interval_id"])
        query_start, query_end, url = broader_query_url(
            registration, interval
        )
        rows.append(
            {
                "interval_id": interval_id,
                "interval_start_utc": str(interval["start_utc"]),
                "interval_end_utc": str(interval["end_utc"]),
                "query_start_utc": query_start,
                "query_end_utc": query_end,
                "event_service": str(settings["event_service"]),
                "catalog": str(settings["catalog"]),
                "bounds_json": json.dumps(
                    settings["bounds"], sort_keys=True, separators=(",", ":")
                ),
                "request_url": url,
                "cache_path": str(cache_root / (interval_id + ".csv")),
                "provenance_path": str(
                    cache_root / (interval_id + ".provenance.json")
                ),
                "association_role": str(settings["association_role"]),
                "catalog_event_rows_opened_at_registration": 0,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "heldout_catalog_audit.json",
    )
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    paths = validate_frozen_inputs(project, registration)
    parent = load_config(paths["parent_config"])
    development = load_config(paths["development_config"])
    network_registration = load_config(paths["heldout_network_config"])
    network_status = _load_json(paths["network_candidate_generation_status"])
    catalog_provenance = _load_json(paths["target_catalog_provenance"])
    intervals = _read_csv(paths["heldout_intervals"])
    interval_rows = _read_csv(paths["network_interval_status"])
    union_rows = _read_csv(paths["network_time_only_union"])

    validate_catalog_rules(
        registration, parent, development, network_registration
    )
    interval_ids, membership = validate_network_freeze(
        registration,
        network_status,
        union_rows,
        interval_rows,
        intervals,
    )
    expected_catalog_rows = int(
        registration["target_region_catalog"][
            "expected_event_row_count_from_preexisting_provenance"
        ]
    )
    if int(catalog_provenance["catalog_event_count"]) != expected_catalog_rows:
        raise RuntimeError("target catalog provenance row count changed")
    release_status = validate_release_anchor(project, registration)
    query_rows = build_query_manifest(registration, intervals)

    output = project / str(registration["output"]["registration_directory"])
    query_path = output / str(registration["output"]["query_manifest_csv"])
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    _write_csv(query_path, query_rows, QUERY_MANIFEST_FIELDS)
    status = {
        "status": PASS,
        "stage": "heldout_catalog_audit_registered_before_catalog_event_row_access",
        "registration_status": "FROZEN_FOR_CATALOG_AUDIT_RUNNER_IMPLEMENTATION_ONLY",
        "generated_utc": utc_now(),
        "catalog_audit_config_sha256": registration["_config_sha256"],
        "frozen_input_sha256": {
            name: str(declaration["sha256"])
            for name, declaration in registration["frozen_inputs"].items()
        },
        "release_anchor": release_status,
        "network_union_candidate_count": len(union_rows),
        "network_union_candidate_ids_unique": True,
        "network_union_rows_retained_for_future_audit": len(union_rows),
        "network_union_sha256": sha256_file(
            paths["network_time_only_union"]
        ),
        "interval_count": len(interval_ids),
        "interval_ids": interval_ids,
        "template_only_candidate_count": membership["template_bank"],
        "generic_only_candidate_count": membership["generic_trigger"],
        "cross_branch_candidate_count": membership[
            "template_bank+generic_trigger"
        ],
        "target_catalog_sha256": sha256_file(paths["target_catalog"]),
        "target_catalog_expected_event_row_count": expected_catalog_rows,
        "target_catalog_file_hashed_without_row_parsing": True,
        "target_catalog_event_rows_opened": 0,
        "broader_catalog_query_count": len(query_rows),
        "broader_catalog_query_manifest_path": str(query_path),
        "broader_catalog_query_manifest_sha256": sha256_file(query_path),
        "broader_catalog_event_rows_opened": 0,
        "network_union_rows_opened": len(union_rows),
        "heldout_interval_metadata_rows_opened": len(intervals),
        "heldout_network_waveform_files_opened": 0,
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "heldout_family_label_rows_opened": 0,
        "candidate_rows_deleted": 0,
        "candidate_times_edited": 0,
        "family_assignments_made": 0,
        "template_origin_tolerance_s": registration[
            "target_region_catalog"
        ]["template_origin_association"]["maximum_absolute_residual_s"],
        "generic_origin_audit_tolerance_s": registration[
            "target_region_catalog"
        ]["generic_trigger_association"]["maximum_absolute_residual_s"],
        "broader_physical_arrival_rule_frozen": True,
        "cataloged_event_can_still_extend_repeater_family": True,
        "catalog_unassociated_is_not_automatically_new": True,
        "next_stage_gate": "PASS_IMPLEMENT_TEST_COMMIT_PUSH_AND_REMOTE_RELEASE_CATALOG_AUDIT_RUNNER",
        "catalog_event_row_access_gate": "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH",
        "heldout_DAS_access_gate": "STOP_PENDING_SEPARATE_INDEPENDENT_DAS_REPLAY_REGISTRATION",
        "heldout_family_label_access_gate": "STOP_FORBIDDEN",
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
