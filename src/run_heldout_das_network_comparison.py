#!/usr/bin/env python
"""Run the remotely released held-out DAS/network comparison exactly once."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Mapping, Sequence

from .common import PASS, load_config, project_root, sha256_file, utc_now, write_json
from .heldout_comparison_access import (
    validate_frozen_inputs,
    validate_stage_gates,
)
from .heldout_comparison_runner import (
    INTERVAL_SUMMARY_FIELDS,
    NETWORK_CONTEXT_FIELDS,
    TIME_ONLY_FIELDS,
    attach_network_context,
    build_interval_summary,
    build_time_only_comparison,
    validate_candidate_rows,
)
from .heldout_comparison_runner_access import (
    RUNNER_RELEASE_RELATIVE_PATH,
    comparison_output_paths,
    load_registration_status,
    load_runner_release,
    validate_runner_release,
)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path) -> List[Dict[str, str]]:
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
        default=(
            project_root()
            / "config"
            / "heldout_das_network_comparison.json"
        ),
    )
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    registration_status = load_registration_status(project, registration)

    # This gate is validated before candidate tables are checksummed or parsed.
    release = load_runner_release(project)
    runner_sha = validate_runner_release(
        project, registration, registration_status, release
    )

    paths = validate_frozen_inputs(project, registration)
    network_status = _load_json(
        paths["network_candidate_generation_status"]
    )
    catalog_status = _load_json(paths["catalog_audit_status"])
    das_status = _load_json(paths["DAS_candidate_generation_status"])
    heldout_das_config = load_config(paths["heldout_DAS_config"])
    validate_stage_gates(
        registration,
        network_status,
        catalog_status,
        das_status,
        heldout_das_config,
    )

    output = comparison_output_paths(project, registration)
    present = [
        str(path)
        for name, path in output.items()
        if name != "root" and path.exists()
    ]
    if present:
        raise PermissionError(
            "held-out comparison output already exists: {}".format(present)
        )

    # Only the two frozen time-only candidate tables are parsed at this stage.
    das_rows = _read_csv(paths["DAS_v2_candidates_time_only"])
    network_rows = _read_csv(paths["network_union_time_only"])
    expected_das = int(
        registration["frozen_inputs"]["DAS_v2_candidates_time_only"][
            "expected_row_count"
        ]
    )
    expected_network = int(
        registration["frozen_inputs"]["network_union_time_only"][
            "expected_row_count"
        ]
    )
    validate_candidate_rows(
        das_rows,
        network_rows,
        expected_das_count=expected_das,
        expected_network_count=expected_network,
    )
    window_s = float(
        registration["time_only_matching"][
            "maximum_absolute_time_difference_s"
        ]
    )
    time_only_rows = build_time_only_comparison(
        das_rows, network_rows, window_s
    )
    _write_csv(output["time_only"], time_only_rows, TIME_ONLY_FIELDS)
    time_only_sha256 = sha256_file(output["time_only"])

    # The access order below is scientific: context opens only after the
    # complete match/unmatched table has a stable byte hash.
    network_adjudicated_rows = _read_csv(paths["network_union_adjudicated"])
    evaluation_unit_rows = _read_csv(paths["network_evaluation_units"])
    if len(network_adjudicated_rows) != int(
        registration["frozen_inputs"]["network_union_adjudicated"][
            "expected_row_count"
        ]
    ):
        raise RuntimeError("adjudicated network row count changed")
    if len(evaluation_unit_rows) != int(
        registration["frozen_inputs"]["network_evaluation_units"][
            "expected_row_count"
        ]
    ):
        raise RuntimeError("network evaluation-unit count changed")
    context_rows = attach_network_context(
        time_only_rows,
        network_adjudicated_rows,
        evaluation_unit_rows,
    )
    observed_units = {
        str(row["network_evaluation_unit_id"])
        for row in context_rows
        if str(row["network_evaluation_unit_id"])
    }
    expected_units = {
        str(row["evaluation_unit_id"]) for row in evaluation_unit_rows
    }
    if observed_units != expected_units:
        raise RuntimeError("network event-unit context is incomplete")
    if sha256_file(output["time_only"]) != time_only_sha256:
        raise RuntimeError("time-only comparison changed during context access")

    interval_ids = list(
        map(str, heldout_das_config["heldout_population"]["interval_ids"])
    )
    interval_rows = build_interval_summary(context_rows, interval_ids)
    _write_csv(
        output["network_context"], context_rows, NETWORK_CONTEXT_FIELDS
    )
    _write_csv(
        output["interval_summary"],
        interval_rows,
        INTERVAL_SUMMARY_FIELDS,
    )

    memberships = Counter(
        str(row["comparison_membership"]) for row in time_only_rows
    )
    match_differences = [
        float(row["absolute_time_difference_s"])
        for row in time_only_rows
        if str(row["comparison_membership"]) == "DAS+network"
    ]
    matched_units = {
        str(row["network_evaluation_unit_id"])
        for row in context_rows
        if str(row["comparison_membership"]) == "DAS+network"
        and str(row["network_evaluation_unit_id"])
    }
    known_class_counts = Counter(
        str(row["network_known_event_class"])
        for row in context_rows
        if str(row["network_evaluation_unit_id"])
    )
    status = {
        "status": PASS,
        "stage": (
            "heldout_DAS_network_time_only_comparison_complete_"
            "before_DAS_only_adjudication"
        ),
        "generated_utc": utc_now(),
        "comparison_config_sha256": registration["_config_sha256"],
        "comparison_registration_status_sha256": sha256_file(
            project
            / str(registration["output"]["registration_directory"])
            / str(registration["output"]["registration_status_json"])
        ),
        "comparison_runner_release_sha256": sha256_file(
            project / RUNNER_RELEASE_RELATIVE_PATH
        ),
        "runner_commit_sha": runner_sha,
        "frozen_DAS_v2_candidate_sha256": sha256_file(
            paths["DAS_v2_candidates_time_only"]
        ),
        "frozen_network_union_sha256": sha256_file(
            paths["network_union_time_only"]
        ),
        "frozen_network_adjudicated_sha256": sha256_file(
            paths["network_union_adjudicated"]
        ),
        "frozen_network_evaluation_units_sha256": sha256_file(
            paths["network_evaluation_units"]
        ),
        "time_only_comparison_path": str(output["time_only"]),
        "time_only_comparison_sha256": time_only_sha256,
        "network_context_path": str(output["network_context"]),
        "network_context_sha256": sha256_file(output["network_context"]),
        "interval_summary_path": str(output["interval_summary"]),
        "interval_summary_sha256": sha256_file(output["interval_summary"]),
        "registered_interval_count": len(interval_ids),
        "registered_total_duration_h": float(
            das_status["heldout_total_duration_h"]
        ),
        "DAS_v2_candidate_count": len(das_rows),
        "network_raw_candidate_count": len(network_rows),
        "network_evaluation_unit_count": len(expected_units),
        "time_only_output_row_count": len(time_only_rows),
        "DAS_network_matched_pair_count": memberships["DAS+network"],
        "DAS_only_candidate_count": memberships["DAS_only"],
        "network_only_candidate_count": memberships["network_only"],
        "network_event_units_with_DAS_match": len(matched_units),
        "network_event_units_without_DAS_match": len(
            expected_units - matched_units
        ),
        "network_duplicate_raw_candidate_count": len(network_rows)
        - len(expected_units),
        "network_known_event_class_raw_row_counts": dict(known_class_counts),
        "match_absolute_time_difference_min_s": (
            min(match_differences) if match_differences else None
        ),
        "match_absolute_time_difference_median_s": (
            median(match_differences) if match_differences else None
        ),
        "match_absolute_time_difference_max_s": (
            max(match_differences) if match_differences else None
        ),
        "matching_window_s": window_s,
        "matching_algorithm": registration["time_only_matching"][
            "matching_algorithm"
        ],
        "cross_interval_matching_performed": False,
        "catalog_fields_used_in_time_only_matching": 0,
        "family_assignments_made": 0,
        "DAS_candidate_rows_opened_after_remote_release": len(das_rows),
        "network_candidate_rows_opened_after_remote_release": len(network_rows),
        "time_only_output_written_and_checksummed_before_context_access": True,
        "network_adjudication_rows_opened_after_time_only_checksum": len(
            network_adjudicated_rows
        ),
        "network_evaluation_rows_opened_after_time_only_checksum": len(
            evaluation_unit_rows
        ),
        "DAS_only_independent_adjudication_pending_count": memberships[
            "DAS_only"
        ],
        "network_threshold_repair_performed": False,
        "DAS_threshold_or_support_repair_performed": False,
        "matching_window_sweep_performed": False,
        "candidate_rank_selection_performed": False,
        "candidate_deletion_performed": False,
        "scientific_extension_claim_gate": (
            "STOP_PENDING_INDEPENDENT_DAS_ONLY_ADJUDICATION_AND_"
            "INTERVAL_LEVEL_UNCERTAINTY"
        ),
        "matched_false_discovery_rate_claim_gate": (
            "STOP_PENDING_DAS_ONLY_EVENT_TRUTH"
        ),
        "repeater_family_extension_claim_gate": (
            "STOP_FAMILY_ASSIGNMENT_NOT_EVALUATED"
        ),
        "next_stage_gate": (
            "PASS_REGISTER_INDEPENDENT_DAS_ONLY_CANDIDATE_ADJUDICATION"
        ),
    }
    write_json(output["status"], status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
