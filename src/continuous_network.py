"""Guarded continuous-network retrieval for the nonblind development interval.

The frozen historical model config is read, not modified.  This module permits
only the exact 2025-01-20 development interval declared in the separate
development config.  It has no code path for held-out interval access.
Downloaded miniSEED is a local ignored cache; JSON sidecars retain request and
hash provenance.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from obspy import Stream, read

from .common import iso_utc, parse_utc, sha256_file, utc_now, write_json


def validate_development_access(
    parent: Mapping[str, Any], development: Mapping[str, Any]
) -> Tuple[float, float]:
    """Return exact interval bounds after all access/hash checks pass."""

    parent_path = Path(str(development["parent_config_path"]))
    if not parent_path.is_absolute():
        parent_path = Path(__file__).resolve().parents[1] / parent_path
    if sha256_file(parent_path) != str(development["parent_config_sha256"]):
        raise PermissionError("parent incremental-value config hash changed")
    if parent.get("_config_sha256") != development["parent_config_sha256"]:
        raise PermissionError("loaded parent config hash does not match declaration")
    declared = development["interval"]
    frozen = parent["development_interval"]
    if (
        str(declared["start_utc"]) != str(frozen["start_utc"])
        or str(declared["end_utc"]) != str(frozen["end_utc"])
        or str(declared["role"]) != "nonblind_development_only"
        or str(declared["heldout_access"]) != "FORBIDDEN"
    ):
        raise PermissionError("continuous access is not the exact development interval")
    start_s = parse_utc(str(declared["start_utc"])).timestamp()
    end_s = parse_utc(str(declared["end_utc"])).timestamp()
    if end_s - start_s != float(declared["duration_s"]):
        raise ValueError("declared development duration is inconsistent")
    return start_s, end_s


def source_request_url(
    parent: Mapping[str, Any],
    development: Mapping[str, Any],
    source: Mapping[str, Any],
) -> str:
    """Build one official dataselect request for the guarded interval."""

    start_s, end_s = validate_development_access(parent, development)
    padding = float(
        development["continuous_network"]["request_padding_s"]
    )
    settings = parent["network_array"]
    params = {
        "net": source["network"],
        "sta": ",".join(source["stations"]),
        "loc": source["location"],
        "cha": ",".join(source["channels"]),
        "start": iso_utc(start_s - padding),
        "end": iso_utc(end_s + padding),
    }
    return settings["dataselect_service"] + "?" + urllib.parse.urlencode(params)


def _cache_paths(
    project: Path,
    development: Mapping[str, Any],
    source_name: str,
) -> Tuple[Path, Path]:
    directory = (
        project
        / str(development["continuous_network"]["cache_directory"])
    )
    return (
        directory / (source_name + ".mseed"),
        directory / (source_name + ".provenance.json"),
    )


def download_source(
    project: Path,
    parent: Mapping[str, Any],
    development: Mapping[str, Any],
    source: Mapping[str, Any],
    force: bool = False,
    timeout_s: float = 180.0,
) -> Path:
    """Download and validate one continuous source for development only."""

    validate_development_access(parent, development)
    source_name = str(source["name"])
    waveform_path, provenance_path = _cache_paths(
        project, development, source_name
    )
    waveform_path.parent.mkdir(parents=True, exist_ok=True)
    url = source_request_url(parent, development, source)
    if waveform_path.exists() and waveform_path.stat().st_size > 0 and not force:
        try:
            with provenance_path.open("r", encoding="utf-8") as handle:
                provenance = json.load(handle)
            if (
                provenance.get("request_url") == url
                and provenance.get("parent_config_sha256")
                == parent["_config_sha256"]
                and provenance.get("development_config_sha256")
                == development["_config_sha256"]
            ):
                return waveform_path
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    request = urllib.request.Request(
        url, headers={"User-Agent": "safod-das-repeaters/0.3"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = response.read()
    if not payload:
        raise RuntimeError("empty continuous response for {}".format(source_name))
    temporary = waveform_path.with_suffix(".mseed.partial")
    with temporary.open("wb") as handle:
        handle.write(payload)
    stream = read(str(temporary))
    if not stream:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            "unreadable continuous response for {}".format(source_name)
        )
    temporary.replace(waveform_path)
    write_json(
        provenance_path,
        {
            "stage": "network_continuous_development_download",
            "source_name": source_name,
            "network": source["network"],
            "stations": source["stations"],
            "channels": source["channels"],
            "request_url": url,
            "retrieved_utc": utc_now(),
            "byte_count": len(payload),
            "trace_count": len(stream),
            "minimum_trace_start_utc": iso_utc(
                min(float(trace.stats.starttime) for trace in stream)
            ),
            "maximum_trace_end_utc": iso_utc(
                max(float(trace.stats.endtime) for trace in stream)
            ),
            "parent_config_sha256": parent["_config_sha256"],
            "development_config_sha256": development["_config_sha256"],
            "heldout_waveform_access": 0,
        },
    )
    return waveform_path


def download_development_stream(
    project: Path,
    parent: Mapping[str, Any],
    development: Mapping[str, Any],
    force: bool = False,
) -> Tuple[Stream, List[Dict[str, Any]]]:
    """Return all available continuous sources and explicit availability rows."""

    validate_development_access(parent, development)
    merged = Stream()
    rows: List[Dict[str, Any]] = []
    for source in parent["network_array"]["sources"]:
        source_name = str(source["name"])
        try:
            path = download_source(
                project,
                parent,
                development,
                source,
                force=force,
            )
            stream = read(str(path))
            merged += stream
            rows.append(
                {
                    "source_name": source_name,
                    "network": source["network"],
                    "status": "available",
                    "trace_count": len(stream),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "source_name": source_name,
                    "network": source["network"],
                    "status": "unavailable",
                    "trace_count": 0,
                    "error": "{}: {}".format(type(exc).__name__, exc),
                }
            )
    merged.sort()
    return merged, rows


def source_cache_paths(
    project: Path,
    development: Mapping[str, Any],
    source_names: Sequence[str],
) -> List[Path]:
    return [
        _cache_paths(project, development, name)[0]
        for name in source_names
    ]
