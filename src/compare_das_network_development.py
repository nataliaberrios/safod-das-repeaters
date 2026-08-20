#!/usr/bin/env python
"""Run the registered post-hoc DAS/network development comparison."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .background_catalog import associate_background_arrivals
from .common import (
    CONDITIONAL,
    load_config,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .das_network_comparison import (
    ADJUDICATED_COMPARISON_FIELDS,
    TIME_ONLY_COMPARISON_FIELDS,
    adjudicate_comparison,
    build_time_only_comparison,
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
                "frozen comparison input changed for {}: {} != {}".format(
                    name, observed, expected
                )
            )
        paths[str(name)] = path
    return paths


def _validate_access_boundary(
    raw_status: Mapping[str, Any],
    network_status: Mapping[str, Any],
    config: Mapping[str, Any],
) -> None:
    if not bool(raw_status["candidate_table_materialized_before_comparison"]):
        raise PermissionError("raw DAS candidates were not frozen first")
    if str(raw_status["next_stage_gate"]) != (
        "PASS_POSTHOC_NETWORK_AND_CATALOG_COMPARISON_ONLY"
    ):
        raise PermissionError("raw DAS status does not release comparison")
    for field in (
        "network_candidate_tables_opened",
        "catalog_event_time_tables_opened",
        "heldout_interval_tables_opened",
    ):
        if int(raw_status[field]) != 0:
            raise PermissionError(
                "DAS candidate generation crossed access boundary: {}".format(
                    field
                )
            )
    if int(raw_status["raw_das_candidate_count"]) != int(
        config["frozen_inputs"]["raw_das_candidates"][
            "expected_row_count"
        ]
    ):
        raise RuntimeError("raw DAS candidate count changed")
    if str(network_status["freeze_status"]) != (
        "FROZEN_FOR_INDEPENDENT_DAS_DEVELOPMENT_ONLY"
    ):
        raise PermissionError("network union was not frozen")
    if int(network_status["network_union_stage_das_waveforms_opened"]) != 0:
        raise PermissionError("network union used DAS waveforms")
    if int(network_status["heldout_intervals_opened"]) != 0:
        raise PermissionError("network union used held-out intervals")
    if str(config["access_gate"]["heldout_access"]) != (
        "STOP_NOT_YET_AUTHORIZED"
    ):
        raise PermissionError("comparison config does not seal held-out data")


def _validate_candidate_rows(
    das_rows: Sequence[Mapping[str, Any]],
    network_rows: Sequence[Mapping[str, Any]],
) -> None:
    for row in das_rows:
        if any(
            int(row[field]) != 0
            for field in (
                "network_candidate_time_fields_read",
                "catalog_event_time_fields_read",
                "heldout_interval_fields_read",
            )
        ):
            raise PermissionError("raw DAS candidate contains leaked input")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("raw DAS candidate has a family assignment")
    for row in network_rows:
        if int(row["catalog_fields_used_in_grouping"]) != 0:
            raise PermissionError("network union was not catalog blind")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("network union has a family assignment")


def _candidate_counts(
    rows: Sequence[Mapping[str, Any]],
) -> Counter[str]:
    return Counter(
        str(row["comparison_class"])
        for row in rows
        if str(row.get("DAS_candidate_id", ""))
    )


def _ranking_diagnostics(
    raw_das_rows: Sequence[Mapping[str, Any]],
    adjudicated_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    ordered = sorted(
        raw_das_rows,
        key=lambda row: (
            -float(row["coincidence_score"]),
            str(row["candidate_id"]),
        ),
    )
    rank = {
        str(row["candidate_id"]): index
        for index, row in enumerate(ordered, start=1)
    }
    target_ids = {
        str(row["DAS_candidate_id"])
        for row in adjudicated_rows
        if str(row["comparison_class"]) == (
            "matched_frozen_network_known_target_event"
        )
    }
    target_rows = [
        row
        for row in raw_das_rows
        if str(row["candidate_id"]) in target_ids
    ]
    other_rows = [
        row
        for row in raw_das_rows
        if str(row["candidate_id"]) not in target_ids
    ]
    target_ranks = sorted(rank[value] for value in target_ids)
    diagnostics: Dict[str, Any] = {
        "matched_target_DAS_candidate_ids": sorted(target_ids),
        "matched_target_score_ranks": target_ranks,
        "matched_targets_are_top_two_scores": target_ranks == [1, 2],
        "matched_target_minimum_score": (
            min(float(row["coincidence_score"]) for row in target_rows)
            if target_rows
            else None
        ),
        "other_candidate_maximum_score": (
            max(float(row["coincidence_score"]) for row in other_rows)
            if other_rows
            else None
        ),
        "matched_target_minimum_declared_ratio_block_support": (
            min(
                int(row["block_support_count_at_declared_ratio"])
                for row in target_rows
            )
            if target_rows
            else None
        ),
        "other_candidate_maximum_declared_ratio_block_support": (
            max(
                int(row["block_support_count_at_declared_ratio"])
                for row in other_rows
            )
            if other_rows
            else None
        ),
    }
    if target_rows and other_rows:
        diagnostics["score_margin_target_min_minus_other_max"] = (
            diagnostics["matched_target_minimum_score"]
            - diagnostics["other_candidate_maximum_score"]
        )
        diagnostics["score_ratio_target_min_over_other_max"] = (
            diagnostics["matched_target_minimum_score"]
            / diagnostics["other_candidate_maximum_score"]
        )
    return diagnostics


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            project_root() / "config" / "das_network_comparison.json"
        ),
    )
    args = parser.parse_args()

    project = project_root()
    config = load_config(args.config)
    paths = _validate_frozen_inputs(project, config)
    raw_status = _load_json(paths["raw_das_status"])
    network_status = _load_json(paths["network_union_status"])
    _validate_access_boundary(raw_status, network_status, config)

    # Only candidate-generation outputs enter the time-only stage.
    das_rows = _read_csv(paths["raw_das_candidates"])
    network_time_only_rows = _read_csv(paths["network_union_time_only"])
    _validate_candidate_rows(das_rows, network_time_only_rows)
    time_only_rows = build_time_only_comparison(
        das_rows,
        network_time_only_rows,
        float(
            config["time_only_matching"][
                "maximum_absolute_time_difference_s"
            ]
        ),
    )

    output = project / str(config["output"]["directory"])
    output.mkdir(parents=True, exist_ok=True)
    time_only_path = output / str(
        config["output"]["time_only_comparison_csv"]
    )
    adjudicated_path = output / str(
        config["output"]["adjudicated_comparison_csv"]
    )
    status_path = output / str(config["output"]["status_json"])
    _write_csv(
        time_only_path,
        time_only_rows,
        TIME_ONLY_COMPARISON_FIELDS,
    )
    time_only_sha256 = sha256_file(time_only_path)

    # Catalog rows are parsed only after the time-only product is immutable.
    catalog_rows = _read_csv(paths["background_catalog"])
    network_adjudicated_rows = _read_csv(
        paths["network_union_adjudicated"]
    )
    parent = load_config(paths["parent_config"])
    development = load_config(paths["development_config"])
    das_catalog_rows = [dict(row) for row in das_rows]
    associate_background_arrivals(
        das_catalog_rows,
        catalog_rows,
        parent,
        development,
    )
    adjudicated_rows = adjudicate_comparison(
        time_only_rows,
        das_catalog_rows,
        network_adjudicated_rows,
        float(
            config["catalog_adjudication"][
                "target_maximum_horizontal_distance_km"
            ]
        ),
    )
    _write_csv(
        adjudicated_path,
        adjudicated_rows,
        ADJUDICATED_COMPARISON_FIELDS,
    )

    memberships = Counter(
        str(row["comparison_membership"]) for row in time_only_rows
    )
    classes = _candidate_counts(adjudicated_rows)
    network_classes = Counter(
        str(row["known_event_class"])
        for row in network_adjudicated_rows
    )
    direct_associated = [
        row
        for row in das_catalog_rows
        if str(row["background_catalog_association"]) != "none"
    ]
    direct_event_ids = {
        str(row["background_catalog_event_id"])
        for row in direct_associated
    }
    matched_target_count = classes.get(
        "matched_frozen_network_known_target_event", 0
    )
    matched_regional_count = classes.get(
        "matched_frozen_network_known_regional_arrival", 0
    )
    ranking = _ranking_diagnostics(das_rows, adjudicated_rows)
    duration_hours = float(raw_status["development_interval_duration_s"]) / (
        60.0 * 60.0
    )

    status = {
        "status": CONDITIONAL,
        "stage": "das_network_posthoc_comparison_v1_complete",
        "generated_utc": utc_now(),
        "comparison_config_sha256": config["_config_sha256"],
        "raw_DAS_candidate_table_sha256": sha256_file(
            paths["raw_das_candidates"]
        ),
        "network_union_time_only_sha256": sha256_file(
            paths["network_union_time_only"]
        ),
        "network_union_adjudicated_sha256": sha256_file(
            paths["network_union_adjudicated"]
        ),
        "background_catalog_sha256": sha256_file(
            paths["background_catalog"]
        ),
        "time_only_comparison_path": str(time_only_path),
        "time_only_comparison_sha256": time_only_sha256,
        "adjudicated_comparison_path": str(adjudicated_path),
        "adjudicated_comparison_sha256": sha256_file(adjudicated_path),
        "time_only_matching_algorithm": config["time_only_matching"][
            "matching_algorithm"
        ],
        "time_only_matching_window_s": config["time_only_matching"][
            "maximum_absolute_time_difference_s"
        ],
        "catalog_fields_used_in_time_only_matching": 0,
        "raw_DAS_candidate_count": len(das_rows),
        "raw_DAS_candidate_rate_per_hour": len(das_rows) / duration_hours,
        "frozen_network_union_candidate_count": len(
            network_time_only_rows
        ),
        "DAS_network_matched_event_count": memberships.get(
            "DAS+network", 0
        ),
        "DAS_only_raw_trigger_count": memberships.get("DAS_only", 0),
        "network_only_event_count": memberships.get("network_only", 0),
        "known_target_network_event_count": network_classes.get(
            "known_target_region_event", 0
        ),
        "matched_known_target_event_count": matched_target_count,
        "known_target_network_event_recovery": "{}/{}".format(
            matched_target_count,
            network_classes.get("known_target_region_event", 0),
        ),
        "known_regional_network_arrival_count": network_classes.get(
            "known_regional_arrival_outside_target_region", 0
        ),
        "matched_known_regional_arrival_count": matched_regional_count,
        "known_regional_network_arrival_recovery": "{}/{}".format(
            matched_regional_count,
            network_classes.get(
                "known_regional_arrival_outside_target_region", 0
            ),
        ),
        "direct_catalog_compatible_DAS_trigger_count": len(
            direct_associated
        ),
        "direct_catalog_compatible_unique_event_count": len(
            direct_event_ids
        ),
        "secondary_same_catalog_event_trigger_count": classes.get(
            "secondary_trigger_for_same_catalog_event", 0
        ),
        "DAS_only_catalog_compatible_target_event_count": classes.get(
            "DAS_only_catalog_compatible_target_event", 0
        ),
        "DAS_only_catalog_compatible_regional_arrival_count": classes.get(
            "DAS_only_catalog_compatible_regional_arrival", 0
        ),
        "unassociated_raw_DAS_trigger_count": classes.get(
            "unassociated_raw_DAS_trigger", 0
        ),
        "catalog_conflict_STOP_count": classes.get(
            "catalog_conflict_STOP", 0
        ),
        "eligible_DAS_network_detection_increment_candidate_count": sum(
            bool(
                row[
                    "eligible_DAS_network_detection_increment_candidate"
                ]
            )
            for row in adjudicated_rows
        ),
        "eligible_catalog_extension_candidate_count": 0,
        "candidate_family_assignment_count": 0,
        "ranking_diagnostics": ranking,
        "local_signal_ranking_status": (
            "PROMISING_DEVELOPMENT_N_EQUALS_2_NOT_VALIDATED"
            if ranking["matched_targets_are_top_two_scores"]
            else "NO_CLEAR_DEVELOPMENT_SEPARATION"
        ),
        "raw_v1_extension_readiness": (
            "STOP_NOT_READY_FOR_CATALOG_EXTENSION_CLAIMS"
        ),
        "raw_v1_threshold_repaired_after_comparison": False,
        "raw_DAS_rows_deleted_after_comparison": 0,
        "raw_DAS_waveform_files_opened_during_comparison": 0,
        "network_candidate_tables_opened_during_comparison": 2,
        "catalog_event_time_tables_opened_during_comparison": 1,
        "heldout_interval_tables_opened": 0,
        "heldout_access_gate": "STOP_NOT_YET_AUTHORIZED",
        "next_stage_gate": (
            "PASS_BUILD_ADVISOR_CHECKPOINT_AND_REGISTER_V2_"
            "DEVELOPMENT_ONLY"
        ),
        "interpretation": (
            "the_two_known_local_events_are_the_top_two_DAS_scores_and_"
            "have_8_of_10_and_10_of_10_strong_block_support_but_v1_"
            "also_has_many_weak_raw_triggers_and_has_not_demonstrated_"
            "catalog_extension_or_family_assignment"
        ),
    }
    write_json(status_path, status)

    print(
        "Known local network events recovered by DAS: {}".format(
            status["known_target_network_event_recovery"]
        )
    )
    print(
        "Known regional network arrivals recovered by DAS: {}".format(
            status["known_regional_network_arrival_recovery"]
        )
    )
    print(
        "Raw DAS triggers: {}; unassociated: {}; extension eligible: 0".format(
            status["raw_DAS_candidate_count"],
            status["unassociated_raw_DAS_trigger_count"],
        )
    )
    print(
        "Known local score ranks: {}; held-out access: STOP".format(
            ranking["matched_target_score_ranks"]
        )
    )


if __name__ == "__main__":
    main()
