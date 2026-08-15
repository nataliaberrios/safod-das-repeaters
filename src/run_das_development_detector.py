#!/usr/bin/env python
"""Run the registered independent DAS-only detector without comparison inputs."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

from .common import (
    CONDITIONAL,
    iso_utc,
    load_config,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .das_continuous_detection import (
    block_characteristic_matrix,
    channel_qc,
    detect_das_candidates,
    higher_quantile,
    null_block_maxima,
    preprocess_registered_chunks,
    score_interval,
)


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

CANDIDATE_FIELDS = [
    "candidate_id",
    "trigger_time",
    "trigger_epoch_s",
    "coincidence_score",
    "threshold",
    "coincidence_block_count",
    "block_support_count_at_declared_ratio",
    "block_characteristic_support_threshold",
    "candidate_generation_label",
    "network_candidate_time_fields_read",
    "catalog_event_time_fields_read",
    "heldout_interval_fields_read",
    "family_assignment",
]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


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


def _validate_runtime_inputs(
    project: Path, registration: Mapping[str, Any]
) -> tuple[Dict[str, Any], Path, List[Dict[str, str]]]:
    output = project / str(registration["output"]["directory"])
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    selection_path = output / str(
        registration["output"]["manifest_selection_csv"]
    )
    registration_status = _load_json(status_path)
    if str(registration_status["das_development_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise RuntimeError("DAS config changed after manifest registration")
    if str(registration_status["registration_status"]) != (
        "FROZEN_BEFORE_RAW_DAS_ACCESS"
    ):
        raise PermissionError("DAS pre-waveform registration is not frozen")
    if str(registration_status["next_stage_gate"]) != (
        "PASS_RAW_DAS_DEVELOPMENT_INTERVAL_PLUS_PADDING_ONLY"
    ):
        raise PermissionError("raw DAS development access is not released")
    for field in (
        "raw_hdf5_files_opened",
        "raw_hdf5_datasets_opened",
        "network_candidate_tables_opened",
        "catalog_event_time_tables_opened",
        "heldout_interval_tables_opened",
    ):
        if int(registration_status[field]) != 0:
            raise PermissionError(
                "pre-waveform registration has nonzero {}".format(field)
            )
    if sha256_file(selection_path) != str(
        registration_status["manifest_selection_sha256"]
    ):
        raise RuntimeError("DAS manifest-selection checksum changed")
    rows = _read_csv(selection_path)
    if len(rows) != int(
        registration_status["selected_manifest_record_count"]
    ):
        raise RuntimeError("DAS manifest-selection row count changed")
    if any(
        str(row["hdf5_file_opened"]).lower() != "false"
        or str(row["hdf5_dataset_opened"]).lower() != "false"
        for row in rows
    ):
        raise PermissionError("manifest ledger was not sealed pre-waveform")
    return registration_status, selection_path, rows


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
    registration_status, selection_path, selection_rows = (
        _validate_runtime_inputs(project, registration)
    )
    paths = [Path(row["path"]) for row in selection_rows]

    (
        data,
        epoch_s,
        columns,
        loci,
        unit,
        chunk_rows,
        files_read,
        file_use_count,
    ) = preprocess_registered_chunks(paths, registration)
    usable_channels, channel_rms, channel_rows = channel_qc(
        data, columns, loci
    )
    if int(np.count_nonzero(usable_channels)) < int(
        registration["channel_qc"]["minimum_usable_sampled_channels"]
    ):
        raise RuntimeError("too few usable registered DAS channels")
    block_matrix_full, block_ids, block_rows = (
        block_characteristic_matrix(
            data,
            columns,
            loci,
            usable_channels,
            channel_rms,
            registration,
        )
    )
    trigger_epoch_s, block_matrix = score_interval(
        block_matrix_full, epoch_s, registration
    )
    del data
    del block_matrix_full

    null_maxima = null_block_maxima(block_matrix, registration)
    threshold = higher_quantile(
        null_maxima,
        float(
            registration["null_calibration"][
                "familywise_threshold_quantile"
            ]
        ),
    )
    candidates, score = detect_das_candidates(
        trigger_epoch_s, block_matrix, threshold, registration
    )

    output = project / str(registration["output"]["directory"])
    output.mkdir(parents=True, exist_ok=True)
    chunk_path = output / str(registration["output"]["chunk_qc_csv"])
    channel_path = output / str(registration["output"]["channel_qc_csv"])
    block_path = output / str(registration["output"]["block_qc_csv"])
    null_path = output / str(registration["output"]["null_maxima_csv"])
    candidate_path = output / str(registration["output"]["candidate_csv"])
    preview_path = output / str(registration["output"]["score_preview_csv"])
    status_path = output / str(registration["output"]["status_json"])
    cache_path = project / str(registration["output"]["full_score_cache"])

    _write_csv(chunk_path, chunk_rows, CHUNK_FIELDS)
    _write_csv(channel_path, channel_rows, CHANNEL_FIELDS)
    _write_csv(block_path, block_rows, BLOCK_FIELDS)
    _write_csv(
        null_path,
        [
            {"replicate": index + 1, "maximum_score": float(value)}
            for index, value in enumerate(null_maxima)
        ],
        ["replicate", "maximum_score"],
    )
    _write_csv(candidate_path, candidates, CANDIDATE_FIELDS)

    score_rate = float(
        registration["preprocessing"]["score_sample_rate_hz"]
    )
    preview_step = int(
        round(float(registration["output"]["preview_interval_s"]) * score_rate)
    )
    support_ratio = float(
        registration["generic_array_trigger"]["block_support_ratio"]
    )
    preview_rows = [
        {
            "trigger_time": iso_utc(float(trigger_epoch_s[index])),
            "trigger_epoch_s": float(trigger_epoch_s[index]),
            "coincidence_score": float(score[index]),
            "threshold": float(threshold),
            "block_support_count_at_declared_ratio": int(
                np.sum(block_matrix[:, index] >= support_ratio)
            ),
        }
        for index in range(0, len(score), max(1, preview_step))
    ]
    _write_csv(
        preview_path,
        preview_rows,
        [
            "trigger_time",
            "trigger_epoch_s",
            "coincidence_score",
            "threshold",
            "block_support_count_at_declared_ratio",
        ],
    )

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        cache_path,
        trigger_epoch_s=np.asarray(trigger_epoch_s, dtype=np.float64),
        coincidence_score=np.asarray(score, dtype=np.float32),
        block_characteristic=np.asarray(block_matrix, dtype=np.float32),
        usable_block_ids=np.asarray(block_ids, dtype=np.int64),
        sampled_columns=np.asarray(columns, dtype=np.int64),
        locus_indices=np.asarray(loci, dtype=np.int64),
        threshold=np.asarray(float(threshold)),
        config_sha256=np.asarray(registration["_config_sha256"]),
    )

    candidate_sha256 = sha256_file(candidate_path)
    status = {
        "status": CONDITIONAL,
        "stage": (
            "das_only_raw_candidates_materialized_before_any_comparison"
        ),
        "candidate_generation_status": "PASS",
        "generated_utc": utc_now(),
        "design_version": registration["design_version"],
        "das_development_config_sha256": registration["_config_sha256"],
        "registration_status_sha256": sha256_file(
            output
            / str(registration["output"]["registration_status_json"])
        ),
        "manifest_selection_path": str(selection_path),
        "manifest_selection_sha256": registration_status[
            "manifest_selection_sha256"
        ],
        "selected_manifest_record_count": len(selection_rows),
        "unique_hdf5_files_read": len(files_read),
        "hdf5_chunk_file_use_count": file_use_count,
        "raw_hdf5_datasets_read": ["RawData", "RawDataTime", "attributes"],
        "development_interval_duration_s": registration["interval"][
            "duration_s"
        ],
        "filter_padding_s": registration["interval"]["filter_padding_s"],
        "processed_optical_phase_unit": unit,
        "strain_claim_enabled": False,
        "absolute_geometry_claim_enabled": False,
        "sampled_channel_count": len(columns),
        "usable_sampled_channel_count": int(
            np.count_nonzero(usable_channels)
        ),
        "usable_block_count": len(block_ids),
        "usable_block_ids": list(map(int, block_ids)),
        "chunk_count": len(chunk_rows),
        "score_sample_rate_hz": score_rate,
        "score_sample_count": len(score),
        "null_method": registration["null_calibration"]["method"],
        "null_replicate_count": len(null_maxima),
        "null_familywise_threshold_quantile": registration[
            "null_calibration"
        ]["familywise_threshold_quantile"],
        "detection_threshold": float(threshold),
        "observed_maximum_score": float(np.max(score)),
        "raw_das_candidate_count": len(candidates),
        "raw_das_candidate_ids": [
            str(row["candidate_id"]) for row in candidates
        ],
        "candidate_family_assignment_count": 0,
        "candidate_table_path": str(candidate_path),
        "candidate_table_sha256": candidate_sha256,
        "candidate_table_materialized_before_comparison": True,
        "score_cache_path": str(cache_path),
        "score_cache_sha256": sha256_file(cache_path),
        "network_candidate_tables_opened": 0,
        "catalog_event_time_tables_opened": 0,
        "heldout_interval_tables_opened": 0,
        "network_candidate_time_fields_read": 0,
        "catalog_event_time_fields_read": 0,
        "heldout_interval_fields_read": 0,
        "next_stage_gate": (
            "PASS_POSTHOC_NETWORK_AND_CATALOG_COMPARISON_ONLY"
        ),
        "heldout_access_gate": "STOP_NOT_YET_AUTHORIZED",
        "interpretation": (
            "raw_DAS_arrival_candidates_not_origins_events_or_family_labels"
        ),
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
