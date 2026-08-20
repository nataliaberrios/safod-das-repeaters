"""Pure mechanics for the registered held-out DAS/network comparison.

The time-only stage uses no catalog or family field.  Network catalog context
is attached by immutable identifiers only after the time-only table has been
written and checksummed by the runner entry point.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Mapping, Sequence

from .common import iso_utc, parse_utc
from .network_union import ordered_time_pairs


TIME_ONLY_FIELDS = [
    "comparison_candidate_id",
    "interval_id",
    "comparison_time",
    "comparison_epoch_s",
    "comparison_time_basis",
    "comparison_membership",
    "DAS_candidate_id",
    "DAS_parent_v1_candidate_id",
    "DAS_trigger_time",
    "DAS_trigger_epoch_s",
    "DAS_coincidence_score",
    "DAS_v1_interval_null_threshold",
    "DAS_strong_block_count",
    "DAS_v1_score_rank_within_interval",
    "network_union_candidate_id",
    "network_representative_time",
    "network_representative_epoch_s",
    "network_representative_time_basis",
    "network_branch_membership",
    "network_template_candidate_id",
    "network_generic_candidate_id",
    "DAS_minus_network_time_s",
    "absolute_time_difference_s",
    "matching_window_s",
    "matching_rule",
    "catalog_fields_used_in_matching",
    "family_assignment",
]


NETWORK_CONTEXT_FIELDS = TIME_ONLY_FIELDS + [
    "network_evaluation_unit_id",
    "network_catalog_event_id",
    "network_known_event_class",
    "network_catalog_detection_extension_status",
    "network_repeater_family_extension_status",
    "network_local_extension_disposition",
    "network_catalog_conflict_STOP",
    "network_eligible_for_independent_DAS_comparison",
    "DAS_independent_adjudication_status",
    "DAS_detection_increment_status",
    "repeater_family_assignment",
]


INTERVAL_SUMMARY_FIELDS = [
    "interval_id",
    "DAS_v2_candidate_count",
    "network_raw_candidate_count",
    "matched_pair_count",
    "DAS_only_candidate_count",
    "network_only_candidate_count",
    "network_event_unit_count",
    "network_event_units_with_DAS_match",
    "network_event_units_without_DAS_match",
    "network_duplicate_raw_candidate_count",
    "DAS_only_independent_adjudication_pending_count",
    "catalog_fields_used_in_time_matching",
    "family_assignments_made",
]


MATCHING_RULE = (
    "ordered_one_to_one_maximum_cardinality_then_"
    "minimum_total_absolute_time_difference_within_interval"
)


def _finite(row: Mapping[str, Any], field: str) -> float:
    value = float(row[field])
    if not math.isfinite(value):
        raise ValueError("comparison time/score must be finite: " + field)
    return value


def _false(value: Any) -> bool:
    return str(value).strip().lower() in {"", "0", "false", "none"}


def _validate_iso_epoch(
    row: Mapping[str, Any], iso_field: str, epoch_field: str
) -> None:
    observed = parse_utc(str(row[iso_field])).timestamp()
    expected = _finite(row, epoch_field)
    if not math.isclose(observed, expected, rel_tol=0.0, abs_tol=0.002):
        raise RuntimeError("ISO and epoch candidate times disagree")


def validate_candidate_rows(
    das_rows: Sequence[Mapping[str, Any]],
    network_rows: Sequence[Mapping[str, Any]],
    expected_das_count: int | None = None,
    expected_network_count: int | None = None,
) -> None:
    """Reject leakage, repaired rules, duplicate IDs, or malformed times."""

    if expected_das_count is not None and len(das_rows) != int(
        expected_das_count
    ):
        raise RuntimeError("frozen DAS-v2 row count changed")
    if expected_network_count is not None and len(network_rows) != int(
        expected_network_count
    ):
        raise RuntimeError("frozen network-union row count changed")

    das_ids = [str(row["candidate_id"]) for row in das_rows]
    network_ids = [str(row["union_candidate_id"]) for row in network_rows]
    if any(not value for value in das_ids + network_ids):
        raise ValueError("comparison candidate identifier is empty")
    if len(das_ids) != len(set(das_ids)):
        raise ValueError("DAS candidate identifiers are not unique")
    if len(network_ids) != len(set(network_ids)):
        raise ValueError("network candidate identifiers are not unique")

    for row in das_rows:
        if not str(row["interval_id"]):
            raise ValueError("DAS candidate has no interval")
        _validate_iso_epoch(row, "trigger_time", "trigger_epoch_s")
        _finite(row, "coincidence_score")
        _finite(row, "v1_interval_null_threshold")
        if int(row["strong_block_count"]) < int(
            row["minimum_required_strong_block_count"]
        ):
            raise PermissionError("non-v2 DAS row entered comparison")
        if int(row["minimum_required_strong_block_count"]) != 4:
            raise PermissionError("DAS support requirement was repaired")
        for field in (
            "network_candidate_time_fields_read",
            "catalog_event_time_fields_read",
            "family_label_fields_read",
        ):
            if int(row[field]) != 0:
                raise PermissionError("DAS candidate contains leaked input")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("DAS candidate has a family assignment")

    for row in network_rows:
        if not str(row["interval_id"]):
            raise ValueError("network candidate has no interval")
        _validate_iso_epoch(
            row, "representative_time", "representative_epoch_s"
        )
        if int(row["catalog_fields_used_in_grouping"]) != 0:
            raise PermissionError("network time union used catalog fields")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("network candidate has a family assignment")


def _time_only_row(
    das: Mapping[str, Any] | None,
    network: Mapping[str, Any] | None,
    window_s: float,
) -> Dict[str, Any]:
    if das is None and network is None:
        raise ValueError("comparison row has neither DAS nor network input")
    if das is not None and network is not None:
        if str(das["interval_id"]) != str(network["interval_id"]):
            raise PermissionError("cross-interval comparison match attempted")
        membership = "DAS+network"
        difference: Any = _finite(das, "trigger_epoch_s") - _finite(
            network, "representative_epoch_s"
        )
        absolute: Any = abs(float(difference))
    elif das is not None:
        membership = "DAS_only"
        difference = ""
        absolute = ""
    else:
        membership = "network_only"
        difference = ""
        absolute = ""

    source = das if das is not None else network
    assert source is not None
    interval_id = str(source["interval_id"])
    if das is not None:
        comparison_epoch_s = _finite(das, "trigger_epoch_s")
        comparison_basis = "DAS_energy_arrival"
    else:
        comparison_epoch_s = _finite(
            network or {}, "representative_epoch_s"
        )
        comparison_basis = "network_representative"

    def value(row: Mapping[str, Any] | None, field: str) -> Any:
        return "" if row is None else row.get(field, "")

    return {
        "interval_id": interval_id,
        "comparison_time": iso_utc(comparison_epoch_s),
        "comparison_epoch_s": comparison_epoch_s,
        "comparison_time_basis": comparison_basis,
        "comparison_membership": membership,
        "DAS_candidate_id": value(das, "candidate_id"),
        "DAS_parent_v1_candidate_id": value(
            das, "parent_v1_candidate_id"
        ),
        "DAS_trigger_time": value(das, "trigger_time"),
        "DAS_trigger_epoch_s": value(das, "trigger_epoch_s"),
        "DAS_coincidence_score": value(das, "coincidence_score"),
        "DAS_v1_interval_null_threshold": value(
            das, "v1_interval_null_threshold"
        ),
        "DAS_strong_block_count": value(das, "strong_block_count"),
        "DAS_v1_score_rank_within_interval": value(
            das, "v1_score_rank_within_interval"
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
        "network_template_candidate_id": value(
            network, "template_candidate_id"
        ),
        "network_generic_candidate_id": value(
            network, "generic_candidate_id"
        ),
        "DAS_minus_network_time_s": difference,
        "absolute_time_difference_s": absolute,
        "matching_window_s": float(window_s),
        "matching_rule": MATCHING_RULE,
        "catalog_fields_used_in_matching": 0,
        "family_assignment": "not_assigned",
    }


def build_time_only_comparison(
    das_rows: Sequence[Mapping[str, Any]],
    network_rows: Sequence[Mapping[str, Any]],
    maximum_difference_s: float,
) -> List[Dict[str, Any]]:
    """Match within intervals and retain every matched/unmatched source row."""

    validate_candidate_rows(das_rows, network_rows)
    window = float(maximum_difference_s)
    if not math.isfinite(window) or window <= 0.0:
        raise ValueError("comparison matching window must be positive")
    das_by_interval: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    network_by_interval: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in das_rows:
        das_by_interval[str(row["interval_id"])].append(row)
    for row in network_rows:
        network_by_interval[str(row["interval_id"])].append(row)

    rows: List[Dict[str, Any]] = []
    intervals = sorted(set(das_by_interval) | set(network_by_interval))
    for interval_id in intervals:
        das = sorted(
            das_by_interval[interval_id],
            key=lambda row: (
                _finite(row, "trigger_epoch_s"),
                str(row["candidate_id"]),
            ),
        )
        network = sorted(
            network_by_interval[interval_id],
            key=lambda row: (
                _finite(row, "representative_epoch_s"),
                str(row["union_candidate_id"]),
            ),
        )
        pairs = ordered_time_pairs(
            [_finite(row, "trigger_epoch_s") for row in das],
            [_finite(row, "representative_epoch_s") for row in network],
            window,
        )
        das_to_network = dict(pairs)
        matched_network = {network_index for _, network_index in pairs}
        for das_index, das_row in enumerate(das):
            network_row = (
                network[das_to_network[das_index]]
                if das_index in das_to_network
                else None
            )
            rows.append(_time_only_row(das_row, network_row, window))
        for network_index, network_row in enumerate(network):
            if network_index not in matched_network:
                rows.append(_time_only_row(None, network_row, window))

    rows.sort(
        key=lambda row: (
            str(row["interval_id"]),
            float(row["comparison_epoch_s"]),
            str(row["comparison_membership"]),
        )
    )
    for index, row in enumerate(rows, start=1):
        row["comparison_candidate_id"] = "heldout_compare_{:04d}".format(
            index
        )
    validate_time_only_output(rows, das_rows, network_rows, window)
    return rows


def validate_time_only_output(
    rows: Sequence[Mapping[str, Any]],
    das_rows: Sequence[Mapping[str, Any]],
    network_rows: Sequence[Mapping[str, Any]],
    maximum_difference_s: float,
) -> None:
    """Prove source-row conservation, one-to-one use, and match limits."""

    observed_das = [
        str(row["DAS_candidate_id"])
        for row in rows
        if str(row.get("DAS_candidate_id", ""))
    ]
    observed_network = [
        str(row["network_union_candidate_id"])
        for row in rows
        if str(row.get("network_union_candidate_id", ""))
    ]
    expected_das = sorted(str(row["candidate_id"]) for row in das_rows)
    expected_network = sorted(
        str(row["union_candidate_id"]) for row in network_rows
    )
    if sorted(observed_das) != expected_das:
        raise RuntimeError("time-only output did not retain each DAS row once")
    if sorted(observed_network) != expected_network:
        raise RuntimeError(
            "time-only output did not retain each network row once"
        )
    identifiers = [str(row["comparison_candidate_id"]) for row in rows]
    if len(identifiers) != len(set(identifiers)):
        raise RuntimeError("time-only comparison IDs are not unique")
    for row in rows:
        membership = str(row["comparison_membership"])
        if membership not in {"DAS+network", "DAS_only", "network_only"}:
            raise RuntimeError("unexpected time-only comparison membership")
        if int(row["catalog_fields_used_in_matching"]) != 0:
            raise PermissionError("catalog field leaked into time matching")
        if str(row["family_assignment"]) != "not_assigned":
            raise PermissionError("time-only output has a family assignment")
        if str(row["matching_rule"]) != MATCHING_RULE:
            raise PermissionError("time-only matching rule changed")
        if not math.isclose(
            float(row["matching_window_s"]),
            float(maximum_difference_s),
            rel_tol=0.0,
            abs_tol=1.0e-12,
        ):
            raise PermissionError("time-only matching window changed")
        if membership == "DAS+network":
            if str(row["interval_id"]) == "":
                raise RuntimeError("matched row has no interval")
            if float(row["absolute_time_difference_s"]) > float(
                maximum_difference_s
            ):
                raise RuntimeError("time-only match exceeds frozen window")


def attach_network_context(
    time_only_rows: Sequence[Mapping[str, Any]],
    network_adjudicated_rows: Sequence[Mapping[str, Any]],
    evaluation_unit_rows: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    """Attach post-match network context without changing any time-only field."""

    network_by_id = {
        str(row["union_candidate_id"]): row
        for row in network_adjudicated_rows
    }
    if len(network_by_id) != len(network_adjudicated_rows):
        raise ValueError("adjudicated network identifiers are not unique")
    units_by_id = {
        str(row["evaluation_unit_id"]): row for row in evaluation_unit_rows
    }
    if len(units_by_id) != len(evaluation_unit_rows):
        raise ValueError("network evaluation-unit identifiers are not unique")
    if any(not identifier for identifier in units_by_id):
        raise ValueError("network evaluation-unit identifier is empty")

    output: List[Dict[str, Any]] = []
    for source in time_only_rows:
        row = dict(source)
        network_id = str(row.get("network_union_candidate_id", ""))
        das_id = str(row.get("DAS_candidate_id", ""))
        network = network_by_id.get(network_id, {})
        if network_id and not network:
            raise RuntimeError("time-only network candidate has no context")
        if network:
            for time_field in (
                "interval_id",
                "representative_time",
                "representative_epoch_s",
                "representative_time_basis",
                "branch_membership",
            ):
                source_field = (
                    "network_" + time_field
                    if time_field != "interval_id"
                    else "interval_id"
                )
                if str(row[source_field]) != str(network[time_field]):
                    raise RuntimeError(
                        "network context changed a frozen time-only field"
                    )
            unit_id = str(network["evaluation_unit_id"])
            if unit_id not in units_by_id:
                raise RuntimeError("network context has no evaluation unit")
            unit = units_by_id[unit_id]
            if not _false(network["catalog_conflict_STOP"]):
                raise PermissionError("network context contains a STOP conflict")
            if not _false(unit["catalog_conflict_STOP"]):
                raise PermissionError("network evaluation unit has a conflict")
        else:
            unit_id = ""
            unit = {}

        membership = str(row["comparison_membership"])
        if membership == "DAS_only":
            adjudication_status = (
                "pending_independent_catalog_forced_network_score_"
                "waveform_and_DAS_artifact_review"
            )
            increment_status = "PENDING_NOT_AN_EXTENSION_CLAIM"
        elif membership == "DAS+network":
            adjudication_status = "matched_frozen_network_candidate"
            increment_status = "NO_same_time_as_frozen_network_candidate"
        else:
            adjudication_status = "not_applicable_no_DAS_candidate"
            increment_status = "not_applicable"

        row.update(
            {
                "network_evaluation_unit_id": unit_id,
                "network_catalog_event_id": unit.get(
                    "catalog_event_id", ""
                ),
                "network_known_event_class": unit.get(
                    "known_event_class", ""
                ),
                "network_catalog_detection_extension_status": unit.get(
                    "catalog_detection_extension_status", ""
                ),
                "network_repeater_family_extension_status": unit.get(
                    "repeater_family_extension_status", ""
                ),
                "network_local_extension_disposition": network.get(
                    "local_extension_disposition", ""
                ),
                "network_catalog_conflict_STOP": network.get(
                    "catalog_conflict_STOP", ""
                ),
                "network_eligible_for_independent_DAS_comparison": network.get(
                    "eligible_for_independent_DAS_comparison", ""
                ),
                "DAS_independent_adjudication_status": adjudication_status,
                "DAS_detection_increment_status": increment_status,
                "repeater_family_assignment": "not_assigned",
            }
        )
        if not das_id and membership != "network_only":
            raise RuntimeError("comparison membership/DAS identifier mismatch")
        output.append(row)
    return output


def build_interval_summary(
    context_rows: Sequence[Mapping[str, Any]],
    interval_ids: Sequence[str],
) -> List[Dict[str, Any]]:
    """Report every registered interval, including intervals with zero rows."""

    expected = list(map(str, interval_ids))
    if len(expected) != len(set(expected)):
        raise ValueError("registered interval identifiers are not unique")
    rows: List[Dict[str, Any]] = []
    for interval_id in expected:
        interval = [
            row
            for row in context_rows
            if str(row["interval_id"]) == interval_id
        ]
        memberships = Counter(
            str(row["comparison_membership"]) for row in interval
        )
        das_count = sum(bool(str(row["DAS_candidate_id"])) for row in interval)
        network_count = sum(
            bool(str(row["network_union_candidate_id"])) for row in interval
        )
        units = {
            str(row["network_evaluation_unit_id"])
            for row in interval
            if str(row["network_evaluation_unit_id"])
        }
        matched_units = {
            str(row["network_evaluation_unit_id"])
            for row in interval
            if str(row["comparison_membership"]) == "DAS+network"
            and str(row["network_evaluation_unit_id"])
        }
        rows.append(
            {
                "interval_id": interval_id,
                "DAS_v2_candidate_count": das_count,
                "network_raw_candidate_count": network_count,
                "matched_pair_count": memberships["DAS+network"],
                "DAS_only_candidate_count": memberships["DAS_only"],
                "network_only_candidate_count": memberships["network_only"],
                "network_event_unit_count": len(units),
                "network_event_units_with_DAS_match": len(matched_units),
                "network_event_units_without_DAS_match": len(
                    units - matched_units
                ),
                "network_duplicate_raw_candidate_count": (
                    network_count - len(units)
                ),
                "DAS_only_independent_adjudication_pending_count": sum(
                    str(row["DAS_detection_increment_status"])
                    == "PENDING_NOT_AN_EXTENSION_CLAIM"
                    for row in interval
                ),
                "catalog_fields_used_in_time_matching": 0,
                "family_assignments_made": 0,
            }
        )
    unexpected = {
        str(row["interval_id"]) for row in context_rows
    } - set(expected)
    if unexpected:
        raise RuntimeError("comparison output contains unregistered intervals")
    return rows
