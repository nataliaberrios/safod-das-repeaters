"""Archive-manifest reconciliation and prospective population construction.

The archive manifest is the only input used to select held-out UTC intervals.
Catalog proximity nominates prospective events but never assigns a repeating-
earthquake family label. External DAS files are treated as read-only.
"""

from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .catalog import local_offsets_m
from .common import iso_utc, parse_utc


MANIFEST_FIELDS = [
    "system",
    "file",
    "nSamples",
    "fs",
    "Desample",
    "startTime",
    "endTime",
    "nChannels",
    "dCh",
    "GaugeLen",
    "firstSample",
]


@dataclass(frozen=True)
class ManifestRecord:
    """One valid primary-configuration DAS file from the archive manifest."""

    path: str
    start_s: float
    end_s: float
    sample_rate_hz: float
    channel_count: int
    gauge_length_m: float

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


@dataclass(frozen=True)
class CoverageSegment:
    """A contiguous union of manifest record extents."""

    segment_id: int
    start_s: float
    end_s: float
    record_count: int

    @property
    def duration_s(self) -> float:
        return self.end_s - self.start_s


def _resolve_path(value: str, rewrites: Sequence[Mapping[str, str]]) -> str:
    for rewrite in rewrites:
        source = str(rewrite["manifest_prefix"])
        if value.startswith(source):
            return str(rewrite["filesystem_prefix"]) + value[len(source) :]
    return value


def read_primary_manifest(
    path: Path, settings: Mapping[str, Any]
) -> Tuple[List[ManifestRecord], Dict[str, Any]]:
    """Read valid rows matching the frozen primary DAS configuration."""

    expected = settings["primary_configuration"]
    rewrites = settings.get("path_rewrites", [])
    records: List[ManifestRecord] = []
    total_rows = 0
    placeholder_rows = 0
    timestamp_invalid_rows = 0
    primary_configuration_timestamp_invalid_rows = 0
    valid_other_configuration_rows = 0
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        header = handle.readline().split()
        if header != MANIFEST_FIELDS:
            raise ValueError("unexpected archive manifest header: {}".format(header))
        for line in handle:
            if not line.strip():
                continue
            total_rows += 1
            fields = line.split()
            if len(fields) < len(MANIFEST_FIELDS):
                placeholder_rows += 1
                continue
            row = dict(zip(MANIFEST_FIELDS, fields[: len(MANIFEST_FIELDS)]))
            try:
                sample_count = int(row["nSamples"])
                pulse_rate = float(row["fs"])
                decimation = int(row["Desample"])
                channel_count = int(row["nChannels"])
                gauge_length_m = float(row["GaugeLen"])
            except (TypeError, ValueError):
                placeholder_rows += 1
                continue
            if sample_count <= 0 or pulse_rate <= 0.0 or decimation <= 0:
                placeholder_rows += 1
                continue
            sample_rate_hz = pulse_rate / decimation
            matches_primary = (
                math.isclose(
                    sample_rate_hz,
                    float(expected["sample_rate_hz"]),
                    rel_tol=0.0,
                    abs_tol=1.0e-6,
                )
                and channel_count == int(expected["channel_count"])
                and math.isclose(
                    gauge_length_m,
                    float(expected["gauge_length_m"]),
                    rel_tol=0.0,
                    abs_tol=1.0e-6,
                )
            )
            try:
                start_s = parse_utc(row["startTime"]).timestamp()
                end_s = parse_utc(row["endTime"]).timestamp()
            except (TypeError, ValueError):
                timestamp_invalid_rows += 1
                if matches_primary:
                    primary_configuration_timestamp_invalid_rows += 1
                continue
            if end_s <= start_s:
                timestamp_invalid_rows += 1
                if matches_primary:
                    primary_configuration_timestamp_invalid_rows += 1
                continue
            if not matches_primary:
                valid_other_configuration_rows += 1
                continue
            records.append(
                ManifestRecord(
                    path=_resolve_path(row["file"], rewrites),
                    start_s=start_s,
                    end_s=end_s,
                    sample_rate_hz=sample_rate_hz,
                    channel_count=channel_count,
                    gauge_length_m=gauge_length_m,
                )
            )
    records.sort(key=lambda item: (item.start_s, item.end_s, item.path))
    stats = {
        "manifest_rows": total_rows,
        "placeholder_or_numeric_invalid_rows": placeholder_rows,
        "timestamp_invalid_rows": timestamp_invalid_rows,
        "primary_configuration_timestamp_invalid_rows": (
            primary_configuration_timestamp_invalid_rows
        ),
        "primary_configuration_time_valid_rows": len(records),
        "valid_other_configuration_rows": valid_other_configuration_rows,
        "path_rewrite_count": len(rewrites),
    }
    return records, stats


