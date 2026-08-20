#!/usr/bin/env python
"""Register all held-out DAS files without opening an HDF5 header or dataset."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .archive_population import read_primary_manifest
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
from .heldout_das_access import (
    select_manifest_records,
    validate_detector_and_stage_gates,
    validate_frozen_inputs,
    validate_heldout_intervals,
    validate_release_anchor,
)


MANIFEST_SELECTION_FIELDS = [
    "selection_index",
    "interval_id",
    "interval_file_index",
    "path",
    "manifest_start_utc",
    "manifest_end_utc",
    "sample_rate_hz",
    "channel_count",
    "gauge_length_m",
    "file_exists",
    "size_bytes",
    "selection_basis",
    "network_candidate_time_fields_read",
    "catalog_event_time_fields_read",
    "family_label_fields_read",
    "hdf5_file_opened",
    "hdf5_header_opened",
    "hdf5_dataset_opened",
]

INTERVAL_SELECTION_FIELDS = [
    "interval_id",
    "interval_start_utc",
    "interval_end_utc",
    "interval_duration_s",
    "padded_request_start_utc",
    "padded_request_end_utc",
    "filter_padding_s",
    "selected_manifest_record_count",
    "selected_existing_file_count",
    "selected_total_size_bytes",
    "selected_coverage_start_utc",
    "selected_coverage_end_utc",
    "maximum_manifest_gap_s",
    "selection_basis",
    "network_candidate_time_fields_read",
    "catalog_event_time_fields_read",
    "family_label_fields_read",
    "hdf5_files_opened",
    "hdf5_headers_opened",
    "hdf5_datasets_opened",
    "status",
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


def _archive_root(registration: Mapping[str, Any]) -> Path:
    roots = {
        str(row["filesystem_prefix"])
        for row in registration["manifest"]["path_rewrites"]
    }
    if len(roots) != 1:
        raise RuntimeError("held-out DAS registration must declare one archive root")
    return Path(next(iter(roots))).resolve()


def build_manifest_selection(
    records: Sequence[Any],
    intervals: Sequence[Mapping[str, Any]],
    registration: Mapping[str, Any],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build a file ledger from interval and manifest metadata only."""

    archive_root = _archive_root(registration)
    padding_s = float(registration["heldout_population"]["filter_padding_s"])
    maximum_gap_s = float(registration["manifest"]["maximum_internal_gap_s"])
    selection_rows: List[Dict[str, Any]] = []
    interval_rows: List[Dict[str, Any]] = []
    for interval in intervals:
        interval_id = str(interval["interval_id"])
        start_s = parse_utc(str(interval["start_utc"])).timestamp()
        end_s = parse_utc(str(interval["end_utc"])).timestamp()
        request_start_s = start_s - padding_s
        request_end_s = end_s + padding_s
        selected, summary = select_manifest_records(
            records,
            request_start_s,
            request_end_s,
            maximum_gap_s,
        )
        one_interval: List[Dict[str, Any]] = []
        for file_index, record in enumerate(selected, start=1):
            path = Path(str(record.path))
            resolved = path.resolve()
            try:
                resolved.relative_to(archive_root)
            except ValueError as exc:
                raise PermissionError(
                    "manifest path escapes registered DAS archive root: {}".format(path)
                ) from exc
            exists = path.is_file()
            size_bytes = path.stat().st_size if exists else ""
            row = {
                "selection_index": len(selection_rows) + 1,
                "interval_id": interval_id,
                "interval_file_index": file_index,
                "path": str(path),
                "manifest_start_utc": iso_utc(float(record.start_s)),
                "manifest_end_utc": iso_utc(float(record.end_s)),
                "sample_rate_hz": float(record.sample_rate_hz),
                "channel_count": int(record.channel_count),
                "gauge_length_m": float(record.gauge_length_m),
                "file_exists": exists,
                "size_bytes": size_bytes,
                "selection_basis": "interval_and_manifest_acquisition_metadata_only",
                "network_candidate_time_fields_read": 0,
                "catalog_event_time_fields_read": 0,
                "family_label_fields_read": 0,
                "hdf5_file_opened": False,
                "hdf5_header_opened": False,
                "hdf5_dataset_opened": False,
            }
            selection_rows.append(row)
            one_interval.append(row)
        missing = [row["path"] for row in one_interval if not row["file_exists"]]
        if missing:
            raise FileNotFoundError(
                "{} registered DAS files are missing for {}".format(
                    len(missing), interval_id
                )
            )
        interval_rows.append(
            {
                "interval_id": interval_id,
                "interval_start_utc": str(interval["start_utc"]),
                "interval_end_utc": str(interval["end_utc"]),
                "interval_duration_s": float(interval["duration_s"]),
                "padded_request_start_utc": iso_utc(request_start_s),
                "padded_request_end_utc": iso_utc(request_end_s),
                "filter_padding_s": padding_s,
                "selected_manifest_record_count": len(one_interval),
                "selected_existing_file_count": sum(
                    bool(row["file_exists"]) for row in one_interval
                ),
                "selected_total_size_bytes": sum(
                    int(row["size_bytes"]) for row in one_interval
                ),
                "selected_coverage_start_utc": iso_utc(
                    summary["coverage_start_epoch_s"]
                ),
                "selected_coverage_end_utc": iso_utc(
                    summary["coverage_end_epoch_s"]
                ),
                "maximum_manifest_gap_s": summary["maximum_manifest_gap_s"],
                "selection_basis": "interval_and_manifest_acquisition_metadata_only",
                "network_candidate_time_fields_read": 0,
                "catalog_event_time_fields_read": 0,
                "family_label_fields_read": 0,
                "hdf5_files_opened": 0,
                "hdf5_headers_opened": 0,
                "hdf5_datasets_opened": 0,
                "status": PASS,
            }
        )
    return selection_rows, interval_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "heldout_das_replay.json",
    )
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    paths = validate_frozen_inputs(project, registration)
    parent = load_config(paths["parent_config"])
    v1 = load_config(paths["DAS_v1_config"])
    v2 = load_config(paths["DAS_v2_config"])
    v2_status = _load_json(paths["DAS_v2_development_status"])
    network_status = _load_json(paths["network_candidate_generation_status"])
    catalog_status = _load_json(paths["catalog_audit_status"])
    population_status = _load_json(paths["population_status"])
    intervals = _read_csv(paths["heldout_intervals"])

    validate_detector_and_stage_gates(
        registration,
        parent,
        v1,
        v2,
        v2_status,
        network_status,
        catalog_status,
        population_status,
    )
    total_h, interval_ids = validate_heldout_intervals(
        intervals, registration
    )
    release_status = validate_release_anchor(project, registration)

    records, manifest_stats = read_primary_manifest(
        paths["DAS_manifest"], registration["manifest"]
    )
    expected_manifest_rows = int(
        registration["registration_access_ledger"]["DAS_manifest_rows_opened"]
    )
    if int(manifest_stats["manifest_rows"]) != expected_manifest_rows:
        raise RuntimeError("DAS manifest row count changed")
    if len(records) != int(population_status["primary_manifest_record_count"]):
        raise RuntimeError("primary DAS manifest record count changed")
    selection_rows, interval_rows = build_manifest_selection(
        records, intervals, registration
    )
    if [row["interval_id"] for row in interval_rows] != interval_ids:
        raise RuntimeError("DAS interval selection ordering changed")
    if any(str(row["status"]) != PASS for row in interval_rows):
        raise RuntimeError("not every held-out DAS interval selection passed")

    output = project / str(registration["output"]["registration_directory"])
    selection_path = output / str(
        registration["output"]["manifest_selection_csv"]
    )
    interval_path = output / str(
        registration["output"]["interval_selection_csv"]
    )
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    _write_csv(selection_path, selection_rows, MANIFEST_SELECTION_FIELDS)
    _write_csv(interval_path, interval_rows, INTERVAL_SELECTION_FIELDS)

    unique_paths = {str(row["path"]) for row in selection_rows}
    status = {
        "status": PASS,
        "stage": "heldout_DAS_manifest_registered_before_any_HDF5_access",
        "registration_status": (
            "FROZEN_FOR_HELDOUT_DAS_RUNNER_IMPLEMENTATION_ONLY"
        ),
        "generated_utc": utc_now(),
        "heldout_DAS_config_sha256": registration["_config_sha256"],
        "frozen_input_sha256": {
            name: str(declaration["sha256"])
            for name, declaration in registration["frozen_inputs"].items()
        },
        "release_anchor": release_status,
        "interval_count": len(interval_rows),
        "interval_ids": interval_ids,
        "heldout_total_duration_h": total_h,
        "filter_padding_s": registration["heldout_population"][
            "filter_padding_s"
        ],
        "manifest_rows_opened": manifest_stats["manifest_rows"],
        "manifest_primary_record_count": len(records),
        "manifest_stats": manifest_stats,
        "manifest_selection_path": str(selection_path),
        "manifest_selection_sha256": sha256_file(selection_path),
        "manifest_selection_row_count": len(selection_rows),
        "manifest_selection_unique_file_count": len(unique_paths),
        "manifest_selection_total_size_bytes": sum(
            int(row["size_bytes"]) for row in selection_rows
        ),
        "interval_selection_path": str(interval_path),
        "interval_selection_sha256": sha256_file(interval_path),
        "interval_selection_PASS_count": sum(
            str(row["status"]) == PASS for row in interval_rows
        ),
        "manifest_selection_used_interval_metadata_fields": True,
        "manifest_selection_used_DAS_acquisition_metadata_fields": True,
        "manifest_selection_used_network_candidate_times": False,
        "manifest_selection_used_catalog_event_times": False,
        "manifest_selection_used_family_labels": False,
        "raw_HDF5_files_stat_checked": len(unique_paths),
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_headers_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "network_stage_status_files_opened": 1,
        "catalog_stage_status_files_opened": 1,
        "network_candidate_table_rows_opened": 0,
        "catalog_association_table_rows_opened": 0,
        "network_or_catalog_candidate_time_fields_read": 0,
        "heldout_family_label_rows_opened": 0,
        "base_v1_candidate_rows_materialized": 0,
        "v2_candidate_rows_materialized": 0,
        "threshold_recalibration_or_repair_enabled": False,
        "v2_threshold_or_support_sweep_enabled": False,
        "candidate_family_assignment_enabled": False,
        "next_stage_gate": (
            "PASS_IMPLEMENT_TEST_COMMIT_PUSH_AND_REMOTE_RELEASE_"
            "HELDOUT_DAS_RUNNER"
        ),
        "heldout_DAS_waveform_access_gate": (
            "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
        ),
        "network_or_catalog_candidate_time_access_gate": (
            "STOP_UNTIL_COMPLETE_DAS_CANDIDATE_TABLES_ARE_FROZEN"
        ),
        "heldout_family_label_access_gate": "STOP_FORBIDDEN",
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
