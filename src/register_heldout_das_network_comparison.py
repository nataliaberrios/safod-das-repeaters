#!/usr/bin/env python
"""Register held-out time-only comparison without parsing candidate rows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict

from .common import PASS, load_config, project_root, utc_now, write_json
from .heldout_comparison_access import (
    read_csv_header,
    validate_frozen_inputs,
    validate_stage_gates,
)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


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

    schema_paths = {
        "network_union_time_only": paths["network_union_time_only"],
        "DAS_v2_candidates_time_only": paths[
            "DAS_v2_candidates_time_only"
        ],
        "network_evaluation_units": paths["network_evaluation_units"],
        "network_union_adjudicated": paths["network_union_adjudicated"],
    }
    observed_headers = {
        name: read_csv_header(path) for name, path in schema_paths.items()
    }
    for name, observed in observed_headers.items():
        expected = list(registration["frozen_schema_headers"][name])
        if observed != expected:
            raise RuntimeError(
                "held-out comparison schema changed for {}".format(name)
            )

    output = project / str(registration["output"]["registration_directory"])
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    if status_path.is_file():
        raise PermissionError("held-out comparison is already registered")

    comparison_root = project / str(
        registration["output"]["comparison_directory"]
    )
    comparison_paths = [
        comparison_root / str(registration["output"][field])
        for field in (
            "time_only_comparison_csv",
            "network_context_csv",
            "interval_summary_csv",
            "comparison_status_json",
        )
    ]
    present = [str(path) for path in comparison_paths if path.exists()]
    if present:
        raise PermissionError(
            "comparison outputs exist before registration: {}".format(
                present
            )
        )

    access = registration["registration_access_ledger"]
    if int(access["candidate_or_evaluation_rows_opened"]) != 0:
        raise PermissionError("registration declares candidate-row access")
    for field in (
        "network_candidate_time_fields_read",
        "DAS_candidate_time_fields_read",
        "catalog_association_rows_opened",
        "family_label_rows_opened",
    ):
        if int(access[field]) != 0:
            raise PermissionError(
                "registration declares forbidden access: {}".format(field)
            )

    status = {
        "status": PASS,
        "stage": (
            "heldout_DAS_network_comparison_registered_before_"
            "candidate_time_row_access"
        ),
        "registration_status": (
            "FROZEN_FOR_TIME_ONLY_COMPARISON_RUNNER_IMPLEMENTATION_ONLY"
        ),
        "generated_utc": utc_now(),
        "comparison_config_sha256": registration["_config_sha256"],
        "frozen_input_sha256": {
            name: str(declaration["sha256"])
            for name, declaration in registration["frozen_inputs"].items()
        },
        "release_anchor": registration["release_anchor"],
        "network_raw_candidate_count": int(
            registration["frozen_inputs"]["network_union_time_only"][
                "expected_row_count"
            ]
        ),
        "network_evaluation_unit_count": int(
            registration["frozen_inputs"]["network_evaluation_units"][
                "expected_row_count"
            ]
        ),
        "DAS_v2_candidate_count": int(
            registration["frozen_inputs"]["DAS_v2_candidates_time_only"][
                "expected_row_count"
            ]
        ),
        "registered_interval_count": int(das_status["interval_count"]),
        "registered_total_duration_h": float(
            das_status["heldout_total_duration_h"]
        ),
        "schema_headers_opened": len(observed_headers),
        "schema_headers_verified_exact": True,
        "verified_schema_fields": observed_headers,
        "checksum_bytes_read_are_not_candidate_field_parsing": True,
        "network_candidate_rows_opened": 0,
        "DAS_candidate_rows_opened": 0,
        "network_evaluation_rows_opened": 0,
        "network_adjudication_rows_opened": 0,
        "network_candidate_time_fields_read": 0,
        "DAS_candidate_time_fields_read": 0,
        "catalog_association_rows_opened": 0,
        "family_label_rows_opened": 0,
        "comparison_output_products_present_before_registration": 0,
        "time_only_matching_window_s": registration["time_only_matching"][
            "maximum_absolute_time_difference_s"
        ],
        "time_only_matching_algorithm": registration["time_only_matching"][
            "matching_algorithm"
        ],
        "cross_interval_matching_enabled": False,
        "unmatched_DAS_rows_retained": True,
        "unmatched_network_rows_retained": True,
        "candidate_rank_selection_enabled": False,
        "candidate_deletion_enabled": False,
        "threshold_or_window_sweep_enabled": False,
        "family_assignment_enabled": False,
        "next_stage_gate": (
            "PASS_IMPLEMENT_TEST_COMMIT_PUSH_AND_REMOTE_RELEASE_"
            "TIME_ONLY_COMPARISON_RUNNER"
        ),
        "candidate_time_table_access_gate": (
            "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
        ),
        "catalog_or_evaluation_row_access_gate": (
            "STOP_UNTIL_TIME_ONLY_OUTPUT_IS_WRITTEN_AND_CHECKSUMMED"
        ),
        "heldout_family_label_access_gate": "STOP_FORBIDDEN",
        "scientific_extension_claim_gate": (
            "STOP_PENDING_INDEPENDENT_DAS_ONLY_ADJUDICATION"
        ),
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
