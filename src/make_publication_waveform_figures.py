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
    axis.set_ylabel("DAS channel number")
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
    """Draw filtered seismometer recordings on the same relative-time axis."""
    traces = list(stream)[:4]
    for index, trace in enumerate(traces):
        time = np.arange(len(trace), dtype=float) / float(trace.stats.sampling_rate) - 10.0
        axis.plot(time, np.asarray(trace.data, dtype=float) + 1.35 * index, color=INK, linewidth=0.7)
        axis.text(6.15, 1.35 * index, trace.id, va="center", fontsize=7.5)
    axis.set_title(title, loc="left")
    axis.set_yticks([])
    axis.set_ylim(-0.9, max(1.0, 1.35 * max(len(traces), 1) - 0.1))
    axis.set_xlabel("seconds from the marked DAS time")
    if network_offset_s is not None:
        axis.axvline(network_offset_s, color=ORANGE, linestyle="--", linewidth=1.0, label="seismometer arrival")
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
        ("75120101", 1737350090.4516, 1737350088.490),
        ("75120116", 1737350671.0016, 1737350669.130),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(12.5, 7.4), sharex=True, constrained_layout=True)
    if axes.ndim == 1:
        axes = axes[None, :]
    das_image = None
    for row, (event_id, das_epoch, network_epoch) in enumerate(controls):
        times, values, _blocks, _block_ids, _ = _das_panel(manifest, das_epoch, config)
        stream = _station_stream(station_paths, das_epoch, 10.0)
        das_image = _draw_das(axes[row, 0], times, values, "DAS recording" if row == 0 else "")
        _draw_stations(axes[row, 1], stream, "Seismometer recordings (5–20 Hz)" if row == 0 else "", network_epoch - das_epoch)
        axes[row, 0].text(-0.12, 0.5, f"Known earthquake\n{event_id}", transform=axes[row, 0].transAxes, ha="right", va="center", fontweight="bold", fontsize=9)
    fig.suptitle("Known earthquakes appear in DAS and seismometer recordings", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.005, "Both instruments are filtered from 5–20 Hz. Each horizontal row is one DAS channel; the dashed line marks the seismometer arrival.", ha="center", fontsize=8, color="#4d5b63")
    if das_image is not None:
        fig.colorbar(das_image, ax=axes[:, 0], fraction=0.025, pad=0.02, label="scaled DAS recording")
    _save_bundle(fig, output, "figure_1_known_earthquake_checks")
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
    fig, axes = plt.subplots(4, 2, figsize=(12.5, 12.5), sharex=True, constrained_layout=True)
    if axes.ndim == 1:
        axes = axes[None, :]
    das_image = None
    for row, candidate in enumerate(candidates):
        interval_id = candidate["interval_id"]
        interval = registered_interval(project, registration, status, interval_id)
        manifest = [Path(item["path"]) for item in registered_manifest_rows(project, registration, status, interval)]
        station_paths = sorted((project / "cached_continuous" / "network" / "heldout_v1" / interval_id).glob("*.mseed"))
        epoch = float(candidate["DAS_trigger_epoch_s"])
        times, values, _blocks, _block_ids, _ = _das_panel(manifest, epoch, v1)
        stream = _station_stream(station_paths, epoch, 10.0)
        das_image = _draw_das(axes[row, 0], times, values, "DAS recording" if row == 0 else "")
        _draw_stations(axes[row, 1], stream, "Seismometer recordings (5–20 Hz)" if row == 0 else "")
        axes[row, 0].text(-0.12, 0.5, f"Possible event\n{candidate['DAS_candidate_id']}", transform=axes[row, 0].transAxes, ha="right", va="center", fontweight="bold", fontsize=8)
    fig.suptitle("Possible earthquakes seen in DAS but not the seismometer recordings", fontsize=16, fontweight="bold")
    fig.text(0.5, 0.005, "Both instruments are filtered from 5–20 Hz. These four possible events are visible in DAS recordings but not in the selected seismometer recordings; each still needs event and noise review.", ha="center", fontsize=8, color="#4d5b63")
    if das_image is not None:
        fig.colorbar(das_image, ax=axes[:, 0], fraction=0.025, pad=0.02, label="scaled DAS recording")
    _save_bundle(fig, output, "figure_2_das_possible_events")
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
        "figure_1": "figure_1_known_earthquake_checks",
        "figure_2": "figure_2_das_possible_events",
        "fiber_only_possible_event_ids": candidate_ids,
        "formats": ["png_300dpi", "pdf_vector", "svg_vector"],
        "interpretation": "These plots support review; they do not prove earthquakes or assign repeater families.",
        "depth_or_strain_claim": False,
        "family_assignments_made": 0,
    })


if __name__ == "__main__":
    main()
