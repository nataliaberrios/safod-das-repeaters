#!/usr/bin/env python
"""Register the network-first held-out run without opening held-out waveforms."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import (
    PASS,
    load_config,
    parse_utc,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)


TEMPLATE_INVENTORY_FIELDS = [
    "event_id",
    "source_name",
    "network",
    "availability",
    "waveform_path",
    "waveform_bytes",
    "waveform_sha256",
    "provenance_path",
    "provenance_sha256",
    "request_url",
    "access_role",
    "trace_count",
]


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


def _validate_frozen_inputs(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Path]:
    paths: Dict[str, Path] = {}
    for name, declaration in registration["frozen_inputs"].items():
        raw = Path(str(declaration["path"]))
        path = raw if raw.is_absolute() else project / raw
        if not path.is_file():
            raise FileNotFoundError(
                "missing held-out network frozen input: {}".format(path)
            )
        observed = sha256_file(path)
        expected = str(declaration["sha256"])
        if observed != expected:
            raise RuntimeError(
                "held-out network frozen input changed for {}: {} != {}".format(
                    name, observed, expected
                )
            )
        paths[str(name)] = path
    return paths


def validate_heldout_interval_rows(
    rows: Sequence[Mapping[str, Any]], registration: Mapping[str, Any]
) -> Tuple[float, List[str]]:
    """Validate the sealed metadata population without waveform access."""

    expected = registration["heldout_population"]
    if len(rows) != int(expected["interval_count"]):
        raise RuntimeError("held-out interval count changed")
    identifiers = [str(row["interval_id"]) for row in rows]
    if identifiers != list(map(str, expected["interval_ids"])):
        raise RuntimeError("held-out interval identities or order changed")
    if len(set(identifiers)) != len(identifiers):
        raise RuntimeError("held-out interval identifiers are not unique")

    required_status = str(expected["required_analysis_status_at_registration"])
    required_inputs = str(expected["required_selection_inputs"])
    required_seed = int(expected["required_selection_seed"])
    duration_s = float(expected["interval_duration_s"])
    total_s = 0.0
    previous_end = float("-inf")
    for row in rows:
        if str(row["analysis_status"]) != required_status:
            raise PermissionError("a held-out interval is no longer sealed")
        if str(row["selection_inputs"]) != required_inputs:
            raise PermissionError("held-out interval selection inputs changed")
        if int(row["selection_seed"]) != required_seed:
            raise PermissionError("held-out interval selection seed changed")
        start_s = parse_utc(str(row["start_utc"])).timestamp()
        end_s = parse_utc(str(row["end_utc"])).timestamp()
        row_duration = float(row["duration_s"])
        if not math.isclose(
            row_duration, duration_s, rel_tol=0.0, abs_tol=1.0e-6
        ):
            raise RuntimeError("held-out interval duration changed")
        if not math.isclose(
            end_s - start_s, duration_s, rel_tol=0.0, abs_tol=1.0e-6
        ):
            raise RuntimeError("held-out interval timestamps changed")
        if start_s < previous_end:
            raise RuntimeError("held-out intervals overlap or are unordered")
        previous_end = end_s
        total_s += duration_s
    expected_total_s = float(expected["total_duration_h"]) * 3600.0
    if not math.isclose(
        total_s, expected_total_s, rel_tol=0.0, abs_tol=1.0e-6
    ):
        raise RuntimeError("held-out total duration changed")
    return total_s / 3600.0, identifiers


def _assert_equal(
    observed: Any, expected: Any, label: str
) -> None:
    if isinstance(observed, (int, float)) and isinstance(
        expected, (int, float)
    ):
        if not math.isclose(
            float(observed),
            float(expected),
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise RuntimeError("{} changed".format(label))
    elif observed != expected:
        raise RuntimeError("{} changed".format(label))


def validate_detector_inheritance(
    registration: Mapping[str, Any],
    parent: Mapping[str, Any],
    development: Mapping[str, Any],
    development_status: Mapping[str, Any],
    development_union: Mapping[str, Any],
    development_union_status: Mapping[str, Any],
    das_v2: Mapping[str, Any],
    das_v2_status: Mapping[str, Any],
) -> None:
    """Prove that held-out network mechanics equal the development freeze."""

    if str(registration["registration_state"]) != (
        "SPECIFIED_AFTER_REMOTE_DAS_V2_CHECKPOINT_BEFORE_ANY_"
        "HELDOUT_NETWORK_WAVEFORM_ACCESS"
    ):
        raise PermissionError("held-out network stage was not preregistered")
    release = registration["release_anchor"]
    if not bool(release["remote_branch_sha_verified_equal_before_registration"]):
        raise PermissionError("DAS-v2 remote checkpoint was not verified")
    if str(release["repository_visibility"]) != "private":
        raise PermissionError("release anchor does not record a private repo")

    if str(development_status["development_config_sha256"]) != str(
        registration["frozen_inputs"]["development_config"]["sha256"]
    ):
        raise RuntimeError("development status/config checksum mismatch")
    _assert_equal(
        registration["template_branch"]["threshold"],
        development_status["template_detection_threshold"],
        "template threshold",
    )
    _assert_equal(
        registration["generic_branch"]["threshold"],
        development_status["generic_detection_threshold"],
        "generic threshold",
    )
    if str(
        registration["template_branch"][
            "threshold_recalibration_on_heldout"
        ]
    ) != "FORBIDDEN":
        raise PermissionError("template threshold can be recalibrated")
    if str(
        registration["generic_branch"][
            "threshold_recalibration_on_heldout"
        ]
    ) != "FORBIDDEN":
        raise PermissionError("generic threshold can be recalibrated")
    if str(registration["candidate_generation"]["null_recalibration"]) != "FORBIDDEN":
        raise PermissionError("held-out null recalibration is permitted")
    if str(
        registration["candidate_generation"][
            "candidate_deletion_after_review"
        ]
    ) != "FORBIDDEN":
        raise PermissionError("held-out candidate deletion is permitted")

    expected_template_ids = list(
        map(str, registration["historical_template_bank"]["event_ids"])
    )
    if list(
        map(str, development_status["template_bank_event_ids"])
    ) != expected_template_ids:
        raise RuntimeError("historical template bank membership changed")
    if str(development_status["injection_acceptance_status"]) != "STOP":
        raise RuntimeError("generic development STOP was not preserved")
    primary = {
        str(row["detector"]): str(row["acceptance_status"])
        for row in development_status["injection_primary_acceptance_rows"]
    }
    if primary != {"template_bank": "PASS", "generic_trigger": "STOP"}:
        raise RuntimeError("unexpected development branch acceptance state")
    if str(registration["generic_branch"]["development_warning"]) != (
        "SNR1_INJECTION_RECOVERY_22_OF_30_STOP"
    ):
        raise RuntimeError("generic development warning changed")

    template_source = development["repeater_template_bank"]
    for field in (
        "template_window_s",
        "template_noise_window_s",
        "band_hz",
        "target_sample_rate_hz",
        "score_sample_rate_hz",
        "minimum_template_trace_snr",
        "minimum_components_per_template",
        "minimum_stations_per_template",
        "component_aggregation",
        "station_aggregation",
        "bank_aggregation",
        "correlation_polarity",
        "candidate_minimum_separation_s",
        "station_support_recording_threshold",
    ):
        _assert_equal(
            registration["template_branch"][field],
            template_source[field],
            "template {}".format(field),
        )

    generic_source = development["generic_network_trigger"]
    _assert_equal(
        registration["generic_branch"]["band_hz"],
        template_source["band_hz"],
        "generic inherited band",
    )
    _assert_equal(
        registration["generic_branch"]["target_sample_rate_hz"],
        template_source["target_sample_rate_hz"],
        "generic target sample rate",
    )
    for field in (
        "sta_window_s",
        "lta_window_s",
        "coincidence_station_count",
        "station_characteristic_support_threshold",
        "score_sample_rate_hz",
        "candidate_minimum_separation_s",
    ):
        _assert_equal(
            registration["generic_branch"][field],
            generic_source[field],
            "generic {}".format(field),
        )

    acquisition = registration["waveform_acquisition"]
    _assert_equal(
        acquisition["request_padding_s"],
        development["continuous_network"]["request_padding_s"],
        "network request padding",
    )
    _assert_equal(
        acquisition["merge_maximum_gap_s"],
        development["continuous_network"]["merge_maximum_gap_s"],
        "network merge gap",
    )
    if str(acquisition["dataselect_service"]) != "inherit_parent_exactly":
        raise PermissionError("network dataselect service is not inherited")
    if str(acquisition["network_sources"]) != "inherit_parent_exactly":
        raise PermissionError("network sources are not inherited")
    if len(parent["network_array"]["sources"]) != int(
        registration["historical_template_bank"][
            "expected_declared_source_count_per_event"
        ]
    ):
        raise RuntimeError("declared network source count changed")

    union = registration["time_only_union"]
    _assert_equal(
        union["cross_branch_match_window_s"],
        development_union["time_only_union"][
            "cross_branch_match_window_s"
        ],
        "network union match window",
    )
    _assert_equal(
        union["matching_algorithm"],
        development_union["time_only_union"]["matching_algorithm"]
        + "_within_each_interval",
        "network union matching algorithm",
    )
    _assert_equal(
        union["representative_time_rule"],
        development_union["time_only_union"]["representative_time_rule"],
        "network union representative time",
    )
    if int(development_union_status["heldout_intervals_opened"]) != 0:
        raise PermissionError("development union opened held-out data")
    if int(
        development_union_status["network_union_stage_das_waveforms_opened"]
    ) != 0:
        raise PermissionError("development union opened DAS waveforms")

    if str(das_v2_status["das_v2_config_sha256"]) != str(
        das_v2["_config_sha256"]
    ):
        raise RuntimeError("DAS-v2 status/config checksum mismatch")
    if int(das_v2_status["heldout_network_waveform_files_opened"]) != 0:
        raise PermissionError("DAS-v2 stage opened held-out network data")
    if int(das_v2_status["heldout_DAS_HDF5_files_opened"]) != 0:
        raise PermissionError("DAS-v2 stage opened held-out DAS data")
    if str(das_v2_status["heldout_DAS_access_gate"]) != (
        "STOP_UNTIL_HELDOUT_NETWORK_UNION_IS_FROZEN"
    ):
        raise PermissionError("DAS-v2 held-out ordering changed")


def _relative(project: Path, path: Path) -> str:
    try:
        return str(path.relative_to(project))
    except ValueError:
        return str(path)


def build_template_input_inventory(
    project: Path,
    registration: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Hash historical templates and sidecars; never parse held-out data."""

    settings = registration["historical_template_bank"]
    cache_root = project / str(settings["waveform_cache_directory"])
    event_ids = list(map(str, settings["event_ids"]))
    sources = list(parent["network_array"]["sources"])
    rows: List[Dict[str, Any]] = []
    available_by_event: Counter[str] = Counter()
    for event_id in event_ids:
        for source in sources:
            source_name = str(source["name"])
            base = cache_root / event_id / source_name
            waveform_path = base.with_suffix(".mseed")
            provenance_path = base.with_suffix(".provenance.json")
            waveform_exists = (
                waveform_path.is_file() and waveform_path.stat().st_size > 0
            )
            provenance_exists = provenance_path.is_file()
            if waveform_exists != provenance_exists:
                raise RuntimeError(
                    "historical template waveform/sidecar mismatch: {}".format(
                        base
                    )
                )
            row: Dict[str, Any] = {
                "event_id": event_id,
                "source_name": source_name,
                "network": str(source["network"]),
                "availability": "missing",
                "waveform_path": _relative(project, waveform_path),
                "waveform_bytes": "",
                "waveform_sha256": "",
                "provenance_path": _relative(project, provenance_path),
                "provenance_sha256": "",
                "request_url": "",
                "access_role": "",
                "trace_count": "",
            }
            if waveform_exists:
                provenance = _load_json(provenance_path)
                if str(provenance.get("event_id", "")) != event_id:
                    raise RuntimeError("historical template event ID mismatch")
                if str(provenance.get("source_name", "")) != source_name:
                    raise RuntimeError("historical template source mismatch")
                if str(provenance.get("config_sha256", "")) != str(
                    parent["_config_sha256"]
                ):
                    raise RuntimeError("historical template config mismatch")
                if str(provenance.get("access_role", "")) != (
                    "historical_exact_id_training"
                ):
                    raise PermissionError(
                        "historical template access role changed"
                    )
                row.update(
                    {
                        "availability": "available",
                        "waveform_bytes": waveform_path.stat().st_size,
                        "waveform_sha256": sha256_file(waveform_path),
                        "provenance_sha256": sha256_file(provenance_path),
                        "request_url": str(provenance.get("request_url", "")),
                        "access_role": str(provenance["access_role"]),
                        "trace_count": int(provenance["trace_count"]),
                    }
                )
                available_by_event[event_id] += 1
            rows.append(row)

    minimum_sources = int(settings["minimum_available_historical_sources_per_event"])
    insufficient = [
        event_id
        for event_id in event_ids
        if available_by_event[event_id] < minimum_sources
    ]
    if insufficient:
        raise RuntimeError(
            "historical templates have too few cached sources: {}".format(
                insufficient
            )
        )
    return rows


