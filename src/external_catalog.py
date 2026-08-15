"""Published repeating-earthquake catalog ingestion and exact-ID crosswalks.

The Waldhauser--Schaff catalog is an external label source, not waveform input.
Event IDs are matched exactly.  Location-only matching is intentionally forbidden
because catalog absolute depths and hypocenters differ substantially at Parkfield.
"""

from __future__ import annotations

import csv
import hashlib
import math
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .common import iso_utc, parse_utc, utc_now


def md5_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return an MD5 checksum for verification against the published record."""

    digest = hashlib.md5()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def obtain_catalog(
    target: Path,
    url: str,
    expected_md5: str,
    force: bool = False,
    timeout_s: float = 120.0,
) -> Path:
    """Download the public catalog atomically and verify its published checksum."""

    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        observed = md5_file(target)
        if observed == expected_md5:
            return target
        raise ValueError(
            "cached external catalog checksum mismatch: {} != {}".format(
                observed, expected_md5
            )
        )
    request = urllib.request.Request(
        url, headers={"User-Agent": "safod-repeaters-v2/0.2"}
    )
    temporary = target.with_suffix(target.suffix + ".partial")
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        with temporary.open("wb") as handle:
            while True:
                block = response.read(1024 * 1024)
                if not block:
                    break
                handle.write(block)
    observed = md5_file(temporary)
    if observed != expected_md5:
        temporary.unlink(missing_ok=True)
        raise ValueError(
            "downloaded external catalog checksum mismatch: {} != {}".format(
                observed, expected_md5
            )
        )
    temporary.replace(target)
    return target


def _optional_float(value: str) -> Optional[float]:
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if math.isfinite(parsed) else None


def _event_time(fields: Sequence[str]) -> str:
    second = float(fields[5])
    start = datetime(
        int(fields[0]),
        int(fields[1]),
        int(fields[2]),
        int(fields[3]),
        int(fields[4]),
        tzinfo=timezone.utc,
    )
    return iso_utc(start + timedelta(seconds=second))


def parse_waldhauser_schaff(
    path: Path,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Parse Table S1 into sequence and exact-event-ID dictionaries."""

    sequences: Dict[str, Dict[str, Any]] = {}
    events: Dict[str, Dict[str, Any]] = {}
    current_sequence: Optional[Dict[str, Any]] = None
    with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            fields = line.split()
            if line.startswith("#"):
                if (
                    len(fields) >= 15
                    and fields[1].isdigit()
                    and fields[-1].startswith("R")
                ):
                    sequence_id = fields[-1]
                    current_sequence = {
                        "sequence_id": sequence_id,
                        "published_event_count": int(fields[1]),
                        "median_latitude": float(fields[2]),
                        "median_longitude": float(fields[3]),
                        "median_depth_km": float(fields[4]),
                        "median_differential_magnitude": _optional_float(fields[5]),
                        "differential_magnitude_std": _optional_float(fields[6]),
                        "median_recurrence_days": _optional_float(fields[7]),
                        "recurrence_std_days": _optional_float(fields[8]),
                        "recurrence_cv": _optional_float(fields[9]),
                        "median_long_window_correlation": _optional_float(fields[13]),
                        "events": [],
                    }
                    if sequence_id in sequences:
                        raise ValueError("duplicate sequence ID {}".format(sequence_id))
                    sequences[sequence_id] = current_sequence
                continue
            if len(fields) != 18 or not fields[0].isdigit():
                continue
            if current_sequence is None:
                raise ValueError("event encountered before sequence header")
            event_id = fields[17]
            event = {
                "event_id": event_id,
                "origin_time": _event_time(fields),
                "days_since_1984_01_01": float(fields[6]),
                "latitude": float(fields[7]),
                "longitude": float(fields[8]),
                "depth_km": float(fields[9]),
                "relative_error_x_m": float(fields[10]),
                "relative_error_y_m": float(fields[11]),
                "relative_error_z_m": float(fields[12]),
                "magnitude": float(fields[13]),
                "differential_magnitude": _optional_float(fields[14]),
                "differential_magnitude_error": _optional_float(fields[15]),
                "median_long_window_correlation": _optional_float(fields[16]),
                "sequence_id": current_sequence["sequence_id"],
            }
            if event_id in events:
                raise ValueError("event {} occurs in multiple sequences".format(event_id))
            events[event_id] = event
            current_sequence["events"].append(event)
    if not sequences or not events:
        raise ValueError("external catalog parsed no sequences or events")
    for sequence in sequences.values():
        if len(sequence["events"]) != sequence["published_event_count"]:
            raise ValueError(
                "sequence {} declares {} events but contains {}".format(
                    sequence["sequence_id"],
                    sequence["published_event_count"],
                    len(sequence["events"]),
                )
            )
    return sequences, events


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _external_role(
    sequence_id: Optional[str],
    target_sequence_id: str,
    hard_negative_sequence_ids: Sequence[str],
) -> str:
    if sequence_id == target_sequence_id:
        return "published_target_positive"
    if sequence_id in set(hard_negative_sequence_ids):
        return "published_neighbor_family_negative"
    if sequence_id:
        return "published_other_family"
    return "no_published_label"


