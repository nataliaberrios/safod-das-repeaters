"""Fail-closed access to catalogs for the registered held-out audit."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import iso_utc, sha256_file, utc_now, write_json


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
    "source_url",
    "retrieved_utc",
]
CATALOG_RELEASE_RELATIVE_PATH = Path(
    "outputs/heldout_v2/registration/catalog_audit_runner_release.json"
)
CATALOG_RUNNER_IMPLEMENTATION_PATHS = (
    "src/heldout_catalog_access.py",
    "src/run_heldout_catalog_audit.py",
    "src/release_heldout_catalog_runner.py",
    "tests/test_heldout_catalog_runner.py",
)
SOURCE_LEDGER_FIELDS = [
    "interval_id",
    "status",
    "retrieval_status",
    "request_url",
    "event_row_count",
    "cache_path",
    "cache_sha256",
    "provenance_path",
    "provenance_sha256",
    "http_status",
    "response_sha256",
    "runner_commit_sha",
    "error",
]


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _git(project: Path, arguments: Sequence[str]) -> str:
    return subprocess.run(
        ["git", *map(str, arguments)],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def current_branch(project: Path) -> str:
    branch = _git(project, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        raise PermissionError("catalog access requires an attached Git branch")
    return branch


def configured_remote_url(project: Path, remote: str) -> str:
    url = _git(project, ["config", "--get", "remote.{}.url".format(remote)])
    if not url:
        raise RuntimeError("catalog runner remote URL is absent")
    return url


def remote_branch_sha(project: Path, remote: str, branch: str) -> str:
    reference = "refs/heads/{}".format(branch)
    output = _git(project, ["ls-remote", "--heads", remote, reference])
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != reference:
        raise RuntimeError("catalog runner remote branch did not resolve uniquely")
    sha = rows[0][0]
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("catalog runner remote branch SHA is invalid")
    return sha


def load_catalog_runner_release(project: Path) -> Dict[str, Any]:
    path = project / CATALOG_RELEASE_RELATIVE_PATH
    if not path.is_file():
        raise PermissionError("catalog access denied: runner release is absent")
    return _load_json(path)


def _committed_file_matches(project: Path, head: str, relative: str) -> bool:
    committed = subprocess.run(
        ["git", "show", "{}:{}".format(head, relative)],
        cwd=project,
        check=True,
        capture_output=True,
    ).stdout
    observed = hashlib.sha256((project / relative).read_bytes()).hexdigest()
    return hashlib.sha256(committed).hexdigest() == observed


def validate_catalog_runner_release(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    release: Mapping[str, Any],
    remote: str = "origin",
) -> str:
    """Return the released runner SHA before any catalog row is parsed."""

    if str(release.get("status", "")) != (
        "REMOTE_VERIFIED_FOR_HELDOUT_CATALOG_ACCESS"
    ):
        raise PermissionError("held-out catalog runner is not released")
    if str(release.get("stage", "")) != "heldout_catalog_runner_remote_release":
        raise PermissionError("unexpected catalog runner release stage")
    if str(release.get("catalog_audit_config_sha256", "")) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("catalog runner/config checksum mismatch")

    output = project / str(registration["output"]["registration_directory"])
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    query_path = output / str(registration["output"]["query_manifest_csv"])
    if str(release.get("catalog_registration_status_sha256", "")) != (
        sha256_file(status_path)
    ):
        raise PermissionError("catalog runner/registration checksum mismatch")
    if str(release.get("catalog_query_manifest_sha256", "")) != sha256_file(
        query_path
    ):
        raise PermissionError("catalog runner/query-manifest checksum mismatch")
    if str(registration_status["catalog_audit_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("catalog registration/config checksum mismatch")
    if str(registration_status["catalog_event_row_access_gate"]) != (
        "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
    ):
        raise PermissionError("unexpected preregistration catalog gate")

    runner_sha = str(release.get("runner_commit_sha", ""))
    remote_sha = str(release.get("remote_branch_sha", ""))
    if (
        len(runner_sha) != 40
        or runner_sha != remote_sha
        or not bool(release.get("remote_branch_sha_verified_equal", False))
    ):
        raise PermissionError("catalog runner commit was not remotely verified")
    head = _git(project, ["rev-parse", "HEAD"])
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", runner_sha, head],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise PermissionError("released catalog runner is not an ancestor of HEAD")

    released_files = release.get("runner_implementation_files", {})
    if set(map(str, released_files)) != set(CATALOG_RUNNER_IMPLEMENTATION_PATHS):
        raise PermissionError("catalog runner release file set changed")
    for relative in CATALOG_RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file() or sha256_file(path) != str(
            released_files[relative]
        ):
            raise PermissionError("released catalog runner file changed: " + relative)

    for field in (
        "target_catalog_event_rows_opened_before_release",
        "broader_catalog_event_rows_opened_before_release",
        "heldout_DAS_HDF5_files_opened_before_release",
        "heldout_network_waveform_files_opened_before_release",
        "heldout_family_label_rows_opened_before_release",
    ):
        if int(release.get(field, -1)) != 0:
            raise PermissionError("catalog release access ledger is nonzero")

    branch = current_branch(project)
    if branch != str(registration["release_anchor"]["branch"]):
        raise PermissionError("catalog runner is on an unexpected branch")
    expected_slug = str(registration["release_anchor"]["repository"])
    allowed_urls = {
        "git@github.com:{}.git".format(expected_slug),
        "https://github.com/{}.git".format(expected_slug),
    }
    if configured_remote_url(project, remote) not in allowed_urls:
        raise PermissionError("catalog runner remote changed")
    if remote_branch_sha(project, remote, branch) != head:
        raise PermissionError("catalog release artifact is not pushed remotely")
    if not _committed_file_matches(
        project, head, str(CATALOG_RELEASE_RELATIVE_PATH)
    ):
        raise PermissionError("catalog release artifact differs from HEAD")
    return runner_sha


def _normalize_ncedc_utc(value: str) -> str:
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


def parse_ncedc_text(text: str, source_url: str) -> List[Dict[str, Any]]:
    """Parse an NCEDC text response without importing waveform/DAS modules."""

    base_fields = CATALOG_FIELDS[:14]
    retrieved = utc_now()
    rows: List[Dict[str, Any]] = []
    for line in io.StringIO(text):
        if not line.strip() or line.startswith("#"):
            continue
        values = [value.strip() for value in line.rstrip("\n").split("|")]
        if len(values) < len(base_fields):
            raise RuntimeError("NCEDC catalog row has fewer than 14 fields")
        row: Dict[str, Any] = dict(zip(base_fields, values[:14]))
        row["origin_time"] = _normalize_ncedc_utc(str(row["origin_time"]))
        for field in ("latitude", "longitude", "depth_km", "magnitude"):
            row[field] = float(row[field])
        row["source_url"] = source_url
        row["retrieved_utc"] = retrieved
        rows.append(row)
    return rows


def _validate_catalog_rows(
    rows: Sequence[Mapping[str, Any]], label: str
) -> None:
    identifiers = [str(row.get("event_id", "")) for row in rows]
    if any(not identifier for identifier in identifiers):
        raise RuntimeError(label + " contains an empty event ID")
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError(label + " contains duplicate event IDs")
    from .common import parse_utc

    for row in rows:
        parse_utc(str(row["origin_time"])).timestamp()
        for field in ("latitude", "longitude", "depth_km", "magnitude"):
            float(row[field])


def _read_normalized_catalog(path: Path) -> List[Dict[str, str]]:
    rows = _read_csv(path)
    _validate_catalog_rows(rows, str(path))
    return rows


def _write_normalized_catalog(
    path: Path, rows: Sequence[Mapping[str, Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=CATALOG_FIELDS,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def _manifest_rows(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
) -> List[Dict[str, str]]:
    output = project / str(registration["output"]["registration_directory"])
    path = output / str(registration["output"]["query_manifest_csv"])
    if sha256_file(path) != str(
        registration_status["broader_catalog_query_manifest_sha256"]
    ):
        raise RuntimeError("catalog query manifest changed")
    rows = _read_csv(path)
    if len(rows) != int(registration_status["broader_catalog_query_count"]):
        raise RuntimeError("catalog query manifest row count changed")
    if any(int(row["catalog_event_rows_opened_at_registration"]) != 0 for row in rows):
        raise PermissionError("query manifest reports prerelease catalog access")
    return rows


def _target_catalog_after_release(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
) -> List[Dict[str, str]]:
    declaration = registration["frozen_inputs"]["target_catalog"]
    path = project / str(declaration["path"])
    if sha256_file(path) != str(declaration["sha256"]):
        raise RuntimeError("target catalog changed after registration")
    rows = _read_normalized_catalog(path)
    if len(rows) != int(registration_status["target_catalog_expected_event_row_count"]):
        raise RuntimeError("target catalog row count differs from preregistration")
    return rows


def _obtain_broader_after_release(
    project: Path,
    registration: Mapping[str, Any],
    manifest: Mapping[str, Any],
    runner_sha: str,
    timeout_s: float,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    interval_id = str(manifest["interval_id"])
    url = str(manifest["request_url"])
    cache_path = project / str(manifest["cache_path"])
    provenance_path = project / str(manifest["provenance_path"])
    if cache_path.is_file() and provenance_path.is_file():
        provenance = _load_json(provenance_path)
        if (
            str(provenance.get("request_url", "")) == url
            and str(provenance.get("interval_id", "")) == interval_id
            and str(provenance.get("catalog_audit_config_sha256", ""))
            == str(registration["_config_sha256"])
            and str(provenance.get("runner_commit_sha", "")) == runner_sha
            and str(provenance.get("cache_sha256", "")) == sha256_file(cache_path)
        ):
            rows = _read_normalized_catalog(cache_path)
            if len(rows) != int(provenance["event_row_count"]):
                raise RuntimeError("cached broader catalog row count changed")
            return rows, {
                "interval_id": interval_id,
                "status": "PASS",
                "retrieval_status": "verified_cache_hit",
                "request_url": url,
                "event_row_count": len(rows),
                "cache_path": str(cache_path),
                "cache_sha256": sha256_file(cache_path),
                "provenance_path": str(provenance_path),
                "provenance_sha256": sha256_file(provenance_path),
                "http_status": provenance.get("http_status", ""),
                "response_sha256": provenance.get("response_sha256", ""),
                "runner_commit_sha": runner_sha,
                "error": "",
            }

    request = urllib.request.Request(
        url, headers={"User-Agent": "safod-das-repeaters/0.5"}
    )
    http_status = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            http_status = int(getattr(response, "status", 200))
            payload = response.read()
    except urllib.error.HTTPError as exc:
        if int(exc.code) != 204:
            raise
        http_status = 204
        payload = b""
    response_sha256 = hashlib.sha256(payload).hexdigest()
    rows_raw = parse_ncedc_text(payload.decode("utf-8"), url) if payload else []
    _validate_catalog_rows(rows_raw, interval_id + " broader catalog")
    _write_normalized_catalog(cache_path, rows_raw)
    write_json(
        provenance_path,
        {
            "stage": "heldout_broader_catalog_download_after_remote_release",
            "access_role": "post_union_catalog_adjudication_only",
            "interval_id": interval_id,
            "request_url": url,
            "retrieved_utc": utc_now(),
            "http_status": http_status,
            "response_sha256": response_sha256,
            "event_row_count": len(rows_raw),
            "cache_path": str(cache_path),
            "cache_sha256": sha256_file(cache_path),
            "catalog_audit_config_sha256": registration["_config_sha256"],
            "runner_commit_sha": runner_sha,
            "heldout_DAS_HDF5_files_opened": 0,
            "network_waveform_files_opened": 0,
            "family_label_rows_opened": 0,
        },
    )
    rows = _read_normalized_catalog(cache_path)
    return rows, {
        "interval_id": interval_id,
        "status": "PASS",
        "retrieval_status": "downloaded",
        "request_url": url,
        "event_row_count": len(rows),
        "cache_path": str(cache_path),
        "cache_sha256": sha256_file(cache_path),
        "provenance_path": str(provenance_path),
        "provenance_sha256": sha256_file(provenance_path),
        "http_status": http_status,
        "response_sha256": response_sha256,
        "runner_commit_sha": runner_sha,
        "error": "",
    }


def acquire_registered_catalogs(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    release: Mapping[str, Any],
    timeout_s: float = 90.0,
) -> Tuple[
    List[Dict[str, str]],
    Dict[str, List[Dict[str, str]]],
    List[Dict[str, Any]],
    str,
]:
    """Validate the pushed release, then open all registered catalog rows."""

    runner_sha = validate_catalog_runner_release(
        project, registration, registration_status, release
    )
    target = _target_catalog_after_release(
        project, registration, registration_status
    )
    broader: Dict[str, List[Dict[str, str]]] = {}
    ledger: List[Dict[str, Any]] = []
    for manifest in _manifest_rows(project, registration, registration_status):
        rows, source = _obtain_broader_after_release(
            project,
            registration,
            manifest,
            runner_sha,
            timeout_s,
        )
        interval_id = str(manifest["interval_id"])
        if interval_id in broader:
            raise RuntimeError("duplicate broader catalog interval")
        broader[interval_id] = rows
        ledger.append(source)
    return target, broader, ledger, runner_sha
