#!/usr/bin/env python
"""Run the frozen network-only detector on one registered held-out interval.

This stage has no catalog import and no DAS reader.  Every threshold is read
from the pre-waveform registration; no null is recalculated on held-out data.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from .common import (
    CONDITIONAL,
    PASS,
    STOP,
    load_config,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .heldout_network_access import (
    RUNNER_RELEASE_RELATIVE_PATH,
    download_heldout_stream,
    interval_development_view,
    load_historical_template_streams,
    load_runner_release,
    load_template_metadata,
    registered_interval,
    validate_runner_release,
)
from .network_continuous_detection import (
    detect_candidates,
    detect_generic_candidates,
    generic_scan_indices_and_times,
    prepare_continuous_traces,
    prepare_template,
    scan_indices_and_times,
    station_energy_ratio_matrix,
    template_station_score_matrix,
)


SOURCE_FIELDS = [
    "interval_id",
    "interval_start_utc",
    "interval_end_utc",
    "source_name",
    "network",
    "request_url",
    "request_padding_s",
    "access_role",
    "status",
    "trace_count",
    "waveform_path",
    "waveform_sha256",
    "provenance_path",
    "provenance_sha256",
    "error",
]
TRACE_QC_FIELDS = [
    "interval_id",
    "trace_id",
    "status",
    "reason",
    "maximum_positive_gap_s",
    "native_sample_rate_hz",
    "target_sample_rate_hz",
    "sample_count",
    "request_start_utc",
    "request_end_utc",
]
TEMPLATE_QC_FIELDS = [
    "interval_id",
    "template_event_id",
    "usable_component_count",
    "usable_station_count",
    "median_template_trace_snr",
    "scored_component_count",
    "scored_station_count",
    "status",
    "reason",
]
TEMPLATE_CANDIDATE_FIELDS = [
    "interval_id",
    "candidate_id",
    "origin_time",
    "origin_epoch_s",
    "bank_score",
    "threshold",
    "best_template_event_id",
    "best_template_score",
    "best_template_station_count",
    "station_support_count_at_0p2",
    "candidate_generation_label",
    "catalog_fields_used_in_candidate_generation",
    "DAS_fields_used_in_candidate_generation",
    "family_assignment",
]
GENERIC_CANDIDATE_FIELDS = [
    "interval_id",
    "candidate_id",
    "trigger_time",
    "trigger_epoch_s",
    "coincidence_score",
    "threshold",
    "coincidence_station_count",
    "station_support_count_at_declared_ratio",
    "station_characteristic_support_threshold",
    "candidate_generation_label",
    "catalog_fields_used_in_candidate_generation",
    "DAS_fields_used_in_candidate_generation",
    "family_assignment",
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


def finalize_template_candidates(
    rows: Sequence[Mapping[str, Any]], interval_id: str
) -> List[Dict[str, Any]]:
    """Attach held-out IDs and remove any family-bearing template field."""

    finalized: List[Dict[str, Any]] = []
    prefix = "network_template_{}".format(interval_id)
    for index, source in enumerate(rows, start=1):
        row = {
            field: source.get(field, "")
            for field in TEMPLATE_CANDIDATE_FIELDS
        }
        row.update(
            {
                "interval_id": interval_id,
                "candidate_id": "{}_{:04d}".format(prefix, index),
                "candidate_generation_label": (
                    "frozen_template_bank_candidate_not_family_truth"
                ),
                "catalog_fields_used_in_candidate_generation": 0,
                "DAS_fields_used_in_candidate_generation": 0,
                "family_assignment": "not_assigned",
            }
        )
        finalized.append(row)
    return finalized


def finalize_generic_candidates(
    rows: Sequence[Mapping[str, Any]], interval_id: str
) -> List[Dict[str, Any]]:
    """Attach held-out IDs and explicit zero-leakage fields."""

    finalized: List[Dict[str, Any]] = []
    prefix = "network_generic_{}".format(interval_id)
    for index, source in enumerate(rows, start=1):
        row = {
            field: source.get(field, "")
            for field in GENERIC_CANDIDATE_FIELDS
        }
        row.update(
            {
                "interval_id": interval_id,
                "candidate_id": "{}_{:04d}".format(prefix, index),
                "candidate_generation_label": (
                    "frozen_generic_network_candidate_arrival_not_origin"
                ),
                "catalog_fields_used_in_candidate_generation": 0,
                "DAS_fields_used_in_candidate_generation": 0,
                "family_assignment": "not_assigned",
            }
        )
        finalized.append(row)
    return finalized


def _station_count(trace_keys: Sequence[str]) -> int:
    return len(
        {
            ".".join(str(key).split(".")[:2])
            for key in trace_keys
            if str(key)
        }
    )


def _runtime_paths(
    project: Path, registration: Mapping[str, Any], interval_id: str
) -> Dict[str, Path]:
    output = (
        project
        / str(registration["output"]["network_directory"])
        / "intervals"
        / interval_id
    )
    return {
        "output": output,
        "source": output / "source_availability.csv",
        "trace_qc": output / "trace_qc.csv",
        "template_qc": output / "template_qc.csv",
        "template_candidates": output / "template_candidates.csv",
        "generic_candidates": output / "generic_candidates.csv",
        "status": output / "status.json",
        "score_cache": (
            project
            / "cached_continuous"
            / "analysis"
            / "heldout_network"
            / (interval_id + "_scores.npz")
        ),
    }


def _load_runtime(
    project: Path, config_path: Path, interval_id: str
) -> Tuple[
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, Any],
    Dict[str, str],
]:
    registration = load_config(config_path)
    parent_path = project / str(
        registration["frozen_inputs"]["parent_config"]["path"]
    )
    development_path = project / str(
        registration["frozen_inputs"]["development_config"]["path"]
    )
    status_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["registration_status_json"])
    )
    parent = load_config(parent_path)
    development = load_config(development_path)
    registration_status = _load_json(status_path)
    release = load_runner_release(project)
    validate_runner_release(
        project, registration, registration_status, release
    )
    interval = registered_interval(project, registration, interval_id)
    return (
        registration,
        parent,
        development,
        registration_status,
        release,
        interval,
    )


def _template_detection(
    continuous: Mapping[str, np.ndarray],
    request_start: float,
    view: Mapping[str, Any],
    registration: Mapping[str, Any],
    template_metadata: Sequence[Mapping[str, Any]],
    template_streams: Mapping[str, Any],
    interval_id: str,
) -> Tuple[
    List[Dict[str, Any]],
    List[Dict[str, Any]],
    np.ndarray,
    np.ndarray,
    np.ndarray,
    str,
    str,
]:
    template_qc: List[Dict[str, Any]] = []
    matrices_full: List[np.ndarray] = []
    station_names: List[List[str]] = []
    eligible_rows: List[Dict[str, Any]] = []
    curve_length: int | None = None
    for event in template_metadata:
        templates, _, raw_qc = prepare_template(
            event,
            template_streams[str(event["event_id"])],
            list(continuous),
            view,
        )
        qc = {
            "interval_id": interval_id,
            "template_event_id": str(event["event_id"]),
            "usable_component_count": raw_qc["usable_component_count"],
            "usable_station_count": raw_qc["usable_station_count"],
            "median_template_trace_snr": raw_qc[
                "median_template_trace_snr"
            ],
            "scored_component_count": "",
            "scored_station_count": "",
            "status": raw_qc["status"],
            "reason": raw_qc["reason"],
        }
        if raw_qc["status"] != "PASS":
            template_qc.append(qc)
            continue
        matrix, names, component_count = template_station_score_matrix(
            continuous, templates
        )
        qc["scored_component_count"] = component_count
        qc["scored_station_count"] = len(names)
        if (
            component_count
            < int(registration["template_branch"]["minimum_components_per_template"])
            or len(names)
            < int(registration["template_branch"]["minimum_stations_per_template"])
        ):
            qc["status"] = "STOP"
            qc["reason"] = "insufficient_scored_components_or_stations"
            template_qc.append(qc)
            continue
        if curve_length is None:
            curve_length = matrix.shape[1]
        elif matrix.shape[1] != curve_length:
            raise RuntimeError("held-out template score lengths differ")
        matrices_full.append(matrix)
        station_names.append(names)
        eligible_rows.append(dict(event))
        template_qc.append(qc)

    if not matrices_full or curve_length is None:
        return (
            [],
            template_qc,
            np.asarray([], dtype=float),
            np.asarray([], dtype=float),
            np.asarray([], dtype=int),
            STOP,
            "no_historical_template_passed_interval_QC",
        )
    scan_indices, origins = scan_indices_and_times(
        request_start, curve_length, view
    )
    matrices = [
        matrix[:, scan_indices].astype(np.float32)
        for matrix in matrices_full
    ]
    scores = np.stack(
        [
            np.mean(matrix, axis=0, dtype=np.float64).astype(np.float32)
            for matrix in matrices
        ],
        axis=0,
    )
    raw_candidates, bank_score, best_template = detect_candidates(
        origins,
        scores,
        matrices,
        station_names,
        eligible_rows,
        float(registration["template_branch"]["threshold"]),
        view,
    )
    candidates = finalize_template_candidates(
        raw_candidates, interval_id
    )
    return (
        candidates,
        template_qc,
        origins,
        bank_score,
        best_template,
        PASS,
        "fixed_threshold_scan_complete",
    )


def _generic_detection(
    continuous: Mapping[str, np.ndarray],
    request_start: float,
    view: Mapping[str, Any],
    registration: Mapping[str, Any],
    interval_id: str,
) -> Tuple[List[Dict[str, Any]], np.ndarray, np.ndarray, str, str, int, int]:
    settings = registration["generic_branch"]
    matrix_full, station_names, component_count = station_energy_ratio_matrix(
        continuous,
        float(settings["target_sample_rate_hz"]),
        float(settings["sta_window_s"]),
        float(settings["lta_window_s"]),
    )
    if len(station_names) < int(settings["coincidence_station_count"]):
        return (
            [],
            np.asarray([], dtype=float),
            np.asarray([], dtype=float),
            STOP,
            "fewer_than_registered_coincidence_station_count",
            len(station_names),
            component_count,
        )
    scan_indices, trigger_times = generic_scan_indices_and_times(
        request_start, matrix_full.shape[1], view
    )
    matrix = matrix_full[:, scan_indices].astype(np.float32)
    if not np.all(np.isfinite(matrix)):
        raise RuntimeError("generic held-out characteristic matrix is not finite")
    raw_candidates, score = detect_generic_candidates(
        trigger_times,
        matrix,
        float(settings["threshold"]),
        view,
    )
    candidates = finalize_generic_candidates(raw_candidates, interval_id)
    return (
        candidates,
        trigger_times,
        score,
        PASS,
        "fixed_threshold_scan_complete",
        len(station_names),
        component_count,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval-id", required=True)
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            project_root() / "config" / "heldout_network_validation.json"
        ),
    )
    parser.add_argument("--force-download", action="store_true")
    args = parser.parse_args()

    project = project_root()
    (
        registration,
        parent,
        development,
        registration_status,
        release,
        interval,
    ) = _load_runtime(project, args.config, str(args.interval_id))
    interval_id = str(interval["interval_id"])
    paths = _runtime_paths(project, registration, interval_id)
    final_status_path = (
        project
        / str(registration["output"]["network_directory"])
        / str(registration["output"]["candidate_generation_status_json"])
    )
    if final_status_path.is_file():
        raise PermissionError(
            "held-out network union is frozen; interval reruns are forbidden"
        )
    if paths["status"].is_file():
        raise PermissionError(
            "held-out interval is already materialized; rerun is forbidden"
        )
    view = interval_development_view(development, registration, interval)

    stream, source_rows = download_heldout_stream(
        project,
        parent,
        registration,
        registration_status,
        release,
        interval,
        force=bool(args.force_download),
    )
    _write_csv(paths["source"], source_rows, SOURCE_FIELDS)
    continuous, trace_rows, request_start = prepare_continuous_traces(
        stream, parent, view
    )
    for row in trace_rows:
        row["interval_id"] = interval_id
    _write_csv(paths["trace_qc"], trace_rows, TRACE_QC_FIELDS)

    template_metadata = load_template_metadata(project, registration)
    template_streams = load_historical_template_streams(
        project, registration, registration_status
    )
    (
        template_candidates,
        template_qc,
        template_times,
        template_scores,
        best_template,
        template_status,
        template_reason,
    ) = _template_detection(
        continuous,
        request_start,
        view,
        registration,
        template_metadata,
        template_streams,
        interval_id,
    )
    (
        generic_candidates,
        generic_times,
        generic_scores,
        generic_status,
        generic_reason,
        generic_station_count,
        generic_component_count,
    ) = _generic_detection(
        continuous,
        request_start,
        view,
        registration,
        interval_id,
    )

    _write_csv(paths["template_qc"], template_qc, TEMPLATE_QC_FIELDS)
    _write_csv(
        paths["template_candidates"],
        template_candidates,
        TEMPLATE_CANDIDATE_FIELDS,
    )
    _write_csv(
        paths["generic_candidates"],
        generic_candidates,
        GENERIC_CANDIDATE_FIELDS,
    )
    paths["score_cache"].parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        paths["score_cache"],
        template_origin_epoch_s=np.asarray(template_times, dtype=np.float64),
        template_bank_score=np.asarray(template_scores, dtype=np.float32),
        template_best_index=np.asarray(best_template, dtype=np.int16),
        generic_trigger_epoch_s=np.asarray(generic_times, dtype=np.float64),
        generic_score=np.asarray(generic_scores, dtype=np.float32),
    )

    if template_status == PASS and generic_status == PASS:
        overall = PASS
    elif template_status == PASS or generic_status == PASS:
        overall = CONDITIONAL
    else:
        overall = STOP
    usable_components = len(continuous)
    usable_stations = _station_count(list(continuous))
    available_sources = sum(
        str(row["status"]) == "available" for row in source_rows
    )
    status = {
        "status": overall,
        "stage": "heldout_network_interval_candidate_generation_complete",
        "generated_utc": utc_now(),
        "interval_id": interval_id,
        "interval_start_utc": str(interval["start_utc"]),
        "interval_end_utc": str(interval["end_utc"]),
        "heldout_network_config_sha256": registration["_config_sha256"],
        "runner_commit_sha": str(release["runner_commit_sha"]),
        "network_runner_release_sha256": sha256_file(
            project / RUNNER_RELEASE_RELATIVE_PATH
        ),
        "template_threshold": registration["template_branch"]["threshold"],
        "generic_threshold": registration["generic_branch"]["threshold"],
        "threshold_recalibration_performed": False,
        "generic_development_SNR1_STOP_preserved": True,
        "available_source_count": available_sources,
        "unavailable_source_count": len(source_rows) - available_sources,
        "usable_component_count": usable_components,
        "usable_station_count": usable_stations,
        "template_branch_status": template_status,
        "template_branch_reason": template_reason,
        "generic_branch_status": generic_status,
        "generic_branch_reason": generic_reason,
        "generic_station_count": generic_station_count,
        "generic_component_count": generic_component_count,
        "template_candidate_count": len(template_candidates),
        "generic_candidate_count": len(generic_candidates),
        "source_availability_sha256": sha256_file(paths["source"]),
        "trace_QC_sha256": sha256_file(paths["trace_qc"]),
        "template_QC_sha256": sha256_file(paths["template_qc"]),
        "template_candidate_sha256": sha256_file(
            paths["template_candidates"]
        ),
        "generic_candidate_sha256": sha256_file(
            paths["generic_candidates"]
        ),
        "full_score_cache_path": str(paths["score_cache"]),
        "full_score_cache_sha256": sha256_file(paths["score_cache"]),
        "heldout_network_waveform_files_opened": available_sources,
        "historical_template_waveform_files_opened": (
            registration_status[
                "historical_template_available_waveform_count"
            ]
        ),
        "heldout_catalog_event_rows_opened": 0,
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "heldout_family_label_rows_opened": 0,
        "candidate_family_assignments_made": 0,
        "next_stage_gate": (
            "WAIT_FOR_ALL_12_INTERVALS_THEN_FREEZE_TIME_ONLY_UNION"
        ),
        "catalog_access_gate": (
            "STOP_UNTIL_ALL_INTERVAL_TIME_ONLY_UNIONS_ARE_CHECKSUMMED"
        ),
        "DAS_access_gate": (
            "STOP_UNTIL_COMPLETE_HELDOUT_NETWORK_UNION_IS_FROZEN"
        ),
    }
    write_json(paths["status"], status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
