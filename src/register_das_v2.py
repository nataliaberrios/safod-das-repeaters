#!/usr/bin/env python
"""Register DAS version 2 while every held-out waveform remains sealed."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping

from .common import (
    PASS,
    load_config,
    parse_utc,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .das_v2 import validate_v2_inheritance


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _validate_frozen_inputs(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Path]:
    paths: Dict[str, Path] = {}
    for name, declaration in registration["frozen_inputs"].items():
        raw = Path(str(declaration["path"]))
        path = raw if raw.is_absolute() else project / raw
        if not path.is_file():
            raise FileNotFoundError("missing v2 frozen input: {}".format(path))
        observed = sha256_file(path)
        expected = str(declaration["sha256"])
        if observed != expected:
            raise RuntimeError(
                "v2 frozen input changed for {}: {} != {}".format(
                    name, observed, expected
                )
            )
        paths[str(name)] = path
    return paths


def _validate_heldout_rows(
    path: Path, registration: Mapping[str, Any]
) -> list[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    declaration = registration["frozen_inputs"]["heldout_intervals"]
    if len(rows) != int(declaration["expected_row_count"]):
        raise RuntimeError("held-out interval count changed")
    identifiers = [str(row["interval_id"]) for row in rows]
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError("held-out interval identifiers are not unique")
    required_status = str(declaration["required_analysis_status"])
    if any(str(row["analysis_status"]) != required_status for row in rows):
        raise PermissionError("a held-out interval is no longer sealed")
    total_s = 0.0
    previous_end = float("-inf")
    for row in rows:
        start_s = parse_utc(str(row["start_utc"])).timestamp()
        end_s = parse_utc(str(row["end_utc"])).timestamp()
        duration_s = float(row["duration_s"])
        if not math.isclose(
            end_s - start_s, duration_s, rel_tol=0.0, abs_tol=1.0e-6
        ):
            raise RuntimeError("held-out interval duration changed")
        if start_s < previous_end:
            raise RuntimeError("held-out intervals overlap or are unordered")
        previous_end = end_s
        total_s += duration_s
    expected_s = float(declaration["expected_total_duration_h"]) * 3600.0
    if not math.isclose(total_s, expected_s, rel_tol=0.0, abs_tol=1.0e-6):
        raise RuntimeError("held-out total duration changed")
    return rows


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
    heldout = _validate_heldout_rows(paths["heldout_intervals"], registration)

    population = _load_json(paths["population_status"])
    if int(population["heldout_interval_count"]) != len(heldout):
        raise RuntimeError("population status held-out count changed")
    if not math.isclose(
        float(population["heldout_total_hours"]),
        float(
            registration["frozen_inputs"]["heldout_intervals"][
                "expected_total_duration_h"
            ]
        ),
        rel_tol=0.0,
        abs_tol=1.0e-12,
    ):
        raise RuntimeError("population status held-out duration changed")
    v1_status = _load_json(paths["v1_DAS_status"])
    for field in (
        "network_candidate_tables_opened",
        "catalog_event_time_tables_opened",
        "heldout_interval_tables_opened",
    ):
        if int(v1_status[field]) != 0:
            raise PermissionError("v1 candidate generation crossed its embargo")
    comparison = _load_json(paths["v1_comparison_status"])
    if str(comparison["heldout_access_gate"]) != "STOP_NOT_YET_AUTHORIZED":
        raise PermissionError("v1 comparison does not preserve held-out seal")
    network = _load_json(paths["network_union_status"])
    if int(network["heldout_intervals_opened"]) != 0:
        raise PermissionError("development network union opened held-out data")
    ledger = registration["access_ledger_at_registration"]
    for field in (
        "heldout_DAS_HDF5_files_opened",
        "heldout_DAS_HDF5_datasets_opened",
        "heldout_network_waveform_files_opened",
        "heldout_network_candidate_rows_opened",
        "heldout_catalog_event_rows_opened",
        "heldout_family_label_rows_opened",
    ):
        if int(ledger[field]) != 0:
            raise PermissionError("v2 registration access ledger is nonzero")

    output = project / str(registration["output"]["development_directory"])
    status_path = output / str(
        registration["output"]["development_registration_status_json"]
    )
    status = {
        "status": PASS,
        "stage": "das_v2_registered_before_any_heldout_waveform_access",
        "registration_status": "FROZEN_FOR_DEVELOPMENT_REPLAY_ONLY",
        "generated_utc": utc_now(),
        "das_v2_config_sha256": registration["_config_sha256"],
        "frozen_input_sha256": {
            name: str(declaration["sha256"])
            for name, declaration in registration["frozen_inputs"].items()
        },
        "development_tuning_disclosed": True,
        "threshold_sweep_used_to_select_gate": False,
        "strong_block_characteristic_ratio": registration[
            "v2_candidate_rule"
        ]["strong_block_characteristic_ratio"],
        "minimum_strong_block_count": registration["v2_candidate_rule"][
            "minimum_strong_block_count"
        ],
        "v1_score_threshold_repair_enabled": False,
        "heldout_interval_metadata_rows_opened": len(heldout),
        "heldout_interval_ids": [str(row["interval_id"]) for row in heldout],
        "heldout_total_duration_h": sum(
            float(row["duration_s"]) for row in heldout
        )
        / 3600.0,
        "heldout_intervals_all_sealed": True,
        "v1_DAS_candidate_rows_opened_by_registration": 0,
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "heldout_network_waveform_files_opened": 0,
        "heldout_network_candidate_rows_opened": 0,
        "heldout_catalog_event_rows_opened": 0,
        "heldout_family_label_rows_opened": 0,
        "next_stage_gate": "PASS_V2_DEVELOPMENT_REPLAY_ONLY",
        "heldout_network_access_gate": (
            "STOP_UNTIL_V2_IMPLEMENTATION_IS_TESTED_COMMITTED_AND_PUSHED"
        ),
        "heldout_DAS_access_gate": (
            "STOP_UNTIL_HELDOUT_NETWORK_UNION_IS_FROZEN"
        ),
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
