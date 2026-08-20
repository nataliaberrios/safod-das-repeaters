#!/usr/bin/env python
"""Force the frozen generic network score at every held-out DAS-only time.

This is the first deterministic adjudication pass.  It reads the registered
21 DAS-only rows, downloads only the three affected network intervals, applies
the already frozen generic detector, and samples its score at each DAS time.
It does not retune thresholds, assign families, or decide whether a candidate
is an earthquake.  Template-branch scoring, catalog review, and waveform
adjudication remain explicit later stages.
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np

from .common import load_config, project_root, sha256_file, utc_now, write_json
from .heldout_network_access import (
    download_heldout_stream,
    interval_development_view,
    load_runner_release,
    validate_runner_release,
)
from .network_continuous_detection import (
    coincidence_score,
    generic_scan_indices_and_times,
    prepare_continuous_traces,
    station_energy_ratio_matrix,
)


RESULT_FIELDS = [
    "comparison_candidate_id",
    "interval_id",
    "DAS_candidate_id",
    "DAS_trigger_time",
    "DAS_trigger_epoch_s",
    "DAS_coincidence_score",
    "DAS_strong_block_count",
    "generic_network_score_at_DAS_time",
    "generic_network_threshold",
    "generic_network_above_frozen_threshold",
    "nearest_network_score_time",
    "DAS_minus_nearest_network_score_time_s",
    "network_score_sample_offset_s",
    "usable_network_station_count",
    "usable_network_component_count",
    "forced_network_score_status",
    "template_branch_status",
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
            handle,
            fieldnames=RESULT_FIELDS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def _nearest_score(
    trigger_epoch_s: float,
    score_times: np.ndarray,
    score: np.ndarray,
) -> Dict[str, float]:
    if len(score_times) == 0 or len(score) == 0:
        raise RuntimeError("network score curve is empty")
    index = int(np.searchsorted(score_times, trigger_epoch_s))
    index = max(0, min(index, len(score_times) - 1))
    if index > 0 and abs(score_times[index - 1] - trigger_epoch_s) < abs(
        score_times[index] - trigger_epoch_s
    ):
        index -= 1
    return {
        "score": float(score[index]),
        "time": float(score_times[index]),
        "offset": float(trigger_epoch_s - score_times[index]),
    }


def main() -> None:
    project = project_root()
    adjudication = load_config(
        project / "config" / "heldout_das_adjudication.json"
    )
    network_registration = load_config(
        project / "config" / "heldout_network_validation.json"
    )
    parent = load_config(project / "config" / "incremental_value.json")
    registration_status = _load_json(
        project
        / "outputs"
        / "heldout_v2"
        / "registration"
        / "network_registration_status.json"
    )
    release = load_runner_release(project)
    validate_runner_release(
        project, network_registration, registration_status, release
    )

    declared = adjudication["frozen_inputs"]
    for name, declaration in declared.items():
        path = project / str(declaration["path"])
        if sha256_file(path) != str(declaration["sha256"]):
            raise RuntimeError("adjudication input changed: {}".format(name))

    output = project / "outputs" / "heldout_v2" / "adjudication"
    result_path = output / "forced_generic_network_scores.csv"
    status_path = output / "forced_generic_network_score_status.json"
    if result_path.exists() or status_path.exists():
        raise PermissionError("forced network adjudication output already exists")

    context = _read_csv(
        project
        / "outputs"
        / "heldout_v2"
        / "comparison"
        / "das_network_network_context.csv"
    )
    das_only = [
        row for row in context if row["comparison_membership"] == "DAS_only"
    ]
    if len(das_only) != int(adjudication["heldout_DAS_only_adjudication"]["population"].split("_")[0] == "all") * 21:
        raise RuntimeError("DAS-only adjudication population changed")
    if any(row.get("network_union_candidate_id", "") for row in das_only):
        raise RuntimeError("DAS-only row has a network candidate ID")

    by_interval: Dict[str, List[Dict[str, str]]] = {}
    for row in das_only:
        by_interval.setdefault(str(row["interval_id"]), []).append(row)

    generic_settings = network_registration["generic_branch"]
    results: List[Dict[str, Any]] = []
    interval_status: List[Dict[str, Any]] = []
    for interval_id in sorted(by_interval):
        interval_rows = by_interval[interval_id]
        interval = next(
            row
            for row in _read_csv(
                project / "outputs" / "incremental_value" / "heldout_intervals.csv"
            )
            if row["interval_id"] == interval_id
        )
        view = interval_development_view(
            load_config(project / "config" / "development_detection.json"),
            network_registration,
            interval,
        )
        stream, source_rows = download_heldout_stream(
            project,
            parent,
            network_registration,
            registration_status,
            release,
            interval,
            force=False,
        )
        continuous, trace_rows, request_start = prepare_continuous_traces(
            stream, parent, view
        )
        matrix_full, station_names, component_count = station_energy_ratio_matrix(
            continuous,
            float(generic_settings["target_sample_rate_hz"]),
            float(generic_settings["sta_window_s"]),
            float(generic_settings["lta_window_s"]),
        )
        if len(station_names) < int(generic_settings["coincidence_station_count"]):
            raise RuntimeError(
                "insufficient network stations for forced score: {}".format(interval_id)
            )
        scan_indices, score_times = generic_scan_indices_and_times(
            request_start, matrix_full.shape[1], view
        )
        score = coincidence_score(
            matrix_full[:, scan_indices],
            int(generic_settings["coincidence_station_count"]),
        )
        for row in interval_rows:
            trigger = float(row["DAS_trigger_epoch_s"])
            nearest = _nearest_score(trigger, score_times, score)
            results.append(
                {
                    "comparison_candidate_id": row["comparison_candidate_id"],
                    "interval_id": interval_id,
                    "DAS_candidate_id": row["DAS_candidate_id"],
                    "DAS_trigger_time": row["DAS_trigger_time"],
                    "DAS_trigger_epoch_s": trigger,
                    "DAS_coincidence_score": row["DAS_coincidence_score"],
                    "DAS_strong_block_count": row["DAS_strong_block_count"],
                    "generic_network_score_at_DAS_time": nearest["score"],
                    "generic_network_threshold": generic_settings["threshold"],
                    "generic_network_above_frozen_threshold": (
                        nearest["score"] >= float(generic_settings["threshold"])
                    ),
                    "nearest_network_score_time": nearest["time"],
                    "DAS_minus_nearest_network_score_time_s": nearest["offset"],
                    "network_score_sample_offset_s": abs(nearest["offset"]),
                    "usable_network_station_count": len(station_names),
                    "usable_network_component_count": component_count,
                    "forced_network_score_status": "PASS_FIXED_GENERIC_SCORE",
                    "template_branch_status": "PENDING_FIXED_TEMPLATE_SCORE",
                    "catalog_adjudication_status": "PENDING",
                    "DAS_artifact_status": "PENDING",
                    "waveform_review_status": "PENDING",
                    "repeater_family_assignment": "not_assigned",
                }
            )
        interval_status.append(
            {
                "interval_id": interval_id,
                "DAS_only_candidate_count": len(interval_rows),
                "network_source_count": len(source_rows),
                "network_trace_qc_row_count": len(trace_rows),
                "usable_network_component_count": component_count,
                "usable_network_station_count": len(station_names),
                "generic_threshold": generic_settings["threshold"],
            }
        )

    results.sort(key=lambda row: row["comparison_candidate_id"])
    if len(results) != 21:
        raise RuntimeError("forced-score result count changed")
    _write_csv(result_path, results)
    status = {
        "status": "PARTIAL",
        "stage": "heldout_DAS_only_forced_generic_network_score_complete",
        "generated_utc": utc_now(),
        "adjudication_config_sha256": adjudication["_config_sha256"],
        "comparison_context_sha256": sha256_file(
            project
            / "outputs"
            / "heldout_v2"
            / "comparison"
            / "das_network_network_context.csv"
        ),
        "DAS_only_candidate_count": len(results),
        "interval_count_scored": len(interval_status),
        "intervals_scored": sorted(by_interval),
        "forced_network_branch": "generic_auxiliary_branch",
        "forced_network_threshold_repair": False,
        "forced_network_window_sweep": False,
        "template_branch_status": "PENDING_FIXED_TEMPLATE_SCORE",
        "catalog_adjudication_status": "PENDING",
        "DAS_artifact_status": "PENDING",
        "waveform_review_status": "PENDING",
        "family_assignments_made": 0,
        "result_sha256": sha256_file(result_path),
        "interval_status": interval_status,
        "next_stage_gate": "PASS_FIXED_TEMPLATE_SCORE_CATALOG_CHECKS_AND_WAVEFORM_REVIEW",
        "scientific_extension_claim_gate": "STOP",
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