def _diagnostic_outcome(role: str, single_anchor_decision: str) -> str:
    predicts_target = single_anchor_decision == "family"
    abstains = single_anchor_decision in (
        "insufficient_data",
        "download_or_data_error",
        "seed",
    )
    if role == "published_target_positive":
        if predicts_target:
            return "agreement_target_positive"
        if abstains:
            return "published_positive_single_anchor_abstain"
        return "discordant_target_false_negative"
    if role == "published_neighbor_family_negative":
        if predicts_target:
            return "discordant_single_anchor_overmerge"
        if abstains:
            return "published_negative_single_anchor_abstain"
        return "agreement_neighbor_negative"
    return "unlabeled_not_scored"


def build_shortlist_crosswalk(
    shortlist_rows: Sequence[Mapping[str, Any]],
    ncedc_rows: Sequence[Mapping[str, Any]],
    published_events: Mapping[str, Mapping[str, Any]],
    target_sequence_id: str,
    hard_negative_sequence_ids: Sequence[str],
    catalog_end_utc: str,
) -> List[Dict[str, Any]]:
    """Crosswalk every frozen-shortlist row using exact event IDs only."""

    ncedc = {str(row["event_id"]): row for row in ncedc_rows}
    cutoff = parse_utc(catalog_end_utc)
    rows: List[Dict[str, Any]] = []
    for item in shortlist_rows:
        event_id = str(item["event_id"])
        published = published_events.get(event_id)
        sequence_id = str(published["sequence_id"]) if published else None
        role = _external_role(
            sequence_id, target_sequence_id, hard_negative_sequence_ids
        )
        ncedc_event = ncedc.get(event_id, {})
        origin_time = str(item.get("origin_time") or ncedc_event.get("origin_time"))
        after_catalog = parse_utc(origin_time) > cutoff
        decision = str(item.get("decision", ""))
        ncedc_depth = ncedc_event.get("depth_km", "")
        published_depth = published.get("depth_km") if published else None
        depth_difference = ""
        if ncedc_depth not in (None, "") and published_depth is not None:
            depth_difference = float(ncedc_depth) - float(published_depth)
        rows.append(
            {
                "event_id": event_id,
                "ncedc_dd_origin_time": origin_time,
                "ncedc_dd_latitude": ncedc_event.get("latitude", ""),
                "ncedc_dd_longitude": ncedc_event.get("longitude", ""),
                "ncedc_dd_depth_km": ncedc_depth,
                "ncedc_dd_magnitude": item.get(
                    "magnitude", ncedc_event.get("magnitude", "")
                ),
                "single_anchor_decision": decision,
                "single_anchor_status": item.get("membership_status", ""),
                "single_anchor_median_correlation": item.get(
                    "overall_median_correlation", ""
                ),
                "single_anchor_weakest_band_correlation": item.get(
                    "weakest_band_median_correlation", ""
                ),
                "external_match_type": "exact_event_id" if published else "none",
                "external_sequence_id": sequence_id or "",
                "external_role": role,
                "external_origin_time": published.get("origin_time", "")
                if published
                else "",
                "external_latitude": published.get("latitude", "")
                if published
                else "",
                "external_longitude": published.get("longitude", "")
                if published
                else "",
                "external_depth_km": published_depth if published else "",
                "external_magnitude": published.get("magnitude", "")
                if published
                else "",
                "external_median_long_window_correlation": published.get(
                    "median_long_window_correlation", ""
                )
                if published
                else "",
                "ncedc_minus_external_depth_km": depth_difference,
                "epoch_role": "prospective_post_catalog"
                if after_catalog
                else "published_catalog_era",
                "diagnostic_outcome": _diagnostic_outcome(role, decision),
            }
        )
    return rows


