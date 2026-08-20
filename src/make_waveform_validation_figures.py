#!/usr/bin/env python
"""Create presentation-quality DAS/station waveform validation figures.

The panels are descriptive waveform evidence, not a new detector.  Development
positive controls and the strongest held-out DAS-only candidates are shown with
the same processing and axes.  DAS spatial coordinates remain sampled HDF5
columns/locus indices; no depth or strain interpretation is introduced.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np
from obspy import Stream, UTCDateTime, read
from scipy.signal import butter, resample_poly, sosfiltfilt

from .common import load_config, project_root, sha256_file, utc_now, write_json
from .das_continuous_detection import block_characteristic_matrix, channel_qc
from .h5io import read_window
from .heldout_das_runner_access import (
    load_frozen_detector_configs,
    load_runner_release,
    registered_interval,
    registered_interval_rows,
    registered_manifest_rows,
    validate_runner_release,
)


def _csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _standardize_rows(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    med = np.nanmedian(array, axis=1, keepdims=True)
    array = array - med
    scale = np.nanpercentile(np.abs(array), 99.0, axis=1, keepdims=True)
    scale[~np.isfinite(scale) | (scale <= 0.0)] = 1.0
    return np.clip(array / scale, -4.0, 4.0).astype(np.float32)


def _das_panel(
    paths: Sequence[Path],
    epoch_s: float,
    config: Mapping[str, Any],
    half_width_s: float = 10.0,
    band_hz: Tuple[float, float] | None = None,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, float]:
    sampling = config["channel_sampling"]
    preprocessing = config["preprocessing"]
    raw_rate = 500.0
    target_rate = float(preprocessing["target_sample_rate_hz"])
    down = int(round(raw_rate / target_rate))
    window = read_window(
        paths,
        epoch_s - half_width_s,
        epoch_s + half_width_s,
        channel_start=int(sampling["column_start"]),
        channel_stop=int(sampling["column_stop"]),
        channel_stride=int(sampling["column_stride"]),
    )
    values = np.asarray(window.data, dtype=np.float32)
    values = values - np.mean(values, axis=0, dtype=np.float64).astype(np.float32)
    if band_hz is None:
        band_hz = tuple(float(value) for value in preprocessing["primary_band_hz"])
    sos = butter(
        4,
        band_hz,
        btype="bandpass",
        fs=raw_rate,
        output="sos",
    )
    filtered = sosfiltfilt(sos, values, axis=0)
    processed = np.asarray(resample_poly(filtered, 1, down, axis=0), dtype=np.float32)
    times = float(window.time_epoch_s[0]) + np.arange(len(processed), dtype=float) / target_rate
    usable, rms, _ = channel_qc(processed, window.column_indices, window.locus_indices)
    blocks, block_ids, _ = block_characteristic_matrix(
        processed,
        window.column_indices,
        window.locus_indices,
        usable,
        rms,
        config,
    )
    return times - epoch_s, _standardize_rows(processed.T), blocks, np.asarray(block_ids), float(target_rate)


def _station_stream(
    paths: Iterable[Path],
    center_epoch_s: float,
    half_width_s: float,
    band_hz: Tuple[float, float] = (5.0, 20.0),
) -> Stream:
    stream = Stream()
    for path in paths:
        stream += read(str(path))
    start = UTCDateTime(center_epoch_s - half_width_s)
    end = UTCDateTime(center_epoch_s + half_width_s)
    selected = Stream()
    preferred = ["BP.EADB.40.DP1", "BP.EADB.40.DP2", "BP.EADB.40.DP3", "BK.PKD.00.HHZ", "NC.PSM..EHZ", "BP.FROB.40.DP1"]
    for identifier in preferred:
        matches = stream.select(id=identifier)
        if not matches:
            continue
        trace = matches[0].copy()
        trace.trim(start, end, pad=False)
        if len(trace) < 100:
            continue
        trace.detrend("demean")
        trace.filter("bandpass", freqmin=float(band_hz[0]), freqmax=float(band_hz[1]), corners=4, zerophase=True)
        data = np.asarray(trace.data, dtype=np.float64)
        data -= np.median(data)
        scale = np.percentile(np.abs(data), 99.0)
        if scale > 0.0:
            data /= scale
        trace.data = np.clip(data, -4.0, 4.0).astype(np.float32)
        selected += trace
    return selected


def _plot_case(
    axis_das: Any,
    axis_blocks: Any,
    axis_station: Any,
    axis_text: Any,
    label: str,
    das_epoch_s: float,
    das_times: np.ndarray,
    das_values: np.ndarray,
    blocks: np.ndarray,
    block_ids: np.ndarray,
    target_rate: float,
    station_stream: Stream,
    network_epoch_s: float | None,
    evidence: str,
) -> None:
    image = axis_das.imshow(
        das_values,
        aspect="auto",
        extent=[float(das_times[0]), float(das_times[-1]), 0, das_values.shape[0]],
        origin="lower",
        cmap="RdBu_r",
        vmin=-2.5,
        vmax=2.5,
    )
    axis_das.axvline(0.0, color="k", lw=1.2, label="DAS trigger")
    if network_epoch_s is not None:
        axis_das.axvline(network_epoch_s - das_epoch_s, color="#f0a202", lw=1.2, ls="--", label="network time")
    axis_das.set_ylabel("sampled DAS channel")
    axis_das.set_title(f"{label}: DAS filtered waveform", loc="left", fontweight="bold")
    axis_das.set_xlim(-8, 8)
    axis_das.legend(fontsize=7, loc="upper right")
    axis_das.figure.colorbar(image, ax=axis_das, pad=0.01, fraction=0.04, label="row-normalized phase")

    block_times = np.linspace(float(das_times[0]), float(das_times[-1]), blocks.shape[1])
    axis_blocks.imshow(
        blocks,
        aspect="auto",
        extent=[block_times[0], block_times[-1], float(block_ids[0]) - 0.5, float(block_ids[-1]) + 0.5],
        origin="lower",
        cmap="magma",
        vmin=0.5,
        vmax=4.0,
    )
    axis_blocks.axvline(0.0, color="w", lw=1.0)
    if network_epoch_s is not None:
        axis_blocks.axvline(network_epoch_s - das_epoch_s, color="#f0a202", lw=1.0, ls="--")
    axis_blocks.set_ylabel("DAS block ID")
    axis_blocks.set_title("Block characteristic", loc="left")
    axis_blocks.set_xlim(-8, 8)

    for index, trace in enumerate(station_stream):
        times = np.asarray(trace.times(), dtype=float) + float(trace.stats.starttime - UTCDateTime(das_epoch_s))
        axis_station.plot(times, trace.data + index * 1.2, lw=0.7, color="#263238")
        axis_station.text(8.05, index * 1.2, trace.id, va="center", fontsize=7)
    axis_station.axvline(0.0, color="k", lw=1.2)
    if network_epoch_s is not None:
        axis_station.axvline(network_epoch_s - das_epoch_s, color="#f0a202", lw=1.2, ls="--")
    axis_station.set_xlim(-8, 10)
    axis_station.set_yticks([])
    axis_station.set_title("5–20 Hz station traces", loc="left")
    axis_station.set_xlabel("seconds relative to DAS trigger")

    axis_text.axis("off")
    axis_text.text(0.02, 0.9, evidence, va="top", fontsize=9, wrap=True)
    axis_text.text(0.02, 0.1, "DAS axis is sampled HDF5 channel index; no depth or strain claim.", va="bottom", fontsize=7, color="#455a64")


def main() -> None:
    project = project_root()
    output = project / "outputs" / "heldout_v2" / "figures"
    output.mkdir(parents=True, exist_ok=True)
    development = load_config(project / "config" / "das_development.json")
    dev_manifest = [Path(row["path"]) for row in _csv(project / "outputs" / "development_das" / "manifest_selection.csv")]
    dev_station_paths = sorted((project / "cached_continuous" / "network" / "development").glob("*.mseed"))
    controls = [
        ("Known local repeater A", 1737350090.4516, 1737350088.490, 8, "Development positive control; catalog event 75120101; DAS and network arrivals are both visible."),
        ("Known local repeater B", 1737350671.0016, 1737350669.130, 10, "Development positive control; catalog event 75120116; DAS and network arrivals are both visible."),
    ]
    fig = plt.figure(figsize=(16, 13), constrained_layout=True)
    grid = fig.add_gridspec(2, 4, width_ratios=[1.7, 1.2, 1.5, 0.8], hspace=0.22)
    for row, (label, das_epoch, network_epoch, support, evidence) in enumerate(controls):
        times, values, blocks, block_ids, rate = _das_panel(dev_manifest, das_epoch, development)
        station = _station_stream(dev_station_paths, das_epoch, 10.0)
        _plot_case(
            fig.add_subplot(grid[row, 0]),
            fig.add_subplot(grid[row, 1]),
            fig.add_subplot(grid[row, 2]),
            fig.add_subplot(grid[row, 3]),
            label,
            das_epoch,
            times,
            values,
            blocks,
            block_ids,
            rate,
            station,
            network_epoch,
            evidence + f"\n\nStrong blocks at DAS trigger: {support}/10.",
        )
    fig.suptitle("Development validation: known local events on DAS and the seismic network", fontsize=18, fontweight="bold")
    fig.savefig(output / "development_positive_control_waveforms.png", dpi=220, bbox_inches="tight")
    plt.close(fig)

    adjudication = load_config(project / "config" / "heldout_das_adjudication.json")
    registration = load_config(project / "config" / "heldout_das_replay.json")
    reg_status = json.loads((project / "outputs" / "heldout_v2" / "registration" / "das_registration_status.json").read_text())
    release = load_runner_release(project)
    validate_runner_release(project, registration, reg_status, release)
    v1, _ = load_frozen_detector_configs(project, registration)
    context = _csv(project / "outputs" / "heldout_v2" / "comparison" / "das_network_network_context.csv")
    candidates = [row for row in context if row["comparison_membership"] == "DAS_only"]
    candidates.sort(key=lambda row: float(row["DAS_coincidence_score"]), reverse=True)
    candidates = candidates[:4]
    interval_rows = {row["interval_id"]: row for row in registered_interval_rows(project, registration, reg_status)}
    network_files = {row["interval_id"]: sorted((project / "cached_continuous" / "network" / "heldout_v1" / row["interval_id"]).glob("*.mseed")) for row in interval_rows.values()}
    figure = plt.figure(figsize=(16, 24), constrained_layout=True)
    grid = figure.add_gridspec(4, 4, width_ratios=[1.7, 1.2, 1.5, 0.8], hspace=0.22)
    for row_index, candidate in enumerate(candidates):
        interval_id = candidate["interval_id"]
        interval = registered_interval(project, registration, reg_status, interval_id)
        manifest = registered_manifest_rows(project, registration, reg_status, interval)
        paths = [Path(row["path"]) for row in manifest]
        das_epoch = float(candidate["DAS_trigger_epoch_s"])
        times, values, blocks, block_ids, rate = _das_panel(paths, das_epoch, v1)
        station = _station_stream(network_files[interval_id], das_epoch, 10.0)
        _plot_case(
            figure.add_subplot(grid[row_index, 0]),
            figure.add_subplot(grid[row_index, 1]),
            figure.add_subplot(grid[row_index, 2]),
            figure.add_subplot(grid[row_index, 3]),
            f"{candidate['DAS_candidate_id']} ({interval_id})",
            das_epoch,
            times,
            values,
            blocks,
            block_ids,
            rate,
            station,
            None,
            "Held-out DAS-only candidate.\n\nNo frozen generic/template network crossing and no cached regional catalog association within 30 s.\n\nThis panel is evidence for manual adjudication, not a validated earthquake claim.",
        )
    figure.suptitle("Held-out DAS-only candidates: waveform evidence requiring adjudication", fontsize=18, fontweight="bold")
    figure.savefig(output / "heldout_das_only_waveform_panels.png", dpi=220, bbox_inches="tight")
    plt.close(figure)
    status = {
        "status": "PARTIAL",
        "generated_utc": utc_now(),
        "development_positive_control_count": len(controls),
        "heldout_candidate_panel_count": len(candidates),
        "heldout_candidates": [row["DAS_candidate_id"] for row in candidates],
        "processing": "5-20_Hz_zero_phase_bandpass; DAS_500_to_100_Hz; row_normalized_display_only",
        "station_processing": "5-20_Hz_zero_phase_bandpass; per_trace_display_normalization_only",
        "das_spatial_coordinate": "sampled_HDF5_channel_index",
        "depth_or_strain_claim": False,
        "family_assignments_made": 0,
        "scientific_interpretation": "waveform_evidence_panels_for_manual_adjudication_not_event_validation",
        "development_figure": str(output / "development_positive_control_waveforms.png"),
        "heldout_figure": str(output / "heldout_das_only_waveform_panels.png"),
    }
    write_json(output / "waveform_figure_status.json", status)


if __name__ == "__main__":
    main()
