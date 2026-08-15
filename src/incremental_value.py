"""Frozen design and diagnostic summaries for DAS incremental-value tests."""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Mapping, Sequence

from .common import CONDITIONAL, PASS, STOP


def diagnostic_confusion(
    crosswalk: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Summarize the old single-anchor decision against external labels.

    This is a repair diagnostic, not the final network-only benchmark.
    """

    rows: List[Dict[str, Any]] = []
    for role in (
        "published_target_positive",
        "published_neighbor_family_negative",
    ):
        selected = [row for row in crosswalk if row["external_role"] == role]
        counts = Counter()
        for row in selected:
            decision = row["single_anchor_decision"]
            prediction = (
                "target_positive"
                if decision == "family"
                else ("target_negative" if decision == "not_family" else "abstain")
            )
            counts[prediction] += 1
        rows.append(
            {
                "external_role": role,
                "event_count": len(selected),
                "predicted_target_positive": counts["target_positive"],
                "predicted_target_negative": counts["target_negative"],
                "abstained": counts["abstain"],
                "interpretation": "repair diagnostic only; thresholds were not trained for multiclass family separation",
            }
        )
    return rows


def pipeline_manifest() -> List[Dict[str, Any]]:
    return [
        {
            "pipeline": "network_only",
            "inputs": "NCEDC DD plus continuous HRSN; add regional NCSN where HRSN is unavailable",
            "candidate_generation": "strong conventional phase association and multi-template matched filtering on the exact DAS UTC intervals",
            "family_assignment": "published-family multi-anchor waveform features plus differential relocation",
            "blind_to": "DAS trigger times and DAS scores",
            "detection_status": "NOT_RUN",
            "family_assignment_status": "CORRELATION_ONLY_DIAGNOSTIC_COMPLETE; DIFFERENTIAL_RELOCATION_NOT_RUN",
            "status": "NOT_RUN",
        },
        {
            "pipeline": "das_only",
            "inputs": "continuous DAS within homogeneous configuration epochs",
            "candidate_generation": "array-coherent phase picking and/or multi-channel template matching",
            "family_assignment": "DAS similarity to withheld family templates",
            "blind_to": "network trigger times and network scores",
            "detection_status": "NOT_RUN",
            "family_assignment_status": "NOT_RUN",
            "status": "NOT_RUN",
        },
        {
            "pipeline": "joint",
            "inputs": "frozen network-only and DAS-only candidate/score products",
            "candidate_generation": "predeclared evidence fusion; no retuning on test labels",
            "family_assignment": "combined likelihood with an explicit abstain class",
            "blind_to": "held-out adjudication labels",
            "detection_status": "NOT_RUN",
            "family_assignment_status": "NOT_RUN",
            "status": "NOT_RUN",
        },
    ]


def metric_manifest() -> List[Dict[str, Any]]:
    return [
        {
            "claim": "catalog_detection_extension",
            "primary_metric": "event recall at matched event-level false-discovery rate",
            "comparison": "joint versus network_only and das_only versus network_only",
            "pass_gate": "bootstrap lower confidence bound on recall difference is greater than zero",
            "current_status": STOP,
        },
        {
            "claim": "catalog_completeness_extension",
            "primary_metric": "validated detection probability versus magnitude and completeness magnitude",
            "comparison": "same UTC/configuration intervals for all pipelines",
            "pass_gate": "DAS gain persists after distance, noise, and configuration stratification",
            "current_status": STOP,
        },
        {
            "claim": "family_classification_extension",
            "primary_metric": "event-level macro F1 and cross-family merge rate",
            "comparison": "joint versus network_only on held-out family-labeled events",
            "pass_gate": "bootstrap F1 improvement above zero without reduced target precision",
            "current_status": STOP,
        },
        {
            "claim": "near_source_resolution_extension",
            "primary_metric": "within-family versus between-family DAS differential-time/spatial-distance separation",
            "comparison": "same-config positive and neighboring-family controls",
            "pass_gate": "effect exceeds block-bootstrap uncertainty and survives band/channel sensitivity",
            "current_status": STOP,
        },
    ]


def claim_status(
    external_status: Mapping[str, Any], deep_status: Mapping[str, Any]
) -> List[Dict[str, Any]]:
    return [
        {
            "claim": "external_validation_population",
            "status": PASS
            if external_status["external_population_gate"] == PASS
            else STOP,
            "evidence": "{} target positives and {} neighboring families are frozen".format(
                external_status["published_target_population_events"],
                external_status["published_neighbor_family_count"],
            ),
            "next_gate": "add event-held-out differential relocation and continuous same-interval network detection",
        },
        {
            "claim": "single_anchor_family_catalog",
            "status": STOP,
            "evidence": "{} externally labeled neighboring-family events were overmerged".format(
                external_status["discordant_single_anchor_overmerges"]
            ),
            "next_gate": "replace one-seed thresholding with published-family multi-anchor classification",
        },
        {
            "claim": "DAS_catalog_extension",
            "status": STOP,
            "evidence": "DAS detectability has been shown, but no blinded DAS-only versus network-only scan has run",
            "next_gate": "same-interval network-only, DAS-only, and joint comparison",
        },
        {
            "claim": "deep_DAS_source_physics",
            "status": deep_status.get("direct_das_repeatability", CONDITIONAL),
            "evidence": "one prospective 2026 target-family candidate is detected on the deep fiber",
            "next_gate": "second independently classified target-family member in the same deep configuration",
        },
    ]
