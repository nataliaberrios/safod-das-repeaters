#!/usr/bin/env python
"""Replay the disclosed DAS v2 rule on frozen development candidates only."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .common import (
    PASS,
    load_config,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .das_v2 import (
    DEVELOPMENT_CANDIDATE_FIELDS,
    select_v2_candidates,
    validate_v2_inheritance,
)
from .register_das_v2 import _validate_frozen_inputs


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path) -> list[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(
    path: Path,
    rows: Sequence[Mapping[str, Any]],
    fields: Sequence[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=list(fields),
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "das_v2_validation.json",
    )
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    paths = _validate_frozen_inputs(project, registration)
    v1 = load_config(paths["v1_DAS_config"])
    validate_v2_inheritance(registration, v1)

    output = project / str(registration["output"]["development_directory"])
    registration_status_path = output / str(
        registration["output"]["development_registration_status_json"]
    )
    registration_status = _load_json(registration_status_path)
    if str(registration_status["das_v2_config_sha256"]) != str(
        registration["_config_sha256"]
    ):
        raise RuntimeError("v2 registration status/config checksum mismatch")
    if str(registration_status["next_stage_gate"]) != (
        "PASS_V2_DEVELOPMENT_REPLAY_ONLY"
    ):
        raise PermissionError("v2 registration does not release replay")
    if not bool(registration_status["heldout_intervals_all_sealed"]):
        raise PermissionError("v2 registration reports unsealed intervals")

    v1_status = _load_json(paths["v1_DAS_status"])
    raw_rows = _read_csv(paths["v1_DAS_candidates"])
    expected_raw = int(
        registration["development_tuning_disclosure"][
            "observed_v1_raw_candidate_count"
        ]
    )
    if len(raw_rows) != expected_raw:
        raise RuntimeError("v1 raw candidate count changed")
    if int(v1_status["raw_das_candidate_count"]) != len(raw_rows):
        raise RuntimeError("v1 raw status count disagrees with table")
    if str(v1_status["candidate_table_sha256"]) != sha256_file(
        paths["v1_DAS_candidates"]
    ):
        raise RuntimeError("v1 raw status checksum disagrees with table")

    candidates = select_v2_candidates(raw_rows, registration)
    replay = registration["development_replay"]
    retained_ids = [
        str(row["parent_v1_candidate_id"]) for row in candidates
    ]
    expected_ids = list(
        map(str, replay["disclosed_expected_retained_candidate_ids"])
    )
    if retained_ids != expected_ids:
        raise RuntimeError(
            "v2 replay differs from disclosed development outcome: {} != {}".format(
                retained_ids, expected_ids
            )
        )
    if len(candidates) != int(replay["disclosed_expected_retained_count"]):
        raise RuntimeError("v2 retained count differs from disclosure")
    if len(raw_rows) - len(candidates) != int(
        replay["disclosed_expected_rejected_count"]
    ):
        raise RuntimeError("v2 rejected count differs from disclosure")

    candidate_path = output / str(
        registration["output"]["development_candidate_csv"]
    )
    status_path = output / str(
        registration["output"]["development_status_json"]
    )
    _write_csv(candidate_path, candidates, DEVELOPMENT_CANDIDATE_FIELDS)
    status = {
        "status": PASS,
        "stage": "das_v2_development_replay_complete",
        "generated_utc": utc_now(),
        "interpretation": (
            "mechanical_replay_of_disclosed_posthoc_development_tuning_"
            "not_independent_validation"
        ),
        "das_v2_config_sha256": registration["_config_sha256"],
        "registration_status_sha256": sha256_file(
            registration_status_path
        ),
        "v1_DAS_candidate_table_sha256": sha256_file(
            paths["v1_DAS_candidates"]
        ),
        "v1_raw_candidate_count": len(raw_rows),
        "v2_retained_candidate_count": len(candidates),
        "v2_rejected_candidate_count": len(raw_rows) - len(candidates),
        "v2_retained_candidate_ids": [
            str(row["candidate_id"]) for row in candidates
        ],
        "v2_retained_parent_v1_candidate_ids": retained_ids,
        "v2_retained_v1_score_ranks": [
            int(row["v1_score_rank"]) for row in candidates
        ],
        "v2_retained_strong_block_counts": [
            int(row["strong_block_count"]) for row in candidates
        ],
        "strong_block_characteristic_ratio": registration[
            "v2_candidate_rule"
        ]["strong_block_characteristic_ratio"],
        "minimum_strong_block_count": registration["v2_candidate_rule"][
            "minimum_strong_block_count"
        ],
        "score_threshold_changed_from_v1": False,
        "preprocessing_changed_from_v1": False,
        "channel_sampling_changed_from_v1": False,
        "candidate_table_path": str(candidate_path),
        "candidate_table_sha256": sha256_file(candidate_path),
        "network_candidate_tables_opened_during_replay": 0,
        "catalog_event_time_tables_opened_during_replay": 0,
        "heldout_interval_tables_opened_during_replay": 0,
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "heldout_network_waveform_files_opened": 0,
        "family_assignments_made": 0,
        "development_replay_regression_status": "PASS_DISCLOSED_2_OF_65",
        "next_stage_gate": (
            "PASS_COMMIT_AND_PUSH_V2_IMPLEMENTATION_BEFORE_"
            "HELDOUT_NETWORK_ACCESS"
        ),
        "heldout_network_access_gate": (
            "STOP_PENDING_TESTED_IMPLEMENTATION_COMMIT_AND_REMOTE_PUSH"
        ),
        "heldout_DAS_access_gate": (
            "STOP_UNTIL_HELDOUT_NETWORK_UNION_IS_FROZEN"
        ),
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
