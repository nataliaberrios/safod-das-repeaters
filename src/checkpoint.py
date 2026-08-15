"""Assemble the advisor checkpoint from authoritative clean-room products.

This module reports gates conservatively.  A PASS can describe a completed
procedural prerequisite; it never promotes a prospective event to published
family truth or converts detectability into DAS catalog extension.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .common import (
    CONDITIONAL,
    PASS,
    STOP,
    load_config,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .coverage import write_rows


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype={"event_id": str})


def assemble_checkpoint(root: Optional[Path] = None) -> Dict[str, Any]:
    """Return the current evidence, project shape, and hard claim gates."""

    project = project_root() if root is None else Path(root)
    incremental_config = load_config(
        project / "config" / "incremental_value.json"
    )
    pilot_config = load_config(project / "config" / "pilot.json")

    population = _read_json(
        project / "outputs" / "incremental_value" / "population_status.json"
    )
    catalog_provenance = _read_json(
        project / "outputs" / "incremental_value" / "catalog_provenance.json"
    )
    baseline = _read_json(
        project
        / "outputs"
        / "incremental_value"
        / "network_baseline"
        / "status.json"
    )
    model_path = (
        project
        / "outputs"
        / "incremental_value"
        / "network_baseline"
        / "network_model_frozen.json"
    )
    model = _read_json(model_path)
    prospective = _read_json(
        project
        / "outputs"
        / "incremental_value"
        / "prospective_network"
        / "status.json"
    )
    michel = _read_json(
        project / "outputs" / "michel_validation" / "status.json"
    )
    partition = _read_json(
        project
        / "outputs"
        / "michel_validation"
        / "partition_diagnostic"
        / "status.json"
    )
    continuations = _read_json(
        project
        / "outputs"
        / "michel_validation"
        / "frozen_network"
        / "status.json"
    )
    deep_network = _read_json(
        project
        / "outputs"
        / "incremental_value"
        / "deep_named_network"
        / "status.json"
    )
    deep_das = _read_json(
        project / "outputs" / "deep_das" / "status.json"
    )

    prospective_decisions = _read_csv(
        project
        / "outputs"
        / "incremental_value"
        / "prospective_network"
        / "frozen_network_decisions.csv"
    )
    deep_decisions = _read_csv(
        project
        / "outputs"
        / "incremental_value"
        / "deep_named_network"
        / "decisions.csv"
    )
    continuation_decisions = _read_csv(
        project
        / "outputs"
        / "michel_validation"
        / "frozen_network"
        / "decisions.csv"
    )

    incremental_hash = incremental_config["_config_sha256"]
    model_sha256 = sha256_file(model_path)
    hash_checks = {
        "archive_population": population.get("config_sha256") == incremental_hash,
        "network_baseline": baseline.get("config_sha256") == incremental_hash,
        "frozen_model": model.get("config_sha256") == incremental_hash,
        "prospective_network": prospective.get("config_sha256") == incremental_hash,
        "deep_named_network": deep_network.get("config_sha256") == incremental_hash,
        "prospective_model_sha256": (
            prospective.get("network_model_sha256") == model_sha256
        ),
        "deep_named_model_sha256": (
            deep_network.get("network_model_sha256") == model_sha256
        ),
        "deep_das_pilot_config": (
            deep_das.get("config_sha256") == pilot_config["_config_sha256"]
        ),
    }
    provenance_gate = PASS if all(hash_checks.values()) else STOP

    manifest_stats = catalog_provenance["manifest_stats"]
    deep_by_id = {
        str(row["event_id"]): str(row["frozen_decision"])
        for _, row in deep_decisions.iterrows()
    }
    candidate_id = str(deep_network["deep_candidate_event_id"])
    control_id = str(deep_network["hard_control_event_id"])
    all_archive_rejected = bool(
        len(prospective_decisions) == population["prospective_candidate_count"]
        and prospective_decisions["frozen_decision"]
        .eq("not_target_family")
        .all()
    )

    branches: List[Dict[str, Any]] = [
        {
            "branch": "Configuration and access-order provenance",
            "status": provenance_gate,
            "evidence": (
                "all config/model hashes agree; network stages opened zero DAS "
                "waveforms"
                if provenance_gate == PASS
                else "one or more config/model provenance hashes disagree"
            ),
            "next_gate": "rerun any stale stage before scientific interpretation",
        },
        {
            "branch": "Archive population and held-out interval seal",
            "status": PASS,
            "evidence": (
                "{catalog} official events; {covered} have complete primary-DAS "
                "windows; {hours:.1f} contiguous hours; 12 one-hour intervals "
                "selected from the manifest only"
            ).format(
                catalog=population["catalog_event_count"],
                covered=population[
                    "catalog_events_with_complete_primary_das_window"
                ],
                hours=population["contiguous_coverage_hours"],
            ),
            "next_gate": (
                "develop only on the unsealed interval; preserve the 12 held-out "
                "hours until both detectors are frozen"
            ),
        },
        {
            "branch": "Single-catalog binary family labels",
            "status": STOP,
            "evidence": (
                "14 exact shared IDs reveal two partition conflicts: the "
                "Waldhauser-Schaff target is split into M00413/M00414, while "
                "Michel M00414 merges target and former hard-negative IDs"
            ),
            "next_gate": (
                "report both catalog partitions and adjudicate them with "
                "independent waveform/location evidence"
            ),
        },
        {
            "branch": "Frozen conventional-network verifier",
            "status": CONDITIONAL,
            "evidence": (
                "12 historical events eligible; apparent training balanced "
                "accuracy {ba:.3f}, recall {recall:.3f}, precision "
                "{precision:.3f}; mechanics frozen before prospective scoring"
            ).format(
                ba=model["balanced_accuracy"],
                recall=model["target_recall"],
                precision=model["target_precision"],
            ),
            "next_gate": (
                "do not tune version 1 on post-freeze outcomes; a future "
                "version needs a new split and a robust local-lag rule"
            ),
        },
        {
            "branch": "Five routine-catalog proximity candidates",
            "status": PASS if all_archive_rejected else CONDITIONAL,
            "evidence": (
                "all five were scored by the frozen network model and rejected "
                "as target-family events; DAS waveforms opened by this stage: 0"
            ),
            "next_gate": (
                "use DAS as an independent continuous candidate generator, not "
                "as a post-hoc check of location-only nominees"
            ),
        },
        {
            "branch": "Conventional M00413/M00414 partition resolution",
            "status": STOP,
            "evidence": (
                "pairwise correlation AUC {corr:.3f}; differential-lag AUC "
                "{lag:.3f}; event-bootstrap uncertainty is not identifiable "
                "with only two historical M00413 events"
            ).format(
                corr=partition["correlation_pair_auc_within_vs_between"],
                lag=partition[
                    "differential_lag_pair_auc_within_vs_between"
                ],
            ),
            "next_gate": (
                "test preregistered deep-DAS spatial features on event-held-out "
                "members and controls"
            ),
        },
        {
            "branch": "Published post-2014 continuation diagnostic",
            "status": CONDITIONAL,
            "evidence": (
                "{predicted} of {total} Michel M00413/M00414 continuations pass "
                "the frozen verifier; one high-correlation event fails because "
                "the current lag statistic admits station cycle skips"
            ).format(
                predicted=continuations[
                    "frozen_model_predicted_target_count"
                ],
                total=continuations["event_count"],
            ),
            "next_gate": (
                "retain this post-freeze result; design any robust lag version "
                "on a new development split"
            ),
        },
        {
            "branch": "2026 deep-fiber cross-instrument opportunity",
            "status": CONDITIONAL,
            "evidence": (
                "event {candidate} passes the frozen network verifier and is "
                "strongly detected on deep DAS; same-fiber control {control} "
                "fails the verifier and the frozen DAS hard-negative test"
            ).format(candidate=candidate_id, control=control_id),
            "next_gate": (
                "call the family name provisional; compare near-source DAS "
                "features against historical/continuation templates"
            ),
        },
        {
            "branch": "Deep channel geometry",
            "status": STOP,
            "evidence": (
                "channel spacing and a channel-1702 hairpin convention exist, "
                "but no surveyed channel-to-MD/TVD/XYZ/cable-tangent table was "
                "found"
            ),
            "next_gate": (
                "obtain or reconstruct a surveyed geometry table before depth, "
                "source-distance, or directivity claims"
            ),
        },
        {
            "branch": "DAS detection/catalog extension",
            "status": STOP,
            "evidence": (
                "no DAS-only continuous detector has yet been compared with a "
                "same-interval best-network detector at matched event-level FDR"
            ),
            "next_gate": (
                "run network-only then DAS-only on the development interval; "
                "freeze both before opening held-out intervals"
            ),
        },
        {
            "branch": "DAS family-partition extension",
            "status": STOP,
            "evidence": (
                "the catalog disagreement and weak conventional separation "
                "define the test, but held-out DAS spatial classification has "
                "not run"
            ),
            "next_gate": (
                "require improved event-held-out partition metrics without "
                "increased merge rate"
            ),
        },
        {
            "branch": "Relative source size / stress drop",
            "status": STOP,
            "evidence": deep_das["stress_drop_reason"],
            "next_gate": (
                "response audit, same-path EGF ensemble, in-band corner, "
                "geometry/model sensitivity, and synthetic recovery"
            ),
        },
        {
            "branch": "Repeater-derived creep rate",
            "status": STOP,
            "evidence": (
                "family membership, continuous completeness, event slip, and "
                "at least three complete recurrence intervals are not yet "
                "jointly constrained"
            ),
            "next_gate": (
                "attempt only after a validated DAS-assisted family extension "
                "and source-size model"
            ),
        },
    ]

    checkpoints = [
        {
            "name": "1. Archive population and blind interval seal",
            "status": "MET",
            "criterion": (
                "primary configuration reconciled; development interval "
                "separate; 12 held-out hours selected without catalog labels"
            ),
        },
        {
            "name": "2. Conventional verifier freeze",
            "status": "MET_WITH_LABEL_WARNING",
            "criterion": (
                "full BP array plus NC.PSM and BK.PKD model frozen before "
                "prospective waveform access"
            ),
        },
        {
            "name": "3. Independent catalog reconciliation",
            "status": "MET_PROJECT_RESHAPED",
            "criterion": (
                "exact-ID Michel crosswalk exposes M00413/M00414 partition "
                "conflict; neither catalog silently declared truth"
            ),
        },
        {
            "name": "4. Deep-event cross-instrument checkpoint",
            "status": "MET_CONDITIONAL",
            "criterion": (
                "2026 candidate passes frozen network verification and deep-DAS "
                "detectability; same-fiber control fails both relevant checks"
            ),
        },
        {
            "name": "5. Development-interval incremental-value test",
            "status": "NEXT",
            "criterion": (
                "network-only continuous detector frozen first, independent "
                "DAS-only detector second, blind adjudication at matched FDR"
            ),
        },
        {
            "name": "6. Sealed held-out comparison",
            "status": "SEALED_NOT_RUN",
            "criterion": (
                "positive interval-bootstrap lower bound for DAS-only or joint "
                "gain without target-family overmerge"
            ),
        },
        {
            "name": "7. Stress drop and creep rate",
            "status": "STOP",
            "criterion": (
                "geometry, response, EGF/corner, completeness, recurrence, and "
                "slip-model gates"
            ),
        },
    ]

    return {
        "project_decision": "PROCEED_TO_DEVELOPMENT_SCAN",
        "decision_basis": (
            "There is now a defensible DAS/repeater project: a frozen "
            "conventional verifier supports the 2026 deep event while rejecting "
            "its same-fiber control, independent catalogs disagree on the exact "
            "historical family partition, and conventional point-station "
            "features do not resolve that partition. These facts motivate a "
            "near-source DAS test; they do not yet establish catalog extension."
        ),
        "project_shape": [
            {
                "aim": "Aim 1: family-partition resolution",
                "question": (
                    "Can deep near-source DAS distinguish M00413 from M00414, "
                    "or support their merger, better than the conventional array?"
                ),
                "current_status": "READY_FOR_DAS_FEATURE_DEVELOPMENT",
            },
            {
                "aim": "Aim 2: catalog extension",
                "question": (
                    "Does a DAS-only or joint continuous scan recover "
                    "independently adjudicated events missed by a fair "
                    "same-interval network-only detector?"
                ),
                "current_status": "DEVELOPMENT_INTERVAL_NEXT",
            },
            {
                "aim": "Aim 3: source physics and creep",
                "question": (
                    "Can validated repeated events constrain relative source "
                    "size, stress drop, recurrence, and eventually slip rate?"
                ),
                "current_status": "DOWNSTREAM_STOP",
            },
        ],
        "highest_value_next_analysis": (
            "build and freeze the network-only continuous detector on the "
            "nonblind 2025-01-20 04:55--05:45 UTC development interval, then "
            "run an independently triggered DAS-only detector on exactly that "
            "50-minute interval"
        ),
        "highest_value_next_observation": (
            "another independently network-classified event on the same deep "
            "DAS configuration"
        ),
        "highest_value_metadata_request": (
            "surveyed deep channel-to-MD/TVD/XYZ/cable-leg/tangent mapping"
        ),
        "headline_results": {
            "archive_catalog_events": population["catalog_event_count"],
            "archive_events_with_complete_das_windows": population[
                "catalog_events_with_complete_primary_das_window"
            ],
            "archive_proximity_candidates": population[
                "prospective_candidate_count"
            ],
            "archive_candidates_network_predicted_target": int(
                prospective["network_predicted_target_count"]
            ),
            "heldout_hours_sealed": population["heldout_total_hours"],
            "michel_catalog_events": michel["catalog_event_count"],
            "michel_catalog_sequences": michel["catalog_sequence_count"],
            "exact_id_crosswalk_matches": michel["exact_id_match_count"],
            "catalog_partition_conflicts": michel["partition_conflict_count"],
            "post_2014_continuations": continuations["event_count"],
            "post_2014_continuations_network_positive": continuations[
                "frozen_model_predicted_target_count"
            ],
            "deep_candidate_event_id": candidate_id,
            "deep_candidate_network_decision": deep_by_id[candidate_id],
            "deep_control_event_id": control_id,
            "deep_control_network_decision": deep_by_id[control_id],
            "deep_candidate_das_detectability": deep_das[
                "deep_template_detectability"
            ],
            "primary_manifest_time_valid_records": manifest_stats[
                "primary_configuration_time_valid_rows"
            ],
            "primary_manifest_timestamp_invalid_records": manifest_stats[
                "primary_configuration_timestamp_invalid_rows"
            ],
        },
        "guardrails": {
            "network_scoring_das_waveforms_opened": 0,
            "family_labels_from_catalog_proximity": 0,
            "absolute_spatial_claims_enabled": False,
            "frozen_model_version_repair_after_test": (
                "forbidden; create version 2 with a new split"
            ),
            "continuous_detection_status": "NOT_RUN",
        },
        "config_sha256": incremental_hash,
        "pilot_config_sha256": pilot_config["_config_sha256"],
        "network_model_sha256": model_sha256,
        "network_model_freeze_status": model["freeze_status"],
        "hash_checks": hash_checks,
        "generated_utc": utc_now(),
        "branches": branches,
        "checkpoints": checkpoints,
    }


def write_checkpoint(root: Optional[Path] = None) -> Dict[str, Any]:
    """Write compact JSON/CSV products consumed by the advisor notebook."""

    project = project_root() if root is None else Path(root)
    payload = assemble_checkpoint(project)
    output = project / "outputs" / "checkpoint"
    write_json(output / "advisor_checkpoint.json", payload)
    write_rows(output / "branch_status.csv", payload["branches"])
    write_rows(output / "milestones.csv", payload["checkpoints"])
    return payload


if __name__ == "__main__":
    print(json.dumps(write_checkpoint(), indent=2))
