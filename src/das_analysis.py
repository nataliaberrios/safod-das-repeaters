"""Gated detection and response summaries for deep-fiber DAS windows.

All spatial axes are HDF5 column or OptaSense locus index.  No column is treated
as depth.  Processed optical phase is used for relative SNR/coherence only; the
module does not label it strain, infer moment, or estimate stress drop.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import butter, detrend, resample_poly, sosfiltfilt, welch

from .common import PASS, STOP
from .h5io import WaveformWindow


def robust_z(value: float, reference: np.ndarray) -> float:
    reference = np.asarray(reference, dtype=float)
    median = float(np.median(reference))
    scale = 1.4826 * float(np.median(np.abs(reference - median)))
    if scale <= np.finfo(float).eps:
        scale = float(np.std(reference))
    return (float(value) - median) / (scale + np.finfo(float).eps)


def bandpass_matrix(
    data: np.ndarray, sample_rate_hz: float, band_hz: Sequence[float]
) -> np.ndarray:
    low, high = map(float, band_hz)
    if not (0.0 < low < high < 0.5 * sample_rate_hz):
        raise ValueError("invalid band {} at fs {}".format(band_hz, sample_rate_hz))
    matrix = detrend(np.asarray(data, dtype=np.float32), axis=0, type="linear")
    sos = butter(4, [low, high], btype="bandpass", fs=sample_rate_hz, output="sos")
    return np.asarray(sosfiltfilt(sos, matrix, axis=0), dtype=np.float32)


def _window_mask(relative_time_s: np.ndarray, bounds: Sequence[float]) -> np.ndarray:
    return (relative_time_s >= float(bounds[0])) & (
        relative_time_s < float(bounds[1])
    )


def _adjacent_correlations(matrix: np.ndarray) -> np.ndarray:
    if matrix.shape[1] < 2:
        return np.asarray([], dtype=float)
    centered = matrix - np.mean(matrix, axis=0, keepdims=True)
    norms = np.sqrt(np.sum(centered ** 2, axis=0))
    numerator = np.sum(centered[:, :-1] * centered[:, 1:], axis=0)
    denominator = norms[:-1] * norms[1:]
    valid = denominator > np.finfo(float).eps
    result = np.full(matrix.shape[1] - 1, np.nan, dtype=float)
    result[valid] = numerator[valid] / denominator[valid]
    return result


def _usable_frequency_ranges(
    frequency_hz: np.ndarray, power_ratio: np.ndarray, minimum_ratio: float = 9.0
) -> List[List[float]]:
    usable = np.isfinite(power_ratio) & (power_ratio >= minimum_ratio)
    ranges: List[List[float]] = []
    if not np.any(usable):
        return ranges
    indices = np.flatnonzero(usable)
    split = np.flatnonzero(np.diff(indices) > 1) + 1
    for group in np.split(indices, split):
        if len(group) >= 2:
            ranges.append(
                [float(frequency_hz[group[0]]), float(frequency_hz[group[-1]])]
            )
    return ranges


def analyze_window(
    window: WaveformWindow,
    reference_epoch_s: float,
    band_hz: Sequence[float],
    noise_window_s: Sequence[float],
    block_size_columns: int,
    minimum_robust_peak_z: float,
    minimum_detected_block_fraction: float,
    signal_window_s: Optional[Sequence[float]] = None,
    search_window_s: Optional[Sequence[float]] = None,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]], Dict[str, np.ndarray]]:
    """Measure detectability, channel blocks, adjacency, and empirical bandwidth."""

    relative = window.time_epoch_s - float(reference_epoch_s)
    filtered = bandpass_matrix(window.data, window.sample_rate_hz, band_hz)
    noise_mask = _window_mask(relative, noise_window_s)
    if np.count_nonzero(noise_mask) < int(window.sample_rate_hz):
        raise ValueError("noise window contains fewer than one second of data")
    array_envelope = np.sqrt(np.mean(filtered ** 2, axis=1))
    if search_window_s is not None:
        search_mask = _window_mask(relative, search_window_s)
        if not np.any(search_mask):
            raise ValueError("empty peak-search window")
        search_indices = np.flatnonzero(search_mask)
        peak_index = int(search_indices[np.argmax(array_envelope[search_mask])])
        peak_relative_s = float(relative[peak_index])
        signal_window_used = [peak_relative_s - 0.25, peak_relative_s + 2.75]
    elif signal_window_s is not None:
        signal_mask_initial = _window_mask(relative, signal_window_s)
        if not np.any(signal_mask_initial):
            raise ValueError("empty signal window")
        signal_indices = np.flatnonzero(signal_mask_initial)
        peak_index = int(
            signal_indices[np.argmax(array_envelope[signal_mask_initial])]
        )
        peak_relative_s = float(relative[peak_index])
        signal_window_used = list(map(float, signal_window_s))
    else:
        raise ValueError("signal_window_s or search_window_s is required")
    signal_mask = _window_mask(relative, signal_window_used)
    if np.count_nonzero(signal_mask) < int(0.5 * window.sample_rate_hz):
        raise ValueError("selected signal window is too short")

    noise_rms = np.sqrt(np.mean(filtered[noise_mask] ** 2, axis=0))
    signal_rms = np.sqrt(np.mean(filtered[signal_mask] ** 2, axis=0))
    channel_snr = signal_rms / (noise_rms + np.finfo(np.float32).eps)
    peak_z = robust_z(array_envelope[peak_index], array_envelope[noise_mask])
    adjacent = _adjacent_correlations(filtered[signal_mask])

    block_rows: List[Dict[str, Any]] = []
    block_ids = window.column_indices // int(block_size_columns)
    for block_id in np.unique(block_ids):
        select = block_ids == block_id
        block_envelope = np.sqrt(np.mean(filtered[:, select] ** 2, axis=1))
        block_peak = float(np.max(block_envelope[signal_mask]))
        block_peak_z = robust_z(block_peak, block_envelope[noise_mask])
        block_rows.append(
            {
                "block_id": int(block_id),
                "column_start": int(np.min(window.column_indices[select])),
                "column_end": int(np.max(window.column_indices[select])),
                "locus_start": int(np.min(window.locus_indices[select])),
                "locus_end": int(np.max(window.locus_indices[select])),
                "sampled_channel_count": int(np.count_nonzero(select)),
                "median_channel_snr": float(np.median(channel_snr[select])),
                "peak_robust_z": block_peak_z,
                "detected": block_peak_z >= float(minimum_robust_peak_z),
            }
        )
    detected_fraction = float(
        np.mean([row["detected"] for row in block_rows]) if block_rows else 0.0
    )

    nperseg = min(1024, int(np.count_nonzero(noise_mask)), int(np.count_nonzero(signal_mask)))
    frequency, signal_psd = welch(
        filtered[signal_mask],
        fs=window.sample_rate_hz,
        nperseg=nperseg,
        axis=0,
        detrend="constant",
    )
    _, noise_psd = welch(
        filtered[noise_mask],
        fs=window.sample_rate_hz,
        nperseg=nperseg,
        axis=0,
        detrend="constant",
    )
    median_signal_psd = np.median(signal_psd, axis=1)
    median_noise_psd = np.median(noise_psd, axis=1)
    power_ratio = median_signal_psd / (
        median_noise_psd + np.finfo(np.float32).eps
    )
    usable_ranges = _usable_frequency_ranges(frequency, power_ratio)
    detected = bool(
        peak_z >= float(minimum_robust_peak_z)
        and detected_fraction >= float(minimum_detected_block_fraction)
    )
    summary = {
        "status": PASS if detected else STOP,
        "detected": detected,
        "peak_relative_s": peak_relative_s,
        "peak_robust_z": float(peak_z),
        "median_channel_snr": float(np.median(channel_snr)),
        "p10_channel_snr": float(np.percentile(channel_snr, 10)),
        "detected_block_fraction": detected_fraction,
        "block_count": len(block_rows),
        "median_adjacent_correlation": float(np.nanmedian(adjacent)),
        "median_absolute_adjacent_correlation": float(np.nanmedian(np.abs(adjacent))),
        "signal_window_used_s": signal_window_used,
        "noise_window_s": list(map(float, noise_window_s)),
        "band_hz": list(map(float, band_hz)),
        "sample_rate_hz": float(window.sample_rate_hz),
        "sampled_channel_count": int(window.data.shape[1]),
        "column_start": int(window.column_indices[0]),
        "column_end": int(window.column_indices[-1]),
        "locus_start": int(window.locus_indices[0]),
        "locus_end": int(window.locus_indices[-1]),
        "maximum_gap_s": float(window.maximum_gap_s),
        "missing_fraction": float(window.missing_fraction),
        "unit": window.unit,
        "usable_power_snr_ranges_hz": usable_ranges,
        "spatial_axis": "OptaSense locus index; physical trajectory unavailable",
    }
    arrays = {
        "relative_time_s": relative,
        "filtered": filtered,
        "noise_rms": noise_rms,
        "channel_snr": channel_snr,
        "array_envelope": array_envelope,
        "frequency_hz": frequency,
        "median_signal_psd": median_signal_psd,
        "median_noise_psd": median_noise_psd,
        "power_ratio": power_ratio,
    }
    return summary, block_rows, arrays


def write_cache(
    path: Path,
    window: WaveformWindow,
    arrays: Mapping[str, np.ndarray],
    metadata: Mapping[str, Any],
    target_display_rate_hz: float = 250.0,
) -> None:
    """Write a compact filtered display cache; authoritative metrics stay in CSV/JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    down = max(1, int(round(window.sample_rate_hz / target_display_rate_hz)))
    filtered = np.asarray(arrays["filtered"], dtype=np.float32)
    if down > 1:
        display = np.asarray(resample_poly(filtered, 1, down, axis=0), dtype=np.float32)
        display_time = np.linspace(
            float(arrays["relative_time_s"][0]),
            float(arrays["relative_time_s"][-1]),
            display.shape[0],
        )
        display_rate = window.sample_rate_hz / down
    else:
        display = filtered
        display_time = np.asarray(arrays["relative_time_s"], dtype=float)
        display_rate = window.sample_rate_hz
    np.savez_compressed(
        path,
        filtered_phase=display,
        relative_time_s=display_time,
        display_sample_rate_hz=np.asarray(display_rate),
        column_indices=window.column_indices,
        locus_indices=window.locus_indices,
        noise_rms=np.asarray(arrays["noise_rms"], dtype=np.float32),
        channel_snr=np.asarray(arrays["channel_snr"], dtype=np.float32),
        frequency_hz=np.asarray(arrays["frequency_hz"], dtype=np.float32),
        median_signal_psd=np.asarray(arrays["median_signal_psd"], dtype=np.float32),
        median_noise_psd=np.asarray(arrays["median_noise_psd"], dtype=np.float32),
        metadata_json=np.asarray(json.dumps(dict(metadata), sort_keys=True)),
    )

