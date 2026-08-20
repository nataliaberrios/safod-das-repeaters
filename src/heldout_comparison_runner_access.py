"""Post-release access gate for the held-out DAS/network comparison runner."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Dict, Mapping

from .common import PASS, sha256_file


RUNNER_RELEASE_RELATIVE_PATH = Path(
    "outputs/heldout_v2/registration/comparison_runner_release.json"
)
RUNNER_IMPLEMENTATION_PATHS = (
    "src/common.py",
    "src/network_union.py",
    "src/heldout_comparison_access.py",
    "src/register_heldout_das_network_comparison.py",
    "src/heldout_comparison_runner.py",
    "src/heldout_comparison_runner_access.py",
    "src/run_heldout_das_network_comparison.py",
    "src/release_heldout_comparison_runner.py",
    "tests/test_heldout_comparison_registration.py",
    "tests/test_heldout_comparison_runner.py",
)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _git_ancestor(project: Path, ancestor: str, descendant: str) -> bool:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def registration_status_path(
    project: Path, registration: Mapping[str, Any]
) -> Path:
    return (
        project
        / str(registration["output"]["registration_directory"])
        / str(registration["output"]["registration_status_json"])
    )


def load_registration_status(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Any]:
    path = registration_status_path(project, registration)
    if not path.is_file():
        raise PermissionError("held-out comparison registration is absent")
    return _load_json(path)


def validate_registration_status(
    registration: Mapping[str, Any], status: Mapping[str, Any]
) -> None:
    if str(status.get("status", "")) != PASS:
        raise PermissionError("held-out comparison registration did not pass")
    if str(status.get("registration_status", "")) != (
        "FROZEN_FOR_TIME_ONLY_COMPARISON_RUNNER_IMPLEMENTATION_ONLY"
    ):
        raise PermissionError("unexpected comparison registration state")
    if str(status.get("comparison_config_sha256", "")) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("comparison registration/config hash mismatch")
    if str(status.get("candidate_time_table_access_gate", "")) != (
        "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
    ):
        raise PermissionError("unexpected candidate-time registration gate")
    if str(status.get("catalog_or_evaluation_row_access_gate", "")) != (
        "STOP_UNTIL_TIME_ONLY_OUTPUT_IS_WRITTEN_AND_CHECKSUMMED"
    ):
        raise PermissionError("unexpected post-time-only context gate")
    for field in (
        "network_candidate_rows_opened",
        "DAS_candidate_rows_opened",
        "network_evaluation_rows_opened",
        "network_adjudication_rows_opened",
        "network_candidate_time_fields_read",
        "DAS_candidate_time_fields_read",
        "catalog_association_rows_opened",
        "family_label_rows_opened",
        "comparison_output_products_present_before_registration",
    ):
        if int(status.get(field, -1)) != 0:
            raise PermissionError(
                "comparison registration access ledger is nonzero: " + field
            )
    if int(status.get("network_raw_candidate_count", -1)) != 33:
        raise RuntimeError("registered network row count changed")
    if int(status.get("network_evaluation_unit_count", -1)) != 32:
        raise RuntimeError("registered network event-unit count changed")
    if int(status.get("DAS_v2_candidate_count", -1)) != 22:
        raise RuntimeError("registered DAS-v2 row count changed")


def load_runner_release(project: Path) -> Dict[str, Any]:
    """Deny candidate-time access until the release artifact exists."""

    path = project / RUNNER_RELEASE_RELATIVE_PATH
    if not path.is_file():
        raise PermissionError(
            "held-out comparison access denied: runner release is absent"
        )
    return _load_json(path)


def validate_runner_release(
    project: Path,
    registration: Mapping[str, Any],
    registration_status: Mapping[str, Any],
    release: Mapping[str, Any],
) -> str:
    """Return the remote-verified runner SHA after every gate passes."""

    validate_registration_status(registration, registration_status)
    if str(release.get("status", "")) != (
        "REMOTE_VERIFIED_FOR_HELDOUT_COMPARISON_ACCESS"
    ):
        raise PermissionError("held-out comparison runner is not released")
    if str(release.get("stage", "")) != (
        "heldout_DAS_network_comparison_runner_remote_release"
    ):
        raise PermissionError("unexpected held-out comparison release stage")
    if str(release.get("comparison_config_sha256", "")) != str(
        registration["_config_sha256"]
    ):
        raise PermissionError("comparison release/config hash mismatch")
    expected_status_hash = sha256_file(
        registration_status_path(project, registration)
    )
    if str(release.get("comparison_registration_status_sha256", "")) != (
        expected_status_hash
    ):
        raise PermissionError("comparison registration changed after release")

    runner_sha = str(release.get("runner_commit_sha", ""))
    remote_sha = str(release.get("remote_branch_sha", ""))
    if (
        len(runner_sha) != 40
        or runner_sha != remote_sha
        or not bool(release.get("remote_branch_sha_verified_equal", False))
    ):
        raise PermissionError("comparison runner commit was not remote-verified")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if not _git_ancestor(project, runner_sha, head):
        raise PermissionError(
            "released comparison runner is not an ancestor of HEAD"
        )

    released_files = release.get("runner_implementation_files", {})
    if set(map(str, released_files)) != set(RUNNER_IMPLEMENTATION_PATHS):
        raise PermissionError("comparison runner release file set changed")
    for relative in RUNNER_IMPLEMENTATION_PATHS:
        path = project / relative
        if not path.is_file():
            raise FileNotFoundError(
                "released comparison runner file is absent: " + relative
            )
        if sha256_file(path) != str(released_files[relative]):
            raise PermissionError(
                "released comparison runner file changed: " + relative
            )

    for field in (
        "network_candidate_rows_opened_before_release",
        "DAS_candidate_rows_opened_before_release",
        "network_evaluation_rows_opened_before_release",
        "network_adjudication_rows_opened_before_release",
        "network_candidate_time_fields_read_before_release",
        "DAS_candidate_time_fields_read_before_release",
        "catalog_association_rows_opened_before_release",
        "family_label_rows_opened_before_release",
        "comparison_output_products_present_before_release",
    ):
        if int(release.get(field, -1)) != 0:
            raise PermissionError(
                "comparison release access ledger is nonzero: " + field
            )
    return runner_sha


def comparison_output_paths(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Path]:
    root = project / str(registration["output"]["comparison_directory"])
    return {
        "root": root,
        "time_only": root
        / str(registration["output"]["time_only_comparison_csv"]),
        "network_context": root
        / str(registration["output"]["network_context_csv"]),
        "interval_summary": root
        / str(registration["output"]["interval_summary_csv"]),
        "status": root
        / str(registration["output"]["comparison_status_json"]),
    }
