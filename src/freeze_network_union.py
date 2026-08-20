#!/usr/bin/env python
"""Freeze the blind network candidate union before any DAS detector access."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .common import (
    CONDITIONAL,
    load_config,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .network_union import (
    ADJUDICATION_FIELDS,
    TIME_ONLY_FIELDS,
    adjudicate_time_only_union,
    build_time_only_union,
)


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


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_frozen_inputs(
    project: Path, config: Mapping[str, Any]
) -> Dict[str, Path]:
    paths: Dict[str, Path] = {}
    for name, declaration in config["frozen_inputs"].items():
        path = project / str(declaration["path"])
        if not path.is_file():
            raise FileNotFoundError("missing frozen input: {}".format(path))
        observed = sha256_file(path)
        expected = str(declaration["sha256"])
        if observed != expected:
            raise RuntimeError(
                "frozen input checksum changed for {}: {} != {}".format(
                    name, observed, expected
                )
            )
        paths[str(name)] = path
    return paths


def _acceptance_by_detector(
    status: Mapping[str, Any],
) -> Dict[str, str]:
    return {
        str(row["detector"]): str(row["acceptance_status"])
        for row in status["injection_primary_acceptance_rows"]
    }


def _validate_development_stop(
    status: Mapping[str, Any],
    config: Mapping[str, Any],
    development: Mapping[str, Any],
) -> None:
    if str(status["development_config_sha256"]) != str(
        config["development_config_sha256"]
    ):
        raise RuntimeError("development status/config checksum mismatch")
    if int(status["network_only_stage_das_waveforms_opened"]) != 0:
        raise PermissionError("DAS access occurred before network union freeze")
    if int(status["heldout_intervals_opened"]) != 0:
        raise PermissionError("held-out access occurred before union freeze")
    acceptance = _acceptance_by_detector(status)
    if acceptance != {"template_bank": "PASS", "generic_trigger": "STOP"}:
        raise RuntimeError(
            "expected the preserved template PASS / generic STOP result"
        )
    if str(status["injection_zero_control_status"]) != "PASS":
        raise RuntimeError("zero-amplitude injection controls did not pass")
    if str(status["injection_acceptance_status"]) != "STOP":
        raise RuntimeError("the registered generic STOP was not preserved")
    if str(status["overall_network_detector_status"]) != (
        "STOP_INJECTION_RECOVERY_ACCEPTANCE_FAILED"
    ):
        raise RuntimeError("unexpected development detector status")

    union_window = float(
        config["time_only_union"]["cross_branch_match_window_s"]
    )
    template_window = float(
        development["repeater_template_bank"][
            "candidate_minimum_separation_s"
        ]
    )
    generic_window = float(
        development["generic_network_trigger"][
            "candidate_minimum_separation_s"
        ]
    )
    if union_window != template_window or union_window != generic_window:
        raise RuntimeError(
            "union window must reuse both v4 branch separation values"
        )


def _validate_background_membership(
    generic_rows: Sequence[Mapping[str, Any]],
    background_rows: Sequence[Mapping[str, Any]],
) -> None:
    background_ids = {str(row["event_id"]) for row in background_rows}
    for row in generic_rows:
        if str(row.get("background_catalog_association", "")) != (
            "physically_plausible_known_event_arrival"
        ):
            continue
        event_id = str(row.get("background_catalog_event_id", ""))
        if event_id not in background_ids:
            raise RuntimeError(
                "generic candidate references an event absent from the "
                "frozen background catalog: {}".format(event_id)
            )


def _count_true(rows: Sequence[Mapping[str, Any]], field: str) -> int:
    return sum(bool(row[field]) for row in rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "network_union.json",
    )
    args = parser.parse_args()

    project = project_root()
    config = load_config(args.config)
    development_path = project / str(config["development_config_path"])
    if sha256_file(development_path) != str(
        config["development_config_sha256"]
    ):
        raise RuntimeError("development configuration changed after v4")
    development = load_config(development_path)
    paths = _validate_frozen_inputs(project, config)
    development_status = _load_json(paths["development_status"])
    _validate_development_stop(development_status, config, development)

    template_rows = _read_csv(paths["template_candidates"])
    generic_rows = _read_csv(paths["generic_candidates"])
    background_rows = _read_csv(paths["background_catalog"])
    _validate_background_membership(generic_rows, background_rows)

    output_settings = config["output"]
    output_directory = project / str(output_settings["directory"])
    time_only_path = output_directory / str(
        output_settings["time_only_union_csv"]
    )
    adjudicated_path = output_directory / str(
        output_settings["adjudicated_union_csv"]
    )
    status_path = output_directory / str(output_settings["status_json"])

    union_rows = build_time_only_union(
        template_rows,
        generic_rows,
        float(config["time_only_union"]["cross_branch_match_window_s"]),
    )
    _write_csv(time_only_path, union_rows, TIME_ONLY_FIELDS)
    time_only_sha256 = sha256_file(time_only_path)

    parent_path = project / str(development["parent_config_path"])
    parent = load_config(parent_path)
    target_limit_km = float(
        parent["family_neighborhood"]["maximum_horizontal_distance_m"]
    ) / 1000.0
    adjudicated = adjudicate_time_only_union(
        union_rows,
        template_rows,
        generic_rows,
        target_limit_km,
    )
    _write_csv(adjudicated_path, adjudicated, ADJUDICATION_FIELDS)

    membership_counts = Counter(
        str(row["branch_membership"]) for row in union_rows
    )
    class_counts = Counter(
        str(row["known_event_class"]) for row in adjudicated
    )
    status = {
        "status": CONDITIONAL,
        "stage": "network_candidate_union_v1_frozen",
        "generated_utc": utc_now(),
        "freeze_status": "FROZEN_FOR_INDEPENDENT_DAS_DEVELOPMENT_ONLY",
        "development_only_not_heldout_performance": True,
        "network_detector_performance_status": (
            "MIXED_TEMPLATE_PASS_GENERIC_SNR1_STOP"
        ),
        "generic_stop_preserved_without_threshold_repair": True,
        "template_branch_role": config["branch_roles"]["template_bank"][
            "role"
        ],
        "generic_branch_role": config["branch_roles"]["generic_trigger"][
            "role"
        ],
        "template_detection_threshold": development_status[
            "template_detection_threshold"
        ],
        "generic_detection_threshold": development_status[
            "generic_detection_threshold"
        ],
        "target_snr1_template_recovery": "30/30_PASS",
        "target_snr1_generic_recovery": "22/30_STOP",
        "zero_amplitude_control_status": "PASS_0/50_each_branch",
        "cross_branch_match_window_s": config["time_only_union"][
            "cross_branch_match_window_s"
        ],
        "cross_branch_matching_algorithm": config["time_only_union"][
            "matching_algorithm"
        ],
        "representative_time_rule": config["time_only_union"][
            "representative_time_rule"
        ],
        "template_input_candidate_count": len(template_rows),
        "generic_input_candidate_count": len(generic_rows),
        "raw_branch_candidate_count": len(template_rows) + len(generic_rows),
        "time_only_union_candidate_count": len(union_rows),
        "cross_branch_matched_candidate_count": membership_counts.get(
            "template_bank+generic_trigger", 0
        ),
        "template_only_candidate_count": membership_counts.get(
            "template_bank", 0
        ),
        "generic_only_candidate_count": membership_counts.get(
            "generic_trigger", 0
        ),
        "known_target_region_event_count": class_counts.get(
            "known_target_region_event", 0
        ),
        "known_regional_arrival_veto_count": class_counts.get(
            "known_regional_arrival_outside_target_region", 0
        ),
        "unassociated_after_broader_catalog_count": class_counts.get(
            "unassociated_after_broader_catalog_audit", 0
        ),
        "catalog_conflict_stop_count": sum(
            count
            for name, count in class_counts.items()
            if name.endswith("STOP")
        ),
        "eligible_uncataloged_local_extension_candidate_count": _count_true(
            adjudicated,
            "eligible_uncataloged_local_extension_candidate",
        ),
        "catalog_fields_used_in_time_only_grouping": 0,
        "family_assignments_made": 0,
        "regional_veto_removes_rows_from_raw_union": False,
        "network_union_config_sha256": config["_config_sha256"],
        "development_config_sha256": config[
            "development_config_sha256"
        ],
        "frozen_input_sha256": {
            name: str(declaration["sha256"])
            for name, declaration in config["frozen_inputs"].items()
        },
        "time_only_union_path": str(time_only_path),
        "time_only_union_sha256": time_only_sha256,
        "adjudicated_union_path": str(adjudicated_path),
        "adjudicated_union_sha256": sha256_file(adjudicated_path),
        "network_union_stage_das_waveforms_opened": 0,
        "heldout_intervals_opened": 0,
        "next_stage_gate": "PASS_INDEPENDENT_DAS_DEVELOPMENT_ONLY",
        "heldout_access_gate": "STOP_NOT_YET_AUTHORIZED",
        "next_stage_constraint": (
            "DAS_only_candidate_generation_must_not_import_network_"
            "candidate_times_or_catalog_event_times"
        ),
    }
    write_json(status_path, status)

    print("Network union freeze: {}".format(status["freeze_status"]))
    print(
        "Raw branch candidates: {}; time-only union events: {}".format(
            status["raw_branch_candidate_count"],
            status["time_only_union_candidate_count"],
        )
    )
    print(
        "Known local: {}; regional veto: {}; unassociated: {}".format(
            status["known_target_region_event_count"],
            status["known_regional_arrival_veto_count"],
            status["unassociated_after_broader_catalog_count"],
        )
    )
    print("DAS opened: 0; held-out intervals opened: 0")


if __name__ == "__main__":
    main()
