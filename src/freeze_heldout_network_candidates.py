#!/usr/bin/env python
"""Freeze all network-only held-out candidates before catalog or DAS access.

Every per-interval table and score cache is checksum-verified first.  Template
and generic candidates are then joined by time only, separately inside each
registered interval.  This module imports no catalog and no DAS reader.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import (
    CONDITIONAL,
    PASS,
    STOP,
    load_config,
    parse_utc,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .heldout_network_access import (
    RUNNER_RELEASE_RELATIVE_PATH,
    load_runner_release,
    registered_interval_rows,
    source_request_url,
    validate_runner_release,
)
from .network_union import TIME_ONLY_FIELDS, build_time_only_union
from .run_heldout_network_interval import (
    GENERIC_CANDIDATE_FIELDS,
    SOURCE_FIELDS,
    TEMPLATE_CANDIDATE_FIELDS,
    TEMPLATE_QC_FIELDS,
    TRACE_QC_FIELDS,
)


UNION_FIELDS = ["interval_id"] + TIME_ONLY_FIELDS
INTERVAL_STATUS_FIELDS = [
    "interval_id",
    "interval_start_utc",
    "interval_end_utc",
    "interval_status",
    "available_source_count",
    "unavailable_source_count",
    "usable_component_count",
    "usable_station_count",
    "template_branch_status",
    "template_branch_reason",
    "generic_branch_status",
    "generic_branch_reason",
    "template_candidate_count",
    "generic_candidate_count",
    "source_availability_sha256",
    "trace_QC_sha256",
    "template_QC_sha256",
    "template_candidate_sha256",
    "generic_candidate_sha256",
    "full_score_cache_sha256",
    "historical_template_waveform_files_opened",
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


def _same_float(first: Any, second: Any) -> bool:
    return math.isclose(
        float(first), float(second), rel_tol=0.0, abs_tol=1.0e-12
    )


def _validate_zero_leakage(row: Mapping[str, Any]) -> None:
    if int(row["catalog_fields_used_in_candidate_generation"]) != 0:
        raise PermissionError("catalog field used in candidate generation")
    if int(row["DAS_fields_used_in_candidate_generation"]) != 0:
        raise PermissionError("DAS field used in candidate generation")
    if str(row["family_assignment"]) != "not_assigned":
        raise PermissionError("family membership assigned before union freeze")


def validate_candidate_rows(
    rows: Sequence[Mapping[str, Any]],
    interval: Mapping[str, Any],
    registration: Mapping[str, Any],
    branch: str,
) -> None:
    """Validate fixed-threshold, target-blind candidate rows."""

    interval_id = str(interval["interval_id"])
    if branch == "template":
        threshold = registration["template_branch"]["threshold"]
        identifier_prefix = "network_template_{}_".format(interval_id)
        time_field = "origin_epoch_s"
        score_field = "bank_score"
        expected_label = "frozen_template_bank_candidate_not_family_truth"
    elif branch == "generic":
        threshold = registration["generic_branch"]["threshold"]
        identifier_prefix = "network_generic_{}_".format(interval_id)
        time_field = "trigger_epoch_s"
        score_field = "coincidence_score"
        expected_label = (
            "frozen_generic_network_candidate_arrival_not_origin"
        )
    else:
        raise ValueError("unknown held-out candidate branch")

    start_s = parse_utc(str(interval["start_utc"])).timestamp()
    end_s = parse_utc(str(interval["end_utc"])).timestamp()
    identifiers: List[str] = []
    for row in rows:
        if str(row["interval_id"]) != interval_id:
            raise PermissionError("candidate crossed a registered interval")
        identifier = str(row["candidate_id"])
        identifiers.append(identifier)
        if not identifier.startswith(identifier_prefix):
            raise RuntimeError("candidate identifier prefix changed")
        if not _same_float(row["threshold"], threshold):
            raise PermissionError("held-out detector threshold changed")
        epoch_s = float(row[time_field])
        if not math.isfinite(epoch_s) or not start_s <= epoch_s < end_s:
            raise RuntimeError("candidate time lies outside its interval")
        score = float(row[score_field])
        if not math.isfinite(score) or score + 1.0e-12 < float(threshold):
            raise RuntimeError("candidate score is below its frozen threshold")
        if str(row["candidate_generation_label"]) != expected_label:
            raise RuntimeError("candidate generation label changed")
        _validate_zero_leakage(row)
        if branch == "template" and str(
            row["best_template_event_id"]
        ) not in set(
            map(str, registration["historical_template_bank"]["event_ids"])
        ):
            raise RuntimeError("candidate used an unregistered template")
    expected_ids = [
        "{}{:04d}".format(identifier_prefix, index)
        for index in range(1, len(rows) + 1)
    ]
    if identifiers != expected_ids:
        raise RuntimeError("candidate identifiers or order changed")


def build_interval_scoped_unions(
    template_rows: Sequence[Mapping[str, Any]],
    generic_rows: Sequence[Mapping[str, Any]],
    interval_ids: Sequence[str],
    maximum_difference_s: float,
) -> List[Dict[str, Any]]:
    """Join branches independently per interval, never across boundaries."""

    expected = list(map(str, interval_ids))
    if len(expected) != len(set(expected)):
        raise ValueError("interval identifiers are not unique")
    expected_set = set(expected)
    template_by_interval: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    generic_by_interval: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for source_rows, target in (
        (template_rows, template_by_interval),
        (generic_rows, generic_by_interval),
    ):
        for row in source_rows:
            interval_id = str(row.get("interval_id", ""))
            if interval_id not in expected_set:
                raise PermissionError("candidate has an unregistered interval")
            target[interval_id].append(row)

    output: List[Dict[str, Any]] = []
    for interval_id in expected:
        local_rows = build_time_only_union(
            template_by_interval[interval_id],
            generic_by_interval[interval_id],
            float(maximum_difference_s),
            identifier_prefix="network_union_{}".format(interval_id),
        )
        for row in local_rows:
            output.append({"interval_id": interval_id, **row})
    identifiers = [str(row["union_candidate_id"]) for row in output]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("held-out union identifiers are not unique")
    return output


def _interval_paths(
    project: Path, registration: Mapping[str, Any], interval_id: str
) -> Dict[str, Path]:
    directory = (
        project
        / str(registration["output"]["network_directory"])
        / "intervals"
        / interval_id
    )
    return {
        "source": directory / "source_availability.csv",
        "trace": directory / "trace_qc.csv",
        "template_qc": directory / "template_qc.csv",
        "template": directory / "template_candidates.csv",
        "generic": directory / "generic_candidates.csv",
        "status": directory / "status.json",
    }


def _verify_file_hash(
    status: Mapping[str, Any], key: str, path: Path
) -> None:
    if not path.is_file():
        raise FileNotFoundError("missing interval product: {}".format(path))
    if sha256_file(path) != str(status[key]):
        raise RuntimeError("interval product checksum changed: {}".format(path))


def _validate_interval_status(
    status: Mapping[str, Any],
    interval: Mapping[str, Any],
    registration: Mapping[str, Any],
    release: Mapping[str, Any],
    release_sha256: str,
) -> None:
    if str(status["stage"]) != (
        "heldout_network_interval_candidate_generation_complete"
    ):
        raise RuntimeError("unexpected interval stage")
    for field in ("interval_id", "interval_start_utc", "interval_end_utc"):
        expected_field = field.replace("interval_", "")
        if field == "interval_id":
            expected_field = "interval_id"
        if str(status[field]) != str(interval[expected_field]):
            raise RuntimeError("interval status metadata changed")
    if str(status["heldout_network_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise RuntimeError("interval/config checksum mismatch")
    if str(status["runner_commit_sha"]) != str(release["runner_commit_sha"]):
        raise RuntimeError("interval runner SHA changed")
    if str(status["network_runner_release_sha256"]) != release_sha256:
        raise RuntimeError("interval runner release checksum changed")
    if not _same_float(
        status["template_threshold"],
        registration["template_branch"]["threshold"],
    ):
        raise PermissionError("interval template threshold changed")
    if not _same_float(
        status["generic_threshold"],
        registration["generic_branch"]["threshold"],
    ):
        raise PermissionError("interval generic threshold changed")
    if bool(status["threshold_recalibration_performed"]):
        raise PermissionError("held-out threshold recalibration was performed")
    if not bool(status["generic_development_SNR1_STOP_preserved"]):
        raise PermissionError("generic development STOP was lost")
    if str(status["status"]) not in {"PASS", "CONDITIONAL", "STOP"}:
        raise RuntimeError("unexpected interval generation status")
    template_state = str(status["template_branch_status"])
    generic_state = str(status["generic_branch_status"])
    if template_state not in {"PASS", "STOP"}:
        raise RuntimeError("unexpected template branch status")
    if generic_state not in {"PASS", "STOP"}:
        raise RuntimeError("unexpected generic branch status")
    expected_overall = (
        "PASS"
        if template_state == generic_state == "PASS"
        else (
            "CONDITIONAL"
            if "PASS" in {template_state, generic_state}
            else "STOP"
        )
    )
    if str(status["status"]) != expected_overall:
        raise RuntimeError("interval aggregate status is inconsistent")
    for field in (
        "heldout_catalog_event_rows_opened",
        "heldout_DAS_HDF5_files_opened",
        "heldout_DAS_HDF5_datasets_opened",
        "heldout_family_label_rows_opened",
        "candidate_family_assignments_made",
    ):
        if int(status[field]) != 0:
            raise PermissionError("interval access ledger is not blind")


def _validate_source_rows(
    project: Path,
    rows: Sequence[Mapping[str, Any]],
    interval: Mapping[str, Any],
    parent: Mapping[str, Any],
    registration: Mapping[str, Any],
) -> None:
    interval_id = str(interval["interval_id"])
    sources = list(parent["network_array"]["sources"])
    expected_names = [str(source["name"]) for source in sources]
    if [str(row["source_name"]) for row in rows] != expected_names:
        raise RuntimeError("interval source membership or order changed")
    for row, source in zip(rows, sources):
        if str(row["interval_id"]) != interval_id:
            raise RuntimeError("source row interval changed")
        if str(row["interval_start_utc"]) != str(interval["start_utc"]):
            raise RuntimeError("source request start changed")
        if str(row["interval_end_utc"]) != str(interval["end_utc"]):
            raise RuntimeError("source request end changed")
        if str(row["network"]) != str(source["network"]):
            raise RuntimeError("source network changed")
        expected_url = source_request_url(
            parent, registration, interval, source
        )
        if str(row["request_url"]) != expected_url:
            raise RuntimeError("source request URL changed")
        if not _same_float(
            row["request_padding_s"],
            registration["waveform_acquisition"]["request_padding_s"],
        ):
            raise RuntimeError("source request padding changed")
        if str(row["access_role"]) != "registered_heldout_network_only":
            raise PermissionError("source access role changed")
        source_status = str(row["status"])
        if source_status not in {"available", "unavailable"}:
            raise RuntimeError("unexpected network source status")
        if source_status == "available":
            if (
                int(row["trace_count"]) < 1
                or not str(row["waveform_path"])
                or len(str(row["waveform_sha256"])) != 64
                or not str(row["provenance_path"])
                or len(str(row["provenance_sha256"])) != 64
            ):
                raise RuntimeError("available source provenance is incomplete")
            cache_root = (
                project
                / str(
                    registration["waveform_acquisition"]["cache_directory"]
                )
            ).resolve()
            waveform_path = Path(str(row["waveform_path"])).resolve()
            provenance_path = Path(str(row["provenance_path"])).resolve()
            if (
                cache_root not in waveform_path.parents
                or cache_root not in provenance_path.parents
            ):
                raise PermissionError("source path escaped the held-out cache")
            if sha256_file(waveform_path) != str(row["waveform_sha256"]):
                raise RuntimeError("held-out network waveform changed")
            if sha256_file(provenance_path) != str(
                row["provenance_sha256"]
            ):
                raise RuntimeError("held-out network provenance changed")
        elif (
            int(row["trace_count"]) != 0
            or str(row["waveform_path"])
            or str(row["waveform_sha256"])
            or str(row["provenance_path"])
            or str(row["provenance_sha256"])
            or not str(row["error"])
        ):
            raise RuntimeError("unavailable source provenance is inconsistent")


def _validate_trace_rows(
    rows: Sequence[Mapping[str, Any]], interval_id: str
) -> None:
    trace_ids: List[str] = []
    for row in rows:
        if str(row["interval_id"]) != interval_id:
            raise RuntimeError("trace QC row interval changed")
        if str(row["status"]) not in {"usable", "rejected"}:
            raise RuntimeError("unexpected trace QC status")
        trace_ids.append(str(row["trace_id"]))
    if len(trace_ids) != len(set(trace_ids)):
        raise RuntimeError("trace QC identifiers are not unique")


def _validate_template_qc(
    rows: Sequence[Mapping[str, Any]], registration: Mapping[str, Any]
) -> None:
    observed = [str(row["template_event_id"]) for row in rows]
    expected = list(
        map(str, registration["historical_template_bank"]["event_ids"])
    )
    if observed != expected:
        raise RuntimeError("interval template QC membership changed")
    if any(str(row["status"]) not in {"PASS", "STOP"} for row in rows):
        raise RuntimeError("unexpected template QC status")


def _status_row(status: Mapping[str, Any]) -> Dict[str, Any]:
    row = {field: status.get(field, "") for field in INTERVAL_STATUS_FIELDS}
    row["interval_status"] = status["status"]
    return row


def collect_interval_products(
    project: Path,
    registration: Mapping[str, Any],
    release: Mapping[str, Any],
    intervals: Sequence[Mapping[str, Any]],
) -> Tuple[
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, str]],
    List[Dict[str, Any]],
]:
    """Verify every interval before returning any aggregate candidate set."""

    release_sha256 = sha256_file(project / RUNNER_RELEASE_RELATIVE_PATH)
    parent = load_config(
        project / str(registration["frozen_inputs"]["parent_config"]["path"])
    )
    all_sources: List[Dict[str, str]] = []
    all_traces: List[Dict[str, str]] = []
    all_templates: List[Dict[str, str]] = []
    all_generics: List[Dict[str, str]] = []
    interval_statuses: List[Dict[str, Any]] = []

    for interval in intervals:
        interval_id = str(interval["interval_id"])
        paths = _interval_paths(project, registration, interval_id)
        if not paths["status"].is_file():
            raise FileNotFoundError(
                "held-out interval is not complete: {}".format(interval_id)
            )
        status = _load_json(paths["status"])
        _validate_interval_status(
            status, interval, registration, release, release_sha256
        )
        for status_key, path_key in (
            ("source_availability_sha256", "source"),
            ("trace_QC_sha256", "trace"),
            ("template_QC_sha256", "template_qc"),
            ("template_candidate_sha256", "template"),
            ("generic_candidate_sha256", "generic"),
        ):
            _verify_file_hash(status, status_key, paths[path_key])

        score_path = Path(str(status["full_score_cache_path"]))
        if not score_path.is_absolute():
            score_path = project / score_path
        if not score_path.is_file():
            raise FileNotFoundError("held-out score cache is missing")
        if sha256_file(score_path) != str(status["full_score_cache_sha256"]):
            raise RuntimeError("held-out score cache checksum changed")

        source_rows = _read_csv(paths["source"])
        trace_rows = _read_csv(paths["trace"])
        template_qc = _read_csv(paths["template_qc"])
        template_rows = _read_csv(paths["template"])
        generic_rows = _read_csv(paths["generic"])
        _validate_source_rows(
            project, source_rows, interval, parent, registration
        )
        _validate_trace_rows(trace_rows, interval_id)
        _validate_template_qc(template_qc, registration)
        usable_trace_count = sum(
            str(row["status"]) == "usable" for row in trace_rows
        )
        usable_station_count = len(
            {
                ".".join(str(row["trace_id"]).split(".")[:2])
                for row in trace_rows
                if str(row["status"]) == "usable"
            }
        )
        if usable_trace_count != int(status["usable_component_count"]):
            raise RuntimeError("usable component count changed")
        if usable_station_count != int(status["usable_station_count"]):
            raise RuntimeError("usable station count changed")
        validate_candidate_rows(
            template_rows, interval, registration, "template"
        )
        validate_candidate_rows(
            generic_rows, interval, registration, "generic"
        )
        if len(template_rows) != int(status["template_candidate_count"]):
            raise RuntimeError("template candidate count changed")
        if len(generic_rows) != int(status["generic_candidate_count"]):
            raise RuntimeError("generic candidate count changed")
        available = sum(
            str(row["status"]) == "available" for row in source_rows
        )
        if available != int(status["available_source_count"]):
            raise RuntimeError("available source count changed")
        if len(source_rows) - available != int(
            status["unavailable_source_count"]
        ):
            raise RuntimeError("unavailable source count changed")

        all_sources.extend(source_rows)
        all_traces.extend(trace_rows)
        all_templates.extend(template_rows)
        all_generics.extend(generic_rows)
        interval_statuses.append(_status_row(status))

    return (
        all_sources,
        all_traces,
        all_templates,
        all_generics,
        interval_statuses,
    )


def aggregate_execution_status(
    interval_status_rows: Sequence[Mapping[str, Any]],
) -> str:
    """Preserve the strongest interval-level warning in the aggregate."""

    states = [str(row["interval_status"]) for row in interval_status_rows]
    if not states:
        raise ValueError("no interval statuses were supplied")
    if any(state not in {PASS, CONDITIONAL, STOP} for state in states):
        raise ValueError("unexpected interval status")
    if STOP in states:
        return STOP
    if CONDITIONAL in states:
        return CONDITIONAL
    return PASS


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            project_root() / "config" / "heldout_network_validation.json"
        ),
    )
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    registration_status_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["registration_status_json"])
    )
    registration_status = _load_json(registration_status_path)
    release = load_runner_release(project)
    validate_runner_release(
        project, registration, registration_status, release
    )
    output = project / str(registration["output"]["network_directory"])
    final_status_path = output / str(
        registration["output"]["candidate_generation_status_json"]
    )
    if final_status_path.is_file():
        raise PermissionError("held-out network union is already frozen")

    intervals = registered_interval_rows(project, registration)
    (
        source_rows,
        trace_rows,
        template_rows,
        generic_rows,
        interval_status_rows,
    ) = collect_interval_products(
        project, registration, release, intervals
    )
    interval_ids = [str(row["interval_id"]) for row in intervals]

    source_path = output / str(
        registration["output"]["source_request_ledger_csv"]
    )
    trace_path = output / str(registration["output"]["trace_QC_csv"])
    interval_status_path = output / str(
        registration["output"]["interval_status_csv"]
    )
    template_path = output / str(
        registration["output"]["template_candidate_csv"]
    )
    generic_path = output / str(
        registration["output"]["generic_candidate_csv"]
    )
    union_path = output / str(
        registration["output"]["time_only_union_csv"]
    )
    _write_csv(source_path, source_rows, SOURCE_FIELDS)
    _write_csv(trace_path, trace_rows, TRACE_QC_FIELDS)
    _write_csv(
        interval_status_path,
        interval_status_rows,
        INTERVAL_STATUS_FIELDS,
    )
    _write_csv(template_path, template_rows, TEMPLATE_CANDIDATE_FIELDS)
    _write_csv(generic_path, generic_rows, GENERIC_CANDIDATE_FIELDS)

    union_rows = build_interval_scoped_unions(
        template_rows,
        generic_rows,
        interval_ids,
        registration["time_only_union"]["cross_branch_match_window_s"],
    )
    for row in union_rows:
        if int(row["catalog_fields_used_in_grouping"]) != 0:
            raise PermissionError("catalog field used in held-out union")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("family assigned in held-out union")
    _write_csv(union_path, union_rows, UNION_FIELDS)

    interval_states = Counter(
        str(row["interval_status"]) for row in interval_status_rows
    )
    aggregate_status = aggregate_execution_status(interval_status_rows)
    branch_states = {
        "template": dict(
            Counter(
                str(row["template_branch_status"])
                for row in interval_status_rows
            )
        ),
        "generic": dict(
            Counter(
                str(row["generic_branch_status"])
                for row in interval_status_rows
            )
        ),
    }
    status = {
        "status": aggregate_status,
        "stage": (
            "heldout_network_time_only_union_frozen_before_catalog_or_DAS_access"
        ),
        "generated_utc": utc_now(),
        "heldout_network_config_sha256": registration["_config_sha256"],
        "network_registration_status_sha256": sha256_file(
            registration_status_path
        ),
        "network_runner_release_sha256": sha256_file(
            project / RUNNER_RELEASE_RELATIVE_PATH
        ),
        "runner_commit_sha": str(release["runner_commit_sha"]),
        "interval_count": len(intervals),
        "interval_ids": interval_ids,
        "heldout_total_duration_h": registration["heldout_population"][
            "total_duration_h"
        ],
        "interval_generation_status_counts": dict(interval_states),
        "failed_interval_count": int(interval_states.get(STOP, 0)),
        "conditional_interval_count": int(
            interval_states.get(CONDITIONAL, 0)
        ),
        "branch_status_counts": branch_states,
        "template_threshold": registration["template_branch"]["threshold"],
        "generic_threshold": registration["generic_branch"]["threshold"],
        "threshold_recalibration_performed": False,
        "generic_development_SNR1_STOP_preserved": True,
        "all_interval_candidate_tables_checksums_verified_before_union": True,
        "candidate_deletion_after_review_performed": False,
        "cross_interval_candidate_matching_performed": False,
        "time_only_cross_branch_match_window_s": registration[
            "time_only_union"
        ]["cross_branch_match_window_s"],
        "source_request_row_count": len(source_rows),
        "available_source_count": sum(
            str(row["status"]) == "available" for row in source_rows
        ),
        "trace_QC_row_count": len(trace_rows),
        "usable_trace_count": sum(
            str(row["status"]) == "usable" for row in trace_rows
        ),
        "template_candidate_count": len(template_rows),
        "generic_candidate_count": len(generic_rows),
        "time_only_union_candidate_count": len(union_rows),
        "cross_branch_pair_count": sum(
            int(row["branch_count"]) == 2 for row in union_rows
        ),
        "source_request_ledger_sha256": sha256_file(source_path),
        "trace_QC_sha256": sha256_file(trace_path),
        "interval_status_sha256": sha256_file(interval_status_path),
        "template_candidate_sha256": sha256_file(template_path),
        "generic_candidate_sha256": sha256_file(generic_path),
        "time_only_union_sha256": sha256_file(union_path),
        "network_union_complete_and_frozen": True,
        "heldout_network_waveform_files_opened": sum(
            str(row["status"]) == "available" for row in source_rows
        ),
        "historical_template_waveform_files_opened": sum(
            int(row["historical_template_waveform_files_opened"])
            for row in interval_status_rows
        ),
        "heldout_catalog_event_rows_opened": 0,
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "heldout_family_label_rows_opened": 0,
        "candidate_family_assignments_made": 0,
        "next_stage_gate": (
            "PASS_REGISTER_POST_UNION_CATALOG_AUDIT_AND_HELDOUT_DAS_REPLAY"
        ),
        "catalog_access_gate": (
            "PASS_COMPLETE_NETWORK_TIME_ONLY_UNION_IS_CHECKSUMMED"
        ),
        "DAS_access_gate": (
            "PASS_COMPLETE_HELDOUT_NETWORK_UNION_IS_FROZEN_"
            "REGISTER_DAS_REPLAY_BEFORE_WAVEFORM_ACCESS"
        ),
        "family_assignment_gate": "STOP_FAMILY_ASSIGNMENT_FORBIDDEN",
    }
    write_json(final_status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
