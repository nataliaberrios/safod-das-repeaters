#!/usr/bin/env python
"""Release held-out comparison only after an exact private remote push."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .common import load_config, project_root, sha256_file, utc_now, write_json
from .heldout_comparison_runner_access import (
    RUNNER_IMPLEMENTATION_PATHS,
    RUNNER_RELEASE_RELATIVE_PATH,
    comparison_output_paths,
    registration_status_path,
    validate_registration_status,
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
        raise PermissionError("comparison release requires an attached branch")
    return branch


def configured_remote_url(project: Path, remote: str) -> str:
    url = _git(project, ["config", "--get", "remote.{}.url".format(remote)])
    if not url:
        raise RuntimeError("comparison release remote URL is absent")
    return url


def remote_branch_sha(project: Path, remote: str, branch: str) -> str:
    reference = "refs/heads/{}".format(branch)
    output = _git(project, ["ls-remote", "--heads", remote, reference])
    rows = [line.split() for line in output.splitlines() if line.strip()]
    if len(rows) != 1 or len(rows[0]) != 2 or rows[0][1] != reference:
        raise RuntimeError("comparison remote branch did not resolve uniquely")
    sha = rows[0][0]
    if len(sha) != 40 or any(char not in "0123456789abcdef" for char in sha):
        raise RuntimeError("comparison remote did not resolve to a full SHA")
    return sha


def implementation_hashes(project: Path, head: str) -> Dict[str, str]:
    """Prove every runtime/scientific dependency is tracked and equals HEAD."""

    hashes: Dict[str, str] = {}
    for relative in RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError(
                "comparison runner implementation is missing: " + relative
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
                "comparison runner differs from HEAD: " + relative
            )
        hashes[relative] = observed
    return hashes


def build_release_payload(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    remote: str = "origin",
) -> Dict[str, Any]:
    """Build a release only when clean local HEAD equals the private remote."""

    validate_registration_status(registration, registration_status)
    anchor = registration["release_anchor"]
    if str(anchor["repository_visibility"]) != "private":
        raise PermissionError("comparison repository is not registered private")
    status = _git(project, ["status", "--porcelain", "--untracked-files=all"])
    if status:
        raise PermissionError(
            "worktree must be completely clean for comparison release"
        )
    branch = current_branch(project)
    if branch != str(anchor["branch"]):
        raise PermissionError("comparison runner is on an unexpected branch")
    remote_url = configured_remote_url(project, remote)
    expected_slug = str(anchor["repository"])
    allowed_urls = {
        "git@github.com:{}.git".format(expected_slug),
        "https://github.com/{}.git".format(expected_slug),
    }
    if remote_url not in allowed_urls:
        raise PermissionError("comparison remote differs from private anchor")

    head = _git(project, ["rev-parse", "HEAD"])
    hashes = implementation_hashes(project, head)
    observed_remote_sha = remote_branch_sha(project, remote, branch)
    if observed_remote_sha != head:
        raise PermissionError(
            "local comparison runner commit is not on the remote"
        )

    output_paths = comparison_output_paths(project, registration)
    products = [
        str(path)
        for name, path in output_paths.items()
        if name != "root" and path.exists()
    ]
    if products:
        raise PermissionError(
            "comparison product exists before release: {}".format(products)
        )
    status_path = registration_status_path(project, registration)

    return {
        "status": "REMOTE_VERIFIED_FOR_HELDOUT_COMPARISON_ACCESS",
        "stage": "heldout_DAS_network_comparison_runner_remote_release",
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
        "comparison_config_sha256": registration["_config_sha256"],
        "comparison_registration_status_sha256": sha256_file(status_path),
        "registered_DAS_v2_candidate_count": registration_status[
            "DAS_v2_candidate_count"
        ],
        "registered_network_raw_candidate_count": registration_status[
            "network_raw_candidate_count"
        ],
        "registered_network_evaluation_unit_count": registration_status[
            "network_evaluation_unit_count"
        ],
        "registered_interval_count": registration_status[
            "registered_interval_count"
        ],
        "matching_window_s": registration_status[
            "time_only_matching_window_s"
        ],
        "matching_algorithm": registration_status[
            "time_only_matching_algorithm"
        ],
        "network_candidate_rows_opened_before_release": 0,
        "DAS_candidate_rows_opened_before_release": 0,
        "network_evaluation_rows_opened_before_release": 0,
        "network_adjudication_rows_opened_before_release": 0,
        "network_candidate_time_fields_read_before_release": 0,
        "DAS_candidate_time_fields_read_before_release": 0,
        "catalog_association_rows_opened_before_release": 0,
        "family_label_rows_opened_before_release": 0,
        "comparison_output_products_present_before_release": 0,
        "next_stage_gate": "PASS_RUN_TIME_ONLY_COMPARISON_EXACTLY_ONCE",
        "post_time_only_context_gate": (
            "STOP_UNTIL_TIME_ONLY_OUTPUT_IS_WRITTEN_AND_CHECKSUMMED"
        ),
        "scientific_extension_claim_gate": (
            "STOP_PENDING_INDEPENDENT_DAS_ONLY_ADJUDICATION"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            project_root()
            / "config"
            / "heldout_das_network_comparison.json"
        ),
    )
    parser.add_argument("--remote", default="origin")
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    status_path = registration_status_path(project, registration)
    registration_status = _load_json(status_path)
    release_path = project / RUNNER_RELEASE_RELATIVE_PATH
    if release_path.is_file():
        raise PermissionError("held-out comparison runner release already exists")
    payload = build_release_payload(
        project,
        registration,
        registration_status,
        remote=str(args.remote),
    )
    write_json(release_path, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
