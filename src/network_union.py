"""Blind time-only union and later catalog adjudication for network candidates.

The two network branches report different time concepts: the template bank
reports an estimated origin time, whereas the generic energy trigger reports
an array arrival time.  This module pairs candidates across branches using
only those times and branch identity.  Catalog fields are attached in a
separate function after the time-only table has been materialized.

No function in this module assigns repeating-earthquake family membership.
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import iso_utc


TEMPLATE_BRANCH = "template_bank"
GENERIC_BRANCH = "generic_trigger"

TIME_ONLY_FIELDS = [
    "union_candidate_id",
    "representative_time",
    "representative_epoch_s",
    "representative_time_basis",
    "branch_membership",
    "branch_count",
    "template_candidate_id",
    "template_origin_time",
    "template_origin_epoch_s",
    "template_bank_score",
    "template_threshold",
    "generic_candidate_id",
    "generic_trigger_time",
    "generic_trigger_epoch_s",
    "generic_coincidence_score",
    "generic_threshold",
    "generic_station_support_count",
    "cross_branch_time_difference_s",
    "union_generation_rule",
    "catalog_fields_used_in_grouping",
    "family_assignment",
]

ADJUDICATION_FIELDS = TIME_ONLY_FIELDS + [
    "adjudication_stage",
    "target_region_catalog_association",
    "target_region_catalog_event_id",
    "target_region_catalog_origin_time",
    "broader_catalog_association",
    "broader_catalog_event_id",
    "broader_catalog_origin_time",
    "broader_catalog_location_name",
    "broader_catalog_magnitude",
    "broader_catalog_horizontal_distance_km",
    "known_event_class",
    "local_extension_disposition",
    "eligible_uncataloged_local_extension_candidate",
]


def _candidate_time(row: Mapping[str, Any], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError("candidate time must be finite")
    return value


def _sorted_unique(
    rows: Sequence[Mapping[str, Any]],
    identifier_field: str,
    time_field: str,
) -> List[Mapping[str, Any]]:
    identifiers = [str(row[identifier_field]) for row in rows]
    if any(not identifier for identifier in identifiers):
        raise ValueError("candidate identifiers must not be empty")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("candidate identifiers must be unique within branch")
    return sorted(
        rows,
        key=lambda row: (
            _candidate_time(row, time_field),
            str(row[identifier_field]),
        ),
    )


def ordered_time_pairs(
    template_times: Sequence[float],
    generic_times: Sequence[float],
    maximum_difference_s: float,
) -> Tuple[Tuple[int, int], ...]:
    """Return a maximum-cardinality, minimum-distance ordered matching.

    The sequences must already be chronological.  Ordering prevents crossing
    event matches.  The objective first maximizes the number of cross-branch
    pairs, then minimizes their total absolute time difference, and finally
    uses pair indices for deterministic tie-breaking.
    """

    window = float(maximum_difference_s)
    if not math.isfinite(window) or window <= 0.0:
        raise ValueError("cross-branch match window must be positive")
    template = tuple(float(value) for value in template_times)
    generic = tuple(float(value) for value in generic_times)
    if any(not math.isfinite(value) for value in template + generic):
        raise ValueError("candidate times must be finite")
    if any(first > second for first, second in zip(template, template[1:])):
        raise ValueError("template times must be chronological")
    if any(first > second for first, second in zip(generic, generic[1:])):
        raise ValueError("generic times must be chronological")

    @lru_cache(maxsize=None)
    def solve(
        template_index: int, generic_index: int
    ) -> Tuple[int, float, Tuple[Tuple[int, int], ...]]:
        if template_index >= len(template) or generic_index >= len(generic):
            return 0, 0.0, ()

        options = [
            solve(template_index + 1, generic_index),
            solve(template_index, generic_index + 1),
        ]
        difference = abs(
            template[template_index] - generic[generic_index]
        )
        if difference <= window:
            count, cost, pairs = solve(
                template_index + 1, generic_index + 1
            )
            options.append(
                (
                    count + 1,
                    cost + difference,
                    ((template_index, generic_index),) + pairs,
                )
            )
        return min(
            options,
            key=lambda result: (
                -result[0],
                round(result[1], 12),
                result[2],
            ),
        )

    return solve(0, 0)[2]


def _value(row: Mapping[str, Any] | None, field: str) -> Any:
    if row is None:
        return ""
    return row.get(field, "")


def _time_only_row(
    template: Mapping[str, Any] | None,
    generic: Mapping[str, Any] | None,
) -> Dict[str, Any]:
    if template is None and generic is None:
        raise ValueError("union row must contain at least one candidate")
    if template is not None:
        representative_epoch_s = _candidate_time(
            template, "origin_epoch_s"
        )
        representative_basis = "template_origin"
    else:
        representative_epoch_s = _candidate_time(
            generic or {}, "trigger_epoch_s"
        )
        representative_basis = "generic_trigger_arrival"

    if template is not None and generic is not None:
        membership = TEMPLATE_BRANCH + "+" + GENERIC_BRANCH
        branch_count = 2
        difference: Any = _candidate_time(
            generic, "trigger_epoch_s"
        ) - _candidate_time(template, "origin_epoch_s")
    elif template is not None:
        membership = TEMPLATE_BRANCH
        branch_count = 1
        difference = ""
    else:
        membership = GENERIC_BRANCH
        branch_count = 1
        difference = ""

    return {
        "representative_time": iso_utc(representative_epoch_s),
        "representative_epoch_s": representative_epoch_s,
        "representative_time_basis": representative_basis,
        "branch_membership": membership,
        "branch_count": branch_count,
        "template_candidate_id": _value(template, "candidate_id"),
        "template_origin_time": _value(template, "origin_time"),
        "template_origin_epoch_s": _value(template, "origin_epoch_s"),
        "template_bank_score": _value(template, "bank_score"),
        "template_threshold": _value(template, "threshold"),
        "generic_candidate_id": _value(generic, "candidate_id"),
        "generic_trigger_time": _value(generic, "trigger_time"),
        "generic_trigger_epoch_s": _value(generic, "trigger_epoch_s"),
        "generic_coincidence_score": _value(
            generic, "coincidence_score"
        ),
        "generic_threshold": _value(generic, "threshold"),
        "generic_station_support_count": _value(
            generic, "station_support_count_at_declared_ratio"
        ),
        "cross_branch_time_difference_s": difference,
        "union_generation_rule": (
            "ordered_one_to_one_maximum_cardinality_then_"
            "minimum_total_absolute_time_difference"
        ),
        "catalog_fields_used_in_grouping": 0,
        "family_assignment": "not_assigned",
    }


def build_time_only_union(
    template_rows: Sequence[Mapping[str, Any]],
    generic_rows: Sequence[Mapping[str, Any]],
    maximum_difference_s: float,
    identifier_prefix: str = "network_union_dev",
) -> List[Dict[str, Any]]:
    """Build the cross-branch union without reading any catalog field."""

    templates = _sorted_unique(
        template_rows, "candidate_id", "origin_epoch_s"
    )
    generics = _sorted_unique(
        generic_rows, "candidate_id", "trigger_epoch_s"
    )
    pairs = ordered_time_pairs(
        [_candidate_time(row, "origin_epoch_s") for row in templates],
        [_candidate_time(row, "trigger_epoch_s") for row in generics],
        maximum_difference_s,
    )
    template_to_generic = dict(pairs)
    matched_generic = {generic_index for _, generic_index in pairs}

    rows: List[Dict[str, Any]] = []
    for template_index, template in enumerate(templates):
        generic_index = template_to_generic.get(template_index)
        generic = (
            generics[generic_index] if generic_index is not None else None
        )
        rows.append(_time_only_row(template, generic))
    for generic_index, generic in enumerate(generics):
        if generic_index not in matched_generic:
            rows.append(_time_only_row(None, generic))

    rows.sort(
        key=lambda row: (
            float(row["representative_epoch_s"]),
            str(row["branch_membership"]),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["union_candidate_id"] = "{}_{:04d}".format(
            identifier_prefix, index
        )
    return rows


def _target_catalog_evidence(
    union_row: Mapping[str, Any],
    template_by_id: Mapping[str, Mapping[str, Any]],
    generic_by_id: Mapping[str, Mapping[str, Any]],
) -> List[Tuple[str, str]]:
    evidence: List[Tuple[str, str]] = []
    for member_field, source in (
        ("template_candidate_id", template_by_id),
        ("generic_candidate_id", generic_by_id),
    ):
        candidate_id = str(union_row.get(member_field, ""))
        candidate = source.get(candidate_id)
        if candidate is None:
            continue
        if str(candidate.get("catalog_association", "")) != (
            "within_tolerance"
        ):
            continue
        event_id = str(candidate.get("nearest_catalog_event_id", ""))
        origin_time = str(
            candidate.get("nearest_catalog_origin_time", "")
        )
        if event_id:
            evidence.append((event_id, origin_time))
    return evidence


def adjudicate_time_only_union(
    union_rows: Sequence[Mapping[str, Any]],
    template_rows: Sequence[Mapping[str, Any]],
    generic_rows: Sequence[Mapping[str, Any]],
    maximum_target_horizontal_distance_km: float,
) -> List[Dict[str, Any]]:
    """Attach catalog evidence only after time-only grouping is complete."""

    target_limit = float(maximum_target_horizontal_distance_km)
    if not math.isfinite(target_limit) or target_limit <= 0.0:
        raise ValueError("target-region distance must be positive")
    template_by_id = {
        str(row["candidate_id"]): row for row in template_rows
    }
    generic_by_id = {
        str(row["candidate_id"]): row for row in generic_rows
    }

    adjudicated: List[Dict[str, Any]] = []
    for source_row in union_rows:
        row = dict(source_row)
        target_evidence = _target_catalog_evidence(
            row, template_by_id, generic_by_id
        )
        target_ids = sorted({event_id for event_id, _ in target_evidence})
        target_origins = sorted(
            {origin for _, origin in target_evidence if origin}
        )
        generic_id = str(row.get("generic_candidate_id", ""))
        generic = generic_by_id.get(generic_id, {})
        broader_status = str(
            generic.get("background_catalog_association", "")
        )
        broader_id = str(generic.get("background_catalog_event_id", ""))
        broader_origin = str(
            generic.get("background_catalog_origin_time", "")
        )
        broader_distance_raw = generic.get(
            "background_catalog_horizontal_distance_km", ""
        )
        broader_physical = (
            broader_status == "physically_plausible_known_event_arrival"
            and bool(broader_id)
        )

        conflict = len(target_ids) > 1
        if target_ids and broader_physical and broader_id not in target_ids:
            conflict = True

        if conflict:
            target_status = "conflicting_catalog_event_ids"
            known_class = "catalog_conflict_STOP"
            disposition = "STOP_retain_for_manual_blind_adjudication"
            eligible = False
        elif target_ids:
            target_status = "known_target_region_event"
            known_class = "known_target_region_event"
            disposition = "retain_known_event_not_catalog_extension"
            eligible = False
        elif broader_physical:
            try:
                broader_distance = float(broader_distance_raw)
            except (TypeError, ValueError):
                broader_distance = float("nan")
            if not math.isfinite(broader_distance):
                target_status = "none"
                known_class = "catalog_distance_missing_STOP"
                disposition = "STOP_retain_for_manual_blind_adjudication"
            elif broader_distance <= target_limit:
                target_status = "known_target_region_event_by_physics"
                known_class = "known_target_region_event"
                disposition = "retain_known_event_not_catalog_extension"
            else:
                target_status = "none"
                known_class = "known_regional_arrival_outside_target_region"
                disposition = (
                    "veto_from_uncataloged_local_extension_count_"
                    "retain_in_raw_union"
                )
            eligible = False
        else:
            target_status = "none"
            known_class = "unassociated_after_broader_catalog_audit"
            disposition = (
                "retain_for_blinded_multisensor_adjudication_not_"
                "automatically_new"
            )
            eligible = True

        row.update(
            {
                "adjudication_stage": "post_time_only_union_catalog_audit",
                "target_region_catalog_association": target_status,
                "target_region_catalog_event_id": ";".join(target_ids),
                "target_region_catalog_origin_time": ";".join(
                    target_origins
                ),
                "broader_catalog_association": broader_status or "none",
                "broader_catalog_event_id": broader_id,
                "broader_catalog_origin_time": broader_origin,
                "broader_catalog_location_name": generic.get(
                    "background_catalog_location_name", ""
                ),
                "broader_catalog_magnitude": generic.get(
                    "background_catalog_magnitude", ""
                ),
                "broader_catalog_horizontal_distance_km": (
                    broader_distance_raw
                ),
                "known_event_class": known_class,
                "local_extension_disposition": disposition,
                "eligible_uncataloged_local_extension_candidate": eligible,
            }
        )
        adjudicated.append(row)
    return adjudicated
