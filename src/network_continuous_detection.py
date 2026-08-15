"""Continuous multi-template network detection for the development interval.

Waveform amplitudes are never compared across instruments.  Every component is
band-limited and scored with a sliding, zero-mean normalized correlation.
Components are collapsed to a station median, stations receive equal weight,
and the template bank uses the maximum eligible historical-template score.

The detection threshold is calibrated from deterministic independent circular
shifts of the station score curves.  This preserves each station's score
distribution while destroying network-wide timing coherence.  It is a
development-interval false-alarm control, not held-out performance or FDR.
"""

from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np
from obspy import Stream
from scipy.signal import butter, fftconvolve, find_peaks, sosfiltfilt

from .common import iso_utc, parse_utc
from .hrsn import trace_key


def _bandpass(
    values: np.ndarray,
    sample_rate_hz: float,
    band_hz: Sequence[float],
) -> np.ndarray:
    low, high = map(float, band_hz)
    if not 0.0 < low < high < 0.5 * sample_rate_hz:
        raise ValueError(
            "invalid band {} for sample rate {}".format(
                list(band_hz), sample_rate_hz
            )
        )
    sos = butter(
        4,
        [low, high],
        btype="bandpass",
        fs=sample_rate_hz,
        output="sos",
    )
    return sosfiltfilt(sos, np.asarray(values, dtype=np.float64))


def _station_id(key: str) -> str:
    fields = key.split(".")
    return ".".join(fields[:2]) if len(fields) >= 2 else key


def _best_location_stream(traces: Sequence[Any]) -> Stream:
    """Choose one location-code epoch, then merge only small internal gaps."""

    by_location: Dict[str, List[Any]] = defaultdict(list)
    for trace in traces:
        by_location[str(trace.stats.location)].append(trace)
    if not by_location:
        return Stream()
    location = max(
        sorted(by_location),
        key=lambda item: sum(
            int(trace.stats.npts) for trace in by_location[item]
        ),
    )
    selected = Stream(
        traces=[trace.copy() for trace in by_location[location]]
    )
    selected.sort()
    return selected


def _merged_trace(
    traces: Sequence[Any], maximum_gap_s: float
) -> Tuple[Any | None, float, str]:
    selected = _best_location_stream(traces)
    if not selected:
        return None, float("nan"), "no_trace"
    positive_gaps = [
        float(row[6]) for row in selected.get_gaps() if float(row[6]) > 0.0
    ]
    maximum_gap = max(positive_gaps, default=0.0)
    if maximum_gap > float(maximum_gap_s):
        return None, maximum_gap, "gap_exceeds_limit"
    try:
        selected.merge(method=1, fill_value="interpolate")
    except Exception as exc:
        return None, maximum_gap, "merge_error:{}".format(type(exc).__name__)
    if len(selected) != 1:
        return None, maximum_gap, "merge_did_not_yield_one_trace"
    trace = selected[0]
    if np.ma.isMaskedArray(trace.data) and np.any(
        np.ma.getmaskarray(trace.data)
    ):
        return None, maximum_gap, "masked_samples_after_merge"
    return trace, maximum_gap, "usable"


def _filtered_uniform_data(
    trace: Any,
    target_start_s: float,
    target_count: int,
    target_sample_rate_hz: float,
    band_hz: Sequence[float],
) -> np.ndarray | None:
    """Filter at native rate, then interpolate onto the common target grid."""

    native_rate = float(trace.stats.sampling_rate)
    if float(band_hz[1]) >= 0.5 * native_rate:
        return None
    target_epoch = (
        float(target_start_s)
        + np.arange(int(target_count), dtype=np.float64)
        / float(target_sample_rate_hz)
    )
    source_start = float(trace.stats.starttime)
    source_epoch = (
        source_start
        + np.arange(int(trace.stats.npts), dtype=np.float64) / native_rate
    )
    tolerance = 1.5 / native_rate
    if (
        source_epoch[0] > target_epoch[0] + tolerance
        or source_epoch[-1] < target_epoch[-1] - tolerance
    ):
        return None
    values = np.asarray(trace.data, dtype=np.float64)
    values = values - float(np.mean(values))
    filtered = _bandpass(values, native_rate, band_hz)
    return np.interp(target_epoch, source_epoch, filtered).astype(
        np.float32
    )


