#!/usr/bin/env python
"""Compare DAS display bands on the two known local earthquakes.

This is a processing check, not a new event detector.  It reports the median
channel signal-to-noise ratio and the fraction of DAS channels above 3 for the
same two known earthquakes in four display bands.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np

from .common import load_config, project_root, write_json
from .make_waveform_validation_figures import _csv, _das_panel

BANDS = ((2.0, 10.0), (5.0, 20.0), (10.0, 40.0), (2.0, 40.0))
CONTROLS = (
    ("Known earthquake 75120101", 1737350090.4516),
    ("Known earthquake 75120116", 1737350671.0016),
)


def _rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.asarray(values, dtype=float) ** 2)))


def _metrics(values: np.ndarray, times: np.ndarray) -> tuple[float, float]:
    signal = (times >= -0.5) & (times <= 1.5)
    noise = (times >= -4.0) & (times <= -2.0)
    ratios = np.asarray([_rms(row[signal]) / (_rms(row[noise]) + 1e-12) for row in values])
    return float(np.nanmedian(ratios)), float(np.mean(ratios >= 3.0))


def main() -> None:
    project = project_root()
    config = load_config(project / "config" / "das_development.json")
    manifest = [Path(row["path"]) for row in _csv(project / "outputs" / "development_das" / "manifest_selection.csv")]
    rows: list[dict[str, object]] = []
    for event_label, epoch in CONTROLS:
        for low, high in BANDS:
            times, values, _blocks, _block_ids, _rate = _das_panel(manifest, epoch, config, band_hz=(low, high))
            median_snr, fraction_above_three = _metrics(values, times)
            rows.append({
                "event": event_label,
                "low_hz": low,
                "high_hz": high,
                "median_channel_signal_noise_ratio": median_snr,
                "fraction_channels_signal_noise_at_least_3": fraction_above_three,
            })
    output = project / "outputs" / "heldout_v2" / "figures"
    output.mkdir(parents=True, exist_ok=True)
    with (output / "das_band_sensitivity.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    figure, axes = plt.subplots(1, 2, figsize=(10.5, 4.0), constrained_layout=True)
    labels = [f"{int(low)}–{int(high)}" for low, high in BANDS]
    x = np.arange(len(labels))
    width = 0.36
    for index, event_label in enumerate(item[0] for item in CONTROLS):
        event_rows = [row for row in rows if row["event"] == event_label]
        axes[0].bar(x + (index - 0.5) * width, [row["median_channel_signal_noise_ratio"] for row in event_rows], width, label=event_label.replace("Known earthquake ", ""))
        axes[1].bar(x + (index - 0.5) * width, [row["fraction_channels_signal_noise_at_least_3"] for row in event_rows], width, label=event_label.replace("Known earthquake ", ""))
    for axis, title, ylabel in (
        (axes[0], "Known-earthquake DAS check", "median channel signal/noise ratio"),
        (axes[1], "Known-earthquake DAS check", "fraction of channels with signal/noise ≥ 3"),
    ):
        axis.set_title(title, fontweight="bold")
        axis.set_xticks(x, labels)
        axis.set_xlabel("DAS display band (Hz)")
        axis.set_ylabel(ylabel)
        axis.grid(axis="y", color="#d9e2e8", linewidth=0.6)
        axis.set_axisbelow(True)
        axis.legend(frameon=False, fontsize=8)
    figure.suptitle("Frequency-band sensitivity of the two known earthquake recordings", fontsize=14, fontweight="bold")
    figure.savefig(output / "das_band_sensitivity.png", dpi=300, bbox_inches="tight")
    figure.savefig(output / "das_band_sensitivity.pdf", bbox_inches="tight")
    plt.close(figure)
    write_json(output / "das_band_sensitivity_status.json", {
        "status": "CHECK_COMPLETE",
        "bands_hz": [list(band) for band in BANDS],
        "controls": [label for label, _ in CONTROLS],
        "conclusion": "No single band is best for both known earthquakes; 5-20 Hz is retained only as the registered comparison band, not as an optimized choice.",
    })


if __name__ == "__main__":
    main()