def build_validation_population(
    sequences: Mapping[str, Mapping[str, Any]],
    shortlist_crosswalk: Sequence[Mapping[str, Any]],
    target_sequence_id: str,
    hard_negative_sequence_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    """Return all events in the frozen published positive/neighbor populations."""

    requested = [target_sequence_id] + list(hard_negative_sequence_ids)
    missing = [sequence_id for sequence_id in requested if sequence_id not in sequences]
    if missing:
        raise KeyError("published validation sequences missing: {}".format(missing))
    shortlist = {str(row["event_id"]): row for row in shortlist_crosswalk}
    rows: List[Dict[str, Any]] = []
    for sequence_id in requested:
        sequence = sequences[sequence_id]
        role = (
            "target_positive"
            if sequence_id == target_sequence_id
            else "neighbor_family_negative"
        )
        for event in sequence["events"]:
            overlap = shortlist.get(str(event["event_id"]), {})
            waveform_origin_time = overlap.get(
                "ncedc_dd_origin_time", event["origin_time"]
            )
            rows.append(
                {
                    "event_id": event["event_id"],
                    "origin_time": event["origin_time"],
                    "waveform_origin_time": waveform_origin_time,
                    "latitude": event["latitude"],
                    "longitude": event["longitude"],
                    "depth_km": event["depth_km"],
                    "magnitude": event["magnitude"],
                    "sequence_id": sequence_id,
                    "validation_role": role,
                    "published_sequence_size": sequence["published_event_count"],
                    "published_event_cc": event["median_long_window_correlation"],
                    "published_sequence_cc": sequence[
                        "median_long_window_correlation"
                    ],
                    "in_frozen_shortlist": bool(overlap),
                    "single_anchor_decision": overlap.get(
                        "single_anchor_decision", "not_tested"
                    ),
                    "single_anchor_median_correlation": overlap.get(
                        "single_anchor_median_correlation", ""
                    ),
                    "evaluation_scheme": "leave_one_event_out_by_published_family",
                }
            )
    rows.sort(key=lambda row: (row["sequence_id"], row["origin_time"]))
    return rows


def catalog_provenance(
    path: Path, settings: Mapping[str, Any], sequence_count: int, event_count: int
) -> Dict[str, Any]:
    return {
        "catalog_name": settings["catalog_name"],
        "citation": settings["citation"],
        "paper_doi": settings["paper_doi"],
        "doi": settings["doi"],
        "zenodo_record_url": settings["zenodo_record_url"],
        "download_url": settings["download_url"],
        "local_path": str(Path(path).resolve()),
        "expected_md5": settings["md5"],
        "observed_md5": md5_file(Path(path)),
        "sequence_count": sequence_count,
        "event_count": event_count,
        "record_advertised_sequence_count": settings[
            "record_advertised_sequence_count"
        ],
        "record_advertised_event_count": settings[
            "record_advertised_event_count"
        ],
        "record_minus_file_event_count": int(
            settings["record_advertised_event_count"]
        )
        - int(event_count),
        "count_note": "the checksummed file contains 7,713 sequence headers whose declared counts sum to 27,674; the Zenodo record and paper advertise 27,675",
        "match_policy": "exact event ID only; no location-only family labels",
        "generated_utc": utc_now(),
    }
