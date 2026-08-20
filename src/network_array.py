"""Multi-network event-window retrieval with prospective-access guardrails.

Historical exact-ID family events may be downloaded for model training.
Prospective 2024-2025 events require a verified frozen-model artifact first.
All waveform files remain ignored data products; compact request sidecars are
trackable provenance.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from obspy import Stream, read

from .common import iso_utc, parse_utc, utc_now, write_json


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def source_request_url(
    event: Mapping[str, Any],
    settings: Mapping[str, Any],
    source: Mapping[str, Any],
) -> str:
    origin_s = parse_utc(str(event["origin_time"])).timestamp()
    request_window = settings["request_window_s"]
    params = {
        "net": source["network"],
        "sta": ",".join(source["stations"]),
        "loc": source["location"],
        "cha": ",".join(source["channels"]),
        "start": iso_utc(origin_s + float(request_window[0])),
        "end": iso_utc(origin_s + float(request_window[1])),
    }
    return settings["dataselect_service"] + "?" + urllib.parse.urlencode(params)


def _cache_paths(
    cache_root: Path, event_id: str, source_name: str
) -> Tuple[Path, Path]:
    if not SAFE_NAME.match(str(event_id)) or not SAFE_NAME.match(source_name):
        raise ValueError("unsafe event or source cache name")
    event_dir = cache_root / str(event_id)
    return (
        event_dir / (source_name + ".mseed"),
        event_dir / (source_name + ".provenance.json"),
    )


def _verify_prospective_release(
    frozen_model_path: Path | None, config_sha256: str
) -> Dict[str, Any]:
    if frozen_model_path is None or not Path(frozen_model_path).exists():
        raise PermissionError(
            "prospective waveform access denied: frozen model artifact is absent"
        )
    with Path(frozen_model_path).open("r", encoding="utf-8") as handle:
        model = json.load(handle)
    if model.get("freeze_status") != "FROZEN_FOR_PROSPECTIVE_SCORING":
        raise PermissionError(
            "prospective waveform access denied: model freeze status is invalid"
        )
    if model.get("config_sha256") != config_sha256:
        raise PermissionError(
            "prospective waveform access denied: config hash differs from model"
        )
    return model


def download_source(
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    source: Mapping[str, Any],
    cache_root: Path,
    access_role: str,
    frozen_model_path: Path | None = None,
    force: bool = False,
    timeout_s: float = 90.0,
) -> Path:
    """Retrieve one source while enforcing the historical/prospective boundary."""

    if access_role == "historical_exact_id_training":
        model: Dict[str, Any] = {}
    elif access_role in (
        "prospective_scoring",
        "external_exact_id_validation",
    ):
        model = _verify_prospective_release(
            frozen_model_path, str(config["_config_sha256"])
        )
    else:
        raise ValueError("unsupported waveform access role {}".format(access_role))

    settings = config["network_array"]
    source_name = str(source["name"])
    waveform_path, provenance_path = _cache_paths(
        cache_root, str(event["event_id"]), source_name
    )
    waveform_path.parent.mkdir(parents=True, exist_ok=True)
    url = source_request_url(event, settings, source)
    if waveform_path.exists() and waveform_path.stat().st_size > 0 and not force:
        try:
            with provenance_path.open("r", encoding="utf-8") as handle:
                provenance = json.load(handle)
            if (
                provenance.get("request_url") == url
                and provenance.get("config_sha256")
                == config["_config_sha256"]
                and provenance.get("access_role") == access_role
            ):
                return waveform_path
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    request = urllib.request.Request(
        url, headers={"User-Agent": "safod-das-repeaters/0.2"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = response.read()
    if not payload:
        raise RuntimeError(
            "empty {} response for event {}".format(
                source_name, event["event_id"]
            )
        )
    temporary = waveform_path.with_suffix(".mseed.partial")
    with temporary.open("wb") as handle:
        handle.write(payload)
    stream = read(str(temporary))
    if not stream:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            "unreadable {} response for event {}".format(
                source_name, event["event_id"]
            )
        )
    temporary.replace(waveform_path)
    write_json(
        provenance_path,
        {
            "event_id": str(event["event_id"]),
            "origin_time": event["origin_time"],
            "source_name": source_name,
            "network": source["network"],
            "stations": source["stations"],
            "channels": source["channels"],
            "request_url": url,
            "retrieved_utc": utc_now(),
            "byte_count": len(payload),
            "trace_count": len(stream),
            "config_sha256": config["_config_sha256"],
            "access_role": access_role,
            "frozen_model_training_population_sha256": model.get(
                "training_population_sha256", ""
            ),
            "scientific_use": "normalized waveform shape and differential arrival-time features",
        },
    )
    return waveform_path


def download_event(
    event: Mapping[str, Any],
    config: Mapping[str, Any],
    cache_root: Path,
    access_role: str,
    frozen_model_path: Path | None = None,
    force: bool = False,
) -> Tuple[Stream, List[Dict[str, Any]]]:
    """Download all declared network sources and return a merged stream."""

    merged = Stream()
    availability: List[Dict[str, Any]] = []
    for source in config["network_array"]["sources"]:
        source_name = str(source["name"])
        try:
            path = download_source(
                event,
                config,
                source,
                cache_root,
                access_role,
                frozen_model_path=frozen_model_path,
                force=force,
            )
            stream = read(str(path))
            merged += stream
            availability.append(
                {
                    "event_id": str(event["event_id"]),
                    "origin_time": event["origin_time"],
                    "source_name": source_name,
                    "network": source["network"],
                    "status": "available",
                    "trace_count": len(stream),
                    "error": "",
                }
            )
        except Exception as exc:
            availability.append(
                {
                    "event_id": str(event["event_id"]),
                    "origin_time": event["origin_time"],
                    "source_name": source_name,
                    "network": source["network"],
                    "status": "unavailable",
                    "trace_count": 0,
                    "error": "{}: {}".format(type(exc).__name__, exc),
                }
            )
    merged.sort()
    return merged, availability


def source_names(settings: Mapping[str, Any]) -> Sequence[str]:
    return [str(source["name"]) for source in settings["sources"]]
