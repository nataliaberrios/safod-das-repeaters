#!/usr/bin/env python
"""Build cached deep-DAS windows for the prospective candidate and control."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import correlate
from scipy.stats import spearmanr

from .common import PASS, STOP, load_config, parse_utc, project_root, write_json
from .coverage import write_rows
from .das_analysis import analyze_window, write_cache
from .h5io import discover_h5, read_window


def _event(config: Dict[str, Any], name: str) -> Dict[str, Any]:
    row = dict(config["events"][name])
    row["role"] = name
    return row


def _plot_cache(path: Path, cache_path: Path, title: str) -> None:
    with np.load(cache_path, allow_pickle=False) as cache:
        data = np.asarray(cache["filtered_phase"], dtype=float)
        time = cache["relative_time_s"]
        loci = cache["locus_indices"]
        noise = np.maximum(cache["noise_rms"], np.finfo(float).eps)
        normalized = data / noise[None, :]
        limit = max(3.0, float(np.nanpercentile(np.abs(normalized), 99.0)))
        limit = min(limit, 20.0)
        fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
        image = axes[0].imshow(
            normalized.T,
            origin="lower",
            aspect="auto",
            extent=[time[0], time[-1], loci[0], loci[-1]],
            cmap="RdBu_r",
            vmin=-limit,
            vmax=limit,
            interpolation="nearest",
        )
        axes[0].axvline(0.0, color="k", linewidth=0.8, linestyle="--")
        axes[0].set_xlabel("Time from catalog origin (s)")
        axes[0].set_ylabel("OptaSense locus index (not depth)")
        axes[0].set_title(title)
        fig.colorbar(image, ax=axes[0], label="Bandpassed phase / prewindow RMS")
        axes[1].plot(cache["channel_snr"], loci, color="0.15", linewidth=0.8)
        axes[1].axvline(3.0, color="tab:red", linestyle="--", linewidth=1.0)
        axes[1].set_xscale("log")
        axes[1].set_xlabel("Signal/prewindow RMS")
        axes[1].set_ylabel("OptaSense locus index (not depth)")
        axes[1].grid(alpha=0.2)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=170)
        plt.close(fig)


def _hard_negative_baseline(
    first_cache: Path,
    second_cache: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    """Describe a non-family pair using the frozen HRSN time/lag convention."""

    with np.load(first_cache, allow_pickle=False) as first, np.load(
        second_cache, allow_pickle=False
    ) as second:
        first_time = first["relative_time_s"]
        second_time = second["relative_time_s"]
        first_data = np.asarray(first["filtered_phase"], dtype=float)
        second_data = np.asarray(second["filtered_phase"], dtype=float)
        first_mask = (first_time >= 0.5) & (first_time < 10.0)
        second_mask = (second_time >= 0.5) & (second_time < 10.0)
        first_data = first_data[first_mask]
        second_data = second_data[second_mask]
        count = min(len(first_data), len(second_data))
        first_data = first_data[:count] - np.mean(first_data[:count], axis=0)
        second_data = second_data[:count] - np.mean(second_data[:count], axis=0)
        denominator = np.sqrt(
            np.sum(first_data ** 2, axis=0) * np.sum(second_data ** 2, axis=0)
        )
        sample_rate = float(first["display_sample_rate_hz"])
        maximum_lag_samples = int(round(0.5 * sample_rate))
        lags = np.arange(-count + 1, count)
        keep = np.abs(lags) <= maximum_lag_samples
        correlations = np.full(first_data.shape[1], np.nan, dtype=float)
        for channel in range(first_data.shape[1]):
            if denominator[channel] <= np.finfo(float).eps:
                continue
            values = correlate(
                first_data[:, channel],
                second_data[:, channel],
                mode="full",
                method="fft",
            )
            correlations[channel] = np.max(values[keep]) / denominator[channel]
        fingerprint = float(
            spearmanr(
                np.log10(np.maximum(first["channel_snr"], 1.0e-6)),
                np.log10(np.maximum(second["channel_snr"], 1.0e-6)),
            ).correlation
        )
        loci = first["locus_indices"]
    finite = correlations[np.isfinite(correlations)]
    metrics = {
        "pair_role": "same-fiber magnitude-matched hard negative",
        "channel_count": int(finite.size),
        "origin_relative_window_s": [0.5, 10.0],
        "maximum_residual_lag_s": 0.5,
        "median_per_channel_correlation": float(np.median(finite)),
        "p90_per_channel_correlation": float(np.percentile(finite, 90)),
        "p95_per_channel_correlation": float(np.percentile(finite, 95)),
        "maximum_per_channel_correlation": float(np.max(finite)),
        "fraction_above_0_8": float(np.mean(finite >= 0.8)),
        "channel_snr_fingerprint_spearman": fingerprint,
        "allowed_use": "negative baseline for a future preregistered DAS test; not classifier performance",
    }
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), constrained_layout=True)
    axes[0].hist(finite, bins=np.linspace(-0.1, 1.0, 45), color="0.25")
    axes[0].axvline(metrics["median_per_channel_correlation"], color="tab:red")
    axes[0].set_xlabel("Peak normalized correlation per sampled locus")
    axes[0].set_ylabel("Count")
    axes[0].set_title("Hard-negative DAS baseline")
    axes[1].plot(correlations, loci, color="0.2", linewidth=0.7)
    axes[1].axvline(0.8, color="tab:red", linestyle="--")
    axes[1].set_xlabel("Peak correlation")
    axes[1].set_ylabel("OptaSense locus index (not depth)")
    axes[1].set_title("Dense same-fiber control")
    fig.savefig(output_dir / "hard_negative_baseline.png", dpi=170)
    plt.close(fig)
    return metrics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    settings = config["deep_das"]
    project = project_root()
    output_dir = project / "outputs" / "deep_das"
    cache_dir = project / "cached_event_windows" / "deep_das"
    paths = []
    for key in ("deep_mar_apr", "deep_may_jun"):
        paths.extend(discover_h5(config["paths"][key]))
    summaries: List[Dict[str, Any]] = []
    block_rows: List[Dict[str, Any]] = []
    for role in ("deep_template", "hard_spatial_control"):
        event = _event(config, role)
        origin = parse_utc(event["origin_time"]).timestamp()
        waveform = read_window(
            paths,
            origin + float(settings["window_s"][0]),
            origin + float(settings["window_s"][1]),
            channel_start=int(settings["channel_start"]),
            channel_stop=int(settings["channel_stop"]),
            channel_stride=int(settings["channel_stride"]),
        )
        summary, blocks, arrays = analyze_window(
            waveform,
            reference_epoch_s=origin,
            band_hz=settings["band_hz"],
            noise_window_s=settings["noise_window_s"],
            signal_window_s=settings["signal_window_s"],
            search_window_s=None,
            block_size_columns=int(settings["channel_block_size_columns"]),
            minimum_robust_peak_z=float(settings["minimum_robust_peak_z"]),
            minimum_detected_block_fraction=float(
                settings["minimum_detected_block_fraction"]
            ),
        )
        summary.update(
            {
                "event_id": str(event["event_id"]),
                "role": role,
                "origin_time": event["origin_time"],
                "source_files": ";".join(waveform.source_files),
                "config_sha256": config["_config_sha256"],
            }
        )
        for row in blocks:
            row["event_id"] = str(event["event_id"])
            row["role"] = role
        block_rows.extend(blocks)
        summaries.append(summary)
        cache_path = cache_dir / (str(event["event_id"]) + ".npz")
        write_cache(cache_path, waveform, arrays, summary)
        _plot_cache(
            output_dir / (str(event["event_id"]) + "_window.png"),
            cache_path,
            "{}: {}".format(role.replace("_", " "), event["event_id"]),
        )
    write_rows(output_dir / "event_metrics.csv", summaries)
    write_rows(output_dir / "block_metrics.csv", block_rows)
    target = next(row for row in summaries if row["role"] == "deep_template")
    control = next(row for row in summaries if row["role"] == "hard_spatial_control")
    hard_negative = _hard_negative_baseline(
        cache_dir / (str(target["event_id"]) + ".npz"),
        cache_dir / (str(control["event_id"]) + ".npz"),
        output_dir,
    )
    write_json(output_dir / "hard_negative_baseline.json", hard_negative)
    status = {
        "deep_template_detectability": target["status"],
        "hard_control_detectability": control["status"],
        "deep_template_label_status": "prospective_target_family_candidate_not_independently_classified",
        "direct_das_repeatability": STOP,
        "direct_das_repeatability_reason": "one prospective 2026 target-family candidate is detectable, but no second independently classified target-family event overlaps this deep-fiber epoch",
        "hard_negative_baseline": PASS,
        "geometry": STOP,
        "geometry_reason": "surveyed locus-to-MD/TVD/XYZ trajectory is unavailable",
        "stress_drop": STOP,
        "stress_drop_reason": "no same-epoch independently classified EGF pair and no passed corner-frequency/model gate",
        "config_sha256": config["_config_sha256"],
    }
    write_json(output_dir / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