def _validate_template_membership(
    rows: Sequence[Mapping[str, Any]], registration: Mapping[str, Any]
) -> None:
    passed_ids = [
        str(row["event_id"])
        for row in rows
        if str(row["status"]) == "PASS"
    ]
    expected = list(
        map(str, registration["historical_template_bank"]["event_ids"])
    )
    if passed_ids != expected:
        raise RuntimeError("development template PASS membership changed")


def _validate_release_anchor(
    project: Path, registration: Mapping[str, Any]
) -> Dict[str, Any]:
    release = registration["release_anchor"]
    anchor = str(release["das_v2_implementation_commit_sha"])
    if len(anchor) != 40 or any(
        character not in "0123456789abcdef" for character in anchor
    ):
        raise ValueError("release anchor is not a full lowercase Git SHA")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "cat-file", "-e", anchor + "^{commit}"],
        cwd=project,
        check=True,
        capture_output=True,
        text=True,
    )
    ancestor = subprocess.run(
        ["git", "merge-base", "--is-ancestor", anchor, head],
        cwd=project,
        check=False,
        capture_output=True,
        text=True,
    )
    if ancestor.returncode != 0:
        raise PermissionError("DAS-v2 release anchor is not an ancestor of HEAD")
    return {
        "registered_DAS_v2_commit_sha": anchor,
        "local_HEAD_at_registration": head,
        "release_anchor_is_ancestor_of_HEAD": True,
        "remote_branch_sha_verified_equal_before_registration": bool(
            release["remote_branch_sha_verified_equal_before_registration"]
        ),
    }


