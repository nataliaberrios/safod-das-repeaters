"""Post-hoc comparison of frozen DAS triggers and the frozen network union.

Candidate generation has already finished before this module is allowed to
run.  Time-only matching is kept separate from later catalog adjudication, and
no function here assigns repeating-earthquake family membership.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import iso_utc
from .network_union import ordered_time_pairs


TIME_ONLY_COMPARISON_FIELDS = [
    "comparison_candidate_id",
    "comparison_time",
    "comparison_epoch_s",
    "comparison_time_basis",
    "comparison_membership",
    "DAS_candidate_id",
    "DAS_trigger_time",
    "DAS_trigger_epoch_s",
    "DAS_coincidence_score",
    "DAS_threshold",
    "DAS_block_support_count_at_declared_ratio",
    "network_union_candidate_id",
    "network_representative_time",
    "network_representative_epoch_s",
    "network_representative_time_basis",
    "network_branch_membership",
    "DAS_minus_network_time_s",
    "matching_rule",
    "catalog_fields_used_in_matching",
    "family_assignment",
]


ADJUDICATED_COMPARISON_FIELDS = TIME_ONLY_COMPARISON_FIELDS + [
    "adjudication_stage",
    "network_known_event_class",
    "network_known_event_id",
    "network_known_event_origin_time",
    "DAS_catalog_association",
    "DAS_catalog_event_id",
    "DAS_catalog_origin_time",
    "DAS_catalog_location_name",
    "DAS_catalog_magnitude",
    "DAS_catalog_horizontal_distance_km",
    "DAS_catalog_path_distance_km",
    "DAS_catalog_observed_delay_s",
    "DAS_catalog_nominal_arrival_s",
    "DAS_catalog_nominal_timing_residual_s",
    "DAS_catalog_plausible_match_count",
    "catalog_event_representative",
    "comparison_class",
    "event_counting_disposition",
    "cross_sensor_known_event_match",
    "eligible_DAS_network_detection_increment_candidate",
    "eligible_catalog_extension_candidate",
    "catalog_conflict",
]


def _finite_time(row: Mapping[str, Any], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError("comparison time must be finite")
    return value


def _sorted_unique(
    rows: Sequence[Mapping[str, Any]],
    identifier_field: str,
    time_field: str,
) -> List[Mapping[str, Any]]:
    identifiers = [str(row[identifier_field]) for row in rows]
    if any(not value for value in identifiers):
        raise ValueError("comparison identifier must not be empty")
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("comparison identifiers must be unique")
    return sorted(
        rows,
        key=lambda row: (
            _finite_time(row, time_field),
            str(row[identifier_field]),
        ),
    )


def _time_only_row(
    das: Mapping[str, Any] | None,
    network: Mapping[str, Any] | None,
    matching_rule: str,
) -> Dict[str, Any]:
    if das is None and network is None:
        raise ValueError("comparison row must contain DAS or network data")
    if das is not None:
        comparison_epoch_s = _finite_time(das, "trigger_epoch_s")
        comparison_basis = "DAS_trigger_arrival"
    else:
        comparison_epoch_s = _finite_time(
            network or {}, "representative_epoch_s"
        )
        comparison_basis = "network_representative"

    if das is not None and network is not None:
        membership = "DAS+network"
        difference: Any = _finite_time(
            das, "trigger_epoch_s"
        ) - _finite_time(network, "representative_epoch_s")
    elif das is not None:
        membership = "DAS_only"
        difference = ""
    else:
        membership = "network_only"
        difference = ""

    def value(row: Mapping[str, Any] | None, field: str) -> Any:
        return "" if row is None else row.get(field, "")

    return {
        "comparison_time": iso_utc(comparison_epoch_s),
        "comparison_epoch_s": comparison_epoch_s,
        "comparison_time_basis": comparison_basis,
        "comparison_membership": membership,
        "DAS_candidate_id": value(das, "candidate_id"),
        "DAS_trigger_time": value(das, "trigger_time"),
        "DAS_trigger_epoch_s": value(das, "trigger_epoch_s"),
        "DAS_coincidence_score": value(das, "coincidence_score"),
        "DAS_threshold": value(das, "threshold"),
        "DAS_block_support_count_at_declared_ratio": value(
            das, "block_support_count_at_declared_ratio"
        ),
        "network_union_candidate_id": value(
            network, "union_candidate_id"
        ),
        "network_representative_time": value(
            network, "representative_time"
        ),
        "network_representative_epoch_s": value(
            network, "representative_epoch_s"
        ),
        "network_representative_time_basis": value(
            network, "representative_time_basis"
        ),
        "network_branch_membership": value(
            network, "branch_membership"
        ),
        "DAS_minus_network_time_s": difference,
        "matching_rule": matching_rule,
        "catalog_fields_used_in_matching": 0,
        "family_assignment": "not_assigned",
    }


def build_time_only_comparison(
    das_rows: Sequence[Mapping[str, Any]],
    network_rows: Sequence[Mapping[str, Any]],
    maximum_difference_s: float,
    identifier_prefix: str = "das_network_dev",
) -> List[Dict[str, Any]]:
    """Retain all candidates after ordered, one-to-one time-only matching."""

    das = _sorted_unique(das_rows, "candidate_id", "trigger_epoch_s")
    network = _sorted_unique(
        network_rows,
        "union_candidate_id",
        "representative_epoch_s",
    )
    pairs = ordered_time_pairs(
        [_finite_time(row, "trigger_epoch_s") for row in das],
        [_finite_time(row, "representative_epoch_s") for row in network],
        maximum_difference_s,
    )
    das_to_network = dict(pairs)
    matched_network = {network_index for _, network_index in pairs}
    matching_rule = (
        "ordered_one_to_one_maximum_cardinality_then_"
        "minimum_total_absolute_time_difference"
    )

    rows = [
        _time_only_row(
            das_row,
            (
                network[das_to_network[das_index]]
                if das_index in das_to_network
                else None
            ),
            matching_rule,
        )
        for das_index, das_row in enumerate(das)
    ]
    rows.extend(
        _time_only_row(None, network_row, matching_rule)
        for network_index, network_row in enumerate(network)
        if network_index not in matched_network
    )
    rows.sort(
        key=lambda row: (
            float(row["comparison_epoch_s"]),
            str(row["comparison_membership"]),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["comparison_candidate_id"] = "{}_{:04d}".format(
            identifier_prefix, index
        )
    return rows


def _network_event_id(row: Mapping[str, Any]) -> str:
    broader = str(row.get("broader_catalog_event_id", ""))
    if broader:
        return broader
    return str(row.get("target_region_catalog_event_id", ""))


def _network_event_origin(row: Mapping[str, Any]) -> str:
    broader = str(row.get("broader_catalog_origin_time", ""))
    if broader:
        return broader
    return str(row.get("target_region_catalog_origin_time", ""))


def _is_physical_catalog_association(row: Mapping[str, Any]) -> bool:
    return str(row.get("background_catalog_association", "")) == (
        "physically_plausible_known_event_arrival"
    ) and bool(str(row.get("background_catalog_event_id", "")))


def _catalog_representatives(
    das_rows: Sequence[Mapping[str, Any]],
    time_only_rows: Sequence[Mapping[str, Any]],
    network_rows: Sequence[Mapping[str, Any]],
) -> Dict[str, str]:
    """Choose one auditable DAS trigger per directly associated event."""

    network_by_id = {
        str(row["union_candidate_id"]): row for row in network_rows
    }
    matched_network_by_das = {
        str(row["DAS_candidate_id"]): str(
            row["network_union_candidate_id"]
        )
        for row in time_only_rows
        if str(row.get("DAS_candidate_id", ""))
        and str(row.get("network_union_candidate_id", ""))
    }
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in das_rows:
        if _is_physical_catalog_association(row):
            groups[str(row["background_catalog_event_id"])].append(row)

    representatives: Dict[str, str] = {}
    for event_id, candidates in groups.items():
        def priority(row: Mapping[str, Any]) -> Tuple[Any, ...]:
            das_id = str(row["candidate_id"])
            network_id = matched_network_by_das.get(das_id, "")
            network_event = _network_event_id(
                network_by_id.get(network_id, {})
            )
            matched_same_event = bool(network_id) and (
                network_event == event_id
            )
            residual = abs(
                float(row["background_catalog_nominal_timing_residual_s"])
            )
            score = float(row["coincidence_score"])
            return (
                0 if matched_same_event else 1,
                residual,
                -score,
                das_id,
            )

        representatives[event_id] = str(
            min(candidates, key=priority)["candidate_id"]
        )
    return representatives


def adjudicate_comparison(
    time_only_rows: Sequence[Mapping[str, Any]],
    das_catalog_rows: Sequence[Mapping[str, Any]],
    network_adjudicated_rows: Sequence[Mapping[str, Any]],
    target_maximum_horizontal_distance_km: float,
) -> List[Dict[str, Any]]:
    """Attach catalog evidence after matching and classify without families."""

    target_limit = float(target_maximum_horizontal_distance_km)
    if not math.isfinite(target_limit) or target_limit <= 0.0:
        raise ValueError("target distance must be positive")
    das_by_id = {
        str(row["candidate_id"]): row for row in das_catalog_rows
    }
    network_by_id = {
        str(row["union_candidate_id"]): row
        for row in network_adjudicated_rows
    }
    representatives = _catalog_representatives(
        das_catalog_rows, time_only_rows, network_adjudicated_rows
    )
    output: List[Dict[str, Any]] = []

    for source in time_only_rows:
        row = dict(source)
        das_id = str(row.get("DAS_candidate_id", ""))
        network_id = str(row.get("network_union_candidate_id", ""))
        das = das_by_id.get(das_id, {})
        network = network_by_id.get(network_id, {})
        network_class = str(network.get("known_event_class", ""))
        network_event_id = _network_event_id(network)
        network_event_origin = _network_event_origin(network)
        direct = _is_physical_catalog_association(das)
        direct_event_id = (
            str(das.get("background_catalog_event_id", ""))
            if direct
            else ""
        )
        representative: Any = ""
        if direct:
            representative = (
                representatives.get(direct_event_id, "") == das_id
            )
        conflict = bool(
            network_event_id
            and direct_event_id
            and network_event_id != direct_event_id
        )

        if conflict:
            comparison_class = "catalog_conflict_STOP"
            disposition = "STOP_retain_for_manual_blind_adjudication"
            cross_sensor = False
            network_increment = False
        elif das_id and network_id:
            if network_class == "known_target_region_event":
                comparison_class = (
                    "matched_frozen_network_known_target_event"
                )
            elif network_class == (
                "known_regional_arrival_outside_target_region"
            ):
                comparison_class = (
                    "matched_frozen_network_known_regional_arrival"
                )
            else:
                comparison_class = "matched_frozen_network_event"
            disposition = (
                "same_event_evidence_not_DAS_catalog_extension"
            )
            cross_sensor = bool(network_event_id)
            network_increment = False
        elif not das_id and network_id:
            comparison_class = "network_event_not_recovered_by_raw_DAS_v1"
            disposition = "network_only_event_preserved_as_DAS_miss"
            cross_sensor = False
            network_increment = False
        elif direct and not bool(representative):
            comparison_class = "secondary_trigger_for_same_catalog_event"
            disposition = "retain_trigger_but_do_not_count_additional_event"
            cross_sensor = False
            network_increment = False
        elif direct:
            distance = float(
                das["background_catalog_horizontal_distance_km"]
            )
            if distance <= target_limit:
                comparison_class = (
                    "DAS_only_catalog_compatible_target_event"
                )
                disposition = (
                    "candidate_network_detection_increment_not_catalog_"
                    "extension_requires_waveform_validation"
                )
                network_increment = True
            else:
                comparison_class = (
                    "DAS_only_catalog_compatible_regional_arrival"
                )
                disposition = (
                    "regional_catalog_compatibility_audit_not_local_"
                    "extension"
                )
                network_increment = False
            cross_sensor = False
        else:
            comparison_class = "unassociated_raw_DAS_trigger"
            disposition = (
                "requires_blind_waveform_and_spatial_morphology_"
                "adjudication_not_automatically_an_earthquake"
            )
            cross_sensor = False
            network_increment = False

        row.update(
            {
                "adjudication_stage": (
                    "post_time_only_comparison_catalog_audit"
                ),
                "network_known_event_class": network_class,
                "network_known_event_id": network_event_id,
                "network_known_event_origin_time": network_event_origin,
                "DAS_catalog_association": das.get(
                    "background_catalog_association", ""
                ),
                "DAS_catalog_event_id": direct_event_id,
                "DAS_catalog_origin_time": das.get(
                    "background_catalog_origin_time", ""
                ),
                "DAS_catalog_location_name": das.get(
                    "background_catalog_location_name", ""
                ),
                "DAS_catalog_magnitude": das.get(
                    "background_catalog_magnitude", ""
                ),
                "DAS_catalog_horizontal_distance_km": das.get(
                    "background_catalog_horizontal_distance_km", ""
                ),
                "DAS_catalog_path_distance_km": das.get(
                    "background_catalog_path_distance_km", ""
                ),
                "DAS_catalog_observed_delay_s": das.get(
                    "background_catalog_observed_delay_s", ""
                ),
                "DAS_catalog_nominal_arrival_s": das.get(
                    "background_catalog_nominal_arrival_s", ""
                ),
                "DAS_catalog_nominal_timing_residual_s": das.get(
                    "background_catalog_nominal_timing_residual_s", ""
                ),
                "DAS_catalog_plausible_match_count": das.get(
                    "background_catalog_plausible_match_count", ""
                ),
                "catalog_event_representative": representative,
                "comparison_class": comparison_class,
                "event_counting_disposition": disposition,
                "cross_sensor_known_event_match": cross_sensor,
                "eligible_DAS_network_detection_increment_candidate": (
                    network_increment
                ),
                "eligible_catalog_extension_candidate": False,
                "catalog_conflict": conflict,
            }
        )
        output.append(row)
    return output
