"""Shared configuration, provenance, UTC, and status helpers.

Scientific rule: a status is derived from explicit evidence and thresholds.
`CONDITIONAL` is not silently promoted to `PASS` by downstream code.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Union


PASS = "PASS"
CONDITIONAL = "CONDITIONAL"
STOP = "STOP"


def project_root() -> Path:
    """Return the v2 project root independent of the current working directory."""

    return Path(__file__).resolve().parents[1]


def default_config_path() -> Path:
    return project_root() / "config" / "pilot.json"


def load_config(path: Union[str, Path, None] = None) -> Dict[str, Any]:
    """Load JSON configuration and retain its resolved source path."""

    source = Path(path) if path is not None else default_config_path()
    source = source.resolve()
    with source.open("r", encoding="utf-8") as handle:
        config = json.load(handle)
    config["_config_path"] = str(source)
    config["_config_sha256"] = sha256_file(source)
    return config


def sha256_file(path: Union[str, Path], chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_utc(value: Union[str, datetime]) -> datetime:
    """Parse an ISO timestamp and return an aware UTC datetime."""

    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("UTC timestamp must include a timezone: {!r}".format(value))
    return parsed.astimezone(timezone.utc)


def iso_utc(value: Union[datetime, float]) -> str:
    """Format an aware datetime or Unix seconds with millisecond precision."""

    if isinstance(value, datetime):
        parsed = parse_utc(value)
    else:
        parsed = datetime.fromtimestamp(float(value), tz=timezone.utc)
    return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def write_json(path: Union[str, Path], payload: Mapping[str, Any]) -> None:
    """Write deterministic JSON for a generated analysis product."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")

