#!/usr/bin/env python
"""Register manifest-only DAS inputs before opening any HDF5 waveform dataset."""

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
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .das_development_access import (
    select_registered_manifest_records,
    validate_das_development_registration,
)


SELECTION_FIELDS = [
    "selection_index",
    "path",
    "manifest_start_utc",
    "manifest_end_utc",
    "sample_rate_hz",
    "channel_count",
    "gauge_length_m",
    "file_exists",
    "size_bytes",
    "selection_basis",
    "hdf5_file_opened",
    "hdf5_dataset_opened",
]


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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
    rewrites = registration["manifest"]["path_rewrites"]
    roots = {
        str(row["filesystem_prefix"])
        for row in rewrites
    }
    if len(roots) != 1:
        raise RuntimeError("registration must declare one DAS archive root")
    return Path(next(iter(roots))).resolve()


def _selection_rows(
    selected: Sequence[Any], archive_root: Path
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, record in enumerate(selected, start=1):
        path = Path(record.path)
        resolved = path.resolve()
        try:
            resolved.relative_to(archive_root)
        except ValueError as exc:
            raise PermissionError(
                "manifest path escapes registered archive root: {}".format(path)
            ) from exc
        exists = path.is_file()
        size_bytes = path.stat().st_size if exists else ""
        rows.append(
            {
                "selection_index": index,
                "path": str(path),
                "manifest_start_utc": iso_utc(record.start_s),
                "manifest_end_utc": iso_utc(record.end_s),
                "sample_rate_hz": record.sample_rate_hz,
                "channel_count": record.channel_count,
                "gauge_length_m": record.gauge_length_m,
                "file_exists": exists,
                "size_bytes": size_bytes,
                "selection_basis": (
                    "primary_configuration_and_padded_interval_only"
                ),
                "hdf5_file_opened": False,
                "hdf5_dataset_opened": False,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "das_development.json",
    )
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    parent_path = project / str(registration["parent_config_path"])
    parent = load_config(parent_path)

    gate_path = project / str(registration["network_union_gate"]["path"])
    if sha256_file(gate_path) != str(
        registration["network_union_gate"]["sha256"]
    ):
        raise RuntimeError("network-union authorization checksum changed")
    union_status = _load_json(gate_path)
    start_s, end_s, request_start_s, request_end_s = (
        validate_das_development_registration(
            parent, registration, union_status
        )
    )

    manifest_path = Path(registration["manifest"]["path"])
    if sha256_file(manifest_path) != str(
        registration["manifest"]["sha256"]
    ):
        raise RuntimeError("read-only DAS manifest checksum changed")
    records, manifest_stats = read_primary_manifest(
        manifest_path, registration["manifest"]
    )
    selected, selection_summary = select_registered_manifest_records(
        records,
        request_start_s,
        request_end_s,
        float(registration["manifest"]["maximum_internal_gap_s"]),
    )
    rows = _selection_rows(selected, _archive_root(registration))
    missing = [row["path"] for row in rows if not row["file_exists"]]
    if missing:
        raise FileNotFoundError(
            "{} registered DAS files are missing".format(len(missing))
        )

    output = project / str(registration["output"]["directory"])
    selection_path = output / str(
        registration["output"]["manifest_selection_csv"]
    )
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    _write_csv(selection_path, rows, SELECTION_FIELDS)

    status = {
        "status": PASS,
        "stage": "das_development_manifest_registered_before_waveform_access",
        "registration_status": "FROZEN_BEFORE_RAW_DAS_ACCESS",
        "generated_utc": utc_now(),
        "das_development_config_sha256": registration["_config_sha256"],
        "parent_config_sha256": registration["parent_config_sha256"],
        "network_union_gate_sha256": registration["network_union_gate"][
            "sha256"
        ],
        "manifest_sha256": registration["manifest"]["sha256"],
        "manifest_primary_record_count": len(records),
        "manifest_stats": manifest_stats,
        "development_interval_start_utc": iso_utc(start_s),
        "development_interval_end_utc": iso_utc(end_s),
        "development_interval_duration_s": end_s - start_s,
        "padded_request_start_utc": iso_utc(request_start_s),
        "padded_request_end_utc": iso_utc(request_end_s),
        "filter_padding_s": registration["interval"]["filter_padding_s"],
        "selected_manifest_record_count": len(selected),
        "selected_existing_file_count": sum(
            bool(row["file_exists"]) for row in rows
        ),
        "selected_total_size_bytes": sum(
            int(row["size_bytes"]) for row in rows
        ),
        "selected_coverage_start_utc": iso_utc(
            selection_summary["coverage_start_epoch_s"]
        ),
        "selected_coverage_end_utc": iso_utc(
            selection_summary["coverage_end_epoch_s"]
        ),
        "maximum_manifest_gap_s": selection_summary[
            "maximum_manifest_gap_s"
        ],
        "manifest_selection_path": str(selection_path),
        "manifest_selection_sha256": sha256_file(selection_path),
        "manifest_selection_used_catalog_fields": 0,
        "manifest_selection_used_network_candidate_times": 0,
        "raw_hdf5_files_stat_checked": len(rows),
        "raw_hdf5_files_opened": 0,
        "raw_hdf5_datasets_opened": 0,
        "network_union_status_files_opened_for_stage_gate": 1,
        "network_candidate_tables_opened": 0,
        "catalog_event_time_tables_opened": 0,
        "heldout_interval_tables_opened": 0,
        "next_stage_gate": (
            "PASS_RAW_DAS_DEVELOPMENT_INTERVAL_PLUS_PADDING_ONLY"
        ),
        "next_stage_forbidden_until_raw_candidate_checksum": (
            "network_union_matching_catalog_adjudication_family_assignment"
        ),
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