def _heldout_cache_files(
    project: Path, registration: Mapping[str, Any]
) -> List[Path]:
    cache = project / str(
        registration["waveform_acquisition"]["cache_directory"]
    )
    if not cache.exists():
        return []
    suffixes = {".mseed", ".partial", ".npz"}
    return sorted(
        path
        for path in cache.rglob("*")
        if path.is_file() and path.suffix in suffixes
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=(
            project_root() / "config" / "heldout_network_validation.json"
        ),
    )
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    paths = _validate_frozen_inputs(project, registration)
    parent = load_config(paths["parent_config"])
    development = load_config(paths["development_config"])
    development_status = _load_json(paths["development_status"])
    development_union = load_config(paths["development_union_config"])
    development_union_status = _load_json(
        paths["development_union_status"]
    )
    das_v2 = load_config(paths["das_v2_config"])
    das_v2_status = _load_json(paths["das_v2_development_status"])

    validate_detector_inheritance(
        registration,
        parent,
        development,
        development_status,
        development_union,
        development_union_status,
        das_v2,
        das_v2_status,
    )
    heldout_rows = _read_csv(paths["heldout_intervals"])
    total_hours, interval_ids = validate_heldout_interval_rows(
        heldout_rows, registration
    )
    population = _load_json(paths["population_status"])
    if int(population["heldout_interval_count"]) != len(heldout_rows):
        raise RuntimeError("population status held-out count changed")
    _assert_equal(
        population["heldout_total_hours"],
        total_hours,
        "population status held-out duration",
    )

    template_rows = _read_csv(paths["development_template_inventory"])
    _validate_template_membership(template_rows, registration)
    release_status = _validate_release_anchor(project, registration)
    prior_cache_files = _heldout_cache_files(project, registration)
    if prior_cache_files:
        raise PermissionError(
            "held-out network waveform cache existed before registration: {}".format(
                prior_cache_files
            )
        )

    inventory = build_template_input_inventory(
        project, registration, parent
    )
    output = project / str(
        registration["output"]["registration_directory"]
    )
    inventory_path = output / str(
        registration["output"]["template_input_inventory_csv"]
    )
    status_path = output / str(
        registration["output"]["registration_status_json"]
    )
    _write_csv(inventory_path, inventory, TEMPLATE_INVENTORY_FIELDS)
    available_count = sum(
        str(row["availability"]) == "available" for row in inventory
    )
    missing_count = len(inventory) - available_count

    status = {
        "status": PASS,
        "stage": "heldout_network_registered_before_waveform_access",
        "registration_status": "FROZEN_FOR_RUNNER_IMPLEMENTATION_ONLY",
        "generated_utc": utc_now(),
        "heldout_network_config_sha256": registration["_config_sha256"],
        "frozen_input_sha256": {
            name: str(declaration["sha256"])
            for name, declaration in registration["frozen_inputs"].items()
        },
        "release_anchor": release_status,
        "interval_count": len(heldout_rows),
        "interval_ids": interval_ids,
        "interval_duration_s": registration["heldout_population"][
            "interval_duration_s"
        ],
        "heldout_total_duration_h": total_hours,
        "heldout_intervals_all_sealed_at_registration": True,
        "template_threshold": registration["template_branch"]["threshold"],
        "generic_threshold": registration["generic_branch"]["threshold"],
        "threshold_recalibration_enabled": False,
        "generic_development_SNR1_STOP_preserved": True,
        "historical_template_event_count": len(
            registration["historical_template_bank"]["event_ids"]
        ),
        "historical_template_declared_source_row_count": len(inventory),
        "historical_template_available_waveform_count": available_count,
        "historical_template_missing_source_count": missing_count,
        "historical_template_inventory_path": str(inventory_path),
        "historical_template_inventory_sha256": sha256_file(inventory_path),
        "heldout_interval_metadata_rows_opened": len(heldout_rows),
        "heldout_network_cache_files_present_before_registration": 0,
        "heldout_network_waveform_files_opened": 0,
        "heldout_network_waveform_datasets_opened": 0,
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "heldout_catalog_event_rows_opened": 0,
        "heldout_family_label_rows_opened": 0,
        "next_stage_gate": (
            "PASS_IMPLEMENT_TEST_COMMIT_AND_PUSH_HELDOUT_NETWORK_RUNNER"
        ),
        "heldout_network_access_gate": (
            "STOP_PENDING_TESTED_RUNNER_IMPLEMENTATION_AND_REMOTE_PUSH"
        ),
        "heldout_DAS_access_gate": (
            "STOP_UNTIL_COMPLETE_HELDOUT_NETWORK_UNION_IS_FROZEN"
        ),
        "heldout_catalog_access_gate": (
            "STOP_UNTIL_ALL_NETWORK_TIME_ONLY_UNION_ROWS_ARE_"
            "MATERIALIZED_AND_CHECKSUMMED"
        ),
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
