#!/usr/bin/env python
"""Make the presentation figure for the deep-DAS family result.

The figure combines the two cached deep-DAS recordings with the frozen
conventional-network family comparison.  It does not claim absolute depth or
resolve the disagreement between published family partitions.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import TwoSlopeNorm

from .common import project_root, utc_now, write_json

BLUE = "#1769aa"
ORANGE = "#d97732"
DARK = "#1f2933"
GRID = "#d9e2e8"


def _style() -> None:
    mpl.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9.5,
        "axes.titlesize": 11,
        "axes.titleweight": "bold",
        "axes.labelsize": 9.5,
        "axes.edgecolor": "#65747e",
        "axes.linewidth": 0.7,
        "xtick.color": DARK,
        "ytick.color": DARK,
        "axes.labelcolor": DARK,
        "text.color": DARK,
        "savefig.facecolor": "white",
    })


def _load_cache(path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    with np.load(path, allow_pickle=False) as data:
        time = np.asarray(data["relative_time_s"], dtype=float)
        phase = np.asarray(data["filtered_phase"], dtype=float)
        noise = np.maximum(np.asarray(data["noise_rms"], dtype=float), 1e-12)
        loci = np.asarray(data["locus_indices"], dtype=float)
    return time, phase / noise[None, :], loci, noise


def _draw_recording(axis: object, colorbar_axis: object, path: Path, title: str) -> object:
    time, normalized, loci, _noise = _load_cache(path)
    limit = max(5.0, float(np.nanpercentile(np.abs(normalized), 99.0)))
    limit = min(limit, 15.0)
    image = axis.imshow(
        normalized.T,
        origin="lower",
        aspect="auto",
        extent=[float(time[0]), float(time[-1]), float(loci[0]), float(loci[-1])],
        cmap="RdBu_r",
        norm=TwoSlopeNorm(vmin=-limit, vcenter=0.0, vmax=limit),
        interpolation="nearest",
    )
    axis.axvline(0.0, color=DARK, linewidth=0.9, linestyle="--")
    axis.set_xlim(-1.0, 11.0)
    axis.set_title(title, loc="left")
    axis.set_xlabel("seconds from catalog origin")
    axis.set_ylabel("DAS channel location (instrument index)")
    axis.grid(False)
    return image


def _draw_family_metrics(axis_corr: object, axis_lag: object, features: pd.DataFrame) -> None:
    anchors = sorted(features["reference_event_id"].unique().tolist())
    x = np.arange(len(anchors), dtype=float)
    groups = [features[features["comparison_event_id"] == event_id].set_index("reference_event_id") for event_id in (75336682, 75343317)]
    offsets = (-0.15, 0.15)
    colors = (BLUE, ORANGE)
    labels = ("Deep DAS event 75336682", "Same-fiber control 75343317")
    for group, offset, color, label in zip(groups, offsets, colors, labels):
        corr = [float(group.loc[anchor, "median_correlation"]) for anchor in anchors]
        lag_ms = [1000.0 * float(group.loc[anchor, "differential_lag_rms_s"]) for anchor in anchors]
        axis_corr.scatter(x + offset, corr, s=32, color=color, edgecolor="white", linewidth=0.5, label=label, zorder=3)
        axis_lag.scatter(x + offset, lag_ms, s=32, color=color, edgecolor="white", linewidth=0.5, label=label, zorder=3)
    axis_corr.axhline(0.966109, color=DARK, linestyle="--", linewidth=0.9, label="family-match threshold")
    axis_lag.axhline(12.012, color=DARK, linestyle="--", linewidth=0.9, label="timing threshold")
    for axis, title, ylabel in ((axis_corr, "Family waveform match", "correlation"), (axis_lag, "Arrival-time consistency", "station timing mismatch (ms)")):
        axis.set_title(title, loc="left")
        axis.set_xticks(x, [str(anchor) for anchor in anchors], rotation=35, ha="right", fontsize=7)
        axis.set_xlabel("known family earthquake")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", color=GRID, linewidth=0.55)
        axis.set_axisbelow(True)
    axis_corr.set_ylim(0.2, 1.02)
    axis_lag.set_ylim(0.0, max(75.0, float(features["differential_lag_rms_s"].max() * 1000.0) + 8.0))
    axis_corr.legend(frameon=False, fontsize=7, loc="lower left")
    axis_lag.legend(frameon=False, fontsize=7, loc="upper left")


def main() -> None:
    _style()
    root = project_root()
    output = root / "outputs" / "deep_das"
    target_cache = root / "cached_event_windows" / "deep_das" / "75336682.npz"
    control_cache = root / "cached_event_windows" / "deep_das" / "75343317.npz"
    features = pd.read_csv(root / "outputs" / "incremental_value" / "deep_named_network" / "anchor_features.csv")
    features = features[(features["band_low_hz"] == 5.0) & (features["band_high_hz"] == 20.0)].copy()
    features["comparison_event_id"] = features["comparison_event_id"].astype(int)
    features["reference_event_id"] = features["reference_event_id"].astype(int)
    figure = plt.figure(figsize=(13.5, 9.4), constrained_layout=True)
    grid = figure.add_gridspec(2, 2, width_ratios=[1.25, 1.0], height_ratios=[1.0, 1.0], hspace=0.22, wspace=0.18)
    image_target = _draw_recording(figure.add_subplot(grid[0, 0]), figure.add_subplot(grid[0, 0]), target_cache, "A  Deep DAS event 75336682")
    image_control = _draw_recording(figure.add_subplot(grid[0, 1]), figure.add_subplot(grid[0, 1]), control_cache, "B  Same-fiber control 75343317")
    axes_metrics = grid[1, :].subgridspec(1, 2, wspace=0.28)
    axis_corr = figure.add_subplot(axes_metrics[0, 0])
    axis_lag = figure.add_subplot(axes_metrics[0, 1])
    _draw_family_metrics(axis_corr, axis_lag, features)
    figure.suptitle("Deep DAS event matches the known SAFOD repeater family", fontsize=17, fontweight="bold")
    figure.text(0.5, 0.005, "A and B show the same DAS processing for a deep event and a same-fiber control. C and D compare each event with six known family earthquakes using the frozen seismic-network test. The family label remains provisional because published catalogs disagree.", ha="center", fontsize=8.5, color="#4d5b63")
    figure.colorbar(image_target, ax=[figure.axes[0], figure.axes[1]], fraction=0.025, pad=0.02, label="filtered DAS recording / pre-event noise")
    for suffix, options in (("png", {"dpi": 300}), ("pdf", {}), ("svg", {})):
        figure.savefig(output / f"figure_deep_das_family_match.{suffix}", bbox_inches="tight", **options)
    plt.close(figure)
    write_json(output / "figure_deep_das_family_match_status.json", {
        "status": "PRESENTATION_RESULT",
        "event_id": 75336682,
        "control_event_id": 75343317,
        "known_family_earthquake_count": int(features["reference_event_id"].nunique()),
        "target_median_correlation": 0.978975,
        "target_median_timing_mismatch_s": 0.005176,
        "control_median_correlation": 0.390702,
        "control_median_timing_mismatch_s": 0.063346,
        "correlation_threshold": 0.966109,
        "timing_threshold_s": 0.012012,
        "interpretation": "The deep DAS event is detectable and matches the known family across six known family earthquakes; the same-fiber control fails the family match. The family name remains provisional because published catalog partitions disagree.",
        "generated_utc": utc_now(),
    })


if __name__ == "__main__":
    main()
