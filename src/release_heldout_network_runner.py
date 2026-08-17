#!/usr/bin/env python
"""Release the held-out network runner only after a verified private push."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .common import PASS, load_config, project_root, sha256_file, utc_now
from .common import write_json
from .heldout_network_access import (
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


def remote_branch_sha(
    project: Path, remote: str, branch: str
) -> str:
    """Return the one exact remote branch SHA or stop."""

    reference = "refs/heads/{}".format(branch)
    output = _git(project, ["ls-remote", "--heads", remote, reference])
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != reference:
        raise RuntimeError("remote branch did not resolve uniquely")
    sha = rows[0][0]
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("remote branch did not resolve to a full Git SHA")
    return sha


def implementation_hashes(
    project: Path, head: str
) -> Dict[str, str]:
    """Prove implementation files are tracked and equal to HEAD."""

    hashes: Dict[str, str] = {}
    for relative in RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError(
                "runner implementation file is missing: {}".format(relative)
            )
        committed = subprocess.run(
            ["git", "show", "{}:{}".format(head, relative)],
            cwd=project,
            check=True,
            capture_output=True,
        ).stdout
        observed = sha256_file(path)
        if _sha256_bytes(committed) != observed:
            raise PermissionError(
                "runner implementation differs from HEAD: {}".format(relative)
            )
        hashes[relative] = observed
    return hashes


def heldout_cache_files(
    project: Path, registration: Mapping[str, Any]
) -> List[Path]:
    """Find any network waveform, sidecar, or score cache before release."""

    roots = [
        project / str(registration["waveform_acquisition"]["cache_directory"]),
        project / "cached_continuous" / "analysis" / "heldout_network",
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
    """Build a release only when local HEAD exactly equals private remote."""

    if str(registration_status["status"]) != PASS:
        raise PermissionError("network registration is not PASS")
    if str(registration_status["registration_status"]) != (
        "FROZEN_FOR_RUNNER_IMPLEMENTATION_ONLY"
    ):
        raise PermissionError("unexpected network registration state")
    if str(registration_status["heldout_network_access_gate"]) != (
        "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
    ):
        raise PermissionError("unexpected network registration gate")
    if str(registration["release_anchor"]["repository_visibility"]) != (
        "private"
    ):
        raise PermissionError("repository is not registered as private")

    status = _git(project, ["status", "--porcelain", "--untracked-files=all"])
    if status:
        raise PermissionError(
            "working tree must be completely clean before runner release"
        )
    branch = _git(project, ["branch", "--show-current"])
    expected_branch = str(registration["release_anchor"]["branch"])
    if branch != expected_branch:
        raise PermissionError("runner is on an unexpected branch")
    remote_url = _git(project, ["remote", "get-url", remote])
    expected_slug = str(registration["release_anchor"]["repository"])
    allowed_urls = {
        "git@github.com:{}.git".format(expected_slug),
        "https://github.com/{}.git".format(expected_slug),
    }
    if remote_url not in allowed_urls:
        raise PermissionError("runner remote does not match private release anchor")

    head = _git(project, ["rev-parse", "HEAD"])
    hashes = implementation_hashes(project, head)
    observed_remote_sha = remote_branch_sha(project, remote, branch)
    if observed_remote_sha != head:
        raise PermissionError("local runner commit is not pushed to the remote")

    registration_status_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["registration_status_json"])
    )
    inventory_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["template_input_inventory_csv"])
    )
    if str(registration_status["heldout_network_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise RuntimeError("registration/config checksum mismatch")
    if str(
        registration_status["historical_template_inventory_sha256"]
    ) != sha256_file(inventory_path):
        raise RuntimeError("historical template inventory checksum changed")
    cache_files = heldout_cache_files(project, registration)
    if cache_files:
        raise PermissionError(
            "held-out cache exists before runner release: {}".format(
                cache_files
            )
        )

    return {
        "status": "REMOTE_VERIFIED_FOR_HELDOUT_NETWORK_ACCESS",
        "stage": "heldout_network_runner_remote_release",
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
        "heldout_network_config_sha256": registration["_config_sha256"],
        "network_registration_status_sha256": sha256_file(
            registration_status_path
        ),
        "template_input_inventory_sha256": sha256_file(inventory_path),
        "registered_interval_count": registration_status["interval_count"],
        "registered_total_duration_h": registration_status[
            "heldout_total_duration_h"
        ],
        "registered_template_threshold": registration_status[
            "template_threshold"
        ],
        "registered_generic_threshold": registration_status[
            "generic_threshold"
        ],
        "generic_development_SNR1_STOP_preserved": True,
        "heldout_cache_files_present_before_release": 0,
        "heldout_network_waveform_files_opened_before_release": 0,
        "heldout_DAS_HDF5_files_opened_before_release": 0,
        "heldout_catalog_event_rows_opened_before_release": 0,
        "heldout_family_label_rows_opened_before_release": 0,
        "next_stage_gate": (
            "PASS_RUN_ALL_12_REGISTERED_NETWORK_INTERVALS_WITHOUT_CATALOG_OR_DAS"
        ),
        "heldout_DAS_access_gate": (
            "STOP_UNTIL_COMPLETE_HELDOUT_NETWORK_UNION_IS_FROZEN"
        ),
        "heldout_catalog_access_gate": (
            "STOP_UNTIL_ALL_NETWORK_TIME_ONLY_UNION_ROWS_ARE_"
            "MATERIALIZED_AND_CHECKSUMMED"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            project_root() / "config" / "heldout_network_validation.json"
        ),
    )
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()

    project = project_root()
    output_path = project / RUNNER_RELEASE_RELATIVE_PATH
    if output_path.is_file():
        raise PermissionError("held-out network runner is already released")
    registration = load_config(args.config)
    registration_status_path = (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["registration_status_json"])
    )
    registration_status = _load_json(registration_status_path)
    payload = build_release_payload(
        project, registration, registration_status, remote=str(args.remote)
    )
    write_json(output_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
