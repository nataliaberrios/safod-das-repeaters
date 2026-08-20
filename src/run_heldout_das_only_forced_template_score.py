#!/usr/bin/env python
"""Force the frozen historical-template network score at DAS-only times."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

from .common import load_config, project_root, sha256_file, utc_now, write_json
from .heldout_network_access import (
    download_heldout_stream,
    interval_development_view,
    load_historical_template_streams,
    load_runner_release,
    load_template_metadata,
    validate_runner_release,
)
from .network_continuous_detection import (
    prepare_continuous_traces,
    prepare_template,
    scan_indices_and_times,
    template_station_score_matrix,
)


FIELDS = [
    "comparison_candidate_id",
    "interval_id",
    "DAS_candidate_id",
    "DAS_trigger_time",
    "DAS_trigger_epoch_s",
    "DAS_coincidence_score",
    "DAS_strong_block_count",
    "template_bank_score_at_DAS_time",
    "template_network_threshold",
    "template_network_above_frozen_threshold",
    "nearest_network_score_time",
    "DAS_minus_nearest_network_score_time_s",
    "network_score_sample_offset_s",
    "eligible_template_count",
    "minimum_template_component_count",
    "minimum_template_station_count",
    "usable_network_component_count",
    "usable_network_station_count",
    "forced_network_score_status",
    "generic_branch_status",
    "catalog_adjudication_status",
    "DAS_artifact_status",
    "waveform_review_status",
    "repeater_family_assignment",
]


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _nearest(epoch_s: float, times: np.ndarray, score: np.ndarray) -> Dict[str, float]:
    if len(times) == 0 or len(score) == 0:
        raise RuntimeError("template score curve is empty")
    index = int(np.searchsorted(times, epoch_s))
    index = max(0, min(index, len(times) - 1))
    if index > 0 and abs(times[index - 1] - epoch_s) < abs(times[index] - epoch_s):
        index -= 1
    return {
        "score": float(score[index]),
        "time": float(times[index]),
        "offset": float(epoch_s - times[index]),
    }


def main() -> None:
    project = project_root()
    adjudication = load_config(project / "config" / "heldout_das_adjudication.json")
    network_registration = load_config(
        project / "config" / "heldout_network_validation.json"
    )
    parent = load_config(project / "config" / "incremental_value.json")
    development = load_config(project / "config" / "development_detection.json")
    registration_status = _load_json(
        project / "outputs" / "heldout_v2" / "registration" / "network_registration_status.json"
    )
    release = load_runner_release(project)
    validate_runner_release(project, network_registration, registration_status, release)

    for name, declaration in adjudication["frozen_inputs"].items():
        path = project / str(declaration["path"])
        if sha256_file(path) != str(declaration["sha256"]):
            raise RuntimeError("adjudication input changed: {}".format(name))

    output = project / "outputs" / "heldout_v2" / "adjudication"
    result_path = output / "forced_template_network_scores.csv"
    status_path = output / "forced_template_network_score_status.json"
    if result_path.exists() or status_path.exists():
        raise PermissionError("forced template adjudication output already exists")

    context = _read_csv(
        project / "outputs" / "heldout_v2" / "comparison" / "das_network_network_context.csv"
    )
    das_only = [row for row in context if row["comparison_membership"] == "DAS_only"]
    if len(das_only) != 21:
        raise RuntimeError("DAS-only adjudication population changed")
    by_interval: Dict[str, List[Dict[str, str]]] = {}
    for row in das_only:
        by_interval.setdefault(str(row["interval_id"]), []).append(row)

    template_metadata = load_template_metadata(project, network_registration)
    template_streams = load_historical_template_streams(
        project, network_registration, registration_status
    )
    template_settings = network_registration["template_branch"]
    results: List[Dict[str, Any]] = []
    interval_status: List[Dict[str, Any]] = []
    interval_rows = _read_csv(project / "outputs" / "incremental_value" / "heldout_intervals.csv")

    for interval_id in sorted(by_interval):
        interval = next(row for row in interval_rows if row["interval_id"] == interval_id)
        view = interval_development_view(development, network_registration, interval)
        stream, source_rows = download_heldout_stream(
            project, parent, network_registration, registration_status, release, interval, force=False
        )
        continuous, trace_rows, request_start = prepare_continuous_traces(stream, parent, view)
        matrices: List[np.ndarray] = []
        station_names: List[List[str]] = []
        eligible_events: List[Mapping[str, Any]] = []
        component_counts: List[int] = []
        station_counts: List[int] = []
        for event in template_metadata:
            templates, _, qc = prepare_template(
                event, template_streams[str(event["event_id"])], list(continuous), view
            )
            if qc["status"] != "PASS":
                continue
            matrix, names, component_count = template_station_score_matrix(continuous, templates)
            if (
                component_count < int(template_settings["minimum_components_per_template"])
                or len(names) < int(template_settings["minimum_stations_per_template"])
            ):
                continue
            matrices.append(matrix)
            station_names.append(names)
            eligible_events.append(event)
            component_counts.append(component_count)
            station_counts.append(len(names))
        if not matrices:
            raise RuntimeError("no historical template passed QC: {}".format(interval_id))
        curve_length = matrices[0].shape[1]
        if any(matrix.shape[1] != curve_length for matrix in matrices):
            raise RuntimeError("template score curves have unequal lengths")
        scan_indices, score_times = scan_indices_and_times(request_start, curve_length, view)
        template_scores = np.stack(
            [np.mean(matrix[:, scan_indices], axis=0, dtype=np.float64) for matrix in matrices],
            axis=0,
        )
        bank_score = np.max(template_scores, axis=0)
        for row in by_interval[interval_id]:
            nearest = _nearest(float(row["DAS_trigger_epoch_s"]), score_times, bank_score)
            results.append(
                {
                    "comparison_candidate_id": row["comparison_candidate_id"],
                    "interval_id": interval_id,
                    "DAS_candidate_id": row["DAS_candidate_id"],
                    "DAS_trigger_time": row["DAS_trigger_time"],
                    "DAS_trigger_epoch_s": row["DAS_trigger_epoch_s"],
                    "DAS_coincidence_score": row["DAS_coincidence_score"],
                    "DAS_strong_block_count": row["DAS_strong_block_count"],
                    "template_bank_score_at_DAS_time": nearest["score"],
                    "template_network_threshold": template_settings["threshold"],
                    "template_network_above_frozen_threshold": nearest["score"] >= float(template_settings["threshold"]),
                    "nearest_network_score_time": nearest["time"],
                    "DAS_minus_nearest_network_score_time_s": nearest["offset"],
                    "network_score_sample_offset_s": abs(nearest["offset"]),
                    "eligible_template_count": len(eligible_events),
                    "minimum_template_component_count": min(component_counts),
                    "minimum_template_station_count": min(station_counts),
                    "usable_network_component_count": len(continuous),
                    "usable_network_station_count": len({".".join(key.split(".")[:2]) for key in continuous}),
                    "forced_network_score_status": "PASS_FIXED_TEMPLATE_SCORE",
                    "generic_branch_status": "COMPLETE_SEPARATE_OUTPUT",
                    "catalog_adjudication_status": "PENDING",
                    "DAS_artifact_status": "PENDING",
                    "waveform_review_status": "PENDING",
                    "repeater_family_assignment": "not_assigned",
                }
            )
        interval_status.append(
            {
                "interval_id": interval_id,
                "DAS_only_candidate_count": len(by_interval[interval_id]),
                "network_source_count": len(source_rows),
                "network_trace_qc_row_count": len(trace_rows),
                "eligible_template_count": len(eligible_events),
                "minimum_template_component_count": min(component_counts),
                "minimum_template_station_count": min(station_counts),
            }
        )

    results.sort(key=lambda row: row["comparison_candidate_id"])
    if len(results) != 21:
        raise RuntimeError("forced template result count changed")
    _write_csv(result_path, results)
    status = {
        "status": "PARTIAL",
        "stage": "heldout_DAS_only_forced_template_network_score_complete",
        "generated_utc": utc_now(),
        "adjudication_config_sha256": adjudication["_config_sha256"],
        "comparison_context_sha256": sha256_file(
            project / "outputs" / "heldout_v2" / "comparison" / "das_network_network_context.csv"
        ),
        "DAS_only_candidate_count": len(results),
        "interval_count_scored": len(interval_status),
        "forced_network_branch": "template_bank_primary_branch",
        "forced_network_threshold_repair": False,
        "forced_network_window_sweep": False,
        "generic_branch_status": "COMPLETE_SEPARATE_OUTPUT",
        "catalog_adjudication_status": "PENDING",
        "DAS_artifact_status": "PENDING",
        "waveform_review_status": "PENDING",
        "family_assignments_made": 0,
        "result_sha256": sha256_file(result_path),
        "interval_status": interval_status,
        "next_stage_gate": "PASS_CATALOG_CHECKS_AND_WAVEFORM_REVIEW",
        "scientific_extension_claim_gate": "STOP",
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
