#!/usr/bin/env python
"""Review raw DAS windows around the 21 held-out DAS-only candidates.

This is an artifact/waveform review aid, not a second detector.  It reuses the
registered DAS manifest and the development detector's fixed preprocessing and
block characteristic definition.  It reports persistence, spatial support,
coverage, and simple common-mode/saturation diagnostics for manual review.  It
does not alter the candidate population, repair a threshold, assign families,
or call a candidate an earthquake.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import butter, resample_poly, sosfiltfilt

from .common import load_config, project_root, sha256_file, utc_now, write_json
from .das_continuous_detection import (
    block_characteristic_matrix,
    channel_qc,
    coincidence_score,
)
from .h5io import read_window
from .heldout_das_runner_access import (
    interval_output_paths,
    load_frozen_detector_configs,
    load_runner_release,
    registered_interval,
    registered_interval_rows,
    registered_manifest_rows,
    validate_runner_release,
)


RESULT_FIELDS = [
    "comparison_candidate_id",
    "interval_id",
    "DAS_candidate_id",
    "DAS_trigger_time",
    "DAS_trigger_epoch_s",
    "DAS_coincidence_score_frozen",
    "DAS_v1_interval_null_threshold",
    "raw_window_start_utc",
    "raw_window_end_utc",
    "raw_sample_rate_hz",
    "raw_sample_count",
    "raw_sampled_channel_count",
    "raw_source_file_count",
    "raw_missing_fraction",
    "raw_maximum_gap_s",
    "finite_fraction",
    "raw_saturation_fraction",
    "processed_sample_rate_hz",
    "processed_sample_count",
    "usable_sampled_channel_count",
    "usable_block_count",
    "recomputed_score_at_candidate",
    "recomputed_score_max_pm_1s",
    "score_above_frozen_threshold_duration_pm_1s",
    "strong_block_count_at_candidate",
    "strong_block_count_max_pm_1s",
    "strong_block_support_duration_pm_1s",
    "common_mode_variance_ratio_pm_1s",
    "spatial_support_status",
    "coverage_status",
    "automated_artifact_status",
    "manual_waveform_review_status",
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
            handle, fieldnames=RESULT_FIELDS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def _iso(epoch_s: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(float(epoch_s), tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _max_run_duration(mask: np.ndarray, sample_rate_hz: float) -> float:
    best = current = 0
    for value in np.asarray(mask, dtype=bool):
        current = current + 1 if value else 0
        best = max(best, current)
    return float(best) / float(sample_rate_hz)


def _common_mode_fraction(values: np.ndarray) -> float:
    """Fraction of near-candidate variance represented by the channel median."""

    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or array.shape[0] < 2:
        return float("nan")
    common = np.median(array, axis=1)
    common_var = float(np.var(common))
    channel_var = float(np.median(np.var(array, axis=0)))
    if channel_var <= np.finfo(float).eps:
        return float("nan")
    return float(common_var / channel_var)


def _plot_review(path: Path, plot_rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(7, 3, figsize=(15, 20), sharex=False, sharey=False)
    for axis, row in zip(axes.flat, plot_rows):
        times = np.asarray(row["relative_times_s"], dtype=float)
        scores = np.asarray(row["scores"], dtype=float)
        support = np.asarray(row["support"], dtype=float)
        axis.plot(times, scores, color="tab:blue", lw=1.0, label="4th block")
        axis.plot(times, support, color="tab:orange", lw=0.8, label="# blocks >= 2")
        axis.axvline(0.0, color="k", lw=0.6)
        axis.set_title(str(row["DAS_candidate_id"]), fontsize=8)
        axis.grid(alpha=0.2)
        axis.set_xlim(-5.0, 5.0)
    for axis in axes.flat[len(plot_rows) :]:
        axis.axis("off")
    axes[0, 0].legend(fontsize=7, loc="upper right")
    fig.suptitle("Held-out DAS-only candidate window review metrics\nAutomated diagnostics; no event/family assignments", fontsize=13)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main() -> None:
    project = project_root()
    adjudication = load_config(project / "config" / "heldout_das_adjudication.json")
    registration = load_config(project / "config" / "heldout_das_replay.json")
    registration_root = project / str(registration["output"]["registration_directory"])
    registration_status_path = registration_root / str(registration["output"]["registration_status_json"])
    registration_status = _load_json(registration_status_path)
    release = load_runner_release(project)
    validate_runner_release(project, registration, registration_status, release)
    v1, _v2 = load_frozen_detector_configs(project, registration)
    for name, declaration in adjudication["frozen_inputs"].items():
        path = project / str(declaration["path"])
        if sha256_file(path) != str(declaration["sha256"]):
            raise RuntimeError("adjudication input changed: " + name)

    output = project / "outputs" / "heldout_v2" / "adjudication"
    result_path = output / "das_only_waveform_review.csv"
    status_path = output / "das_only_waveform_review_status.json"
    figure_path = output / "das_only_waveform_review.png"
    if result_path.exists() or status_path.exists() or figure_path.exists():
        raise PermissionError("DAS waveform review output already exists")

    context = _read_csv(project / "outputs" / "heldout_v2" / "comparison" / "das_network_network_context.csv")
    das_only = [row for row in context if row["comparison_membership"] == "DAS_only"]
    if len(das_only) != 21:
        raise RuntimeError("DAS-only population changed")
    intervals = {
        str(row["interval_id"]): row
        for row in registered_interval_rows(project, registration, registration_status)
    }

    # Use the exact development detector preprocessing and block definition.
    preprocessing = v1["preprocessing"]
    sampling = v1["channel_sampling"]
    target_rate = float(preprocessing["target_sample_rate_hz"])
    raw_rate = float(registration["manifest"]["primary_configuration"]["sample_rate_hz"])
    down = int(round(raw_rate / target_rate))
    sos = butter(4, preprocessing["primary_band_hz"], btype="bandpass", fs=raw_rate, output="sos")
    window_half_width_s = 15.0
    review_half_width_s = 1.0
    rows: List[Dict[str, Any]] = []
    plots: List[Dict[str, Any]] = []
    for source in das_only:
        interval_id = str(source["interval_id"])
        interval = intervals[interval_id]
        registered = registered_interval(project, registration, registration_status, interval_id)
        manifest_rows = registered_manifest_rows(project, registration, registration_status, registered)
        paths = [Path(str(item["path"])) for item in manifest_rows]
        trigger = float(source["DAS_trigger_epoch_s"])
        raw_start = trigger - window_half_width_s
        raw_end = trigger + window_half_width_s
        window = read_window(
            paths,
            raw_start,
            raw_end,
            channel_start=int(sampling["column_start"]),
            channel_stop=int(sampling["column_stop"]),
            channel_stride=int(sampling["column_stride"]),
        )
        raw = np.asarray(window.data, dtype=np.float32)
        finite_fraction = float(np.count_nonzero(np.isfinite(raw))) / float(raw.size)
        finite_raw = np.nan_to_num(raw, nan=0.0, posinf=0.0, neginf=0.0)
        # The registered HDF5 payload is int32 and no valid full-scale limit is
        # declared in metadata; saturation cannot be inferred from this window.
        saturation = float("nan")
        centered = raw - np.mean(raw, axis=0, dtype=np.float64).astype(np.float32)
        filtered = sosfiltfilt(sos, centered, axis=0)
        processed = np.asarray(resample_poly(filtered, 1, down, axis=0), dtype=np.float32)
        processed_times = float(window.time_epoch_s[0]) + np.arange(len(processed), dtype=float) / target_rate
        usable, rms, _channel_rows = channel_qc(processed, window.column_indices, window.locus_indices)
        block_matrix, block_ids, _block_rows = block_characteristic_matrix(
            processed, window.column_indices, window.locus_indices, usable, rms, v1
        )
        valid_times = np.all(np.isfinite(block_matrix), axis=0)
        valid_block_matrix = block_matrix[:, valid_times]
        valid_processed_times = processed_times[valid_times]
        score = coincidence_score(valid_block_matrix, int(v1["generic_array_trigger"]["block_coincidence_count"]))
        candidate_index = int(np.argmin(np.abs(valid_processed_times - trigger)))
        local = np.flatnonzero(np.abs(valid_processed_times - trigger) <= review_half_width_s)
        if len(local) == 0:
            raise RuntimeError("candidate is outside processed review window")
        threshold = float(source["DAS_v1_interval_null_threshold"])
        support = np.sum(valid_block_matrix >= float(v1["generic_array_trigger"]["block_support_ratio"]), axis=0)
        local_score = score[local]
        local_support = support[local]
        block_at_candidate = int(support[candidate_index])
        score_duration = _max_run_duration(local_score >= threshold, target_rate)
        block_duration = _max_run_duration(local_support >= 4, target_rate)
        near_raw = raw[np.abs(window.time_epoch_s - trigger) <= review_half_width_s]
        common_mode = _common_mode_fraction(near_raw)
        coverage_status = "PASS" if finite_fraction >= 0.999 and window.missing_fraction <= 0.0001 else "REVIEW_COVERAGE"
        artifact_status = ("AUTOMATED_COVERAGE_PASS;SATURATION_NOT_ASSESSED_NO_DECLARED_INT32_LIMIT"
                           if coverage_status == "PASS" else "REVIEW_REQUIRED_COVERAGE")
        rows.append({
            "comparison_candidate_id": source["comparison_candidate_id"],
            "interval_id": interval_id,
            "DAS_candidate_id": source["DAS_candidate_id"],
            "DAS_trigger_time": source["DAS_trigger_time"],
            "DAS_trigger_epoch_s": trigger,
            "DAS_coincidence_score_frozen": source["DAS_coincidence_score"],
            "DAS_v1_interval_null_threshold": threshold,
            "raw_window_start_utc": _iso(raw_start),
            "raw_window_end_utc": _iso(raw_end),
            "raw_sample_rate_hz": window.sample_rate_hz,
            "raw_sample_count": len(window.time_epoch_s),
            "raw_sampled_channel_count": raw.shape[1],
            "raw_source_file_count": len(window.source_files),
            "raw_missing_fraction": window.missing_fraction,
            "raw_maximum_gap_s": window.maximum_gap_s,
            "finite_fraction": finite_fraction,
            "raw_saturation_fraction": saturation,
            "processed_sample_rate_hz": target_rate,
            "processed_sample_count": len(processed_times),
            "usable_sampled_channel_count": int(np.count_nonzero(usable)),
            "usable_block_count": len(block_ids),
            "recomputed_score_at_candidate": float(score[candidate_index]),
            "recomputed_score_max_pm_1s": float(np.max(local_score)),
            "score_above_frozen_threshold_duration_pm_1s": score_duration,
            "strong_block_count_at_candidate": block_at_candidate,
            "strong_block_count_max_pm_1s": int(np.max(local_support)),
            "strong_block_support_duration_pm_1s": block_duration,
            "common_mode_variance_ratio_pm_1s": common_mode,
            "spatial_support_status": "PERSISTENT_BLOCK_SUPPORT_RECORDED" if block_at_candidate >= 4 else "BELOW_FOUR_BLOCKS_AT_CANDIDATE",
            "coverage_status": coverage_status,
            "automated_artifact_status": artifact_status,
            "manual_waveform_review_status": "PENDING_MANUAL_FIGURE_REVIEW",
            "repeater_family_assignment": "not_assigned",
        })
        plot_mask = np.abs(valid_processed_times - trigger) <= 5.0
        plots.append({
            "DAS_candidate_id": source["DAS_candidate_id"],
            "relative_times_s": valid_processed_times[plot_mask] - trigger,
            "scores": score[plot_mask],
            "support": support[plot_mask],
        })

    _write_csv(result_path, rows)
    _plot_review(figure_path, plots)
    status = {
        "status": "PARTIAL",
        "stage": "heldout_DAS_only_targeted_waveform_artifact_review_metrics_complete",
        "generated_utc": utc_now(),
        "heldout_DAS_only_row_count": len(rows),
        "interval_count": len(set(row["interval_id"] for row in rows)),
        "window_half_width_s": window_half_width_s,
        "review_half_width_s": review_half_width_s,
        "preprocessing": "frozen_DAS_v1_development_band_and_block_characteristic; no_new_detection_threshold",
        "raw_HDF5_access": "registered_manifest_only_read_only",
        "coverage_failures": sum(row["coverage_status"] != "PASS" for row in rows),
        "saturation_flags": 0,
        "saturation_assessment": "NOT_ASSESSED_RAW_INT32_HAS_NO_DECLARED_FULL_SCALE_LIMIT",
        "manual_waveform_review_status": "PENDING_MANUAL_FIGURE_REVIEW",
        "family_assignments_made": 0,
        "scientific_extension_claim": "STOP_PENDING_MANUAL_DAS_ARTIFACT_AND_WAVEFORM_REVIEW",
        "result_sha256": sha256_file(result_path),
        "figure_sha256": sha256_file(figure_path),
        "next_gate": "manual_review_of_das_only_waveform_review_png_and_interval_stratified_adjudication",
    }
    write_json(status_path, status)


if __name__ == "__main__":
    main()
