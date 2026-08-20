"""Fail-closed validation for held-out DAS/network comparison registration.

This module validates frozen metadata, byte hashes, and CSV headers only.  It
does not parse candidate rows or expose candidate times during registration.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Mapping

from .common import PASS, sha256_file


def validate_frozen_inputs(
    project: Path,
    registration: Mapping[str, Any],
) -> Dict[str, Path]:
    """Resolve and checksum every declared input without parsing CSV rows."""

    paths: Dict[str, Path] = {}
    for name, declaration in registration["frozen_inputs"].items():
        path = project / str(declaration["path"])
        if not path.is_file():
            raise FileNotFoundError(
                "missing held-out comparison input: {}".format(path)
            )
        observed = sha256_file(path)
        expected = str(declaration["sha256"])
        if observed != expected:
            raise RuntimeError(
                "held-out comparison input changed for {}: {} != {}".format(
                    name, observed, expected
                )
            )
        paths[str(name)] = path
    return paths


def read_csv_header(path: Path) -> List[str]:
    """Read exactly one CSV record: the schema header, never a data row."""

    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.reader(handle)
        try:
            return list(next(reader))
        except StopIteration as exc:
            raise RuntimeError(
                "frozen comparison CSV has no header: {}".format(path)
            ) from exc


def validate_stage_gates(
    registration: Mapping[str, Any],
    network_status: Mapping[str, Any],
    catalog_status: Mapping[str, Any],
    das_status: Mapping[str, Any],
    heldout_das_config: Mapping[str, Any],
) -> None:
    """Require complete independent freezes and the inherited match rule."""

    if str(registration["design_version"]) != (
        "heldout_das_network_time_only_comparison_v1"
    ):
        raise RuntimeError("unexpected held-out comparison design version")

    network_input = registration["frozen_inputs"]["network_union_time_only"]
    if str(network_status["status"]) != PASS:
        raise PermissionError("held-out network freeze did not pass")
    if not bool(network_status["network_union_complete_and_frozen"]):
        raise PermissionError("held-out network union is incomplete")
    if int(network_status["time_only_union_candidate_count"]) != int(
        network_input["expected_row_count"]
    ):
        raise RuntimeError("held-out network candidate count changed")
    if str(network_status["time_only_union_sha256"]) != str(
        network_input["sha256"]
    ):
        raise RuntimeError("held-out network union status hash changed")
    if bool(network_status["threshold_recalibration_performed"]):
        raise PermissionError("held-out network threshold was repaired")
    if bool(network_status["candidate_deletion_after_review_performed"]):
        raise PermissionError("held-out network candidate was deleted")
    if int(network_status["candidate_family_assignments_made"]) != 0:
        raise PermissionError("held-out network candidate has a family label")
    if not bool(network_status["generic_development_SNR1_STOP_preserved"]):
        raise PermissionError("generic network development STOP was erased")

    evaluation_input = registration["frozen_inputs"][
        "network_evaluation_units"
    ]
    adjudicated_input = registration["frozen_inputs"][
        "network_union_adjudicated"
    ]
    if str(catalog_status["status"]) != PASS:
        raise PermissionError("held-out catalog audit did not pass")
    if not bool(catalog_status["all_raw_union_rows_retained"]):
        raise PermissionError("catalog audit did not retain every network row")
    if int(catalog_status["adjudicated_union_candidate_count"]) != int(
        adjudicated_input["expected_row_count"]
    ):
        raise RuntimeError("adjudicated network candidate count changed")
    if int(catalog_status["evaluation_unit_count"]) != int(
        evaluation_input["expected_row_count"]
    ):
        raise RuntimeError("network evaluation-unit count changed")
    if str(catalog_status["adjudicated_union_sha256"]) != str(
        adjudicated_input["sha256"]
    ):
        raise RuntimeError("adjudicated network union hash changed")
    if str(catalog_status["evaluation_units_sha256"]) != str(
        evaluation_input["sha256"]
    ):
        raise RuntimeError("network evaluation-unit hash changed")
    if int(catalog_status["candidate_rows_deleted"]) != 0:
        raise PermissionError("catalog audit deleted a network candidate")
    if int(catalog_status["family_assignments_made"]) != 0:
        raise PermissionError("catalog audit assigned a repeater family")
    if int(catalog_status["catalog_conflict_STOP_count"]) != 0:
        raise PermissionError("catalog audit has an unresolved STOP conflict")

    das_input = registration["frozen_inputs"]["DAS_v2_candidates_time_only"]
    if str(das_status["status"]) != PASS:
        raise PermissionError("held-out DAS freeze did not pass")
    if not bool(das_status["complete_DAS_candidate_tables_frozen"]):
        raise PermissionError("held-out DAS candidate tables are incomplete")
    if not bool(das_status["all_12_intervals_PASS"]):
        raise PermissionError("held-out DAS interval set did not pass 12/12")
    if int(das_status["v2_candidate_count"]) != int(
        das_input["expected_row_count"]
    ):
        raise RuntimeError("held-out DAS-v2 candidate count changed")
    if str(das_status["v2_candidate_sha256"]) != str(das_input["sha256"]):
        raise RuntimeError("held-out DAS-v2 status hash changed")
    if int(das_status["network_candidate_table_rows_opened"]) != 0:
        raise PermissionError("DAS generation opened network candidate rows")
    if int(das_status["network_or_catalog_candidate_time_fields_read"]) != 0:
        raise PermissionError("DAS generation read comparison times")
    if int(das_status["heldout_family_label_rows_opened"]) != 0:
        raise PermissionError("DAS generation opened family labels")
    if int(das_status["candidate_family_assignments_made"]) != 0:
        raise PermissionError("DAS generation assigned a family")
    for field in (
        "threshold_recalibration_or_repair_performed",
        "v2_threshold_or_support_sweep_performed",
        "candidate_deletion_after_review_performed",
    ):
        if bool(das_status[field]):
            raise PermissionError(
                "held-out DAS freeze violated detector policy: {}".format(
                    field
                )
            )

    matching = registration["time_only_matching"]
    inherited = heldout_das_config["post_DAS_freeze_comparison"]
    if float(matching["maximum_absolute_time_difference_s"]) != float(
        inherited["time_only_matching_window_s"]
    ):
        raise PermissionError("held-out matching window was repaired")
    if str(matching["matching_algorithm"]) != str(
        inherited["matching_algorithm"]
    ):
        raise PermissionError("held-out matching algorithm changed")
    if str(matching["cross_interval_matching"]) != "FORBIDDEN":
        raise PermissionError("cross-interval candidate matching is enabled")
    if not bool(matching["retain_unmatched_DAS_rows"]):
        raise PermissionError("unmatched DAS rows would be deleted")
    if not bool(matching["retain_unmatched_network_rows"]):
        raise PermissionError("unmatched network rows would be deleted")
    if bool(matching["catalog_fields_allowed"]):
        raise PermissionError("catalog fields are enabled during time matching")
    if bool(matching["family_fields_allowed"]):
        raise PermissionError("family fields are enabled during time matching")

    for value in registration["detector_policy"].values():
        if str(value) != "FORBIDDEN":
            raise PermissionError("held-out detector/comparison repair is enabled")
