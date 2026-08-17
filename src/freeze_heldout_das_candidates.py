#!/usr/bin/env python
"""Freeze complete held-out DAS candidate tables before any comparison read."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import PASS, load_config, project_root, sha256_file, utc_now, write_json
from .heldout_das_runner import (
    BASE_V1_CANDIDATE_FIELDS,
    INTERVAL_STATUS_FIELDS,
    V2_CANDIDATE_FIELDS,
    validate_candidate_tables,
)
from .heldout_das_runner_access import (
    RUNNER_RELEASE_RELATIVE_PATH,
    interval_output_paths,
    load_runner_release,
    registered_interval_rows,
    validate_runner_release,
)


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


def _higher_quantile(values: Sequence[float], quantile: float) -> float:
    ordered = sorted(map(float, values))
    if not ordered or not 0.0 < float(quantile) <= 1.0:
        raise ValueError("invalid held-out DAS null quantile input")
    index = max(
        0,
        min(
            len(ordered) - 1,
            int(math.ceil(float(quantile) * len(ordered))) - 1,
        ),
    )
    return ordered[index]


def _validate_interval_status(
    status: Mapping[str, Any],
    interval: Mapping[str, Any],
    registration: Mapping[str, Any],
    release: Mapping[str, Any],
    release_sha256: str,
) -> None:
    if str(status["status"]) != PASS:
        raise PermissionError("held-out DAS interval did not pass")
    if str(status["stage"]) != (
        "heldout_DAS_interval_candidate_generation_complete"
    ):
        raise PermissionError("unexpected held-out DAS interval stage")
    for field in ("interval_id", "interval_start_utc", "interval_end_utc"):
        expected_field = field.replace("interval_", "interval_", 1)
        if str(status[field]) != str(interval[expected_field]):
            raise RuntimeError("held-out DAS interval identity changed")
    if str(status["heldout_DAS_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise RuntimeError("held-out DAS interval config hash changed")
    if str(status["runner_commit_sha"]) != str(release["runner_commit_sha"]):
        raise RuntimeError("held-out DAS interval runner SHA changed")
    if str(status["DAS_runner_release_sha256"]) != str(release_sha256):
        raise RuntimeError("held-out DAS interval release hash changed")
    if bool(status["threshold_recalibration_or_repair_performed"]):
        raise PermissionError("held-out DAS threshold was repaired")
    if bool(status["v2_threshold_or_support_sweep_performed"]):
        raise PermissionError("held-out DAS v2 rule was swept")
    if bool(status["candidate_deletion_after_review_performed"]):
        raise PermissionError("held-out DAS candidate was deleted")
    if int(status["v2_minimum_strong_block_count"]) != int(
        registration["detector_inheritance"]["DAS_v2_minimum_strong_block_count"]
    ):
        raise PermissionError("held-out DAS v2 support count changed")
    if not math.isclose(
        float(status["v2_strong_block_characteristic_ratio"]),
        float(
            registration["detector_inheritance"][
                "DAS_v2_strong_block_characteristic_ratio"
            ]
        ),
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise PermissionError("held-out DAS v2 support ratio changed")
    for field in (
        "network_candidate_table_rows_opened",
        "catalog_association_table_rows_opened",
        "network_or_catalog_candidate_time_fields_read",
        "heldout_family_label_rows_opened",
        "candidate_family_assignments_made",
    ):
        if int(status[field]) != 0:
            raise PermissionError("held-out DAS interval leaked comparison data")


def _status_row(status: Mapping[str, Any]) -> Dict[str, Any]:
    row = {
        "interval_status": status["status"],
        **{field: status[field] for field in INTERVAL_STATUS_FIELDS if field in status},
    }
    return row


def collect_interval_products(
    project: Path,
    registration: Mapping[str, Any],
    release: Mapping[str, Any],
    intervals: Sequence[Mapping[str, Any]],
) -> Tuple[List[Dict[str, str]], List[Dict[str, str]], List[Dict[str, Any]]]:
    """Checksum and validate all 12 interval products without comparison data."""

    release_sha256 = sha256_file(project / RUNNER_RELEASE_RELATIVE_PATH)
    all_base: List[Dict[str, str]] = []
    all_v2: List[Dict[str, str]] = []
    interval_statuses: List[Dict[str, Any]] = []
    for interval in intervals:
        interval_id = str(interval["interval_id"])
        paths = interval_output_paths(project, registration, interval_id)
        required = [
            "chunk_qc",
            "channel_qc",
            "block_qc",
            "null_maxima",
            "base_v1_candidates",
            "v2_candidates",
            "status",
            "score_cache",
        ]
        missing = [name for name in required if not paths[name].is_file()]
        if missing:
            raise FileNotFoundError(
                "held-out DAS interval {} is incomplete: {}".format(
                    interval_id, missing
                )
            )
        status = _load_json(paths["status"])
        _validate_interval_status(
            status, interval, registration, release, release_sha256
        )
        path_hash_fields = {
            "chunk_qc": "chunk_QC_sha256",
            "channel_qc": "channel_QC_sha256",
            "block_qc": "block_QC_sha256",
            "null_maxima": "null_maxima_sha256",
            "base_v1_candidates": "base_v1_candidate_sha256",
            "v2_candidates": "v2_candidate_sha256",
            "score_cache": "full_score_cache_sha256",
        }
        for name, field in path_hash_fields.items():
            if sha256_file(paths[name]) != str(status[field]):
                raise RuntimeError(
                    "held-out DAS interval product changed: {} {}".format(
                        interval_id, name
                    )
                )

        chunk_rows = _read_csv(paths["chunk_qc"])
        channel_rows = _read_csv(paths["channel_qc"])
        block_rows = _read_csv(paths["block_qc"])
        null_rows = _read_csv(paths["null_maxima"])
        base_rows = _read_csv(paths["base_v1_candidates"])
        v2_rows = _read_csv(paths["v2_candidates"])
        if len(chunk_rows) != int(status["chunk_count"]):
            raise RuntimeError("held-out DAS chunk count changed")
        if any(str(row["status"]) != PASS for row in chunk_rows):
            raise PermissionError("held-out DAS chunk QC did not pass")
        if len(channel_rows) != int(status["sampled_channel_count"]):
            raise RuntimeError("held-out DAS channel QC count changed")
        if sum(str(row["usable"]).lower() == "true" for row in channel_rows) != int(
            status["usable_sampled_channel_count"]
        ):
            raise RuntimeError("held-out DAS usable channel count changed")
        if sum(str(row["status"]) == PASS for row in block_rows) != int(
            status["usable_block_count"]
        ):
            raise RuntimeError("held-out DAS usable block count changed")
        if len(null_rows) != int(status["null_replicate_count"]):
            raise RuntimeError("held-out DAS null replicate count changed")
        null_threshold = _higher_quantile(
            [float(row["maximum_score"]) for row in null_rows],
            float(status["null_familywise_threshold_quantile"]),
        )
        if not math.isclose(
            null_threshold,
            float(status["detection_threshold"]),
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise RuntimeError("held-out DAS null threshold changed")
        if len(base_rows) != int(status["base_v1_candidate_count"]):
            raise RuntimeError("held-out DAS base candidate count changed")
        if len(v2_rows) != int(status["v2_candidate_count"]):
            raise RuntimeError("held-out DAS v2 candidate count changed")
        validate_candidate_tables(
            base_rows,
            v2_rows,
            interval,
            registration,
            float(status["detection_threshold"]),
        )
        all_base.extend(base_rows)
        all_v2.extend(v2_rows)
        interval_statuses.append(_status_row(status))
    return all_base, all_v2, interval_statuses


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
    registration_root = project / str(
        registration["output"]["registration_directory"]
    )
    registration_status_path = registration_root / str(
        registration["output"]["registration_status_json"]
    )
    registration_status = _load_json(registration_status_path)
    release = load_runner_release(project)
    validate_runner_release(
        project, registration, registration_status, release
    )

    output = project / str(registration["output"]["DAS_directory"])
    final_status_path = output / str(
        registration["output"]["candidate_generation_status_json"]
    )
    if final_status_path.is_file():
        raise PermissionError("held-out DAS candidate set is already frozen")
    intervals = registered_interval_rows(
        project, registration, registration_status
    )
    base_rows, v2_rows, interval_status_rows = collect_interval_products(
        project, registration, release, intervals
    )

    base_path = output / str(registration["output"]["base_v1_candidate_csv"])
    v2_path = output / str(registration["output"]["v2_candidate_csv"])
    interval_status_path = output / str(
        registration["output"]["interval_status_csv"]
    )
    _write_csv(base_path, base_rows, BASE_V1_CANDIDATE_FIELDS)
    _write_csv(v2_path, v2_rows, V2_CANDIDATE_FIELDS)
    _write_csv(
        interval_status_path, interval_status_rows, INTERVAL_STATUS_FIELDS
    )

    thresholds = [float(row["detection_threshold"]) for row in interval_status_rows]
    status = {
        "status": PASS,
        "stage": "heldout_DAS_candidate_tables_frozen_before_any_comparison",
        "generated_utc": utc_now(),
        "heldout_DAS_config_sha256": registration["_config_sha256"],
        "DAS_registration_status_sha256": sha256_file(
            registration_status_path
        ),
        "DAS_runner_release_sha256": sha256_file(
            project / RUNNER_RELEASE_RELATIVE_PATH
        ),
        "runner_commit_sha": str(release["runner_commit_sha"]),
        "interval_count": len(intervals),
        "interval_ids": [str(row["interval_id"]) for row in intervals],
        "heldout_total_duration_h": registration["heldout_population"][
            "total_duration_h"
        ],
        "interval_generation_status_counts": dict(
            Counter(str(row["interval_status"]) for row in interval_status_rows)
        ),
        "all_12_intervals_PASS": len(interval_status_rows) == 12
        and all(str(row["interval_status"]) == PASS for row in interval_status_rows),
        "per_interval_null_thresholds": thresholds,
        "threshold_recalibration_or_repair_performed": False,
        "v2_threshold_or_support_sweep_performed": False,
        "candidate_deletion_after_review_performed": False,
        "base_v1_candidate_count": len(base_rows),
        "v2_candidate_count": len(v2_rows),
        "v2_rejected_base_candidate_count": len(base_rows) - len(v2_rows),
        "base_v1_candidate_sha256": sha256_file(base_path),
        "v2_candidate_sha256": sha256_file(v2_path),
        "interval_status_sha256": sha256_file(interval_status_path),
        "all_interval_product_hashes_verified_before_aggregate": True,
        "complete_DAS_candidate_tables_frozen": True,
        "registered_manifest_file_count": registration_status[
            "manifest_selection_unique_file_count"
        ],
        "heldout_DAS_unique_HDF5_files_opened": sum(
            int(row["unique_hdf5_files_read"]) for row in interval_status_rows
        ),
        "network_candidate_table_rows_opened": 0,
        "catalog_association_table_rows_opened": 0,
        "network_or_catalog_candidate_time_fields_read": 0,
        "heldout_family_label_rows_opened": 0,
        "candidate_family_assignments_made": 0,
        "scientific_extension_claim_gate": (
            "STOP_PENDING_TIME_ONLY_COMPARISON_AND_INDEPENDENT_ADJUDICATION"
        ),
        "next_stage_gate": (
            "PASS_REGISTER_SEPARATE_TIME_ONLY_DAS_NETWORK_COMPARISON"
        ),
        "comparison_access_gate": (
            "PASS_COMPLETE_DAS_CANDIDATE_TABLES_ARE_CHECKSUMMED"
        ),
        "heldout_family_label_access_gate": "STOP_PENDING_SEPARATE_REGISTRATION",
    }
    if not status["all_12_intervals_PASS"]:
        raise PermissionError("complete held-out DAS freeze requires 12/12 PASS")
    if int(status["heldout_DAS_unique_HDF5_files_opened"]) != int(
        registration_status["manifest_selection_unique_file_count"]
    ):
        raise RuntimeError("held-out DAS aggregate did not read every selected file")
    write_json(final_status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
