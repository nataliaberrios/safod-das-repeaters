#!/usr/bin/env python
"""Create a cached, explicitly conditional June deep-fiber shot calibration."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from scipy.stats import spearmanr

from .active_source import read_shot_log
from .common import CONDITIONAL, PASS, STOP, load_config, parse_utc, project_root, write_json
from .coverage import write_rows
from .das_analysis import analyze_window, write_cache
from .h5io import discover_h5, read_window


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    settings = config["active_source"]
    project = project_root()
    output_dir = project / "outputs" / "active_source"
    cache_dir = project / "cached_event_windows" / "active_source"
    shots = read_shot_log(Path(config["paths"]["active_source_shot_log"]), settings)
    write_rows(output_dir / "normalized_shots.csv", shots)
    paths = discover_h5(config["paths"]["active_source_deep"])
    summaries: List[Dict[str, Any]] = []
    block_rows: List[Dict[str, Any]] = []
    profiles = []
    for shot in shots:
        nominal = parse_utc(shot["nominal_origin_time"]).timestamp()
        waveform = read_window(
            paths,
            nominal + float(settings["window_s"][0]),
            nominal + float(settings["window_s"][1]),
            channel_start=int(settings["channel_start"]),
            channel_stop=int(settings["channel_stop"]),
            channel_stride=int(settings["channel_stride"]),
        )
        summary, blocks, arrays = analyze_window(
            waveform,
            reference_epoch_s=nominal,
            band_hz=settings["band_hz"],
            noise_window_s=settings["noise_window_s"],
            signal_window_s=None,
            search_window_s=settings["search_window_s"],
            block_size_columns=50,
            minimum_robust_peak_z=float(settings["minimum_robust_peak_z"]),
            minimum_detected_block_fraction=0.10,
        )
        summary.update(
            {
                "shot_number": shot["shot_number"],
                "shot_name": shot["name"],
                "nominal_origin_time": shot["nominal_origin_time"],
                "timing_status": "CONDITIONAL_nominal_log_time",
                "source_files": ";".join(waveform.source_files),
                "config_sha256": config["_config_sha256"],
            }
        )
        calibration_usable = bool(
            summary["status"] == PASS
            and summary["median_channel_snr"]
            >= float(settings["minimum_median_channel_snr_for_calibration"])
            and 0.0
            <= summary["peak_relative_s"]
            <= float(settings["maximum_nominal_peak_offset_s_for_calibration"])
            and bool(summary["usable_power_snr_ranges_hz"])
        )
        summary["calibration_status"] = PASS if calibration_usable else CONDITIONAL
        summary["calibration_exclusion_reason"] = (
            ""
            if calibration_usable
            else "nominal offset, median channel SNR, or array-median usable band failed the calibration-quality rule"
        )
        for row in blocks:
            row["shot_number"] = shot["shot_number"]
            row["shot_name"] = shot["name"]
        block_rows.extend(blocks)
        summaries.append(summary)
        profiles.append(np.log10(np.maximum(arrays["channel_snr"], 1.0e-6)))
        cache_path = cache_dir / ("shot_{:02d}.npz".format(shot["shot_number"]))
        write_cache(cache_path, waveform, arrays, summary)
    write_rows(output_dir / "shot_metrics.csv", summaries)
    write_rows(output_dir / "shot_block_metrics.csv", block_rows)

    profile_matrix = np.vstack(profiles)
    correlation = np.eye(len(shots), dtype=float)
    for first in range(len(shots)):
        for second in range(first + 1, len(shots)):
            value = spearmanr(profile_matrix[first], profile_matrix[second]).correlation
            correlation[first, second] = value
            correlation[second, first] = value
    np.savez_compressed(
        output_dir / "cross_shot_channel_profiles.npz",
        log10_channel_snr=profile_matrix,
        spearman_correlation=correlation,
        shot_numbers=np.asarray([shot["shot_number"] for shot in shots]),
    )
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    for shot, profile in zip(shots, profiles):
        axes[0].plot(profile, label="shot {}".format(shot["shot_number"]), linewidth=0.8)
    axes[0].set_xlabel("Sampled channel ordinal (stride from config)")
    axes[0].set_ylabel("log10 signal/prewindow RMS")
    axes[0].set_title("Relative channel response; not a depth axis")
    axes[0].legend(frameon=False)
    image = axes[1].imshow(correlation, vmin=-1, vmax=1, cmap="RdBu_r")
    axes[1].set_xticks(range(len(shots)), [str(shot["shot_number"]) for shot in shots])
    axes[1].set_yticks(range(len(shots)), [str(shot["shot_number"]) for shot in shots])
    axes[1].set_xlabel("Shot")
    axes[1].set_ylabel("Shot")
    axes[1].set_title("Spearman correlation of channel-SNR profiles")
    fig.colorbar(image, ax=axes[1])
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_dir / "channel_response_summary.png", dpi=170)
    plt.close(fig)

    detected_count = sum(row["status"] == PASS for row in summaries)
    calibration_count = sum(row["calibration_status"] == PASS for row in summaries)
    status = {
        "shot_detectability": PASS if calibration_count >= 3 else STOP,
        "shots_time_domain_detected": detected_count,
        "shots_calibration_usable": calibration_count,
        "shots_total": len(summaries),
        "absolute_timing": CONDITIONAL,
        "absolute_timing_reason": "available shot log has nominal local times but no independent GPS/detonator trigger uncertainty",
        "channel_response": PASS if calibration_count >= 3 else CONDITIONAL,
        "absolute_geometry": STOP,
        "absolute_geometry_reason": "deep locus-to-MD/TVD/XYZ trajectory unavailable",
        "config_sha256": config["_config_sha256"],
    }
    write_json(output_dir / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
