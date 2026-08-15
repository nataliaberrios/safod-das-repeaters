"""Multi-anchor, leave-one-event-out conventional-network family baseline."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np


def pair_lookup(
    summaries: Sequence[Mapping[str, Any]],
) -> Dict[frozenset, Mapping[str, Any]]:
    lookup: Dict[frozenset, Mapping[str, Any]] = {}
    for row in summaries:
        key = frozenset(
            [str(row["reference_event_id"]), str(row["comparison_event_id"])]
        )
        if len(key) != 2:
            continue
        lookup[key] = row
    return lookup


def leave_one_event_out_classification(
    population: Sequence[Mapping[str, Any]],
    summaries: Sequence[Mapping[str, Any]],
    available_event_ids: Sequence[str],
    maximum_anchors_per_family: int,
    minimum_score: float,
    minimum_margin: float,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Score every event against every other member of each published family."""

    available = set(map(str, available_event_ids))
    families: Dict[str, List[str]] = defaultdict(list)
    event_rows: Dict[str, Mapping[str, Any]] = {}
    for row in population:
        event_id = str(row["event_id"])
        event_rows[event_id] = row
        families[str(row["sequence_id"])].append(event_id)
    lookup = pair_lookup(summaries)
    score_rows: List[Dict[str, Any]] = []
    classifications: List[Dict[str, Any]] = []
    for event_id, event in event_rows.items():
        family_scores: List[Tuple[str, float, int]] = []
        for family_id in sorted(families):
            values: List[float] = []
            for anchor_id in families[family_id]:
                if anchor_id == event_id or anchor_id not in available:
                    continue
                summary = lookup.get(frozenset([event_id, anchor_id]))
                if not summary or summary.get("status") != "PASS":
                    continue
                score = summary.get("overall_median_correlation")
                if score is not None and math.isfinite(float(score)):
                    values.append(float(score))
            values.sort(reverse=True)
            selected = values[: max(1, int(maximum_anchors_per_family))]
            score = float(np.median(selected)) if selected else None
            score_rows.append(
                {
                    "event_id": event_id,
                    "published_sequence_id": event["sequence_id"],
                    "scored_sequence_id": family_id,
                    "family_score": score if score is not None else "",
                    "eligible_anchor_count": len(values),
                    "anchors_used": len(selected),
                    "score_definition": "median of up to {} highest eligible leave-one-event-out pair correlations".format(
                        maximum_anchors_per_family
                    ),
                }
            )
            if score is not None:
                family_scores.append((family_id, score, len(selected)))
        family_scores.sort(key=lambda item: item[1], reverse=True)
        predicted = "abstain"
        reason = "event_waveform_unavailable"
        top_family = ""
        top_score = None
        second_score = None
        margin = None
        if event_id in available and family_scores:
            top_family, top_score, _ = family_scores[0]
            second_score = family_scores[1][1] if len(family_scores) > 1 else None
            margin = (
                top_score - second_score if second_score is not None else None
            )
            if top_score < float(minimum_score):
                reason = "top_score_below_frozen_threshold"
            elif margin is None:
                reason = "fewer_than_two_family_scores"
            elif margin < float(minimum_margin):
                reason = "top_two_families_within_frozen_margin"
            else:
                predicted = top_family
                reason = "classified"
        classifications.append(
            {
                "event_id": event_id,
                "origin_time": event["origin_time"],
                "published_sequence_id": event["sequence_id"],
                "validation_role": event["validation_role"],
                "waveform_available": event_id in available,
                "predicted_sequence_id": predicted,
                "top_sequence_id": top_family,
                "top_family_score": top_score if top_score is not None else "",
                "second_family_score": second_score
                if second_score is not None
                else "",
                "classification_margin": margin if margin is not None else "",
                "classification_reason": reason,
                "correct": predicted == str(event["sequence_id"]),
                "minimum_score": minimum_score,
                "minimum_margin": minimum_margin,
                "evaluation_scheme": "leave_one_event_out_by_published_family",
            }
        )
    return score_rows, classifications


def classification_summary(
    classifications: Sequence[Mapping[str, Any]], target_sequence_id: str
) -> Dict[str, Any]:
    """Return event-level multiclass and target-vs-neighbor diagnostic metrics."""

    labels = sorted({str(row["published_sequence_id"]) for row in classifications})
    available = [row for row in classifications if bool(row["waveform_available"])]
    classified = [
        row for row in available if str(row["predicted_sequence_id"]) != "abstain"
    ]
    correct = sum(bool(row["correct"]) for row in classified)
    per_family: List[Dict[str, Any]] = []
    f1_values: List[float] = []
    for label in labels:
        tp = sum(
            row["published_sequence_id"] == label
            and row["predicted_sequence_id"] == label
            for row in available
        )
        fp = sum(
            row["published_sequence_id"] != label
            and row["predicted_sequence_id"] == label
            for row in available
        )
        fn = sum(
            row["published_sequence_id"] == label
            and row["predicted_sequence_id"] != label
            for row in available
        )
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = (
            2.0 * precision * recall / (precision + recall)
            if precision + recall
            else 0.0
        )
        f1_values.append(f1)
        per_family.append(
            {
                "sequence_id": label,
                "true_positive": tp,
                "false_positive": fp,
                "false_negative_or_abstain": fn,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )
    target_rows = [
        row for row in available if row["published_sequence_id"] == target_sequence_id
    ]
    neighbor_rows = [
        row for row in available if row["published_sequence_id"] != target_sequence_id
    ]
    target_tp = sum(
        row["predicted_sequence_id"] == target_sequence_id for row in target_rows
    )
    target_fp = sum(
        row["predicted_sequence_id"] == target_sequence_id for row in neighbor_rows
    )
    top_correct = sum(
        str(row["top_sequence_id"]) == str(row["published_sequence_id"])
        for row in available
    )
    top_target_tp = sum(
        str(row["top_sequence_id"]) == target_sequence_id for row in target_rows
    )
    top_target_fp = sum(
        str(row["top_sequence_id"]) == target_sequence_id for row in neighbor_rows
    )
    return {
        "labeled_events": len(classifications),
        "waveform_available_events": len(available),
        "classified_events": len(classified),
        "abstained_available_events": len(available) - len(classified),
        "classified_accuracy": correct / len(classified) if classified else None,
        "all_available_accuracy_with_abstention_as_error": sum(
            bool(row["correct"]) for row in available
        )
        / len(available)
        if available
        else None,
        "macro_f1_with_abstention_as_error": float(np.mean(f1_values))
        if f1_values
        else None,
        "target_event_count": len(target_rows),
        "target_recall": target_tp / len(target_rows) if target_rows else None,
        "target_precision": target_tp / (target_tp + target_fp)
        if target_tp + target_fp
        else None,
        "neighbor_to_target_overmerge_count": target_fp,
        "neighbor_to_target_overmerge_rate": target_fp / len(neighbor_rows)
        if neighbor_rows
        else None,
        "top_family_accuracy_ignoring_abstention_policy": top_correct
        / len(available)
        if available
        else None,
        "top_target_recall_ignoring_abstention_policy": top_target_tp
        / len(target_rows)
        if target_rows
        else None,
        "top_target_precision_ignoring_abstention_policy": top_target_tp
        / (top_target_tp + top_target_fp)
        if top_target_tp + top_target_fp
        else None,
        "per_family": per_family,
    }