def prepare_continuous_traces(
    stream: Stream,
    parent: Mapping[str, Any],
    development: Mapping[str, Any],
) -> Tuple[Dict[str, np.ndarray], List[Dict[str, Any]], float]:
    """Return strict, common-grid filtered continuous traces and QC rows."""

    settings = development["repeater_template_bank"]
    sample_rate = float(settings["target_sample_rate_hz"])
    band = settings["band_hz"]
    padding = float(
        development["continuous_network"]["request_padding_s"]
    )
    interval = development["interval"]
    request_start = parse_utc(interval["start_utc"]).timestamp() - padding
    request_end = parse_utc(interval["end_utc"]).timestamp() + padding
    target_count = int(round((request_end - request_start) * sample_rate))
    maximum_gap = float(
        development["continuous_network"]["merge_maximum_gap_s"]
    )

    grouped: Dict[str, List[Any]] = defaultdict(list)
    for trace in stream:
        grouped[trace_key(trace)].append(trace)
    prepared: Dict[str, np.ndarray] = {}
    rows: List[Dict[str, Any]] = []
    for key in sorted(grouped):
        trace, gap_s, merge_status = _merged_trace(
            grouped[key], maximum_gap
        )
        if trace is None:
            rows.append(
                {
                    "trace_id": key,
                    "status": "rejected",
                    "reason": merge_status,
                    "maximum_positive_gap_s": gap_s,
                }
            )
            continue
        values = _filtered_uniform_data(
            trace,
            request_start,
            target_count,
            sample_rate,
            band,
        )
        if values is None:
            rows.append(
                {
                    "trace_id": key,
                    "status": "rejected",
                    "reason": "coverage_or_nyquist_failure",
                    "maximum_positive_gap_s": gap_s,
                    "native_sample_rate_hz": float(
                        trace.stats.sampling_rate
                    ),
                }
            )
            continue
        prepared[key] = values
        rows.append(
            {
                "trace_id": key,
                "status": "usable",
                "reason": "",
                "maximum_positive_gap_s": gap_s,
                "native_sample_rate_hz": float(
                    trace.stats.sampling_rate
                ),
                "target_sample_rate_hz": sample_rate,
                "sample_count": len(values),
                "request_start_utc": iso_utc(request_start),
                "request_end_utc": iso_utc(request_end),
            }
        )
    return prepared, rows, request_start


def _longest_trace_by_key(stream: Stream) -> Dict[str, Any]:
    grouped: Dict[str, List[Any]] = defaultdict(list)
    for trace in stream:
        grouped[trace_key(trace)].append(trace)
    selected: Dict[str, Any] = {}
    for key, traces in grouped.items():
        trace, _, status = _merged_trace(traces, maximum_gap_s=0.05)
        if trace is not None and status == "usable":
            selected[key] = trace
    return selected


