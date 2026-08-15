"""Read OptaSense HDF5 headers and small, UTC-addressed waveform windows.

The reader preserves the distinction between HDF5 array columns and OptaSense
locus indices.  It deliberately makes no conversion from either index to depth or
XYZ because the surveyed deep-fiber trajectory has not been supplied.

Raw data are stored as processed optical phase counts with unit metadata
``rad * 2PI/2^16``.  When that exact convention is present, samples are converted
to radians.  They are not called strain or strain rate.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import h5py
import numpy as np

from .common import iso_utc


RAW_GROUP = "/Acquisition/Raw[0]"
RAW_DATA = RAW_GROUP + "/RawData"
RAW_TIME = RAW_GROUP + "/RawDataTime"
FILENAME_TIME = re.compile(r"_(\d{4}-\d{2}-\d{2}T\d{6}Z)\.h5$")


def _decode(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return [_decode(item) for item in value.tolist()]
    return value


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(_decode(value))


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    return int(_decode(value))


@dataclass
class H5Header:
    path: str
    status: str
    error: str = ""
    size_bytes: Optional[int] = None
    start_utc: str = ""
    end_utc: str = ""
    sample_rate_hz: Optional[float] = None
    sample_count: Optional[int] = None
    channel_count: Optional[int] = None
    time_axis: Optional[int] = None
    start_locus_index: Optional[int] = None
    channel_spacing_m: Optional[float] = None
    gauge_length_m: Optional[float] = None
    raw_data_unit: str = ""
    raw_description: str = ""
    root_uuid: str = ""
    acquisition_uuid: str = ""
    raw_uuid: str = ""
    gps_enabled: Optional[int] = None
    gps_sync_guaranteed: Optional[int] = None
    timestamp_span_residual_s: Optional[float] = None
    sample_counter_residual: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WaveformWindow:
    data: np.ndarray
    time_epoch_s: np.ndarray
    sample_rate_hz: float
    column_indices: np.ndarray
    locus_indices: np.ndarray
    unit: str
    source_files: List[str]
    maximum_gap_s: float
    missing_fraction: float


def filename_epoch(path: Union[str, Path]) -> Optional[float]:
    """Return filename UTC seconds, which are only a coarse search index."""

    match = FILENAME_TIME.search(Path(path).name)
    if match is None:
        return None
    parsed = datetime.strptime(match.group(1), "%Y-%m-%dT%H%M%SZ").replace(
        tzinfo=timezone.utc
    )
    return parsed.timestamp()


def discover_h5(root: Union[str, Path]) -> List[Path]:
    """Return deterministic HDF5 candidates beneath a read-only archive root."""

    return sorted(Path(root).rglob("*.h5"), key=lambda item: str(item))


def read_header(path: Union[str, Path]) -> H5Header:
    """Read one HDF5 header, returning an error record instead of aborting."""

    source = Path(path)
    record = H5Header(path=str(source), status="error")
    try:
        record.size_bytes = source.stat().st_size
        with h5py.File(source, "r") as handle:
            acquisition = handle["Acquisition"]
            raw = handle[RAW_GROUP]
            data = handle[RAW_DATA]
            times = handle[RAW_TIME]
            if times.shape[0] <= 0:
                raise ValueError("RawDataTime has no samples")
            sample_rate = float(raw.attrs.get("OutputDataRate"))
            if sample_rate <= 0:
                raise ValueError("invalid OutputDataRate {}".format(sample_rate))
            if data.ndim != 2:
                raise ValueError("RawData must be 2-D, got {}".format(data.shape))
            sample_count = int(times.shape[0])
            if data.shape[0] == sample_count and data.shape[1] != sample_count:
                time_axis = 0
            elif data.shape[1] == sample_count and data.shape[0] != sample_count:
                time_axis = 1
            else:
                raise ValueError(
                    "cannot identify time axis: data={}, times={}".format(
                        data.shape, sample_count
                    )
                )
            channel_count = int(data.shape[1 - time_axis])
            first_us = int(times[0])
            last_us = int(times[-1])
            first_s = first_us * 1.0e-6
            last_s = last_us * 1.0e-6
            expected_span = (sample_count - 1) / sample_rate
            counter_residual = None
            counter_path = RAW_GROUP + "/Custom/SampleCount"
            if counter_path in handle:
                counters = handle[counter_path]
                if counters.shape[0] == sample_count:
                    counter_residual = (
                        int(counters[-1]) - int(counters[0]) - (sample_count - 1)
                    )
            custom = acquisition.get("Custom")
            record.status = "ok"
            record.start_utc = iso_utc(first_s)
            record.end_utc = iso_utc(first_s + sample_count / sample_rate)
            record.sample_rate_hz = sample_rate
            record.sample_count = sample_count
            record.channel_count = channel_count
            record.time_axis = time_axis
            record.start_locus_index = _optional_int(
                raw.attrs.get(
                    "StartLocusIndex", acquisition.attrs.get("StartLocusIndex")
                )
            )
            record.channel_spacing_m = _optional_float(
                acquisition.attrs.get("SpatialSamplingInterval")
            )
            record.gauge_length_m = _optional_float(
                acquisition.attrs.get("GaugeLength")
            )
            record.raw_data_unit = str(_decode(raw.attrs.get("RawDataUnit", "")))
            record.raw_description = str(
                _decode(raw.attrs.get("RawDescription", ""))
            )
            record.root_uuid = str(_decode(handle.attrs.get("uuid", "")))
            record.acquisition_uuid = str(
                _decode(acquisition.attrs.get("uuid", ""))
            )
            record.raw_uuid = str(_decode(raw.attrs.get("uuid", "")))
            if custom is not None:
                record.gps_enabled = _optional_int(custom.attrs.get("GPS Enabled"))
                record.gps_sync_guaranteed = _optional_int(
                    custom.attrs.get("GPS Sync Guaranteed")
                )
            record.timestamp_span_residual_s = (
                last_s - first_s - expected_span
            )
            record.sample_counter_residual = counter_residual
    except Exception as exc:  # Header ledgers must preserve, not hide, bad files.
        record.error = "{}: {}".format(type(exc).__name__, exc)
    return record


def select_coarse_files(
    paths: Sequence[Path], start_epoch_s: float, end_epoch_s: float
) -> List[Path]:
    """Select filename-indexed files that could intersect a UTC window."""

    selected = []
    for path in paths:
        stamp = filename_epoch(path)
        if stamp is None:
            continue
        # OptaSense files are nominally 60 s; the two-second margin admits the
        # observed subsecond filename/header offset without scanning all headers.
        if stamp < end_epoch_s + 2.0 and stamp + 62.0 > start_epoch_s:
            selected.append(path)
    return selected


def _phase_scale(raw_unit: str) -> Tuple[float, str]:
    normalized = raw_unit.lower().replace(" ", "")
    if "rad" in normalized and "2pi/2^16" in normalized:
        return 2.0 * np.pi / float(2 ** 16), "processed_optical_phase_rad"
    return 1.0, raw_unit or "raw_counts_unknown_unit"


def read_window(
    paths_or_root: Union[str, Path, Sequence[Path]],
    start_epoch_s: float,
    end_epoch_s: float,
    channel_start: int = 0,
    channel_stop: Optional[int] = None,
    channel_stride: int = 1,
) -> WaveformWindow:
    """Read a small UTC window, concatenating adjacent HDF5 files safely.

    Parameters use HDF5 array-column indices.  Returned ``locus_indices`` add the
    file's ``StartLocusIndex``; neither index is interpreted as physical depth.
    """

    if end_epoch_s <= start_epoch_s:
        raise ValueError("end_epoch_s must be after start_epoch_s")
    if channel_stride <= 0:
        raise ValueError("channel_stride must be positive")
    if isinstance(paths_or_root, (str, Path)):
        paths = discover_h5(paths_or_root)
    else:
        paths = list(paths_or_root)
    candidates = select_coarse_files(paths, start_epoch_s, end_epoch_s)
    if not candidates:
        raise FileNotFoundError(
            "no HDF5 filename intersects {} to {}".format(
                iso_utc(start_epoch_s), iso_utc(end_epoch_s)
            )
        )

    data_parts: List[np.ndarray] = []
    time_parts: List[np.ndarray] = []
    used_files: List[str] = []
    reference: Optional[Tuple[float, np.ndarray, np.ndarray, str]] = None
    for path in sorted(candidates, key=lambda item: filename_epoch(item) or 0.0):
        with h5py.File(path, "r") as handle:
            raw = handle[RAW_GROUP]
            dataset = handle[RAW_DATA]
            times_us = np.asarray(handle[RAW_TIME][:], dtype=np.int64)
            times_s = times_us.astype(np.float64) * 1.0e-6
            mask_start = int(np.searchsorted(times_s, start_epoch_s, side="left"))
            mask_end = int(np.searchsorted(times_s, end_epoch_s, side="left"))
            if mask_end <= mask_start:
                continue
            sample_rate = float(raw.attrs["OutputDataRate"])
            start_locus = int(
                raw.attrs.get(
                    "StartLocusIndex",
                    handle["Acquisition"].attrs.get("StartLocusIndex", 0),
                )
            )
            n_channels = int(dataset.shape[1])
            stop = n_channels if channel_stop is None else min(channel_stop, n_channels)
            if not (0 <= channel_start < stop):
                raise ValueError(
                    "invalid channel slice {}:{} for {} channels".format(
                        channel_start, stop, n_channels
                    )
                )
            columns = np.arange(channel_start, stop, channel_stride, dtype=np.int64)
            loci = columns + start_locus
            raw_unit = str(_decode(raw.attrs.get("RawDataUnit", "")))
            scale, output_unit = _phase_scale(raw_unit)
            if reference is None:
                reference = (sample_rate, columns, loci, output_unit)
            else:
                ref_rate, ref_columns, ref_loci, ref_unit = reference
                if sample_rate != ref_rate:
                    raise ValueError("sample-rate change inside requested window")
                if not np.array_equal(columns, ref_columns):
                    raise ValueError("channel-column change inside requested window")
                if not np.array_equal(loci, ref_loci):
                    raise ValueError("locus-index change inside requested window")
                if output_unit != ref_unit:
                    raise ValueError("raw-unit change inside requested window")
            block = np.asarray(
                dataset[mask_start:mask_end, channel_start:stop:channel_stride],
                dtype=np.float32,
            )
            block *= np.float32(scale)
            data_parts.append(block)
            time_parts.append(times_s[mask_start:mask_end])
            used_files.append(str(path))

    if not data_parts or reference is None:
        raise FileNotFoundError("candidate files do not contain the requested UTC window")
    data = np.concatenate(data_parts, axis=0)
    times = np.concatenate(time_parts)
    order = np.argsort(times, kind="stable")
    data = data[order]
    times = times[order]
    keep = np.concatenate(([True], np.diff(times) > 0.0))
    data = data[keep]
    times = times[keep]
    sample_rate, columns, loci, unit = reference
    if len(times) > 1:
        gaps = np.diff(times)
        maximum_gap = float(np.max(gaps))
    else:
        maximum_gap = float("inf")
    expected = max(1, int(round((end_epoch_s - start_epoch_s) * sample_rate)))
    missing_fraction = max(0.0, 1.0 - len(times) / float(expected))
    return WaveformWindow(
        data=data,
        time_epoch_s=times,
        sample_rate_hz=sample_rate,
        column_indices=columns,
        locus_indices=loci,
        unit=unit,
        source_files=used_files,
        maximum_gap_s=maximum_gap,
        missing_fraction=missing_fraction,
    )

