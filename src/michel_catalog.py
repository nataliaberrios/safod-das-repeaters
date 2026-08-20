"""Michel et al. (2022) repeater catalog parsing and exact-ID crosswalks."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import iso_utc, parse_utc


def parse_michel_catalog(path: Path) -> List[Dict[str, Any]]:
    """Parse Data Set S3; the final column is the exact NCSN event ID."""

    events: List[Dict[str, Any]] = []
    seen_event_ids = set()
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("|"):
                continue
            fields = line.split()
            if len(fields) != 16:
                raise ValueError(
                    "unexpected Michel catalog row with {} fields".format(
                        len(fields)
                    )
                )
            second = float(fields[6])
            origin = datetime(
                int(fields[1]),
                int(fields[2]),
                int(fields[3]),
                int(fields[4]),
                int(fields[5]),
                tzinfo=timezone.utc,
            ) + timedelta(seconds=second)
            event_id = str(fields[15])
            if event_id in seen_event_ids:
                raise ValueError(
                    "duplicate Michel catalog event ID {}".format(event_id)
                )
            seen_event_ids.add(event_id)
            events.append(
                {
                    "event_id": event_id,
                    "origin_time": iso_utc(origin),
                    "seconds_since_1984_01_01": float(fields[0]),
                    "latitude": float(fields[7]),
                    "longitude": float(fields[8]),
                    "out_of_plane_m": float(fields[9]),
                    "in_plane_m": float(fields[10]),
                    "depth_km": float(fields[11]) / 1000.0,
                    "magnitude": float(fields[12]),
                    "michel_sequence_id": "M{}".format(fields[13]),
                    "event_order": int(fields[14]),
                }
            )
    if not events:
        raise ValueError("Michel catalog contained no events")
    return events


def exact_id_crosswalk(
    published_population: Sequence[Mapping[str, Any]],
    michel_events: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    michel_by_id = {
        str(event["event_id"]): event for event in michel_events
    }
    rows: List[Dict[str, Any]] = []
    for published in published_population:
        event_id = str(published["event_id"])
        michel = michel_by_id.get(event_id)
        rows.append(
            {
                "event_id": event_id,
                "waldhauser_schaff_sequence_id": published["sequence_id"],
                "waldhauser_schaff_validation_role": published[
                    "validation_role"
                ],
                "waldhauser_schaff_origin_time": published["origin_time"],
                "michel_exact_id_match": michel is not None,
                "michel_sequence_id": (
                    michel["michel_sequence_id"] if michel else ""
                ),
                "michel_origin_time": michel["origin_time"] if michel else "",
                "michel_latitude": michel["latitude"] if michel else "",
                "michel_longitude": michel["longitude"] if michel else "",
                "michel_depth_km": michel["depth_km"] if michel else "",
                "mapping_method": "exact_NCSN_event_id",
            }
        )
    return rows


def sequence_overlap_matrix(
    crosswalk: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    counts: Counter = Counter()
    for row in crosswalk:
        if bool(row["michel_exact_id_match"]):
            counts[
                (
                    str(row["waldhauser_schaff_sequence_id"]),
                    str(row["michel_sequence_id"]),
                )
            ] += 1
    return [
        {
            "waldhauser_schaff_sequence_id": key[0],
            "michel_sequence_id": key[1],
            "exact_shared_event_count": count,
        }
        for key, count in sorted(counts.items())
    ]


def partition_conflicts(
    overlap: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    ws_to_michel: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    michel_to_ws: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for row in overlap:
        ws = str(row["waldhauser_schaff_sequence_id"])
        michel = str(row["michel_sequence_id"])
        count = int(row["exact_shared_event_count"])
        ws_to_michel[ws].append((michel, count))
        michel_to_ws[michel].append((ws, count))

    rows: List[Dict[str, Any]] = []
    for ws, mapped in sorted(ws_to_michel.items()):
        if len(mapped) > 1:
            rows.append(
                {
                    "conflict_type": "waldhauser_schaff_family_split_by_michel",
                    "source_sequence_id": ws,
                    "mapped_sequence_ids": ";".join(
                        item[0] for item in sorted(mapped)
                    ),
                    "shared_event_counts": ";".join(
                        "{}:{}".format(item[0], item[1])
                        for item in sorted(mapped)
                    ),
                    "interpretation": "catalogs disagree on whether these exact events form one family",
                }
            )
    for michel, mapped in sorted(michel_to_ws.items()):
        if len(mapped) > 1:
            rows.append(
                {
                    "conflict_type": "michel_family_merges_waldhauser_schaff_families",
                    "source_sequence_id": michel,
                    "mapped_sequence_ids": ";".join(
                        item[0] for item in sorted(mapped)
                    ),
                    "shared_event_counts": ";".join(
                        "{}:{}".format(item[0], item[1])
                        for item in sorted(mapped)
                    ),
                    "interpretation": "catalogs disagree on whether these exact events are distinct families",
                }
            )
    return rows


def continuation_population(
    michel_events: Sequence[Mapping[str, Any]],
    crosswalk: Sequence[Mapping[str, Any]],
    target_sequence_id: str,
    minimum_exact_overlap: int,
    cutoff_utc: str,
) -> List[Dict[str, Any]]:
    """Return later members of Michel sequences overlapping the WS target."""

    counts: Counter = Counter(
        str(row["michel_sequence_id"])
        for row in crosswalk
        if bool(row["michel_exact_id_match"])
        and str(row["waldhauser_schaff_sequence_id"]) == target_sequence_id
    )
    mapped = {
        sequence_id
        for sequence_id, count in counts.items()
        if count >= int(minimum_exact_overlap)
    }
    cutoff = parse_utc(cutoff_utc)
    rows: List[Dict[str, Any]] = []
    for event in michel_events:
        if (
            str(event["michel_sequence_id"]) in mapped
            and parse_utc(str(event["origin_time"])) > cutoff
        ):
            row = dict(event)
            row["validation_role"] = (
                "post_2014_michel_sequence_continuation"
            )
            row["waldhauser_schaff_target_overlap_count"] = counts[
                str(event["michel_sequence_id"])
            ]
            row["family_mapping_status"] = (
                "catalog_partition_requires_reconciliation"
            )
            rows.append(row)
    return sorted(rows, key=lambda item: item["origin_time"])