def prepare_template(
    event: Mapping[str, Any],
    stream: Stream,
    continuous_keys: Sequence[str],
    development: Mapping[str, Any],
) -> Tuple[Dict[str, np.ndarray], Dict[str, float], Dict[str, Any]]:
    """Prepare one historical event template on the common sample grid."""

    settings = development["repeater_template_bank"]
    sample_rate = float(settings["target_sample_rate_hz"])
    band = settings["band_hz"]
    signal_window = list(map(float, settings["template_window_s"]))
    noise_window = list(
        map(float, settings["template_noise_window_s"])
    )
    filter_padding_s = 1.0
    context_start = min(signal_window[0], noise_window[0]) - filter_padding_s
    context_end = max(signal_window[1], noise_window[1]) + filter_padding_s
    context_count = int(round((context_end - context_start) * sample_rate))
    origin_s = parse_utc(str(event["origin_time"])).timestamp()
    traces = _longest_trace_by_key(stream)

    signal_first = int(
        round((signal_window[0] - context_start) * sample_rate)
    )
    signal_last = int(
        round((signal_window[1] - context_start) * sample_rate)
    )
    noise_first = int(
        round((noise_window[0] - context_start) * sample_rate)
    )
    noise_last = int(
        round((noise_window[1] - context_start) * sample_rate)
    )
    minimum_snr = float(settings["minimum_template_trace_snr"])
    templates: Dict[str, np.ndarray] = {}
    snr_by_key: Dict[str, float] = {}
    for key in sorted(set(continuous_keys).intersection(traces)):
        context = _filtered_uniform_data(
            traces[key],
            origin_s + context_start,
            context_count,
            sample_rate,
            band,
        )
        if context is None:
            continue
        signal = np.asarray(
            context[signal_first:signal_last], dtype=np.float64
        )
        noise = np.asarray(
            context[noise_first:noise_last], dtype=np.float64
        )
        signal_rms = float(np.sqrt(np.mean(signal ** 2)))
        noise_rms = float(np.sqrt(np.mean(noise ** 2)))
        snr = signal_rms / (noise_rms + np.finfo(float).eps)
        centered = signal - float(np.mean(signal))
        energy = float(np.dot(centered, centered))
        if (
            not np.isfinite(snr)
            or snr < minimum_snr
            or not np.isfinite(energy)
            or energy <= 0.0
        ):
            continue
        templates[key] = centered.astype(np.float32)
        snr_by_key[key] = snr

    stations = sorted({_station_id(key) for key in templates})
    passes = (
        len(templates) >= int(settings["minimum_components_per_template"])
        and len(stations) >= int(settings["minimum_stations_per_template"])
    )
    row = {
        "event_id": str(event["event_id"]),
        "origin_time": str(event["origin_time"]),
        "sequence_id": str(event["sequence_id"]),
        "usable_component_count": len(templates),
        "usable_station_count": len(stations),
        "median_template_trace_snr": (
            float(np.median(list(snr_by_key.values())))
            if snr_by_key
            else ""
        ),
        "status": "PASS" if passes else "STOP",
        "reason": (
            "eligible_template"
            if passes
            else "insufficient_template_components_or_stations"
        ),
    }
    return templates, snr_by_key, row


def rolling_normalized_correlation(
    continuous: np.ndarray, template: np.ndarray
) -> np.ndarray:
    """Zero-mean normalized sliding correlation, valid-window convention."""

    data = np.asarray(continuous, dtype=np.float64)
    pattern = np.asarray(template, dtype=np.float64)
    pattern = pattern - float(np.mean(pattern))
    length = len(pattern)
    if length < 16 or len(data) < length:
        raise ValueError("invalid continuous/template lengths")
    pattern_energy = float(np.dot(pattern, pattern))
    if not np.isfinite(pattern_energy) or pattern_energy <= 0.0:
        raise ValueError("template has zero or invalid energy")

    numerator = fftconvolve(data, pattern[::-1], mode="valid")
    cumulative = np.concatenate(([0.0], np.cumsum(data, dtype=np.float64)))
    cumulative_sq = np.concatenate(
        ([0.0], np.cumsum(data ** 2, dtype=np.float64))
    )
    window_sum = cumulative[length:] - cumulative[:-length]
    window_sum_sq = cumulative_sq[length:] - cumulative_sq[:-length]
    window_energy = window_sum_sq - window_sum ** 2 / float(length)
    denominator = np.sqrt(
        np.maximum(window_energy, 0.0) * pattern_energy
    )
    result = np.full(numerator.shape, np.nan, dtype=np.float64)
    usable = denominator > np.finfo(float).eps
    result[usable] = numerator[usable] / denominator[usable]
    return np.clip(result, -1.0, 1.0).astype(np.float32)


def template_station_score_matrix(
    continuous: Mapping[str, np.ndarray],
    template: Mapping[str, np.ndarray],
) -> Tuple[np.ndarray, List[str], int]:
    """Return station-median correlation curves for one event template."""

    by_station: Dict[str, List[np.ndarray]] = defaultdict(list)
    component_count = 0
    for key in sorted(set(continuous).intersection(template)):
        curve = rolling_normalized_correlation(
            continuous[key], template[key]
        )
        by_station[_station_id(key)].append(curve)
        component_count += 1
    station_names = sorted(by_station)
    station_curves: List[np.ndarray] = []
    for station in station_names:
        curves = np.stack(by_station[station], axis=0)
        station_curves.append(
            np.median(curves, axis=0).astype(np.float32)
        )
    if not station_curves:
        return np.empty((0, 0), dtype=np.float32), [], component_count
    return np.stack(station_curves, axis=0), station_names, component_count


