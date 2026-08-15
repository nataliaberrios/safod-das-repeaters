"""Assemble advisor-facing checkpoints from authoritative v2 products."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from .common import CONDITIONAL, PASS, STOP, load_config, project_root, utc_now, write_json
from .coverage import write_rows


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def assemble_checkpoint(root: Optional[Path] = None) -> Dict[str, Any]:
    """Return conservative project decisions from generated evidence."""

    project = project_root() if root is None else Path(root)
    config = load_config(project / "config" / "pilot.json")
    hrsn = _read_json(project / "outputs" / "hrsn" / "status.json")
    deep = _read_json(project / "outputs" / "deep_das" / "status.json")
    active = _read_json(project / "outputs" / "active_source" / "status.json")
    external = _read_json(
        project / "outputs" / "external_validation" / "status.json"
    )
    network = _read_json(
        project / "outputs" / "external_validation" / "network_family_status.json"
    )
    negative = _read_json(
        project / "outputs" / "deep_das" / "hard_negative_baseline.json"
    )
    shortlist = pd.read_csv(
        project / "outputs" / "hrsn" / "similarity_shortlist_v2.csv"
    )
    deep_metrics = pd.read_csv(project / "outputs" / "deep_das" / "event_metrics.csv")
    coverage = pd.read_csv(project / "outputs" / "coverage" / "coverage_sources.csv")

    high_similarity = int(
        shortlist["membership_status"]
        .isin(["unlabeled_deep_seed", "single_anchor_high_similarity"])
        .sum()
    )
    provisional = int((shortlist["decision"] == "insufficient_data").sum())
    no_data = int((shortlist["decision"] == "download_or_data_error").sum())
    target = deep_metrics.loc[deep_metrics["role"] == "deep_template"].iloc[0]

    hashes = {
        hrsn.get("config_sha256"),
        deep.get("config_sha256"),
        active.get("config_sha256"),
        external.get("config_sha256"),
        network.get("config_sha256"),
        config["_config_sha256"],
    }
    hashes.discard(None)
    hashes_match = len(hashes) == 1

    branches: List[Dict[str, Any]] = [
        {
            "branch": "Pilot product provenance",
            "status": PASS if hashes_match else STOP,
            "evidence": (
                "all generated status products share config hash {}".format(
                    config["_config_sha256"][:12]
                )
                if hashes_match
                else "configuration hashes disagree"
            ),
            "next_gate": "rerun stale branches after any config edit",
        },
        {
            "branch": "Archive/configuration ledger",
            "status": CONDITIONAL,
            "evidence": "{} sources inventoried; HDF5 filenames complete but headers sampled".format(
                len(coverage)
            ),
            "next_gate": "full header/UUID epoch audit before completeness claims",
        },
        {
            "branch": "Single-pair HRSN target/control diagnostic",
            "status": hrsn["target_control_pair_gate"],
            "evidence": "2026 candidate versus published 2014 target member is high-similarity; matched 2026 spatial control is dissimilar",
            "next_gate": "use as nomination evidence only, never as a family label",
        },
        {
            "branch": "Single-anchor shortlist as a family catalog",
            "status": STOP,
            "evidence": "{} high-similarity candidates, {} insufficient-data rows, and {} no-data rows; {} exact-ID published neighboring-family events were overmerged".format(
                high_similarity,
                provisional,
                no_data,
                external["discordant_single_anchor_overmerges"],
            ),
            "next_gate": "replace one-seed assignment with labeled multi-anchor classification plus relative location",
        },
        {
            "branch": "Published external validation population",
            "status": external["external_population_gate"],
            "evidence": "{} target events and {} events from {} neighboring families; {} exact shortlist ID matches".format(
                external["published_target_population_events"],
                external["published_neighbor_population_events"],
                external["published_neighbor_family_count"],
                external["shortlist_exact_id_matches"],
            ),
            "next_gate": "preserve exact-ID matching and event-held-out evaluation",
        },
        {
            "branch": "HRSN multi-family correlation baseline",
            "status": network["classification_gate"],
            "evidence": "{} of {} labeled events available; {} classified and {} abstained under the frozen margin; diagnostic top-family accuracy without the margin is {:.0%}".format(
                network["waveform_available_events"],
                network["labeled_events"],
                network["classified_events"],
                network["waveform_available_events"] - network["classified_events"],
                float(network["top_family_accuracy_ignoring_abstention_policy"]),
            ),
            "next_gate": "add event-held-out differential-time relocation; do not relax the margin on these test labels",
        },
        {
            "branch": "Continuous best-network detection baseline",
            "status": STOP,
            "evidence": "not run on the exact DAS UTC/configuration intervals",
            "next_gate": "freeze HRSN/NCSN templates and false-discovery controls, then scan blind to DAS",
        },
        {
            "branch": "Prospective 2026 candidate on deep DAS",
            "status": deep["deep_template_detectability"],
            "evidence": "median channel SNR {:.2f}; {:.0%} of sampled channel blocks detected; usable median power-SNR ranges {}".format(
                float(target["median_channel_snr"]),
                float(target["detected_block_fraction"]),
                target["usable_power_snr_ranges_hz"],
            ),
            "next_gate": "independently classify the event and preserve clock/configuration QC",
        },
        {
            "branch": "Matched same-fiber hard negative",
            "status": deep["hard_negative_baseline"],
            "evidence": "{} loci: median correlation {:.3f}, p95 {:.3f}, max {:.3f}".format(
                negative["channel_count"],
                negative["median_per_channel_correlation"],
                negative["p95_per_channel_correlation"],
                negative["maximum_per_channel_correlation"],
            ),
            "next_gate": "retain this frozen negative baseline for a preregistered DAS score",
        },
        {
            "branch": "DAS incremental catalog value",
            "status": STOP,
            "evidence": "no blinded same-interval network-only versus DAS-only versus joint scan has run",
            "next_gate": "compare recall at matched event-level false-discovery rate with interval bootstrap uncertainty",
        },
        {
            "branch": "June active-source detectability/response",
            "status": active["shot_detectability"],
            "evidence": "{} of {} shots pass calibration-quality rules; all {} have time-domain detections".format(
                active["shots_calibration_usable"],
                active["shots_total"],
                active["shots_time_domain_detected"],
            ),
            "next_gate": "obtain precise triggers; keep shot 1 conditional",
        },
        {
            "branch": "Absolute shot/DAS timing",
            "status": active["absolute_timing"],
            "evidence": active["absolute_timing_reason"],
            "next_gate": "GPS/detonator trigger log and clock audit",
        },
        {
            "branch": "Deep channel geometry",
            "status": deep["geometry"],
            "evidence": deep["geometry_reason"],
            "next_gate": "surveyed locus-to-MD/TVD/XYZ/cable-tangent table",
        },
        {
            "branch": "Direct DAS repeatability",
            "status": deep["direct_das_repeatability"],
            "evidence": deep["direct_das_repeatability_reason"],
            "next_gate": "second independently classified target-family event on the same configuration",
        },
        {
            "branch": "Corner frequency / stress drop",
            "status": deep["stress_drop"],
            "evidence": deep["stress_drop_reason"],
            "next_gate": "response correction, same-path EGF ensemble, in-band corner, model and synthetic tests",
        },
        {
            "branch": "Repeater-derived creep rate",
            "status": STOP,
            "evidence": "no DAS-extended complete family exists and event slip is unconstrained",
            "next_gate": "validated extension, at least three complete intervals, and a defensible moment/area/slip model",
        },
    ]

    proceed = bool(
        external["external_population_gate"] == PASS
        and hrsn["target_control_pair_gate"] == PASS
        and deep["deep_template_detectability"] == PASS
        and deep["hard_negative_baseline"] == PASS
        and active["shot_detectability"] == PASS
    )
    return {
        "pilot_decision": "PROCEED_WITH_CONDITIONS" if proceed else "PAUSE_AND_REPAIR",
        "decision_basis": (
            "The dataset supports a rigorous incremental-value experiment: published labels expose where conventional correlation confuses nearby families, while the prospective 2026 candidate and a hard control are both measurable on the deep fiber. This is permission to run the benchmark, not evidence that DAS has extended the catalog."
            if proceed
            else "One or more prerequisites for the incremental-value experiment failed."
        ),
        "highest_value_next_analysis": "run a best-effort network-only detector on the exact DAS intervals, freeze its candidates and scores, then run DAS-only and joint pipelines blind to those results",
        "highest_value_next_observation": "a second independently network-classified target-family event on the same deep-DAS configuration",
        "highest_value_metadata_request": "surveyed deep locus-to-MD/TVD/XYZ/cable-tangent mapping",
        "high_similarity_candidates_in_bounded_pilot": high_similarity,
        "single_anchor_overmerges_in_published_neighbors": external[
            "discordant_single_anchor_overmerges"
        ],
        "published_target_population_events": external[
            "published_target_population_events"
        ],
        "published_neighbor_population_events": external[
            "published_neighbor_population_events"
        ],
        "network_labeled_events_with_waveforms": network[
            "waveform_available_events"
        ],
        "events_in_frozen_shortlist": len(shortlist),
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
        "branches": branches,
        "checkpoints": [
            {
                "name": "Checkpoint 1: repair labels",
                "status": "MET",
                "criterion": "exact-ID published population frozen and single-anchor overmerge documented",
            },
            {
                "name": "Checkpoint 2: best network-only baseline",
                "status": "WAITING",
                "criterion": "event-held-out differential relocation plus continuous same-interval detection",
            },
            {
                "name": "Checkpoint 3: DAS incremental value",
                "status": "WAITING",
                "criterion": "DAS-only or joint recall/classification improves over network-only at matched false-discovery rate",
            },
            {
                "name": "Checkpoint 4: direct DAS source physics",
                "status": "WAITING",
                "criterion": "second independently classified member on one deep configuration and surveyed geometry",
            },
            {
                "name": "Checkpoint 5: stress drop and creep rate",
                "status": "STOP",
                "criterion": "response/EGF/source-model gates plus validated complete recurrence intervals",
            },
        ],
    }


def write_checkpoint(root: Optional[Path] = None) -> Dict[str, Any]:
    project = project_root() if root is None else Path(root)
    payload = assemble_checkpoint(project)
    output = project / "outputs" / "checkpoint"
    write_json(output / "advisor_checkpoint.json", payload)
    write_rows(output / "branch_status.csv", payload["branches"])
    write_rows(output / "milestones.csv", payload["checkpoints"])
    return payload


if __name__ == "__main__":
    print(json.dumps(write_checkpoint(), indent=2))
