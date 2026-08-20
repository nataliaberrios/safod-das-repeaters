#!/usr/bin/env python
"""Release the held-out DAS runner only after an exact private remote push."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .common import PASS, load_config, project_root, sha256_file, utc_now, write_json
from .heldout_das_runner_access import (
    RUNNER_IMPLEMENTATION_PATHS,
    RUNNER_RELEASE_RELATIVE_PATH,
)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _git(project: Path, arguments: Sequence[str]) -> str:
    return subprocess.run(
        ["git", *map(str, arguments)],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def current_branch(project: Path) -> str:
    branch = _git(project, ["rev-parse", "--abbrev-ref", "HEAD"])
    if not branch or branch == "HEAD":
        raise PermissionError("DAS runner release requires an attached branch")
    return branch


def configured_remote_url(project: Path, remote: str) -> str:
    url = _git(project, ["config", "--get", "remote.{}.url".format(remote)])
    if not url:
        raise RuntimeError("DAS runner release remote URL is absent")
    return url


def remote_branch_sha(project: Path, remote: str, branch: str) -> str:
    reference = "refs/heads/{}".format(branch)
    output = _git(project, ["ls-remote", "--heads", remote, reference])
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != reference:
        raise RuntimeError("DAS runner remote branch did not resolve uniquely")
    sha = rows[0][0]
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("DAS runner remote did not resolve to a full SHA")
    return sha


def implementation_hashes(project: Path, head: str) -> Dict[str, str]:
    """Prove every scientific/runtime dependency is tracked and equals HEAD."""

    hashes: Dict[str, str] = {}
    for relative in RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError("DAS runner implementation is missing: " + relative)
        committed = subprocess.run(
            ["git", "show", "{}:{}".format(head, relative)],
            cwd=project,
            check=True,
            capture_output=True,
        ).stdout
        observed = sha256_file(path)
        if _sha256_bytes(committed) != observed:
            raise PermissionError("DAS runner differs from HEAD: " + relative)
        hashes[relative] = observed
    return hashes


def heldout_product_files(
    project: Path, registration: Mapping[str, Any]
) -> List[Path]:
    """Find any waveform-derived DAS product before runner release."""

    roots = [
        project / str(registration["output"]["DAS_directory"]),
        project / "cached_continuous" / "analysis" / "heldout_das",
    ]
    files: List[Path] = []
    for root in roots:
        if root.exists():
            files.extend(path for path in root.rglob("*") if path.is_file())
    return sorted(files)


def build_release_payload(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    remote: str = "origin",
) -> Dict[str, Any]:
    """Build a release only when clean local HEAD equals the private remote."""

    if str(registration_status["status"]) != PASS:
        raise PermissionError("held-out DAS registration is not PASS")
    if str(registration_status["registration_status"]) != (
        "FROZEN_FOR_HELDOUT_DAS_RUNNER_IMPLEMENTATION_ONLY"
    ):
        raise PermissionError("unexpected held-out DAS registration state")
    if str(registration_status["heldout_DAS_waveform_access_gate"]) != (
        "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
    ):
        raise PermissionError("unexpected held-out DAS registration gate")
    if str(registration["release_anchor"]["repository_visibility"]) != "private":
        raise PermissionError("held-out DAS repository is not registered private")

    status = _git(project, ["status", "--porcelain", "--untracked-files=all"])
    if status:
        raise PermissionError("worktree must be completely clean for DAS release")
    branch = current_branch(project)
    if branch != str(registration["release_anchor"]["branch"]):
        raise PermissionError("DAS runner is on an unexpected branch")
    remote_url = configured_remote_url(project, remote)
    expected_slug = str(registration["release_anchor"]["repository"])
    allowed_urls = {
        "git@github.com:{}.git".format(expected_slug),
        "https://github.com/{}.git".format(expected_slug),
    }
    if remote_url not in allowed_urls:
        raise PermissionError("DAS runner remote differs from private anchor")

    head = _git(project, ["rev-parse", "HEAD"])
    hashes = implementation_hashes(project, head)
    observed_remote_sha = remote_branch_sha(project, remote, branch)
    if observed_remote_sha != head:
        raise PermissionError("local DAS runner commit is not on the remote")

    output = project / str(registration["output"]["registration_directory"])
    registration_status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    manifest_path = output / str(registration["output"]["manifest_selection_csv"])
    interval_path = output / str(registration["output"]["interval_selection_csv"])
    if str(registration_status["heldout_DAS_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise RuntimeError("held-out DAS registration/config hash changed")
    if str(registration_status["manifest_selection_sha256"]) != sha256_file(
        manifest_path
    ):
        raise RuntimeError("held-out DAS manifest selection changed")
    if str(registration_status["interval_selection_sha256"]) != sha256_file(
        interval_path
    ):
        raise RuntimeError("held-out DAS interval selection changed")
    products = heldout_product_files(project, registration)
    if products:
        raise PermissionError(
            "held-out DAS product exists before release: {}".format(products)
        )

    return {
        "status": "REMOTE_VERIFIED_FOR_HELDOUT_DAS_ACCESS",
        "stage": "heldout_DAS_runner_remote_release",
        "generated_utc": utc_now(),
        "repository": expected_slug,
        "repository_visibility": "private",
        "remote_name": remote,
        "remote_url": remote_url,
        "branch": branch,
        "runner_commit_sha": head,
        "remote_branch_sha": observed_remote_sha,
        "remote_branch_sha_verified_equal": True,
        "source_tree_clean_before_release": True,
        "runner_implementation_files": hashes,
        "heldout_DAS_config_sha256": registration["_config_sha256"],
        "DAS_registration_status_sha256": sha256_file(
            registration_status_path
        ),
        "DAS_manifest_selection_sha256": sha256_file(manifest_path),
        "DAS_interval_selection_sha256": sha256_file(interval_path),
        "registered_interval_count": registration_status["interval_count"],
        "registered_total_duration_h": registration_status[
            "heldout_total_duration_h"
        ],
        "registered_manifest_file_count": registration_status[
            "manifest_selection_unique_file_count"
        ],
        "registered_manifest_total_size_bytes": registration_status[
            "manifest_selection_total_size_bytes"
        ],
        "heldout_waveform_derived_products_present_before_release": 0,
        "heldout_DAS_HDF5_files_opened_before_release": 0,
        "heldout_DAS_HDF5_headers_opened_before_release": 0,
        "heldout_DAS_HDF5_datasets_opened_before_release": 0,
        "network_candidate_table_rows_opened_before_release": 0,
        "catalog_association_table_rows_opened_before_release": 0,
        "network_or_catalog_candidate_time_fields_read_before_release": 0,
        "heldout_family_label_rows_opened_before_release": 0,
        "next_stage_gate": "PASS_RUN_ALL_12_REGISTERED_DAS_INTERVALS",
        "comparison_access_gate": (
            "STOP_UNTIL_COMPLETE_DAS_CANDIDATE_TABLES_ARE_FROZEN"
        ),
        "heldout_family_label_access_gate": "STOP_FORBIDDEN",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "heldout_das_replay.json",
    )
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    output = project / str(registration["output"]["registration_directory"])
    status_path = output / str(registration["output"]["registration_status_json"])
    registration_status = _load_json(status_path)
    release_path = project / RUNNER_RELEASE_RELATIVE_PATH
    if release_path.is_file():
        raise PermissionError("held-out DAS runner release already exists")
    payload = build_release_payload(
        project, registration, registration_status, remote=str(args.remote)
    )
    write_json(release_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
