"""Post-release access guards for the independent held-out DAS runner.

Only the preregistered manifest and interval ledgers are parsed here.  Network
candidate, catalog-association, and family tables are never imported or read.
"""

from __future__ import annotations

import csv
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from .common import load_config, parse_utc, sha256_file
from .das_v2 import validate_v2_inheritance


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")
RUNNER_RELEASE_RELATIVE_PATH = Path(
    "outputs/heldout_v2/registration/das_runner_release.json"
)
RUNNER_IMPLEMENTATION_PATHS = (
    "src/common.py",
    "src/h5io.py",
    "src/das_continuous_detection.py",
    "src/das_v2.py",
    "src/heldout_das_runner.py",
    "src/heldout_das_runner_access.py",
    "src/run_heldout_das_interval.py",
    "src/freeze_heldout_das_candidates.py",
    "src/release_heldout_das_runner.py",
    "heldout_das_interval_job.sh",
    "tests/test_heldout_das_runner.py",
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
    """Deny waveform access until the post-push release artifact exists."""

    path = project / RUNNER_RELEASE_RELATIVE_PATH
    if not path.is_file():
        raise PermissionError(
            "held-out DAS access denied: runner release is absent"
        )
    return _load_json(path)


def validate_runner_release(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    release: Mapping[str, Any],
) -> str:
    """Return the remote-verified runner SHA after every gate passes."""

    if str(release.get("status", "")) != (
        "REMOTE_VERIFIED_FOR_HELDOUT_DAS_ACCESS"
    ):
        raise PermissionError("held-out DAS runner is not released")
    if str(release.get("stage", "")) != "heldout_DAS_runner_remote_release":
        raise PermissionError("unexpected held-out DAS release stage")
    if str(release.get("heldout_DAS_config_sha256", "")) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("DAS runner release/config checksum mismatch")

    output = project / str(registration["output"]["registration_directory"])
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    manifest_path = output / str(
        registration["output"]["manifest_selection_csv"]
    )
    interval_path = output / str(
        registration["output"]["interval_selection_csv"]
    )
    expected_hashes = {
        "DAS_registration_status_sha256": sha256_file(status_path),
        "DAS_manifest_selection_sha256": sha256_file(manifest_path),
        "DAS_interval_selection_sha256": sha256_file(interval_path),
    }
    for field, expected in expected_hashes.items():
        if str(release.get(field, "")) != expected:
            raise PermissionError("DAS release input changed: " + field)
    if str(registration_status["heldout_DAS_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("DAS registration status/config mismatch")
    if str(registration_status["manifest_selection_sha256"]) != sha256_file(
        manifest_path
    ):
        raise PermissionError("registered DAS manifest selection changed")
    if str(registration_status["interval_selection_sha256"]) != sha256_file(
        interval_path
    ):
        raise PermissionError("registered DAS interval selection changed")
    if str(registration_status["registration_status"]) != (
        "FROZEN_FOR_HELDOUT_DAS_RUNNER_IMPLEMENTATION_ONLY"
    ):
        raise PermissionError("unexpected DAS registration state")
    if str(registration_status["heldout_DAS_waveform_access_gate"]) != (
        "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
    ):
        raise PermissionError("unexpected DAS registration access gate")

    runner_sha = str(release.get("runner_commit_sha", ""))
    remote_sha = str(release.get("remote_branch_sha", ""))
    if (
        len(runner_sha) != 40
        or runner_sha != remote_sha
        or not bool(release.get("remote_branch_sha_verified_equal", False))
    ):
        raise PermissionError("DAS runner commit was not remote-verified")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not _git_ancestor(project, runner_sha, head):
        raise PermissionError("released DAS runner is not an ancestor of HEAD")

    released_files = release.get("runner_implementation_files", {})
    if set(map(str, released_files)) != set(RUNNER_IMPLEMENTATION_PATHS):
        raise PermissionError("DAS runner release file set changed")
    for relative in RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError("released DAS runner file is absent: " + relative)
        if sha256_file(path) != str(released_files[relative]):
            raise PermissionError("released DAS runner file changed: " + relative)

    for field in (
        "heldout_DAS_HDF5_files_opened_before_release",
        "heldout_DAS_HDF5_headers_opened_before_release",
        "heldout_DAS_HDF5_datasets_opened_before_release",
        "network_candidate_table_rows_opened_before_release",
        "catalog_association_table_rows_opened_before_release",
        "network_or_catalog_candidate_time_fields_read_before_release",
        "heldout_family_label_rows_opened_before_release",
    ):
        if int(release.get(field, -1)) != 0:
            raise PermissionError("DAS release access ledger is nonzero")
    return runner_sha


def load_frozen_detector_configs(
    project: Path, registration: Mapping[str, Any]
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Load only checksum-pinned DAS v1/v2 configs."""

    configs: List[Dict[str, Any]] = []
    for name in ("DAS_v1_config", "DAS_v2_config"):
        declaration = registration["frozen_inputs"][name]
        path = project / str(declaration["path"])
        if sha256_file(path) != str(declaration["sha256"]):
            raise RuntimeError("frozen detector config changed: " + name)
        configs.append(load_config(path))
    v1, v2 = configs
    validate_v2_inheritance(v2, v1)
    return v1, v2


def registered_interval_rows(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
) -> List[Dict[str, str]]:
    """Load the compact manifest-derived interval ledger only."""

    path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["interval_selection_csv"])
    )
    if sha256_file(path) != str(registration_status["interval_selection_sha256"]):
        raise RuntimeError("registered DAS interval ledger changed")
    rows = _read_csv(path)
    expected_ids = list(map(str, registration["heldout_population"]["interval_ids"]))
    if [str(row["interval_id"]) for row in rows] != expected_ids:
        raise RuntimeError("registered DAS interval order changed")
    if len(rows) != int(registration["heldout_population"]["interval_count"]):
        raise RuntimeError("registered DAS interval count changed")
    previous_end = float("-inf")
    for row in rows:
        if str(row["status"]) != "PASS":
            raise PermissionError("registered DAS interval did not pass selection")
        start_s = parse_utc(str(row["interval_start_utc"])).timestamp()
        end_s = parse_utc(str(row["interval_end_utc"])).timestamp()
        if start_s < previous_end:
            raise RuntimeError("registered DAS intervals overlap or are unordered")
        previous_end = end_s
        if not math.isclose(
            end_s - start_s,
            float(registration["heldout_population"]["interval_duration_s"]),
            rel_tol=0.0,
            abs_tol=1.0e-6,
        ):
            raise RuntimeError("registered DAS interval duration changed")
        for field in (
            "network_candidate_time_fields_read",
            "catalog_event_time_fields_read",
            "family_label_fields_read",
            "hdf5_files_opened",
            "hdf5_headers_opened",
            "hdf5_datasets_opened",
        ):
            if int(row[field]) != 0:
                raise PermissionError("DAS interval registration ledger is nonzero")
    return rows


def registered_interval(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    interval_id: str,
) -> Dict[str, str]:
    identifier = str(interval_id)
    if not SAFE_NAME.fullmatch(identifier):
        raise ValueError("unsafe held-out DAS interval identifier")
    rows = registered_interval_rows(project, registration, registration_status)
    matches = [row for row in rows if str(row["interval_id"]) == identifier]
    if len(matches) != 1:
        raise KeyError("DAS interval is not uniquely registered: " + identifier)
    return matches[0]


def registered_manifest_rows(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    interval: Mapping[str, Any],
) -> List[Dict[str, str]]:
    """Return one interval's exact file list after metadata-only rechecks."""

    path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["manifest_selection_csv"])
    )
    if sha256_file(path) != str(registration_status["manifest_selection_sha256"]):
        raise RuntimeError("registered DAS manifest ledger changed")
    identifier = str(interval["interval_id"])
    rows = [row for row in _read_csv(path) if str(row["interval_id"]) == identifier]
    if len(rows) != int(interval["selected_manifest_record_count"]):
        raise RuntimeError("registered DAS file count changed")
    expected_indices = list(range(1, len(rows) + 1))
    if [int(row["interval_file_index"]) for row in rows] != expected_indices:
        raise RuntimeError("registered DAS file order changed")
    archive_root = Path(
        registration["manifest"]["path_rewrites"][0]["filesystem_prefix"]
    ).resolve()
    for row in rows:
        source = Path(str(row["path"]))
        try:
            source.resolve().relative_to(archive_root)
        except ValueError as exc:
            raise PermissionError("registered DAS path escaped archive root") from exc
        if str(row["file_exists"]).lower() != "true" or not source.is_file():
            raise FileNotFoundError("registered held-out DAS file is missing")
        if source.stat().st_size != int(row["size_bytes"]):
            raise RuntimeError("registered held-out DAS file size changed")
        for field in (
            "network_candidate_time_fields_read",
            "catalog_event_time_fields_read",
            "family_label_fields_read",
        ):
            if int(row[field]) != 0:
                raise PermissionError("comparison/family field leaked into selection")
        for field in (
            "hdf5_file_opened",
            "hdf5_header_opened",
            "hdf5_dataset_opened",
        ):
            if str(row[field]).lower() != "false":
                raise PermissionError("pre-waveform DAS selection ledger changed")
    coverage_start = parse_utc(str(rows[0]["manifest_start_utc"])).timestamp()
    coverage_end = max(
        parse_utc(str(row["manifest_end_utc"])).timestamp() for row in rows
    )
    if coverage_start > parse_utc(str(interval["padded_request_start_utc"])).timestamp():
        raise RuntimeError("DAS selection no longer covers padded start")
    if coverage_end < parse_utc(str(interval["padded_request_end_utc"])).timestamp():
        raise RuntimeError("DAS selection no longer covers padded end")
    return rows


def interval_output_paths(
    project: Path, registration: Mapping[str, Any], interval_id: str
) -> Dict[str, Path]:
    """Return fixed per-interval paths without creating anything."""

    identifier = str(interval_id)
    if not SAFE_NAME.fullmatch(identifier):
        raise ValueError("unsafe held-out DAS interval identifier")
    root = (
        project
        / str(registration["output"]["DAS_directory"])
        / "intervals"
        / identifier
    )
    return {
        "root": root,
        "chunk_qc": root / "chunk_qc.csv",
        "channel_qc": root / "channel_qc.csv",
        "block_qc": root / "block_qc.csv",
        "null_maxima": root / "null_maxima.csv",
        "base_v1_candidates": root / "base_v1_candidates.csv",
        "v2_candidates": root / "v2_candidates.csv",
        "status": root / "status.json",
        "score_cache": (
            project
            / "cached_continuous"
            / "analysis"
            / "heldout_das"
            / (identifier + "_scores.npz")
        ),
    }
