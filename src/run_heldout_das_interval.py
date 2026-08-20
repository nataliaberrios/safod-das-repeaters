#!/usr/bin/env python
"""Run one remotely released, independently triggered held-out DAS interval."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

import numpy as np

from .common import PASS, load_config, project_root, sha256_file, utc_now, write_json
from .das_continuous_detection import (
    block_characteristic_matrix,
    channel_qc,
    detect_das_candidates,
    higher_quantile,
    null_block_maxima,
    preprocess_registered_chunks,
    score_interval,
)
from .heldout_das_runner import (
    BASE_V1_CANDIDATE_FIELDS,
    BLOCK_FIELDS,
    CHANNEL_FIELDS,
    CHUNK_FIELDS,
    NULL_FIELDS,
    V2_CANDIDATE_FIELDS,
    apply_frozen_v2_gate,
    finalize_base_candidates,
    interval_v1_view,
    validate_candidate_tables,
)
from .heldout_das_runner_access import (
    RUNNER_RELEASE_RELATIVE_PATH,
    interval_output_paths,
    load_frozen_detector_configs,
    load_runner_release,
    registered_interval,
    registered_manifest_rows,
    validate_runner_release,
)


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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-id", required=True)
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

    final_status_path = (
        project
        / str(registration["output"]["DAS_directory"])
        / str(registration["output"]["candidate_generation_status_json"])
    )
    if final_status_path.is_file():
        raise PermissionError("held-out DAS candidate set is already frozen")

    interval_id = str(args.interval_id)
    interval = registered_interval(
        project, registration, registration_status, interval_id
    )
    selection_rows = registered_manifest_rows(
        project, registration, registration_status, interval
    )
    paths = interval_output_paths(project, registration, interval_id)
    materialized = [
        path for key, path in paths.items() if key != "root" and path.exists()
    ]
    if materialized:
        raise PermissionError(
            "held-out DAS interval has a prior product; rerun is forbidden"
        )

    v1, _v2 = load_frozen_detector_configs(project, registration)
    view = interval_v1_view(v1, registration, interval)
    source_paths = [Path(str(row["path"])) for row in selection_rows]

    (
        data,
        epoch_s,
        columns,
        loci,
        unit,
        chunk_rows,
        files_read,
        file_use_count,
    ) = preprocess_registered_chunks(source_paths, view)
    expected_files = {str(path) for path in source_paths}
    if set(map(str, files_read)) != expected_files:
        missing = sorted(expected_files - set(map(str, files_read)))
        extra = sorted(set(map(str, files_read)) - expected_files)
        raise RuntimeError(
            "held-out DAS reader/selection mismatch missing={} extra={}".format(
                missing, extra
            )
        )

    usable_channels, channel_rms, channel_rows = channel_qc(
        data, columns, loci
    )
    if int(np.count_nonzero(usable_channels)) < int(
        view["channel_qc"]["minimum_usable_sampled_channels"]
    ):
        raise RuntimeError("too few usable registered held-out DAS channels")
    block_matrix_full, block_ids, block_rows = block_characteristic_matrix(
        data,
        columns,
        loci,
        usable_channels,
        channel_rms,
        view,
    )
    trigger_epoch_s, block_matrix = score_interval(
        block_matrix_full, epoch_s, view
    )
    del data
    del block_matrix_full

    null_maxima = null_block_maxima(block_matrix, view)
    threshold = higher_quantile(
        null_maxima,
        float(view["null_calibration"]["familywise_threshold_quantile"]),
    )
    raw_candidates, score = detect_das_candidates(
        trigger_epoch_s, block_matrix, threshold, view
    )
    base_candidates = finalize_base_candidates(
        raw_candidates,
        interval_id,
        len(block_ids),
        registration,
    )
    v2_candidates = apply_frozen_v2_gate(
        base_candidates, interval_id, registration
    )
    validate_candidate_tables(
        base_candidates,
        v2_candidates,
        interval,
        registration,
        threshold,
    )

    _write_csv(paths["chunk_qc"], chunk_rows, CHUNK_FIELDS)
    _write_csv(paths["channel_qc"], channel_rows, CHANNEL_FIELDS)
    _write_csv(paths["block_qc"], block_rows, BLOCK_FIELDS)
    _write_csv(
        paths["null_maxima"],
        [
            {"replicate": index, "maximum_score": float(value)}
            for index, value in enumerate(null_maxima, start=1)
        ],
        NULL_FIELDS,
    )
    _write_csv(
        paths["base_v1_candidates"],
        base_candidates,
        BASE_V1_CANDIDATE_FIELDS,
    )
    _write_csv(paths["v2_candidates"], v2_candidates, V2_CANDIDATE_FIELDS)

    paths["score_cache"].parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        paths["score_cache"],
        trigger_epoch_s=np.asarray(trigger_epoch_s, dtype=np.float64),
        coincidence_score=np.asarray(score, dtype=np.float32),
        block_characteristic=np.asarray(block_matrix, dtype=np.float32),
        usable_block_ids=np.asarray(block_ids, dtype=np.int64),
        sampled_columns=np.asarray(columns, dtype=np.int64),
        locus_indices=np.asarray(loci, dtype=np.int64),
        threshold=np.asarray(float(threshold)),
        heldout_DAS_config_sha256=np.asarray(registration["_config_sha256"]),
        interval_id=np.asarray(interval_id),
    )

    status = {
        "status": PASS,
        "stage": "heldout_DAS_interval_candidate_generation_complete",
        "generated_utc": utc_now(),
        "interval_id": interval_id,
        "interval_start_utc": str(interval["interval_start_utc"]),
        "interval_end_utc": str(interval["interval_end_utc"]),
        "heldout_DAS_config_sha256": registration["_config_sha256"],
        "runner_commit_sha": str(release["runner_commit_sha"]),
        "DAS_runner_release_sha256": sha256_file(
            project / RUNNER_RELEASE_RELATIVE_PATH
        ),
        "DAS_registration_status_sha256": sha256_file(
            registration_status_path
        ),
        "manifest_selection_sha256": registration_status[
            "manifest_selection_sha256"
        ],
        "interval_selection_sha256": registration_status[
            "interval_selection_sha256"
        ],
        "selected_manifest_record_count": len(selection_rows),
        "selected_manifest_paths_all_read": True,
        "unique_hdf5_files_read": len(files_read),
        "hdf5_chunk_file_use_count": int(file_use_count),
        "hdf5_dataset_names_read": [
            "/Acquisition/Raw[0]/RawData",
            "/Acquisition/Raw[0]/RawDataTime",
            "Acquisition_and_Raw_attributes",
        ],
        "processed_optical_phase_unit": unit,
        "strain_claim_enabled": False,
        "absolute_geometry_claim_enabled": False,
        "sampled_channel_count": len(columns),
        "usable_sampled_channel_count": int(np.count_nonzero(usable_channels)),
        "usable_block_count": len(block_ids),
        "usable_block_ids": list(map(int, block_ids)),
        "chunk_count": len(chunk_rows),
        "score_sample_rate_hz": view["preprocessing"]["score_sample_rate_hz"],
        "score_sample_count": len(score),
        "null_method": view["null_calibration"]["method"],
        "null_random_seed": view["null_calibration"]["random_seed"],
        "null_replicate_count": len(null_maxima),
        "null_familywise_threshold_quantile": view["null_calibration"][
            "familywise_threshold_quantile"
        ],
        "detection_threshold": float(threshold),
        "observed_maximum_score": float(np.max(score)),
        "base_v1_candidate_count": len(base_candidates),
        "v2_candidate_count": len(v2_candidates),
        "v2_minimum_strong_block_count": registration[
            "detector_inheritance"
        ]["DAS_v2_minimum_strong_block_count"],
        "v2_strong_block_characteristic_ratio": registration[
            "detector_inheritance"
        ]["DAS_v2_strong_block_characteristic_ratio"],
        "threshold_recalibration_or_repair_performed": False,
        "v2_threshold_or_support_sweep_performed": False,
        "candidate_deletion_after_review_performed": False,
        "chunk_QC_sha256": sha256_file(paths["chunk_qc"]),
        "channel_QC_sha256": sha256_file(paths["channel_qc"]),
        "block_QC_sha256": sha256_file(paths["block_qc"]),
        "null_maxima_sha256": sha256_file(paths["null_maxima"]),
        "base_v1_candidate_sha256": sha256_file(paths["base_v1_candidates"]),
        "v2_candidate_sha256": sha256_file(paths["v2_candidates"]),
        "full_score_cache_path": str(paths["score_cache"]),
        "full_score_cache_sha256": sha256_file(paths["score_cache"]),
        "heldout_DAS_HDF5_files_opened": len(files_read),
        "network_candidate_table_rows_opened": 0,
        "catalog_association_table_rows_opened": 0,
        "network_or_catalog_candidate_time_fields_read": 0,
        "heldout_family_label_rows_opened": 0,
        "candidate_family_assignments_made": 0,
        "next_stage_gate": "PASS_WAIT_FOR_ALL_12_DAS_INTERVALS_THEN_FREEZE",
        "comparison_access_gate": (
            "STOP_UNTIL_COMPLETE_DAS_CANDIDATE_TABLES_ARE_FROZEN"
        ),
    }
    write_json(paths["status"], status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