def scan_indices_and_times(
    request_start_s: float,
    curve_length: int,
    development: Mapping[str, Any],
) -> Tuple[np.ndarray, np.ndarray]:
    settings = development["repeater_template_bank"]
    sample_rate = float(settings["target_sample_rate_hz"])
    score_rate = float(settings["score_sample_rate_hz"])
    ratio = sample_rate / score_rate
    step = int(round(ratio))
    if not math.isclose(ratio, step, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError("target and score sample rates are not integer-related")
    template_start = float(settings["template_window_s"][0])
    start_s = parse_utc(development["interval"]["start_utc"]).timestamp()
    end_s = parse_utc(development["interval"]["end_utc"]).timestamp()
    first = max(
        0,
        int(math.ceil(
            (start_s + template_start - request_start_s) * sample_rate
        )),
    )
    last = min(
        int(curve_length),
        int(math.ceil(
            (end_s + template_start - request_start_s) * sample_rate
        )),
    )
    indices = np.arange(first, last, step, dtype=np.int64)
    origins = request_start_s + indices / sample_rate - template_start
    keep = (origins >= start_s) & (origins < end_s)
    return indices[keep], origins[keep]


def null_bank_maxima(
    station_matrices: Sequence[np.ndarray],
    development: Mapping[str, Any],
) -> np.ndarray:
    """Independent station shifts; returns one bank maximum per replicate."""

    settings = development["null_calibration"]
    score_rate = float(
        development["repeater_template_bank"]["score_sample_rate_hz"]
    )
    replicate_count = int(settings["replicate_count"])
    minimum_shift = int(
        round(float(settings["minimum_absolute_shift_s"]) * score_rate)
    )
    rng = np.random.default_rng(int(settings["random_seed"]))
    if not station_matrices:
        return np.asarray([], dtype=float)
    sample_count = station_matrices[0].shape[1]
    if sample_count <= 2 * minimum_shift:
        raise ValueError("score interval too short for declared null shifts")
    maxima = np.full(replicate_count, -np.inf, dtype=np.float64)
    for matrix in station_matrices:
        if matrix.shape[1] != sample_count:
            raise ValueError("template score matrices have unequal lengths")
        for replicate in range(replicate_count):
            summed = np.zeros(sample_count, dtype=np.float32)
            for station_curve in matrix:
                shift = int(
                    rng.integers(minimum_shift, sample_count - minimum_shift)
                )
                summed += np.roll(station_curve, shift)
            null_curve = summed / float(matrix.shape[0])
            maxima[replicate] = max(
                maxima[replicate], float(np.nanmax(null_curve))
            )
    return maxima


def higher_quantile(values: Sequence[float], quantile: float) -> float:
    ordered = np.sort(np.asarray(values, dtype=np.float64))
    if ordered.size == 0:
        raise ValueError("cannot calculate a quantile of no values")
    if not 0.0 < float(quantile) <= 1.0:
        raise ValueError("quantile must lie in (0, 1]")
    index = max(
        0,
        min(
            ordered.size - 1,
            int(math.ceil(float(quantile) * ordered.size)) - 1,
        ),
    )
    return float(ordered[index])


def detect_candidates(
    origin_epoch_s: np.ndarray,
    template_scores: np.ndarray,
    station_matrices: Sequence[np.ndarray],
    station_names_by_template: Sequence[Sequence[str]],
    template_rows: Sequence[Mapping[str, Any]],
    threshold: float,
    development: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], np.ndarray, np.ndarray]:
    """Find separated bank-score peaks and record best-template support."""

    if template_scores.ndim != 2 or template_scores.shape[0] == 0:
        raise ValueError("template score matrix is empty")
    bank_score = np.max(template_scores, axis=0)
    best_template = np.argmax(template_scores, axis=0)
    score_rate = float(
        development["repeater_template_bank"]["score_sample_rate_hz"]
    )
    minimum_distance = int(
        round(
            float(
                development["repeater_template_bank"][
                    "candidate_minimum_separation_s"
                ]
            )
            * score_rate
        )
    )
    peaks, properties = find_peaks(
        bank_score,
        height=float(threshold),
        distance=max(1, minimum_distance),
    )
    support_threshold = float(
        development["repeater_template_bank"][
            "station_support_recording_threshold"
        ]
    )
    rows: List[Dict[str, Any]] = []
    for peak, height in zip(peaks, properties["peak_heights"]):
        template_index = int(best_template[peak])
        station_matrix = station_matrices[template_index]
        support = int(
            np.sum(station_matrix[:, peak] >= support_threshold)
        )
        template_row = template_rows[template_index]
        rows.append(
            {
                "candidate_id": "network_dev_{:04d}".format(len(rows) + 1),
                "origin_time": iso_utc(float(origin_epoch_s[peak])),
                "origin_epoch_s": float(origin_epoch_s[peak]),
                "bank_score": float(height),
                "threshold": float(threshold),
                "best_template_event_id": str(template_row["event_id"]),
                "best_template_sequence_id": str(
                    template_row["sequence_id"]
                ),
                "best_template_score": float(
                    template_scores[template_index, peak]
                ),
                "best_template_station_count": len(
                    station_names_by_template[template_index]
                ),
                "station_support_count_at_0p2": support,
                "candidate_generation_label": (
                    "network_repeater_template_bank_candidate_not_family_truth"
                ),
            }
        )
    return rows, bank_score, best_template

