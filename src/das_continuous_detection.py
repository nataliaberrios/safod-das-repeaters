"""Independent continuous DAS trigger for the registered development interval.

Only the registered DAS configuration, manifest-selected HDF5 files, and
pre-waveform registration products are valid inputs. This module does not read
seismic-network candidates, catalog event times, or held-out interval tables.
All spatial quantities remain HDF5 columns or OptaSense locus indices.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
from scipy.signal import butter, find_peaks, resample_poly, sosfiltfilt

from .common import iso_utc, parse_utc
from .h5io import read_window


def _bandpass_sos(
    sample_rate_hz: float, band_hz: Sequence[float]
) -> np.ndarray:
    low, high = map(float, band_hz)
    if not 0.0 < low < high < 0.5 * float(sample_rate_hz):
        raise ValueError("invalid DAS band for raw sample rate")
    return butter(
        4,
        [low, high],
        btype="bandpass",
        fs=float(sample_rate_hz),
        output="sos",
    )


def _chunk_core_bounds(
    request_start_s: float,
    request_end_s: float,
    chunk_duration_s: float,
) -> List[Tuple[float, float]]:
    if request_end_s <= request_start_s or chunk_duration_s <= 0.0:
        raise ValueError("invalid DAS chunk bounds")
    rows: List[Tuple[float, float]] = []
    start_s = float(request_start_s)
    while start_s < request_end_s:
        end_s = min(request_end_s, start_s + float(chunk_duration_s))
        rows.append((start_s, end_s))
        start_s = end_s
    return rows


def preprocess_registered_chunks(
    paths: Sequence[Any],
    registration: Mapping[str, Any],
) -> Tuple[
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    str,
    List[Dict[str, Any]],
    List[str],
    int,
]:
    """Read, filter, downsample, and stitch only the registered DAS request."""

    interval = registration["interval"]
    preprocessing = registration["preprocessing"]
    channels = registration["channel_sampling"]
    raw_settings = registration["manifest"]["primary_configuration"]
    start_s = parse_utc(str(interval["start_utc"])).timestamp()
    end_s = parse_utc(str(interval["end_utc"])).timestamp()
    padding_s = float(interval["filter_padding_s"])
    request_start_s = start_s - padding_s
    request_end_s = end_s + padding_s

    raw_rate = float(raw_settings["sample_rate_hz"])
    target_rate = float(preprocessing["target_sample_rate_hz"])
    rate_ratio = raw_rate / target_rate
    down = int(round(rate_ratio))
    if not math.isclose(rate_ratio, down, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError("raw and target DAS sample rates are incompatible")
    sos = _bandpass_sos(raw_rate, preprocessing["primary_band_hz"])
    chunk_duration_s = float(preprocessing["chunk_duration_s"])
    overlap_s = float(preprocessing["chunk_filter_overlap_s"])
    maximum_missing = float(
        preprocessing["maximum_chunk_missing_fraction"]
    )
    maximum_sample_interval = float(
        preprocessing["maximum_sample_interval_s"]
    )
    uniformity_tolerance = float(
        preprocessing["timestamp_uniformity_tolerance_s"]
    )

    expected_columns = np.arange(
        int(channels["column_start"]),
        int(channels["column_stop"]),
        int(channels["column_stride"]),
        dtype=np.int64,
    )
    data_parts: List[np.ndarray] = []
    epoch_parts: List[np.ndarray] = []
    chunk_rows: List[Dict[str, Any]] = []
    reference_columns: np.ndarray | None = None
    reference_loci: np.ndarray | None = None
    reference_unit = ""
    unique_files: set[str] = set()
    file_use_count = 0

    for chunk_index, (core_start_s, core_end_s) in enumerate(
        _chunk_core_bounds(
            request_start_s, request_end_s, chunk_duration_s
        ),
        start=1,
    ):
        read_start_s = max(request_start_s, core_start_s - overlap_s)
        read_end_s = min(request_end_s, core_end_s + overlap_s)
        window = read_window(
            paths,
            read_start_s,
            read_end_s,
            channel_start=int(channels["column_start"]),
            channel_stop=int(channels["column_stop"]),
            channel_stride=int(channels["column_stride"]),
        )
        if not math.isclose(
            float(window.sample_rate_hz),
            raw_rate,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        ):
            raise RuntimeError("raw DAS sample rate differs from registration")
        if window.unit != "processed_optical_phase_rad":
            raise RuntimeError(
                "registered DAS raw-unit policy failed: {}".format(window.unit)
            )
        if float(window.missing_fraction) > maximum_missing:
            raise RuntimeError(
                "DAS chunk missing fraction exceeds registration"
            )
        raw_steps = np.diff(window.time_epoch_s)
        maximum_raw_step = (
            float(np.max(raw_steps)) if raw_steps.size else float("inf")
        )
        maximum_uniformity_residual = (
            float(np.max(np.abs(raw_steps - 1.0 / raw_rate)))
            if raw_steps.size
            else float("inf")
        )
        if maximum_raw_step > maximum_sample_interval:
            raise RuntimeError("DAS raw sample interval exceeds registration")
        if maximum_uniformity_residual > uniformity_tolerance:
            raise RuntimeError("DAS raw timestamp grid is not uniform")
        if not np.array_equal(window.column_indices, expected_columns):
            raise RuntimeError("DAS sampled columns differ from registration")
        if reference_columns is None:
            reference_columns = window.column_indices.copy()
            reference_loci = window.locus_indices.copy()
            reference_unit = str(window.unit)
        else:
            if not np.array_equal(reference_columns, window.column_indices):
                raise RuntimeError("DAS column mapping changed between chunks")
            if not np.array_equal(reference_loci, window.locus_indices):
                raise RuntimeError("DAS locus mapping changed between chunks")
            if reference_unit != str(window.unit):
                raise RuntimeError("DAS unit changed between chunks")

        values = np.asarray(window.data, dtype=np.float32)
        means = np.mean(values, axis=0, dtype=np.float64).astype(np.float32)
        values = values - means
        filtered = sosfiltfilt(sos, values, axis=0)
        resampled = np.asarray(
            resample_poly(filtered, 1, down, axis=0),
            dtype=np.float32,
        )
        resampled_epoch_s = (
            float(window.time_epoch_s[0])
            + np.arange(resampled.shape[0], dtype=np.float64) / target_rate
        )
        keep = (
            (resampled_epoch_s >= core_start_s)
            & (resampled_epoch_s < core_end_s)
        )
        core_data = resampled[keep]
        core_epoch_s = resampled_epoch_s[keep]
        expected_core_count = int(
            round((core_end_s - core_start_s) * target_rate)
        )
        if len(core_epoch_s) != expected_core_count:
            raise RuntimeError(
                "resampled DAS chunk has {} samples; expected {}".format(
                    len(core_epoch_s), expected_core_count
                )
            )
        if not np.all(np.isfinite(core_data)):
            raise RuntimeError("nonfinite value after DAS preprocessing")
        data_parts.append(core_data)
        epoch_parts.append(core_epoch_s)
        unique_files.update(map(str, window.source_files))
        file_use_count += len(window.source_files)
        chunk_rows.append(
            {
                "chunk_id": "das_chunk_{:03d}".format(chunk_index),
                "core_start_utc": iso_utc(core_start_s),
                "core_end_utc": iso_utc(core_end_s),
                "read_start_utc": iso_utc(read_start_s),
                "read_end_utc": iso_utc(read_end_s),
                "raw_sample_count": len(window.time_epoch_s),
                "target_core_sample_count": len(core_epoch_s),
                "sampled_channel_count": core_data.shape[1],
                "source_file_count": len(window.source_files),
                "source_files": ";".join(map(str, window.source_files)),
                "raw_missing_fraction": float(window.missing_fraction),
                "maximum_raw_sample_interval_s": maximum_raw_step,
                "maximum_timestamp_uniformity_residual_s": (
                    maximum_uniformity_residual
                ),
                "unit": str(window.unit),
                "status": "PASS",
            }
        )

    if reference_columns is None or reference_loci is None:
        raise RuntimeError("no registered DAS chunk was processed")
    data = np.concatenate(data_parts, axis=0)
    epoch_s = np.concatenate(epoch_parts)
    expected_total = int(
        round((request_end_s - request_start_s) * target_rate)
    )
    if data.shape != (expected_total, len(expected_columns)):
        raise RuntimeError(
            "stitched DAS shape {} differs from expected {}".format(
                data.shape, (expected_total, len(expected_columns))
            )
        )
    steps = np.diff(epoch_s)
    if (
        np.any(steps <= 0.0)
        or np.max(np.abs(steps - 1.0 / target_rate)) > (
            uniformity_tolerance + 1.0e-7
        )
    ):
        raise RuntimeError("stitched target DAS grid is not uniform")

    return (
        data,
        epoch_s,
        reference_columns,
        reference_loci,
        reference_unit,
        chunk_rows,
        sorted(unique_files),
        file_use_count,
    )


def channel_qc(
    data: np.ndarray,
    columns: np.ndarray,
    loci: np.ndarray,
) -> Tuple[np.ndarray, np.ndarray, List[Dict[str, Any]]]:
    """Apply only finite/nonzero QC; amplitude-based selection is forbidden."""

    values = np.asarray(data, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] != len(columns):
        raise ValueError("invalid DAS array for channel QC")
    sum_squares = np.zeros(values.shape[1], dtype=np.float64)
    finite = np.ones(values.shape[1], dtype=bool)
    for first in range(0, values.shape[0], 10000):
        block = np.asarray(values[first : first + 10000], dtype=np.float64)
        finite &= np.all(np.isfinite(block), axis=0)
        sum_squares += np.einsum("ij,ij->j", block, block)
    rms = np.sqrt(sum_squares / float(values.shape[0]))
    nonzero = rms > np.finfo(np.float32).tiny
    usable = finite & nonzero

    rows: List[Dict[str, Any]] = []
    for index, column in enumerate(columns):
        if not finite[index]:
            reason = "nonfinite_filtered_samples"
        elif not nonzero[index]:
            reason = "zero_filtered_rms"
        else:
            reason = ""
        rows.append(
            {
                "column_index": int(column),
                "locus_index": int(loci[index]),
                "filtered_rms_phase_rad": float(rms[index]),
                "finite": bool(finite[index]),
                "nonzero": bool(nonzero[index]),
                "usable": bool(usable[index]),
                "reason": reason,
                "amplitude_based_selection_used": False,
            }
        )
    return usable, rms, rows


def _trailing_mean(values: np.ndarray, window_samples: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    window = int(window_samples)
    if array.ndim != 1 or window < 1 or window > len(array):
        raise ValueError("invalid DAS trailing-mean input")
    cumulative = np.concatenate(
        ([0.0], np.cumsum(array, dtype=np.float64))
    )
    result = np.full(len(array), np.nan, dtype=np.float64)
    result[window - 1 :] = (
        cumulative[window:] - cumulative[:-window]
    ) / float(window)
    return result


def channel_energy_ratio(
    values: np.ndarray,
    sample_rate_hz: float,
    sta_window_s: float,
    lta_window_s: float,
) -> np.ndarray:
    """Return the amplitude-invariant trailing energy ratio for one channel."""

    sta_count = int(round(float(sta_window_s) * float(sample_rate_hz)))
    lta_count = int(round(float(lta_window_s) * float(sample_rate_hz)))
    if sta_count < 1 or lta_count <= sta_count:
        raise ValueError("DAS STA/LTA windows must satisfy 0 < STA < LTA")
    power = np.asarray(values, dtype=np.float64) ** 2
    sta = _trailing_mean(power, sta_count)
    lta = _trailing_mean(power, lta_count)
    ratio = np.full(len(power), np.nan, dtype=np.float64)
    valid = np.isfinite(sta) & np.isfinite(lta) & (
        lta > np.finfo(float).eps
    )
    ratio[valid] = np.sqrt(sta[valid] / lta[valid])
    return ratio.astype(np.float32)


def block_characteristic_matrix(
    data: np.ndarray,
    columns: np.ndarray,
    loci: np.ndarray,
    usable_channels: np.ndarray,
    channel_rms: np.ndarray,
    registration: Mapping[str, Any],
) -> Tuple[np.ndarray, List[int], List[Dict[str, Any]]]:
    """Collapse per-channel ratios to contiguous block medians."""

    channel = registration["channel_sampling"]
    qc = registration["channel_qc"]
    trigger = registration["generic_array_trigger"]
    target_rate = float(
        registration["preprocessing"]["target_sample_rate_hz"]
    )
    block_width = int(channel["block_width_columns"])
    block_ids = columns // block_width
    curves: List[np.ndarray] = []
    usable_block_ids: List[int] = []
    rows: List[Dict[str, Any]] = []
    minimum_channels = int(qc["minimum_usable_channels_per_block"])

    for block_id in sorted(map(int, np.unique(block_ids))):
        in_block = block_ids == block_id
        usable = in_block & usable_channels
        selected_indices = np.flatnonzero(usable)
        status = (
            "PASS" if len(selected_indices) >= minimum_channels else "STOP"
        )
        if status == "PASS":
            ratios = np.stack(
                [
                    channel_energy_ratio(
                        data[:, index],
                        target_rate,
                        float(trigger["sta_window_s"]),
                        float(trigger["lta_window_s"]),
                    )
                    for index in selected_indices
                ],
                axis=0,
            )
            lta_count = int(
                round(float(trigger["lta_window_s"]) * target_rate)
            )
            curve = np.full(data.shape[0], np.nan, dtype=np.float32)
            curve[lta_count - 1 :] = np.median(
                ratios[:, lta_count - 1 :], axis=0
            ).astype(np.float32)
            curves.append(curve)
            usable_block_ids.append(block_id)
        rows.append(
            {
                "block_id": block_id,
                "column_start": int(np.min(columns[in_block])),
                "column_end": int(np.max(columns[in_block])),
                "locus_start": int(np.min(loci[in_block])),
                "locus_end": int(np.max(loci[in_block])),
                "sampled_channel_count": int(np.count_nonzero(in_block)),
                "usable_channel_count": len(selected_indices),
                "median_filtered_rms_phase_rad": float(
                    np.median(channel_rms[in_block])
                ),
                "minimum_usable_channels": minimum_channels,
                "status": status,
            }
        )

    if len(curves) < int(qc["minimum_usable_blocks"]):
        raise RuntimeError("too few usable DAS blocks")
    return np.stack(curves, axis=0), usable_block_ids, rows


def score_interval(
    block_matrix: np.ndarray,
    epoch_s: np.ndarray,
    registration: Mapping[str, Any],
) -> Tuple[np.ndarray, np.ndarray]:
    """Trim to the registered interval and sample block curves at score rate."""

    start_s = parse_utc(
        str(registration["interval"]["start_utc"])
    ).timestamp()
    end_s = parse_utc(
        str(registration["interval"]["end_utc"])
    ).timestamp()
    target_rate = float(
        registration["preprocessing"]["target_sample_rate_hz"]
    )
    score_rate = float(
        registration["preprocessing"]["score_sample_rate_hz"]
    )
    step = int(round(target_rate / score_rate))
    selected = np.flatnonzero((epoch_s >= start_s) & (epoch_s < end_s))
    expected = int(round((end_s - start_s) * target_rate))
    if len(selected) != expected:
        raise RuntimeError("DAS target-grid interval sample count is wrong")
    selected = selected[::step]
    scores = np.asarray(block_matrix[:, selected], dtype=np.float32)
    if not np.all(np.isfinite(scores)):
        raise RuntimeError("DAS block score contains nonfinite values")
    return np.asarray(epoch_s[selected], dtype=np.float64), scores


def coincidence_score(
    block_matrix: np.ndarray, block_count: int
) -> np.ndarray:
    matrix = np.asarray(block_matrix, dtype=np.float32)
    count = int(block_count)
    if matrix.ndim != 2 or matrix.shape[1] == 0:
        raise ValueError("DAS block matrix is empty")
    if count < 1 or count > matrix.shape[0]:
        raise ValueError("invalid DAS coincidence block count")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("DAS block matrix is not finite")
    rank = matrix.shape[0] - count
    return np.partition(matrix, rank, axis=0)[rank].astype(np.float32)


def null_block_maxima(
    block_matrix: np.ndarray,
    registration: Mapping[str, Any],
) -> np.ndarray:
    settings = registration["null_calibration"]
    score_rate = float(
        registration["preprocessing"]["score_sample_rate_hz"]
    )
    minimum_shift = int(
        round(float(settings["minimum_absolute_shift_s"]) * score_rate)
    )
    replicate_count = int(settings["replicate_count"])
    sample_count = block_matrix.shape[1]
    if sample_count <= 2 * minimum_shift:
        raise ValueError("DAS score interval is too short for null shifts")
    rng = np.random.default_rng(int(settings["random_seed"]))
    count = int(
        registration["generic_array_trigger"]["block_coincidence_count"]
    )
    maxima = np.empty(replicate_count, dtype=np.float64)
    for replicate in range(replicate_count):
        shifted = np.empty_like(block_matrix)
        for block_index, curve in enumerate(block_matrix):
            shift = int(
                rng.integers(minimum_shift, sample_count - minimum_shift)
            )
            shifted[block_index] = np.roll(curve, shift)
        maxima[replicate] = float(
            np.max(coincidence_score(shifted, count))
        )
    return maxima


def higher_quantile(values: Sequence[float], quantile: float) -> float:
    ordered = np.sort(np.asarray(values, dtype=np.float64))
    if ordered.size == 0:
        raise ValueError("cannot calculate DAS quantile of no values")
    if not 0.0 < float(quantile) <= 1.0:
        raise ValueError("DAS quantile must lie in (0, 1]")
    index = max(
        0,
        min(
            ordered.size - 1,
            int(math.ceil(float(quantile) * ordered.size)) - 1,
        ),
    )
    return float(ordered[index])


def detect_das_candidates(
    trigger_epoch_s: np.ndarray,
    block_matrix: np.ndarray,
    threshold: float,
    registration: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], np.ndarray]:
    """Detect separated raw DAS arrivals without event or family labels."""

    trigger = registration["generic_array_trigger"]
    score_rate = float(
        registration["preprocessing"]["score_sample_rate_hz"]
    )
    coincidence_count = int(trigger["block_coincidence_count"])
    score = coincidence_score(block_matrix, coincidence_count)
    minimum_distance = int(
        round(float(trigger["candidate_minimum_separation_s"]) * score_rate)
    )
    peaks, properties = find_peaks(
        score,
        height=float(threshold),
        distance=max(1, minimum_distance),
    )
    support_ratio = float(trigger["block_support_ratio"])
    rows: List[Dict[str, Any]] = []
    for peak, height in zip(peaks, properties["peak_heights"]):
        rows.append(
            {
                "candidate_id": "das_generic_dev_{:04d}".format(
                    len(rows) + 1
                ),
                "trigger_time": iso_utc(float(trigger_epoch_s[peak])),
                "trigger_epoch_s": float(trigger_epoch_s[peak]),
                "coincidence_score": float(height),
                "threshold": float(threshold),
                "coincidence_block_count": coincidence_count,
                "block_support_count_at_declared_ratio": int(
                    np.sum(block_matrix[:, peak] >= support_ratio)
                ),
                "block_characteristic_support_threshold": support_ratio,
                "candidate_generation_label": (
                    "raw_DAS_only_candidate_not_origin_event_or_family_truth"
                ),
                "network_candidate_time_fields_read": 0,
                "catalog_event_time_fields_read": 0,
                "heldout_interval_fields_read": 0,
                "family_assignment": "not_assigned",
            }
        )
    return rows, score
