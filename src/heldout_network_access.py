"""Guarded access for the registered network-first held-out experiment.

The downloader requires a release artifact whose runner commit was verified on
the private remote.  This module does not import a catalog or any DAS reader.
Historical template waveforms are hash-pinned separately from held-out network
waveforms.
"""

from __future__ import annotations

import copy
import csv
import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from obspy import Stream, read

from .common import iso_utc, parse_utc, sha256_file, utc_now, write_json
from .register_heldout_network import validate_heldout_interval_rows


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
RUNNER_RELEASE_RELATIVE_PATH = Path(
    "outputs/heldout_v2/registration/network_runner_release.json"
)
RUNNER_IMPLEMENTATION_PATHS = (
    "src/heldout_network_access.py",
    "src/run_heldout_network_interval.py",
    "src/freeze_heldout_network_candidates.py",
    "src/release_heldout_network_runner.py",
    "heldout_network_interval_job.sh",
    "tests/test_heldout_network_runner.py",
)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _git_ancestor(project: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def load_runner_release(project: Path) -> Dict[str, Any]:
    """Load the mandatory post-push release artifact."""

    path = project / RUNNER_RELEASE_RELATIVE_PATH
    if not path.is_file():
        raise PermissionError(
            "held-out network access denied: runner release is absent"
        )
    return _load_json(path)


def validate_runner_release(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    release: Mapping[str, Any],
) -> str:
    """Return the released runner SHA after all access checks pass."""

    if str(release.get("status", "")) != (
        "REMOTE_VERIFIED_FOR_HELDOUT_NETWORK_ACCESS"
    ):
        raise PermissionError("held-out network runner is not released")
    if str(release.get("stage", "")) != (
        "heldout_network_runner_remote_release"
    ):
        raise PermissionError("unexpected held-out network release stage")
    if str(release.get("heldout_network_config_sha256", "")) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("runner release/config checksum mismatch")

    status_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["registration_status_json"])
    )
    inventory_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["template_input_inventory_csv"])
    )
    if str(release.get("network_registration_status_sha256", "")) != (
        sha256_file(status_path)
    ):
        raise PermissionError("runner release/registration checksum mismatch")
    if str(release.get("template_input_inventory_sha256", "")) != (
        sha256_file(inventory_path)
    ):
        raise PermissionError("runner release/template inventory mismatch")
    if str(registration_status["heldout_network_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("registration status/config checksum mismatch")
    if str(
        registration_status["historical_template_inventory_sha256"]
    ) != sha256_file(inventory_path):
        raise PermissionError("registration template inventory changed")
    if not bool(
        registration_status["heldout_intervals_all_sealed_at_registration"]
    ):
        raise PermissionError("registration reports an unsealed interval")
    if str(registration_status["heldout_network_access_gate"]) != (
        "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
    ):
        raise PermissionError("unexpected registration access gate")

    runner_sha = str(release.get("runner_commit_sha", ""))
    remote_sha = str(release.get("remote_branch_sha", ""))
    if (
        len(runner_sha) != 40
        or runner_sha != remote_sha
        or not bool(release.get("remote_branch_sha_verified_equal", False))
    ):
        raise PermissionError("runner commit was not verified on the remote")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not _git_ancestor(project, runner_sha, head):
        raise PermissionError("released runner commit is not an ancestor of HEAD")

    expected_paths = set(RUNNER_IMPLEMENTATION_PATHS)
    released_files = release.get("runner_implementation_files", {})
    if set(map(str, released_files)) != expected_paths:
        raise PermissionError("runner release file set changed")
    for relative in RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError("released runner file is absent: {}".format(path))
        if sha256_file(path) != str(released_files[relative]):
            raise PermissionError(
                "released runner file changed: {}".format(relative)
            )

    for field in (
        "heldout_network_waveform_files_opened_before_release",
        "heldout_DAS_HDF5_files_opened_before_release",
        "heldout_catalog_event_rows_opened_before_release",
        "heldout_family_label_rows_opened_before_release",
    ):
        if int(release.get(field, -1)) != 0:
            raise PermissionError("runner release access ledger is nonzero")
    return runner_sha


def registered_interval_rows(
    project: Path, registration: Mapping[str, Any]
) -> List[Dict[str, str]]:
    """Load and revalidate only the frozen interval metadata table."""

    declaration = registration["frozen_inputs"]["heldout_intervals"]
    path = project / str(declaration["path"])
    if sha256_file(path) != str(declaration["sha256"]):
        raise RuntimeError("held-out interval metadata checksum changed")
    rows = _read_csv(path)
    validate_heldout_interval_rows(rows, registration)
    return rows


def registered_interval(
    project: Path,
    registration: Mapping[str, Any],
    interval_id: str,
) -> Dict[str, str]:
    """Return one registered interval by exact identifier."""

    identifier = str(interval_id)
    if not SAFE_NAME.fullmatch(identifier):
        raise ValueError("unsafe held-out interval identifier")
    rows = registered_interval_rows(project, registration)
    matches = [row for row in rows if str(row["interval_id"]) == identifier]
    if len(matches) != 1:
        raise KeyError("interval is not uniquely registered: {}".format(identifier))
    return matches[0]


def interval_development_view(
    development: Mapping[str, Any],
    registration: Mapping[str, Any],
    interval: Mapping[str, Any],
) -> Dict[str, Any]:
    """Adapt frozen development mechanics to one held-out interval."""

    view = copy.deepcopy(dict(development))
    view["interval"] = {
        "start_utc": str(interval["start_utc"]),
        "end_utc": str(interval["end_utc"]),
        "duration_s": float(interval["duration_s"]),
        "role": "heldout_network_only",
        "heldout_access": "REMOTE_RUNNER_RELEASE_REQUIRED",
    }
    view["continuous_network"] = {
        "request_padding_s": float(
            registration["waveform_acquisition"]["request_padding_s"]
        ),
        "cache_directory": str(
            registration["waveform_acquisition"]["cache_directory"]
        ),
        "source_settings_inherited_from_parent": True,
        "merge_maximum_gap_s": float(
            registration["waveform_acquisition"]["merge_maximum_gap_s"]
        ),
    }
    return view


def source_request_url(
    parent: Mapping[str, Any],
    registration: Mapping[str, Any],
    interval: Mapping[str, Any],
    source: Mapping[str, Any],
) -> str:
    """Build one exact official request without opening a waveform."""

    start_s = parse_utc(str(interval["start_utc"])).timestamp()
    end_s = parse_utc(str(interval["end_utc"])).timestamp()
    padding = float(registration["waveform_acquisition"]["request_padding_s"])
    params = {
        "net": str(source["network"]),
        "sta": ",".join(map(str, source["stations"])),
        "loc": str(source["location"]),
        "cha": ",".join(map(str, source["channels"])),
        "start": iso_utc(start_s - padding),
        "end": iso_utc(end_s + padding),
    }
    return str(parent["network_array"]["dataselect_service"]) + "?" + (
        urllib.parse.urlencode(params)
    )


def _cache_paths(
    project: Path,
    registration: Mapping[str, Any],
    interval_id: str,
    source_name: str,
) -> Tuple[Path, Path]:
    if not SAFE_NAME.fullmatch(interval_id) or not SAFE_NAME.fullmatch(source_name):
        raise ValueError("unsafe held-out cache identifier")
    directory = (
        project
        / str(registration["waveform_acquisition"]["cache_directory"])
        / interval_id
    )
    return (
        directory / (source_name + ".mseed"),
        directory / (source_name + ".provenance.json"),
    )


def download_heldout_source(
    project: Path,
    parent: Mapping[str, Any],
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    release: Mapping[str, Any],
    interval: Mapping[str, Any],
    source: Mapping[str, Any],
    force: bool = False,
    timeout_s: float = 240.0,
) -> Path:
    """Download one source only after validating the remote release."""

    runner_sha = validate_runner_release(
        project, registration, registration_status, release
    )
    interval_id = str(interval["interval_id"])
    registered = registered_interval(project, registration, interval_id)
    if dict(registered) != dict(interval):
        raise PermissionError("held-out interval metadata differs from registration")
    source_name = str(source["name"])
    waveform_path, provenance_path = _cache_paths(
        project, registration, interval_id, source_name
    )
    waveform_path.parent.mkdir(parents=True, exist_ok=True)
    url = source_request_url(parent, registration, interval, source)
    if waveform_path.is_file() and waveform_path.stat().st_size > 0 and not force:
        try:
            provenance = _load_json(provenance_path)
            if (
                str(provenance.get("request_url", "")) == url
                and str(provenance.get("heldout_network_config_sha256", ""))
                == str(registration["_config_sha256"])
                and str(provenance.get("runner_commit_sha", "")) == runner_sha
                and str(provenance.get("access_role", ""))
                == "registered_heldout_network_only"
                and int(provenance.get("byte_count", -1))
                == waveform_path.stat().st_size
                and str(provenance.get("waveform_sha256", ""))
                == sha256_file(waveform_path)
            ):
                return waveform_path
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    request = urllib.request.Request(
        url, headers={"User-Agent": "safod-das-repeaters/0.4"}
    )
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        payload = response.read()
    if not payload:
        raise RuntimeError(
            "empty held-out network response for {} {}".format(
                interval_id, source_name
            )
        )
    temporary = waveform_path.with_suffix(".mseed.partial")
    with temporary.open("wb") as handle:
        handle.write(payload)
    stream = read(str(temporary))
    if not stream:
        temporary.unlink(missing_ok=True)
        raise RuntimeError(
            "unreadable held-out response for {} {}".format(
                interval_id, source_name
            )
        )
    temporary.replace(waveform_path)
    write_json(
        provenance_path,
        {
            "stage": "registered_heldout_network_download",
            "access_role": "registered_heldout_network_only",
            "interval_id": interval_id,
            "interval_start_utc": str(interval["start_utc"]),
            "interval_end_utc": str(interval["end_utc"]),
            "source_name": source_name,
            "network": str(source["network"]),
            "stations": list(map(str, source["stations"])),
            "channels": list(map(str, source["channels"])),
            "request_url": url,
            "retrieved_utc": utc_now(),
            "byte_count": len(payload),
            "waveform_sha256": sha256_file(waveform_path),
            "trace_count": len(stream),
            "minimum_trace_start_utc": iso_utc(
                min(float(trace.stats.starttime) for trace in stream)
            ),
            "maximum_trace_end_utc": iso_utc(
                max(float(trace.stats.endtime) for trace in stream)
            ),
            "parent_config_sha256": parent["_config_sha256"],
            "heldout_network_config_sha256": registration["_config_sha256"],
            "runner_commit_sha": runner_sha,
            "heldout_DAS_HDF5_files_opened": 0,
            "catalog_event_rows_opened": 0,
            "family_label_rows_opened": 0,
        },
    )
    return waveform_path


def download_heldout_stream(
    project: Path,
    parent: Mapping[str, Any],
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    release: Mapping[str, Any],
    interval: Mapping[str, Any],
    force: bool = False,
) -> Tuple[Stream, List[Dict[str, Any]]]:
    """Return all available registered network sources for one interval."""

    validate_runner_release(project, registration, registration_status, release)
    merged = Stream()
    rows: List[Dict[str, Any]] = []
    for source in parent["network_array"]["sources"]:
        source_name = str(source["name"])
        request_url = source_request_url(
            parent, registration, interval, source
        )
        base_row = {
            "interval_id": str(interval["interval_id"]),
            "interval_start_utc": str(interval["start_utc"]),
            "interval_end_utc": str(interval["end_utc"]),
            "source_name": source_name,
            "network": str(source["network"]),
            "request_url": request_url,
            "request_padding_s": float(
                registration["waveform_acquisition"]["request_padding_s"]
            ),
            "access_role": "registered_heldout_network_only",
        }
        try:
            path = download_heldout_source(
                project,
                parent,
                registration,
                registration_status,
                release,
                interval,
                source,
                force=force,
            )
            stream = read(str(path))
            provenance_path = path.with_suffix(".provenance.json")
            merged += stream
            rows.append(
                {
                    **base_row,
                    "status": "available",
                    "trace_count": len(stream),
                    "waveform_path": str(path),
                    "waveform_sha256": sha256_file(path),
                    "provenance_path": str(provenance_path),
                    "provenance_sha256": sha256_file(provenance_path),
                    "error": "",
                }
            )
        except Exception as exc:
            rows.append(
                {
                    **base_row,
                    "status": "unavailable",
                    "trace_count": 0,
                    "waveform_path": "",
                    "waveform_sha256": "",
                    "provenance_path": "",
                    "provenance_sha256": "",
                    "error": "{}: {}".format(type(exc).__name__, exc),
                }
            )
    merged.sort()
    return merged, rows


def load_template_metadata(
    project: Path, registration: Mapping[str, Any]
) -> List[Dict[str, str]]:
    """Read only event ID, origin time, and PASS state from the frozen table."""

    declaration = registration["frozen_inputs"][
        "development_template_inventory"
    ]
    path = project / str(declaration["path"])
    if sha256_file(path) != str(declaration["sha256"]):
        raise RuntimeError("development template metadata changed")
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        event_index = header.index("event_id")
        origin_index = header.index("origin_time")
        status_index = header.index("status")
        observed = {
            row[event_index]: row[origin_index]
            for row in reader
            if row[status_index] == "PASS"
        }
    events = []
    for event_id in registration["historical_template_bank"]["event_ids"]:
        identifier = str(event_id)
        if identifier not in observed:
            raise RuntimeError("registered template metadata is missing")
        events.append(
            {
                "event_id": identifier,
                "origin_time": observed[identifier],
                "sequence_id": "opaque_historical_template",
            }
        )
    expected_ids = {
        str(event_id)
        for event_id in registration["historical_template_bank"]["event_ids"]
    }
    if set(observed) != expected_ids:
        raise RuntimeError("development template PASS membership changed")
    return events


def load_historical_template_streams(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
) -> Dict[str, Stream]:
    """Load only hash-pinned historical template waveforms."""

    inventory_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["template_input_inventory_csv"])
    )
    observed_hash = sha256_file(inventory_path)
    if observed_hash != str(
        registration_status["historical_template_inventory_sha256"]
    ):
        raise RuntimeError("historical template inventory changed")
    rows = _read_csv(inventory_path)
    streams = {
        str(event_id): Stream()
        for event_id in registration["historical_template_bank"]["event_ids"]
    }
    for row in rows:
        if str(row["availability"]) != "available":
            continue
        event_id = str(row["event_id"])
        if event_id not in streams:
            raise RuntimeError("unregistered event in template inventory")
        path = project / str(row["waveform_path"])
        provenance_path = project / str(row["provenance_path"])
        if sha256_file(path) != str(row["waveform_sha256"]):
            raise RuntimeError("historical template waveform changed")
        if sha256_file(provenance_path) != str(row["provenance_sha256"]):
            raise RuntimeError("historical template provenance changed")
        streams[event_id] += read(str(path))
    for event_id, stream in streams.items():
        if not stream:
            raise RuntimeError(
                "registered historical template has no waveform: {}".format(
                    event_id
                )
            )
        stream.sort()
    return streams