def _trailing_mean(values: np.ndarray, window_samples: int) -> np.ndarray:
    """Causal trailing mean with NaN until a complete window is present."""

    array = np.asarray(values, dtype=np.float64)
    window = int(window_samples)
    if array.ndim != 1 or window < 1 or window > len(array):
        raise ValueError("invalid trailing-mean input or window")
    cumulative = np.concatenate(
        ([0.0], np.cumsum(array, dtype=np.float64))
    )
    result = np.full(len(array), np.nan, dtype=np.float64)
    result[window - 1 :] = (
        cumulative[window:] - cumulative[:-window]
    ) / float(window)
    return result


def station_energy_ratio_matrix(
    continuous: Mapping[str, np.ndarray],
    sample_rate_hz: float,
    sta_window_s: float,
    lta_window_s: float,
) -> Tuple[np.ndarray, List[str], int]:
    """Return amplitude-invariant station STA/LTA energy ratios.

    The ratio is formed independently for every component before taking the
    station median, so raw amplitudes are never compared across instruments.
    """

    sample_rate = float(sample_rate_hz)
    sta_count = int(round(float(sta_window_s) * sample_rate))
    lta_count = int(round(float(lta_window_s) * sample_rate))
    if sta_count < 1 or lta_count <= sta_count:
        raise ValueError("STA/LTA windows must satisfy 0 < STA < LTA")

    by_station: Dict[str, List[np.ndarray]] = defaultdict(list)
    curve_length: int | None = None
    for key in sorted(continuous):
        values = np.asarray(continuous[key], dtype=np.float64)
        if values.ndim != 1:
            raise ValueError("continuous component must be one-dimensional")
        if curve_length is None:
            curve_length = len(values)
        elif len(values) != curve_length:
            raise ValueError("continuous components have unequal lengths")
        power = values ** 2
        sta = _trailing_mean(power, sta_count)
        lta = _trailing_mean(power, lta_count)
        ratio = np.full(len(values), np.nan, dtype=np.float64)
        usable = (
            np.isfinite(sta)
            & np.isfinite(lta)
            & (lta > np.finfo(float).eps)
        )
        ratio[usable] = np.sqrt(sta[usable] / lta[usable])
        by_station[_station_id(key)].append(ratio.astype(np.float32))

    station_names = sorted(by_station)
    station_curves = [
        np.median(np.stack(by_station[station], axis=0), axis=0).astype(
            np.float32
        )
        for station in station_names
    ]
    if not station_curves:
        return np.empty((0, 0), dtype=np.float32), [], 0
    return (
        np.stack(station_curves, axis=0),
        station_names,
        len(continuous),
    )


