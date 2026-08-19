#!/usr/bin/env python
"""Make clean, publication-style DAS/station waveform figures.

This presentation layer uses the already generated waveform panels and frozen
candidate times.  It does not rerun detection or assign event/family labels.
The output is deliberately sparse: shared axes, one colorbar per quantity,
large type, and vector PDF/SVG alongside 300-dpi PNG.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import Normalize

from .common import load_config, project_root, utc_now, write_json
from .heldout_das_runner_access import (
    load_frozen_detector_configs,
    load_runner_release,
    registered_interval,
    registered_interval_rows,
    registered_manifest_rows,
    validate_runner_release,
)
from .make_waveform_validation_figures import (
    _csv,
    _das_panel,
    _station_stream,
)


BLUE = "#1f4e79"
ORANGE = "#d97732"
INK = "#1b2733"
GRID = "#d9e2e8"


def _setup() -> None:
    mpl.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9.5,
            "axes.titlesize": 11,
            "axes.labelsize": 9.5,
            "axes.titleweight": "bold",
            "axes.edgecolor": "#6b7c87",
            "axes.linewidth": 0.7,
            "xtick.color": INK,
            "ytick.color": INK,
            "axes.labelcolor": INK,
            "text.color": INK,
            "savefig.facecolor": "white",
        }
    )


def _style_axis(axis: Any) -> None:
    axis.grid(True, color=GRID, linewidth=0.45, alpha=0.7)
    axis.set_xlim(-6.0, 6.0)
    axis.axvline(0.0, color=INK, linewidth=0.8, zorder=5)


def _draw_das(axis: Any, times: np.ndarray, values: np.ndarray, title: str) -> Any:
    image = axis.imshow(
        values,
        aspect="auto",
        extent=[float(times[0]), float(times[-1]), 0, values.shape[0]],
        origin="lower",
        cmap="RdBu_r",
        norm=Normalize(-2.5, 2.5),
        interpolation="nearest",
    )
    axis.set_title(title, loc="left")
    axis.set_ylabel("DAS channel index")
    axis.set_yticks([0, 45, 90, 135, 179])
    axis.set_yticklabels(["0", "225", "450", "675", "895"])
    axis.set_xticklabels([])
    _style_axis(axis)
    return image


def _draw_blocks(axis: Any, times: np.ndarray, blocks: np.ndarray, block_ids: np.ndarray, title: str) -> Any:
    block_times = np.linspace(float(times[0]), float(times[-1]), blocks.shape[1])
    image = axis.imshow(
        blocks,
        aspect="auto",
        extent=[block_times[0], block_times[-1], float(block_ids[0]) - 0.5, float(block_ids[-1]) + 0.5],
        origin="lower",
        cmap="magma",
        norm=Normalize(0.5, 4.0),
        interpolation="nearest",
    )
    axis.set_title(title, loc="left")
    axis.set_ylabel("block")
    axis.set_yticks(block_ids)
    axis.set_yticklabels([str(int(item)) for item in block_ids])
    axis.set_xticklabels([])
    _style_axis(axis)
    return image


def _draw_stations(axis: Any, stream: Any, title: str, network_offset_s: float | None = None) -> None:
    traces = list(stream)[:4]
    for index, trace in enumerate(traces):
        # The stream was trimmed to center +/- 10 s by the calling helper.
        time = np.arange(len(trace), dtype=float) / float(trace.stats.sampling_rate)
        time += -10.0
        axis.plot(time, np.asarray(trace.data, dtype=float) + 1.25 * index, color=INK, linewidth=0.65)
        axis.text(6.15, 1.25 * index, trace.id, va="center", fontsize=7.5)
    axis.set_title(title, loc="left")
    axis.set_yticks([])
    axis.set_xlabel("seconds relative to DAS trigger")
    if network_offset_s is not None:
        axis.axvline(network_offset_s, color=ORANGE, linestyle="--", linewidth=1.0, label="network arrival")
        axis.legend(fontsize=7, loc="upper right", frameon=False)
    _style_axis(axis)


def _save_bundle(fig: Any, output: Path, stem: str) -> None:
    for suffix, options in (
        ("png", {"dpi": 300}),
        ("pdf", {}),
        ("svg", {}),
    ):
        fig.savefig(output / (stem + "." + suffix), bbox_inches="tight", **options)


def _control_figure(output: Path, config: Dict[str, Any]) -> None:
    manifest = [Path(row["path"]) for row in _csv(project_root() / "outputs" / "development_das" / "manifest_selection.csv")]
    station_paths = sorted((project_root() / "cached_continuous" / "network" / "development").glob("*.mseed"))
    controls = [
        ("75120101", 1737350090.4516, 1737350088.490, 8),
        ("75120116", 1737350671.0016, 1737350669.130, 10),
    ]
    fig, axes = plt.subplots(2, 3, figsize=(13.2, 7.6), sharex=True, constrained_layout=True)
    for row, (event_id, das_epoch, network_epoch, support) in enumerate(controls):
        times, values, blocks, block_ids, _ = _das_panel(manifest, das_epoch, config)
        stream = _station_stream(station_paths, das_epoch, 10.0)
        das_image = _draw_das(axes[row, 0], times, values, "DAS waveform" if row == 0 else "")
        block_image = _draw_blocks(axes[row, 1], times, blocks, block_ids, "Block characteristic" if row == 0 else "")
        _draw_stations(axes[row, 2], stream, "Station traces" if row == 0 else "", network_epoch - das_epoch)
        axes[row, 0].text(-0.24, 0.5, f"Event {event_id}\n{support}/10 strong blocks", transform=axes[row, 0].transAxes, ha="right", va="center", fontweight="bold")
        if row == 1:
            axes[row, 0].set_xlabel("seconds relative to DAS trigger")
            axes[row, 1].set_xlabel("seconds relative to DAS trigger")
    fig.suptitle("Known local events are coherent on deep DAS and the seismic network", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.005, "DAS rows use sampled HDF5 channel index; traces are display-normalized 5–20 Hz waveforms. Dashed lines mark the network arrival.", ha="center", fontsize=8, color="#4d5b63")
    fig.colorbar(das_image, ax=axes[:, 0], fraction=0.025, pad=0.02, label="row-normalized phase")
    fig.colorbar(block_image, ax=axes[:, 1], fraction=0.025, pad=0.02, label="block characteristic")
    _save_bundle(fig, output, "figure_1_known_local_controls")
    plt.close(fig)


def _candidate_figure(output: Path, config: Dict[str, Any], registration: Dict[str, Any], status: Dict[str, Any]) -> List[str]:
    project = project_root()
    v1, _ = load_frozen_detector_configs(project, registration)
    context = _csv(project / "outputs" / "heldout_v2" / "comparison" / "das_network_network_context.csv")
    candidates = sorted(
        [row for row in context if row["comparison_membership"] == "DAS_only"],
        key=lambda row: float(row["DAS_coincidence_score"]),
        reverse=True,
    )[:4]
    intervals = {row["interval_id"]: row for row in registered_interval_rows(project, registration, status)}
    fig, axes = plt.subplots(4, 3, figsize=(13.2, 14.0), sharex=True, constrained_layout=True)
    for row, candidate in enumerate(candidates):
        interval_id = candidate["interval_id"]
        interval = registered_interval(project, registration, status, interval_id)
        manifest = [Path(item["path"]) for item in registered_manifest_rows(project, registration, status, interval)]
        station_paths = sorted((project / "cached_continuous" / "network" / "heldout_v1" / interval_id).glob("*.mseed"))
        epoch = float(candidate["DAS_trigger_epoch_s"])
        times, values, blocks, block_ids, _ = _das_panel(manifest, epoch, v1)
        stream = _station_stream(station_paths, epoch, 10.0)
        das_image = _draw_das(axes[row, 0], times, values, "DAS waveform" if row == 0 else "")
        block_image = _draw_blocks(axes[row, 1], times, blocks, block_ids, "Block characteristic" if row == 0 else "")
        _draw_stations(axes[row, 2], stream, "Station traces" if row == 0 else "")
        axes[row, 0].text(-0.24, 0.5, f"{candidate['DAS_candidate_id']}\n{interval_id}", transform=axes[row, 0].transAxes, ha="right", va="center", fontweight="bold", fontsize=8)
        if row == 3:
            for column in axes[row]: column.set_xlabel("seconds relative to DAS trigger")
    fig.suptitle("Independent-test DAS candidates: waveform evidence for adjudication", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.005, "These candidates were not detected by the frozen network branches and have no cached regional association within 30 s. This figure does not validate them as earthquakes.", ha="center", fontsize=8, color="#4d5b63")
    fig.colorbar(das_image, ax=axes[:, 0], fraction=0.025, pad=0.02, label="row-normalized phase")
    fig.colorbar(block_image, ax=axes[:, 1], fraction=0.025, pad=0.02, label="block characteristic")
    _save_bundle(fig, output, "figure_2_independent_test_candidates")
    plt.close(fig)
    return [row["DAS_candidate_id"] for row in candidates]


def main() -> None:
    _setup()
    project = project_root()
    output = project / "outputs" / "heldout_v2" / "figures"
    output.mkdir(parents=True, exist_ok=True)
    development = load_config(project / "config" / "das_development.json")
    _control_figure(output, development)
    registration = load_config(project / "config" / "heldout_das_replay.json")
    registration_status = json.loads((project / "outputs" / "heldout_v2" / "registration" / "das_registration_status.json").read_text())
    release = load_runner_release(project)
    validate_runner_release(project, registration, registration_status, release)
    candidate_ids = _candidate_figure(output, development, registration, registration_status)
    write_json(output / "publication_figure_status.json", {
        "status": "PARTIAL",
        "generated_utc": utc_now(),
        "figure_1": "figure_1_known_local_controls",
        "figure_2": "figure_2_independent_test_candidates",
        "independent_test_candidate_ids": candidate_ids,
        "formats": ["png_300dpi", "pdf_vector", "svg_vector"],
        "interpretation": "publication_style_waveform_evidence_not_event_validation_or_family_assignment",
        "depth_or_strain_claim": False,
        "family_assignments_made": 0,
    })


if __name__ == "__main__":
    main()
