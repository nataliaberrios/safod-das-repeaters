#!/usr/bin/env python
"""Release the catalog runner only after its exact code is privately pushed."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .common import PASS, load_config, project_root, sha256_file, utc_now, write_json
from .heldout_catalog_access import (
    CATALOG_RELEASE_RELATIVE_PATH,
    CATALOG_RUNNER_IMPLEMENTATION_PATHS,
    configured_remote_url,
    current_branch,
    remote_branch_sha,
)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _git(project: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def implementation_hashes(project: Path, head: str) -> Dict[str, str]:
    """Require every catalog-runner file to be tracked and equal to HEAD."""

    hashes: Dict[str, str] = {}
    for relative in CATALOG_RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError("catalog runner file is absent: " + relative)
        committed = subprocess.run(
            ["git", "show", "{}:{}".format(head, relative)],
            cwd=project,
            check=True,
            capture_output=True,
        ).stdout
        observed = sha256_file(path)
        if hashlib.sha256(committed).hexdigest() != observed:
            raise PermissionError("catalog runner differs from HEAD: " + relative)
        hashes[relative] = observed
    return hashes


def preaccess_files(
    project: Path, registration: Mapping[str, Any]
) -> List[Path]:
    """Find any catalog-audit result/cache that would predate release."""

    roots = [
        project / str(registration["output"]["catalog_audit_directory"]),
        project / str(registration["broader_regional_catalog"]["cache_directory"]),
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
    """Build the release only when local HEAD equals the private remote."""

    if str(registration_status["status"]) != PASS:
        raise PermissionError("catalog preregistration is not PASS")
    if str(registration_status["registration_status"]) != (
        "FROZEN_FOR_CATALOG_AUDIT_RUNNER_IMPLEMENTATION_ONLY"
    ):
        raise PermissionError("unexpected catalog preregistration state")
    if str(registration_status["catalog_event_row_access_gate"]) != (
        "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
    ):
        raise PermissionError("unexpected catalog preregistration gate")
    if str(registration["release_anchor"]["repository_visibility"]) != "private":
        raise PermissionError("catalog audit is not anchored to a private repo")
    for field in (
        "target_catalog_event_rows_opened",
        "broader_catalog_event_rows_opened",
        "heldout_DAS_HDF5_files_opened",
        "heldout_network_waveform_files_opened",
        "heldout_family_label_rows_opened",
        "candidate_rows_deleted",
        "family_assignments_made",
    ):
        if int(registration_status[field]) != 0:
            raise PermissionError("catalog preregistration ledger is nonzero")

    if _git(project, "status", "--porcelain", "--untracked-files=all"):
        raise PermissionError("working tree must be clean before catalog release")
    branch = current_branch(project)
    if branch != str(registration["release_anchor"]["branch"]):
        raise PermissionError("catalog runner is on an unexpected branch")
    remote_url = configured_remote_url(project, remote)
    expected_slug = str(registration["release_anchor"]["repository"])
    if remote_url not in {
        "git@github.com:{}.git".format(expected_slug),
        "https://github.com/{}.git".format(expected_slug),
    }:
        raise PermissionError("catalog runner remote does not match registration")
    head = _git(project, "rev-parse", "HEAD")
    hashes = implementation_hashes(project, head)
    observed_remote_sha = remote_branch_sha(project, remote, branch)
    if observed_remote_sha != head:
        raise PermissionError("catalog runner implementation is not pushed")

    output = project / str(registration["output"]["registration_directory"])
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    query_path = output / str(registration["output"]["query_manifest_csv"])
    if str(registration_status["catalog_audit_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise RuntimeError("catalog preregistration/config checksum mismatch")
    if str(registration_status["broader_catalog_query_manifest_sha256"]) != (
        sha256_file(query_path)
    ):
        raise RuntimeError("catalog query manifest changed")
    prior_files = preaccess_files(project, registration)
    if prior_files:
        raise PermissionError(
            "catalog result/cache exists before release: {}".format(prior_files)
        )

    return {
        "status": "REMOTE_VERIFIED_FOR_HELDOUT_CATALOG_ACCESS",
        "stage": "heldout_catalog_runner_remote_release",
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
        "catalog_audit_config_sha256": registration["_config_sha256"],
        "catalog_registration_status_sha256": sha256_file(status_path),
        "catalog_query_manifest_sha256": sha256_file(query_path),
        "registered_network_union_sha256": registration_status[
            "network_union_sha256"
        ],
        "registered_network_union_candidate_count": registration_status[
            "network_union_candidate_count"
        ],
        "registered_target_catalog_sha256": registration_status[
            "target_catalog_sha256"
        ],
        "registered_target_catalog_expected_event_rows": registration_status[
            "target_catalog_expected_event_row_count"
        ],
        "registered_broader_catalog_query_count": registration_status[
            "broader_catalog_query_count"
        ],
        "preaccess_catalog_result_or_cache_file_count": 0,
        "target_catalog_event_rows_opened_before_release": 0,
        "broader_catalog_event_rows_opened_before_release": 0,
        "heldout_DAS_HDF5_files_opened_before_release": 0,
        "heldout_network_waveform_files_opened_before_release": 0,
        "heldout_family_label_rows_opened_before_release": 0,
        "candidate_rows_deleted_before_release": 0,
        "family_assignments_made_before_release": 0,
        "next_stage_gate": "PASS_RUN_ONE_TIME_CATALOG_AUDIT_OF_ALL_33_ROWS",
        "heldout_DAS_access_gate": (
            "STOP_UNTIL_CATALOG_AUDIT_FREEZE_AND_SEPARATE_DAS_REGISTRATION"
        ),
        "heldout_family_label_access_gate": "STOP_FORBIDDEN",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "heldout_catalog_audit.json",
    )
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()

    project = project_root()
    release_path = project / CATALOG_RELEASE_RELATIVE_PATH
    if release_path.is_file():
        raise PermissionError("held-out catalog runner is already released")
    registration = load_config(args.config)
    output = project / str(registration["output"]["registration_directory"])
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    status = _load_json(status_path)
    payload = build_release_payload(
        project, registration, status, remote=str(args.remote)
    )
    write_json(release_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
