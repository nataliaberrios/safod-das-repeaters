"""Authoritative NCEDC catalog retrieval, spatial nomination, and DAS overlap.

Catalog proximity nominates candidates; it never assigns repeating-earthquake
membership.  Membership is decided by the independent HRSN waveform module.
"""

from __future__ import annotations

import csv
import io
import math
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .common import iso_utc, parse_utc, utc_now
from .h5io import discover_h5, filename_epoch, read_header, select_coarse_files


CATALOG_FIELDS = [
    "event_id",
    "origin_time",
    "latitude",
    "longitude",
    "depth_km",
    "author",
    "catalog",
    "contributor",
    "contributor_id",
    "magnitude_type",
    "magnitude",
    "magnitude_author",
    "location_name",
    "event_type",
]


def _normalize_ncedc_utc(value: str) -> str:
    """Normalize NCEDC text times, which omit ``Z`` and vary fraction width."""

    text = value.strip().rstrip("Z")
    if "." in text:
        whole, fraction = text.split(".", 1)
        fraction = (fraction + "000000")[:6]
        parsed = datetime.strptime(
            whole + "." + fraction, "%Y-%m-%dT%H:%M:%S.%f"
        )
    else:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%S")
    return iso_utc(parsed.replace(tzinfo=timezone.utc))


def catalog_query_url(config: Mapping[str, Any]) -> str:
    catalog = config["catalog"]
    params: Dict[str, Any] = {
        "starttime": catalog["starttime"],
        "endtime": catalog["endtime"],
        "format": "text",
        "orderby": "time",
        "catalog": catalog["catalog"],
    }
    params.update(catalog["bounds"])
    return catalog["event_service"] + "?" + urllib.parse.urlencode(params)


def parse_ncedc_text(text: str, source_url: str = "") -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    retrieved = utc_now()
    for line in io.StringIO(text):
        if not line.strip() or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.rstrip("\n").split("|")]
        if len(fields) < 14:
            continue
        event = dict(zip(CATALOG_FIELDS, fields[:14]))
        event["origin_time"] = _normalize_ncedc_utc(event["origin_time"])
        event["latitude"] = float(event["latitude"])
        event["longitude"] = float(event["longitude"])
        event["depth_km"] = float(event["depth_km"])
        event["magnitude"] = float(event["magnitude"])
        event["source_url"] = source_url
        event["retrieved_utc"] = retrieved
        events.append(event)
    return events


def fetch_catalog(config: Mapping[str, Any], timeout_s: float = 90.0) -> List[Dict[str, Any]]:
    url = catalog_query_url(config)
    request = urllib.request.Request(
        url, headers={"User-Agent": "safod-repeaters-v2/0.1"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        text = response.read().decode("utf-8")
    events = parse_ncedc_text(text, source_url=url)
    if not events:
        raise RuntimeError("NCEDC query returned no parseable events")
    return events


def write_catalog(path: Path, events: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not events:
        raise ValueError("cannot write empty catalog")
    fields: List[str] = []
    for event in events:
        for key in event:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(events)


def read_catalog(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for field in ("latitude", "longitude", "depth_km", "magnitude"):
            row[field] = float(row[field])
    return rows


def local_offsets_m(
    event: Mapping[str, Any], reference: Mapping[str, Any]
) -> Dict[str, float]:
    """Small-distance east/north/down offsets using a local spherical tangent."""

    radius_m = 6371008.8
    lat0 = math.radians(float(reference["latitude"]))
    dlat = math.radians(float(event["latitude"]) - float(reference["latitude"]))
    dlon = math.radians(float(event["longitude"]) - float(reference["longitude"]))
    north = radius_m * dlat
    east = radius_m * math.cos(lat0) * dlon
    down = 1000.0 * (float(event["depth_km"]) - float(reference["depth_km"]))
    horizontal = math.hypot(east, north)
    return {
        "east_m": east,
        "north_m": north,
        "down_m": down,
        "horizontal_distance_m": horizontal,
        "distance_3d_m": math.hypot(horizontal, down),
    }


def find_event(events: Sequence[Mapping[str, Any]], event_id: str) -> Dict[str, Any]:
    matches = [dict(event) for event in events if str(event["event_id"]) == str(event_id)]
    if len(matches) != 1:
        raise KeyError("expected one event {}, found {}".format(event_id, len(matches)))
    return matches[0]


def nominate_candidates(
    events: Sequence[Mapping[str, Any]], config: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    seed_id = str(config["catalog"]["seed_event_id"])
    seed = find_event(events, seed_id)
    maximum_distance = float(config["catalog"]["shortlist_max_distance_m"])
    maximum_dm = float(config["catalog"]["shortlist_max_magnitude_difference"])
    candidates: List[Dict[str, Any]] = []
    for event in events:
        row = dict(event)
        row.update(local_offsets_m(event, seed))
        row["magnitude_difference"] = float(event["magnitude"]) - float(seed["magnitude"])
        row["is_seed"] = str(event["event_id"]) == seed_id
        row["nomination_status"] = (
            "seed"
            if row["is_seed"]
            else "waveform_test_required"
        )
        if row["is_seed"] or (
            row["distance_3d_m"] <= maximum_distance
            and abs(row["magnitude_difference"]) <= maximum_dm
        ):
            candidates.append(row)
    return sorted(candidates, key=lambda row: (row["distance_3d_m"], row["origin_time"]))


def check_deep_overlap(
    events: Sequence[Mapping[str, Any]], roots: Sequence[Path], window_s: Sequence[float]
) -> List[Dict[str, Any]]:
    """Verify event-window overlap against raw headers, not directory labels."""

    all_paths: List[Path] = []
    for root in roots:
        all_paths.extend(discover_h5(root))
    rows: List[Dict[str, Any]] = []
    for event in events:
        origin = parse_utc(event["origin_time"]).timestamp()
        start = origin + float(window_s[0])
        end = origin + float(window_s[1])
        candidates = select_coarse_files(all_paths, start, end)
        verified = []
        errors = []
        for path in candidates:
            header = read_header(path)
            if header.status != "ok":
                errors.append(header.error)
                continue
            h_start = parse_utc(header.start_utc).timestamp()
            h_end = parse_utc(header.end_utc).timestamp()
            if h_start < end and h_end > start:
                verified.append(header)
        covered_start = min(
            (parse_utc(header.start_utc).timestamp() for header in verified),
            default=float("nan"),
        )
        covered_end = max(
            (parse_utc(header.end_utc).timestamp() for header in verified),
            default=float("nan"),
        )
        complete = bool(verified) and covered_start <= start and covered_end >= end
        rows.append(
            {
                "event_id": event["event_id"],
                "origin_time": event["origin_time"],
                "requested_start_utc": iso_utc(start),
                "requested_end_utc": iso_utc(end),
                "intersecting_file_count": len(verified),
                "window_complete_by_header_extent": complete,
                "coverage_status": "PASS" if complete else ("CONDITIONAL" if verified else "STOP"),
                "source_files": ";".join(header.path for header in verified),
                "header_errors": ";".join(errors),
            }
        )
    return rows