def generic_scan_indices_and_times(
    request_start_s: float,
    curve_length: int,
    development: Mapping[str, Any],
) -> Tuple[np.ndarray, np.ndarray]:
    """Return generic-trigger samples strictly inside the dev interval."""

    settings = development["generic_network_trigger"]
    sample_rate = float(
        development["repeater_template_bank"]["target_sample_rate_hz"]
    )
    score_rate = float(settings["score_sample_rate_hz"])
    ratio = sample_rate / score_rate
    step = int(round(ratio))
    if not math.isclose(ratio, step, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError("target and score sample rates are not integer-related")
    start_s = parse_utc(development["interval"]["start_utc"]).timestamp()
    end_s = parse_utc(development["interval"]["end_utc"]).timestamp()
    first = max(
        0, int(math.ceil((start_s - request_start_s) * sample_rate))
    )
    last = min(
        int(curve_length),
        int(math.ceil((end_s - request_start_s) * sample_rate)),
    )
    indices = np.arange(first, last, step, dtype=np.int64)
    epochs = request_start_s + indices / sample_rate
    keep = (epochs >= start_s) & (epochs < end_s)
    return indices[keep], epochs[keep]


def coincidence_score(
    station_matrix: np.ndarray, station_count: int
) -> np.ndarray:
    """Return the kth-highest station characteristic at every sample."""

    matrix = np.asarray(station_matrix, dtype=np.float64)
    count = int(station_count)
    if matrix.ndim != 2 or matrix.shape[1] == 0:
        raise ValueError("station characteristic matrix is empty")
    if count < 1 or count > matrix.shape[0]:
        raise ValueError("invalid coincidence station count")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("station characteristic matrix is not finite")
    rank = matrix.shape[0] - count
    return np.partition(matrix, rank, axis=0)[rank].astype(np.float32)


def null_coincidence_maxima(
    station_matrix: np.ndarray,
    development: Mapping[str, Any],
) -> np.ndarray:
    """Null maxima after independent station circular shifts."""

    settings = development["generic_network_trigger"]
    matrix = np.asarray(station_matrix, dtype=np.float32)
    score_rate = float(settings["score_sample_rate_hz"])
    minimum_shift = int(
        round(float(settings["minimum_absolute_shift_s"]) * score_rate)
    )
    replicate_count = int(settings["replicate_count"])
    sample_count = matrix.shape[1]
    if sample_count <= 2 * minimum_shift:
        raise ValueError("score interval too short for declared null shifts")
    rng = np.random.default_rng(int(settings["random_seed"]))
    maxima = np.empty(replicate_count, dtype=np.float64)
    for replicate in range(replicate_count):
        shifted = np.empty_like(matrix)
        for station_index, station_curve in enumerate(matrix):
            shift = int(
                rng.integers(minimum_shift, sample_count - minimum_shift)
            )
            shifted[station_index] = np.roll(station_curve, shift)
        score = coincidence_score(
            shifted, int(settings["coincidence_station_count"])
        )
        maxima[replicate] = float(np.max(score))
    return maxima


def detect_generic_candidates(
    trigger_epoch_s: np.ndarray,
    station_matrix: np.ndarray,
    threshold: float,
    development: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], np.ndarray]:
    """Find separated generic network-coincidence peaks."""

    settings = development["generic_network_trigger"]
    score = coincidence_score(
        station_matrix, int(settings["coincidence_station_count"])
    )
    minimum_distance = int(
        round(
            float(settings["candidate_minimum_separation_s"])
            * float(settings["score_sample_rate_hz"])
        )
    )
    peaks, properties = find_peaks(
        score,
        height=float(threshold),
        distance=max(1, minimum_distance),
    )
    support_threshold = float(
        settings["station_characteristic_support_threshold"]
    )
    rows: List[Dict[str, Any]] = []
    for peak, height in zip(peaks, properties["peak_heights"]):
        rows.append(
            {
                "candidate_id": "network_generic_dev_{:04d}".format(
                    len(rows) + 1
                ),
                "trigger_time": iso_utc(float(trigger_epoch_s[peak])),
                "trigger_epoch_s": float(trigger_epoch_s[peak]),
                "coincidence_score": float(height),
                "threshold": float(threshold),
                "coincidence_station_count": int(
                    settings["coincidence_station_count"]
                ),
                "station_support_count_at_declared_ratio": int(
                    np.sum(station_matrix[:, peak] >= support_threshold)
                ),
                "station_characteristic_support_threshold": (
                    support_threshold
                ),
                "candidate_generation_label": (
                    "generic_network_candidate_not_origin_or_family_truth"
                ),
            }
        )
    return rows, score
