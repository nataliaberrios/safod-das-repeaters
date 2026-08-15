"""Normalize the June shot log and evaluate deep-fiber shot detectability.

Shot times are treated as nominal because independent trigger telemetry and timing
uncertainty are not available.  Detected energy offsets are therefore not travel
times and are not inverted for velocity or geometry.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping
from zoneinfo import ZoneInfo

from .common import iso_utc


def read_shot_log(path: Path, settings: Mapping[str, Any]) -> List[Dict[str, Any]]:
    date = str(settings["shot_date_local"])
    zone = ZoneInfo(str(settings["timezone"]))
    shots: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if not row.get("Shot Num", "").strip():
                continue
            local = datetime.fromisoformat(
                date + "T" + row["shot time, PDT"].strip()
            ).replace(tzinfo=zone)
            shots.append(
                {
                    "shot_number": int(row["Shot Num"]),
                    "name": row["name"].strip(),
                    "latitude": float(row["lat"]),
                    "longitude": float(row["lon"]),
                    "elevation_m": float(row["ele"]),
                    "local_time": local.isoformat(),
                    "nominal_origin_time": iso_utc(local.astimezone(ZoneInfo("UTC"))),
                    "description": row["description"].strip(),
                    "time_status": "nominal_no_independent_trigger_metadata",
                    "source_log": str(path),
                }
            )
    if not shots:
        raise ValueError("no shot rows parsed from {}".format(path))
    return shots

