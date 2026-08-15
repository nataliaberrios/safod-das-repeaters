"""HRSN waveform-similarity features with explicit data-quality gates.

The implementation uses uncorrected counts only for normalized waveform shape.
It does not use amplitudes for moment or stress drop.  Each candidate is tested on
multiple stations, components, and broad/overlapping bands with a frozen lag and
SNR rule. Catalog proximity only determines which events are tested. Pairwise
similarity alone is not treated as a family label.
"""

from __future__ import annotations

import csv
import json
import math
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

import numpy as np
from obspy import Stream, UTCDateTime, read
from scipy.signal import butter, correlate, sosfiltfilt

from .common import CONDITIONAL, PASS, STOP, iso_utc, parse_utc, utc_now, write_json


def trace_key(trace: Any) -> str:
    """Cross-epoch physical component key; location-code epochs are metadata."""

    return ".".join(
        [
            trace.stats.network,
            trace.stats.station,
            trace.stats.channel,
        ]
    )


def _request_url(event: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    settings = config["hrsn"]
    origin = parse_utc(event["origin_time"]).timestamp()
    request_window = settings["request_window_s"]
    params = {
        "net": settings["network"],
        "sta": ",".join(settings["stations"]),
        "loc": settings["location"],
        "cha": ",".join(settings["channels"]),
        "start": iso_utc(origin + float(request_window[0])),
        "end": iso_utc(origin + float(request_window[1])),
    }
    return settings["dataselect_service"] + "?" + urllib.parse.urlencode(params)


def cache_paths(cache_dir: Path, event_id: str) -> Tuple[Path, Path]:
    return cache_dir / (str(event_id) + ".mseed"), cache_dir / (
        str(event_id) + ".provenance.json"
    )


def download_event(
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    cache_dir: Path,
    force: bool = False,
    timeout_s: float = 90.0,
) -> Path:
    """Download a compact official NCEDC miniSEED event window with a sidecar."""

    cache_dir.mkdir(parents=True, exist_ok=True)
    waveform_path, provenance_path = cache_paths(cache_dir, str(event["event_id"]))
    url = _request_url(event, config)
    if waveform_path.exists() and waveform_path.stat().st_size > 0 and not force:
        try:
            with provenance_path.open("r", encoding="utf-8") as handle:
                cached_provenance = json.load(handle)
            if cached_provenance.get("request_url") == url:
                return waveform_path
        except (OSError, ValueError, json.JSONDecodeError):
            pass
    request = urllib.request.Request(
        url, headers={"User-Agent": "safod-repeaters-v2/0.1"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = response.read()
    if not payload:
        raise RuntimeError("empty HRSN response for event {}".format(event["event_id"]))
    temporary = waveform_path.with_suffix(".mseed.partial")
    with temporary.open("wb") as handle:
        handle.write(payload)
    # Validate before exposing the cache entry.
    stream = read(str(temporary))
    if not stream:
        temporary.unlink(missing_ok=True)
        raise RuntimeError("unreadable HRSN response for event {}".format(event["event_id"]))
    temporary.replace(waveform_path)
    write_json(
        provenance_path,
        {
            "event_id": str(event["event_id"]),
            "origin_time": event["origin_time"],
            "request_url": url,
            "retrieved_utc": utc_now(),
            "byte_count": len(payload),
            "config_sha256": config["_config_sha256"],
            "scientific_use": "normalized waveform-shape verification only",
        },
    )
    return waveform_path


def load_stream(path: Path) -> Stream:
    stream = read(str(path))
    stream.sort()
    return stream


def _longest_trace_by_key(stream: Stream) -> Dict[str, Any]:
    selected: Dict[str, Any] = {}
    for trace in stream:
        key = trace_key(trace)
        if key not in selected or trace.stats.npts > selected[key].stats.npts:
            selected[key] = trace
    return selected


def _relative_samples(
    trace: Any, origin_time: str, window_s: Sequence[float]
) -> Optional[np.ndarray]:
    """Interpolate a trace onto an origin-relative grid without bridging gaps."""

    fs = float(trace.stats.sampling_rate)
    start_rel = float(window_s[0])
    end_rel = float(window_s[1])
    origin = UTCDateTime(origin_time)
    absolute_start = origin + start_rel
    absolute_end = origin + end_rel
    tolerance = 1.5 / fs
    if trace.stats.starttime > absolute_start + tolerance:
        return None
    if trace.stats.endtime < absolute_end - tolerance:
        return None
    count = int(round((end_rel - start_rel) * fs))
    if count < 16:
        return None
    target_epoch = float(origin) + start_rel + np.arange(count) / fs
    source_epoch = float(trace.stats.starttime) + np.arange(trace.stats.npts) / fs
    data = np.asarray(trace.data, dtype=np.float64)
    if np.ma.isMaskedArray(trace.data) and np.any(np.ma.getmaskarray(trace.data)):
        return None
    return np.interp(target_epoch, source_epoch, data)


def _bandpass(data: np.ndarray, sample_rate_hz: float, band_hz: Sequence[float]) -> np.ndarray:
    low, high = map(float, band_hz)
    nyquist = 0.5 * sample_rate_hz
    if not (0.0 < low < high < nyquist):
        raise ValueError("invalid band {} for fs {}".format(band_hz, sample_rate_hz))
    sos = butter(4, [low, high], btype="bandpass", fs=sample_rate_hz, output="sos")
    return sosfiltfilt(sos, data)


def _normalized_peak(
    first: np.ndarray, second: np.ndarray, sample_rate_hz: float, max_lag_s: float
) -> Tuple[float, float]:
    count = min(len(first), len(second))
    first = first[:count] - np.mean(first[:count])
    second = second[:count] - np.mean(second[:count])
    denominator = math.sqrt(float(np.dot(first, first) * np.dot(second, second)))
    if denominator <= 0.0:
        return float("nan"), float("nan")
    values = correlate(first, second, mode="full", method="fft") / denominator
    lags = np.arange(-len(second) + 1, len(first))
    keep = np.abs(lags) <= int(round(max_lag_s * sample_rate_hz))
    index = int(np.argmax(values[keep]))
    return float(values[keep][index]), float(lags[keep][index] / sample_rate_hz)


def compare_events(
    reference_event: Mapping[str, Any],
    comparison_event: Mapping[str, Any],
    reference_stream: Stream,
    comparison_stream: Stream,
    config: Mapping[str, Any],
    pair_name: str,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    settings = config["hrsn"]
    ref_traces = _longest_trace_by_key(reference_stream)
    cmp_traces = _longest_trace_by_key(comparison_stream)
    metrics: List[Dict[str, Any]] = []
    band_values: Dict[str, List[float]] = defaultdict(list)
    band_stations: Dict[str, set] = defaultdict(set)
    for key in sorted(set(ref_traces).intersection(cmp_traces)):
        ref = ref_traces[key]
        cmp = cmp_traces[key]
        if abs(float(ref.stats.sampling_rate) - float(cmp.stats.sampling_rate)) > 1.0e-6:
            continue
        sample_rate = float(ref.stats.sampling_rate)
        ref_signal = _relative_samples(
            ref, str(reference_event["origin_time"]), settings["signal_window_s"]
        )
        cmp_signal = _relative_samples(
            cmp, str(comparison_event["origin_time"]), settings["signal_window_s"]
        )
        ref_noise = _relative_samples(
            ref, str(reference_event["origin_time"]), settings["noise_window_s"]
        )
        cmp_noise = _relative_samples(
            cmp, str(comparison_event["origin_time"]), settings["noise_window_s"]
        )
        if any(item is None for item in (ref_signal, cmp_signal, ref_noise, cmp_noise)):
            continue
        for band in settings["bands_hz"]:
            ref_signal_f = _bandpass(ref_signal, sample_rate, band)
            cmp_signal_f = _bandpass(cmp_signal, sample_rate, band)
            ref_noise_f = _bandpass(ref_noise, sample_rate, band)
            cmp_noise_f = _bandpass(cmp_noise, sample_rate, band)
            ref_snr = float(
                np.sqrt(np.mean(ref_signal_f ** 2))
                / (np.sqrt(np.mean(ref_noise_f ** 2)) + np.finfo(float).eps)
            )
            cmp_snr = float(
                np.sqrt(np.mean(cmp_signal_f ** 2))
                / (np.sqrt(np.mean(cmp_noise_f ** 2)) + np.finfo(float).eps)
            )
            correlation_value, lag_s = _normalized_peak(
                ref_signal_f,
                cmp_signal_f,
                sample_rate,
                float(settings["max_lag_s"]),
            )
            band_name = "{}-{}Hz".format(float(band[0]), float(band[1]))
            usable = (
                np.isfinite(correlation_value)
                and min(ref_snr, cmp_snr) >= float(settings["minimum_trace_snr"])
            )
            row = {
                "pair_name": pair_name,
                "reference_event_id": reference_event["event_id"],
                "comparison_event_id": comparison_event["event_id"],
                "trace_id": key,
                "reference_trace_id": ref.id,
                "comparison_trace_id": cmp.id,
                "reference_location": ref.stats.location,
                "comparison_location": cmp.stats.location,
                "station": ref.stats.station,
                "component": ref.stats.channel,
                "sample_rate_hz": sample_rate,
                "band": band_name,
                "band_low_hz": float(band[0]),
                "band_high_hz": float(band[1]),
                "reference_snr": ref_snr,
                "comparison_snr": cmp_snr,
                "correlation": correlation_value,
                "lag_s": lag_s,
                "usable": usable,
            }
            metrics.append(row)
            if usable:
                band_values[band_name].append(correlation_value)
                band_stations[band_name].add(ref.stats.station)

    all_usable = [row["correlation"] for row in metrics if row["usable"]]
    band_summaries: Dict[str, Dict[str, Any]] = {}
    for band in settings["bands_hz"]:
        name = "{}-{}Hz".format(float(band[0]), float(band[1]))
        values = np.asarray(band_values.get(name, []), dtype=float)
        band_summaries[name] = {
            "usable_components": int(values.size),
            "usable_stations": len(band_stations.get(name, set())),
            "median_correlation": float(np.median(values)) if values.size else None,
            "minimum_correlation": float(np.min(values)) if values.size else None,
            "p10_correlation": float(np.percentile(values, 10)) if values.size else None,
        }
    medians = [
        item["median_correlation"]
        for item in band_summaries.values()
        if item["median_correlation"] is not None
    ]
    minimum_components = min(
        (item["usable_components"] for item in band_summaries.values()), default=0
    )
    minimum_stations = min(
        (item["usable_stations"] for item in band_summaries.values()), default=0
    )
    overall_median = float(np.median(all_usable)) if all_usable else None
    weakest_band_median = min(medians) if medians else None
    enough_data = (
        minimum_components >= int(settings["minimum_usable_components"])
        and minimum_stations >= 3
    )
    predicts_family = bool(
        enough_data
        and overall_median is not None
        and weakest_band_median is not None
        and overall_median >= float(settings["family_median_correlation"])
        and weakest_band_median >= float(settings["family_minimum_band_correlation"])
    )
    summary = {
        "pair_name": pair_name,
        "reference_event_id": str(reference_event["event_id"]),
        "comparison_event_id": str(comparison_event["event_id"]),
        "usable_metric_count": len(all_usable),
        "minimum_components_per_band": minimum_components,
        "minimum_stations_per_band": minimum_stations,
        "overall_median_correlation": overall_median,
        "weakest_band_median_correlation": weakest_band_median,
        "predicted_family": predicts_family,
        "decision": "family" if predicts_family else ("not_family" if enough_data else "insufficient_data"),
        "status": PASS if enough_data else STOP,
        "band_summaries": band_summaries,
        "normalization": "per-trace zero-mean L2; positive-polarity peak",
        "instrument_response_removed": False,
        "allowed_use": "waveform-shape similarity feature; family labels require a labeled multi-family validation design",
    }
    return metrics, summary


def write_metrics(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("no HRSN metrics to write")
    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