def merge_coverage(
    records: Sequence[ManifestRecord], maximum_gap_s: float
) -> List[CoverageSegment]:
    """Merge duplicate and adjacent manifest extents into contiguous segments."""

    if not records:
        return []
    segments: List[CoverageSegment] = []
    start_s = records[0].start_s
    end_s = records[0].end_s
    record_count = 1
    for record in records[1:]:
        if record.start_s <= end_s + float(maximum_gap_s):
            end_s = max(end_s, record.end_s)
            record_count += 1
            continue
        segments.append(
            CoverageSegment(
                segment_id=len(segments),
                start_s=start_s,
                end_s=end_s,
                record_count=record_count,
            )
        )
        start_s = record.start_s
        end_s = record.end_s
        record_count = 1
    segments.append(
        CoverageSegment(
            segment_id=len(segments),
            start_s=start_s,
            end_s=end_s,
            record_count=record_count,
        )
    )
    return segments


def segment_rows(segments: Sequence[CoverageSegment]) -> List[Dict[str, Any]]:
    return [
        {
            "segment_id": segment.segment_id,
            "start_utc": iso_utc(segment.start_s),
            "end_utc": iso_utc(segment.end_s),
            "duration_s": segment.duration_s,
            "manifest_record_count": segment.record_count,
        }
        for segment in segments
    ]


def select_heldout_intervals(
    segments: Sequence[CoverageSegment],
    duration_s: float,
    count: int,
    seed: int,
    exclusions: Sequence[Tuple[float, float]] = (),
) -> List[Dict[str, Any]]:
    """Select non-overlapping intervals using coverage and the frozen seed only."""

    duration = float(duration_s)
    eligible = [segment for segment in segments if segment.duration_s >= duration]
    weights = [segment.duration_s - duration for segment in eligible]
    if not eligible:
        raise ValueError("no coverage segment can hold a held-out interval")
    if sum(weights) <= 0.0:
        weights = [1.0 for _ in eligible]
    rng = random.Random(int(seed))
    accepted: List[Tuple[float, float, int, int]] = []
    maximum_draws = max(10000, 5000 * int(count))
    for draw_index in range(maximum_draws):
        segment = rng.choices(eligible, weights=weights, k=1)[0]
        slack = max(0.0, segment.duration_s - duration)
        start_s = segment.start_s + rng.random() * slack
        start_s = round(start_s, 3)
        end_s = start_s + duration
        conflicts = list(exclusions) + [
            (item[0], item[1]) for item in accepted
        ]
        if any(start_s < other_end and end_s > other_start for other_start, other_end in conflicts):
            continue
        accepted.append((start_s, end_s, segment.segment_id, draw_index))
        if len(accepted) == int(count):
            break
    if len(accepted) != int(count):
        raise RuntimeError(
            "selected {} of {} held-out intervals".format(len(accepted), count)
        )
    accepted.sort(key=lambda item: item[0])
    return [
        {
            "interval_id": "heldout_{:02d}".format(index + 1),
            "start_utc": iso_utc(item[0]),
            "end_utc": iso_utc(item[1]),
            "duration_s": duration,
            "coverage_segment_id": item[2],
            "selection_draw_index": item[3],
            "selection_seed": int(seed),
            "selection_inputs": "DAS_manifest_only_blind_to_catalog_and_waveform_scores",
            "analysis_status": "SEALED_NOT_RUN",
        }
        for index, item in enumerate(accepted)
    ]


def _segment_for_window(
    segments: Sequence[CoverageSegment],
    segment_starts: Sequence[float],
    start_s: float,
    end_s: float,
) -> CoverageSegment | None:
    index = bisect.bisect_right(segment_starts, start_s) - 1
    if index < 0:
        return None
    segment = segments[index]
    return segment if segment.start_s <= start_s and segment.end_s >= end_s else None


def _files_for_window(
    records: Sequence[ManifestRecord],
    record_starts: Sequence[float],
    maximum_duration_s: float,
    start_s: float,
    end_s: float,
) -> List[str]:
    first = bisect.bisect_left(record_starts, start_s - maximum_duration_s - 0.001)
    stop = bisect.bisect_left(record_starts, end_s)
    return sorted(
        {
            record.path
            for record in records[first:stop]
            if record.start_s < end_s and record.end_s > start_s
        }
    )


def archive_overlap_rows(
    events: Sequence[Mapping[str, Any]],
    records: Sequence[ManifestRecord],
    segments: Sequence[CoverageSegment],
    settings: Mapping[str, Any],
    neighborhood: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Join catalog events to verified manifest coverage and nomination gates."""

    reference = {
        "latitude": float(neighborhood["reference_latitude"]),
        "longitude": float(neighborhood["reference_longitude"]),
        "depth_km": float(neighborhood["reference_depth_km"]),
    }
    window = settings["event_window_s"]
    record_starts = [record.start_s for record in records]
    segment_starts = [segment.start_s for segment in segments]
    maximum_duration_s = max(
        (record.duration_s for record in records), default=0.0
    )
    all_rows: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for event in events:
        origin_s = parse_utc(str(event["origin_time"])).timestamp()
        start_s = origin_s + float(window[0])
        end_s = origin_s + float(window[1])
        segment = _segment_for_window(
            segments, segment_starts, start_s, end_s
        )
        files = (
            _files_for_window(
                records, record_starts, maximum_duration_s, start_s, end_s
            )
            if segment
            else []
        )
        offsets = local_offsets_m(event, reference)
        depth_difference_km = (
            float(event["depth_km"]) - float(reference["depth_km"])
        )
        magnitude = float(event["magnitude"])
        spatial_gate = (
            offsets["horizontal_distance_m"]
            <= float(neighborhood["maximum_horizontal_distance_m"])
            and abs(depth_difference_km)
            <= float(neighborhood["maximum_depth_difference_km"])
            and float(neighborhood["minimum_magnitude"])
            <= magnitude
            <= float(neighborhood["maximum_magnitude"])
        )
        complete = segment is not None
        row = {
            "event_id": str(event["event_id"]),
            "origin_time": event["origin_time"],
            "latitude": event["latitude"],
            "longitude": event["longitude"],
            "depth_km": event["depth_km"],
            "magnitude": event["magnitude"],
            "horizontal_distance_m": offsets["horizontal_distance_m"],
            "east_m": offsets["east_m"],
            "north_m": offsets["north_m"],
            "depth_difference_km": depth_difference_km,
            "requested_start_utc": iso_utc(start_s),
            "requested_end_utc": iso_utc(end_s),
            "coverage_complete": complete,
            "coverage_segment_id": segment.segment_id if segment else "",
            "intersecting_file_count": len(files),
            "source_files": ";".join(files),
            "source_files_exist": bool(files) and all(Path(item).exists() for item in files),
            "family_neighborhood_gate": spatial_gate,
            "nomination_status": (
                "prospective_candidate_waveform_verification_required"
                if complete and spatial_gate
                else "not_nominated"
            ),
            "family_label": "",
            "waveform_access_status": "EMBARGOED_UNTIL_NETWORK_MODEL_FROZEN",
        }
        all_rows.append(row)
        if complete and spatial_gate:
            candidates.append(dict(row))
    all_rows.sort(key=lambda item: item["origin_time"])
    candidates.sort(
        key=lambda item: (item["horizontal_distance_m"], item["origin_time"])
    )
    return all_rows, candidates


def heldout_catalog_counts(
    intervals: Sequence[Mapping[str, Any]], events: Sequence[Mapping[str, Any]]
) -> List[Dict[str, Any]]:
    """Count routine catalog events only after interval selection is complete."""

    event_times = [
        parse_utc(str(event["origin_time"])).timestamp() for event in events
    ]
    rows: List[Dict[str, Any]] = []
    for interval in intervals:
        start_s = parse_utc(str(interval["start_utc"])).timestamp()
        end_s = parse_utc(str(interval["end_utc"])).timestamp()
        count = sum(start_s <= value < end_s for value in event_times)
        rows.append(
            {
                "interval_id": interval["interval_id"],
                "start_utc": interval["start_utc"],
                "end_utc": interval["end_utc"],
                "routine_catalog_event_count": count,
                "count_revealed_after_manifest_only_selection": True,
            }
        )
    return rows
