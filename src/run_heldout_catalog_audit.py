#!/usr/bin/env python
"""Run the one-time catalog audit of the immutable held-out network union."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .common import (
    CONDITIONAL,
    PASS,
    load_config,
    parse_utc,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .heldout_catalog_access import (
    SOURCE_LEDGER_FIELDS,
    acquire_registered_catalogs,
    load_catalog_runner_release,
)


TARGET_ASSOCIATION_FIELDS = [
    "interval_id",
    "union_candidate_id",
    "branch_membership",
    "template_match_count",
    "template_event_id",
    "template_event_origin_time",
    "template_time_residual_s",
    "generic_direct_match_count",
    "generic_direct_event_id",
    "generic_direct_event_origin_time",
    "generic_direct_time_residual_s",
    "target_unique_event_ids",
    "target_association_status",
]
BROADER_ASSOCIATION_FIELDS = [
    "interval_id",
    "union_candidate_id",
    "broader_catalog_applicable",
    "plausible_match_count",
    "event_id",
    "origin_time",
    "location_name",
    "magnitude",
    "horizontal_distance_km",
    "path_distance_km",
    "observed_delay_s",
    "earliest_plausible_arrival_s",
    "latest_plausible_arrival_s",
    "nominal_arrival_s",
    "nominal_timing_residual_s",
    "association_status",
]
ADJUDICATION_FIELDS = [
    "catalog_audit_stage",
    "target_catalog_association",
    "target_catalog_event_id",
    "target_catalog_origin_time",
    "template_target_time_residual_s",
    "generic_target_time_residual_s",
    "broader_catalog_association",
    "broader_catalog_event_id",
    "broader_catalog_origin_time",
    "broader_catalog_location_name",
    "broader_catalog_magnitude",
    "broader_catalog_horizontal_distance_km",
    "broader_catalog_path_distance_km",
    "broader_catalog_observed_delay_s",
    "broader_catalog_nominal_timing_residual_s",
    "broader_catalog_plausible_match_count",
    "catalog_event_id_final",
    "known_event_class",
    "family_neighborhood_horizontal_distance_km",
    "family_neighborhood_depth_difference_km",
    "family_neighborhood_spatial_annotation",
    "evaluation_unit_id",
    "catalog_detection_extension_status",
    "repeater_family_extension_status",
    "local_extension_disposition",
    "catalog_conflict_STOP",
    "eligible_for_independent_DAS_comparison",
]
EVALUATION_UNIT_FIELDS = [
    "evaluation_unit_id",
    "catalog_event_id",
    "known_event_class",
    "candidate_count",
    "union_candidate_ids",
    "interval_ids",
    "branch_memberships",
    "catalog_detection_extension_status",
    "repeater_family_extension_status",
    "catalog_conflict_STOP",
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


def nearest_origin_event(
    candidate_epoch_s: float,
    catalog: Sequence[Mapping[str, Any]],
    tolerance_s: float,
) -> Dict[str, Any]:
    """Return the deterministic nearest direct origin-time association."""

    plausible: List[Tuple[float, str, Mapping[str, Any]]] = []
    for event in catalog:
        residual = float(candidate_epoch_s) - parse_utc(
            str(event["origin_time"])
        ).timestamp()
        if abs(residual) <= float(tolerance_s):
            plausible.append((abs(residual), str(event["event_id"]), event))
    if not plausible:
        return {"match_count": 0, "event": None, "residual_s": ""}
    _, _, event = min(plausible, key=lambda item: (item[0], item[1]))
    residual = float(candidate_epoch_s) - parse_utc(
        str(event["origin_time"])
    ).timestamp()
    return {
        "match_count": len(plausible),
        "event": dict(event),
        "residual_s": residual,
    }


def _local_offsets_km(
    event: Mapping[str, Any], latitude: float, longitude: float, depth_km: float
) -> Tuple[float, float]:
    radius_km = 6371.0088
    latitude_radians = math.radians(float(latitude))
    north = radius_km * math.radians(float(event["latitude"]) - latitude)
    east = (
        radius_km
        * math.cos(latitude_radians)
        * math.radians(float(event["longitude"]) - longitude)
    )
    horizontal = math.hypot(east, north)
    vertical = float(event["depth_km"]) - depth_km
    return horizontal, math.hypot(horizontal, vertical)


def physical_arrival_event(
    trigger_epoch_s: float,
    catalog: Sequence[Mapping[str, Any]],
    settings: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return the registered physically plausible generic-trigger arrival."""

    minimum_velocity = float(settings["minimum_plausible_phase_velocity_km_s"])
    maximum_velocity = float(settings["maximum_plausible_phase_velocity_km_s"])
    nominal_velocity = float(settings["nominal_ranking_phase_velocity_km_s"])
    early_slack = float(settings["early_arrival_slack_s"])
    late_slack = float(settings["late_arrival_slack_s"])
    plausible: List[Tuple[float, str, Dict[str, Any]]] = []
    for event in catalog:
        horizontal_km, path_km = _local_offsets_km(
            event,
            float(settings["array_reference_latitude"]),
            float(settings["array_reference_longitude"]),
            float(settings["array_reference_depth_km"]),
        )
        origin_s = parse_utc(str(event["origin_time"])).timestamp()
        delay_s = float(trigger_epoch_s) - origin_s
        earliest = path_km / maximum_velocity - early_slack
        latest = path_km / minimum_velocity + late_slack
        nominal = path_km / nominal_velocity
        if earliest <= delay_s <= latest:
            row = {
                "event": dict(event),
                "horizontal_distance_km": horizontal_km,
                "path_distance_km": path_km,
                "observed_delay_s": delay_s,
                "earliest_plausible_arrival_s": earliest,
                "latest_plausible_arrival_s": latest,
                "nominal_arrival_s": nominal,
                "nominal_timing_residual_s": delay_s - nominal,
            }
            plausible.append(
                (
                    abs(float(row["nominal_timing_residual_s"])),
                    str(event["event_id"]),
                    row,
                )
            )
    if not plausible:
        return {"match_count": 0, "event": None}
    _, _, best = min(plausible, key=lambda item: (item[0], item[1]))
    return {"match_count": len(plausible), **best}


def _target_association(
    row: Mapping[str, Any],
    target_catalog: Sequence[Mapping[str, Any]],
    registration: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    target = registration["target_region_catalog"]
    template = {"match_count": 0, "event": None, "residual_s": ""}
    generic = {"match_count": 0, "event": None, "residual_s": ""}
    if str(row.get("template_candidate_id", "")):
        template = nearest_origin_event(
            float(row["template_origin_epoch_s"]),
            target_catalog,
            float(
                target["template_origin_association"][
                    "maximum_absolute_residual_s"
                ]
            ),
        )
    if str(row.get("generic_candidate_id", "")):
        generic = nearest_origin_event(
            float(row["generic_trigger_epoch_s"]),
            target_catalog,
            float(
                target["generic_trigger_association"][
                    "maximum_absolute_residual_s"
                ]
            ),
        )
    template_event = template.get("event") or {}
    generic_event = generic.get("event") or {}
    identifiers = sorted(
        {
            str(event["event_id"])
            for event in (template_event, generic_event)
            if event
        }
    )
    if len(identifiers) > 1:
        status = "conflicting_direct_catalog_event_ids_STOP"
    elif template_event and generic_event:
        status = "template_and_generic_direct_same_event"
    elif template_event:
        status = "template_origin_direct_match"
    elif generic_event:
        status = "generic_origin_audit_match_requires_physical_confirmation"
    else:
        status = "none"
    output = {
        "interval_id": str(row["interval_id"]),
        "union_candidate_id": str(row["union_candidate_id"]),
        "branch_membership": str(row["branch_membership"]),
        "template_match_count": int(template["match_count"]),
        "template_event_id": str(template_event.get("event_id", "")),
        "template_event_origin_time": str(template_event.get("origin_time", "")),
        "template_time_residual_s": template.get("residual_s", ""),
        "generic_direct_match_count": int(generic["match_count"]),
        "generic_direct_event_id": str(generic_event.get("event_id", "")),
        "generic_direct_event_origin_time": str(
            generic_event.get("origin_time", "")
        ),
        "generic_direct_time_residual_s": generic.get("residual_s", ""),
        "target_unique_event_ids": ";".join(identifiers),
        "target_association_status": status,
    }
    return output, template, generic


def _broader_association(
    row: Mapping[str, Any],
    broader_catalog: Sequence[Mapping[str, Any]],
    registration: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not str(row.get("generic_candidate_id", "")):
        output = {
            "interval_id": str(row["interval_id"]),
            "union_candidate_id": str(row["union_candidate_id"]),
            "broader_catalog_applicable": False,
            "plausible_match_count": 0,
            "association_status": (
                "not_applicable_template_time_is_origin_not_array_arrival"
            ),
        }
        return output, {"match_count": 0, "event": None}
    match = physical_arrival_event(
        float(row["generic_trigger_epoch_s"]),
        broader_catalog,
        registration["broader_regional_catalog"],
    )
    event = match.get("event") or {}
    output = {
        "interval_id": str(row["interval_id"]),
        "union_candidate_id": str(row["union_candidate_id"]),
        "broader_catalog_applicable": True,
        "plausible_match_count": int(match["match_count"]),
        "event_id": str(event.get("event_id", "")),
        "origin_time": str(event.get("origin_time", "")),
        "location_name": str(event.get("location_name", "")),
        "magnitude": event.get("magnitude", ""),
        "horizontal_distance_km": match.get("horizontal_distance_km", ""),
        "path_distance_km": match.get("path_distance_km", ""),
        "observed_delay_s": match.get("observed_delay_s", ""),
        "earliest_plausible_arrival_s": match.get(
            "earliest_plausible_arrival_s", ""
        ),
        "latest_plausible_arrival_s": match.get(
            "latest_plausible_arrival_s", ""
        ),
        "nominal_arrival_s": match.get("nominal_arrival_s", ""),
        "nominal_timing_residual_s": match.get(
            "nominal_timing_residual_s", ""
        ),
        "association_status": (
            "physically_plausible_known_event_arrival" if event else "none"
        ),
    }
    return output, match


def _inside_target_query(
    event: Mapping[str, Any], parent: Mapping[str, Any]
) -> bool:
    bounds = parent["archive_catalog"]["bounds"]
    checks = [
        float(bounds["minlatitude"]) <= float(event["latitude"])
        <= float(bounds["maxlatitude"]),
        float(bounds["minlongitude"]) <= float(event["longitude"])
        <= float(bounds["maxlongitude"]),
        float(bounds["mindepth"]) <= float(event["depth_km"])
        <= float(bounds["maxdepth"]),
        float(event["magnitude"]) >= float(bounds["minmagnitude"]),
    ]
    return all(checks)


def _spatial_annotation(
    event: Mapping[str, Any] | None,
    registration: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> Dict[str, Any]:
    if not event:
        return {
            "horizontal_distance_km": "",
            "depth_difference_km": "",
            "annotation": "not_available",
            "known_event_class": "",
        }
    neighborhood = registration["family_neighborhood_annotation"]
    horizontal, _ = _local_offsets_km(
        event,
        float(neighborhood["reference_latitude"]),
        float(neighborhood["reference_longitude"]),
        float(neighborhood["reference_depth_km"]),
    )
    depth_difference = abs(
        float(event["depth_km"]) - float(neighborhood["reference_depth_km"])
    )
    if (
        horizontal <= float(neighborhood["maximum_horizontal_distance_km"])
        and depth_difference
        <= float(neighborhood["maximum_depth_difference_km"])
    ):
        annotation = "inside_family_neighborhood_not_a_family_label"
        known_class = "known_family_neighborhood_catalog_event"
    elif _inside_target_query(event, parent):
        annotation = "inside_target_catalog_box_outside_family_neighborhood"
        known_class = "known_target_box_event_outside_family_neighborhood"
    else:
        annotation = "outside_target_catalog_box"
        known_class = "known_regional_arrival_outside_target_box"
    return {
        "horizontal_distance_km": horizontal,
        "depth_difference_km": depth_difference,
        "annotation": annotation,
        "known_event_class": known_class,
    }


def adjudicate_network_union(
    union_rows: Sequence[Mapping[str, Any]],
    target_catalog: Sequence[Mapping[str, Any]],
    broader_by_interval: Mapping[str, Sequence[Mapping[str, Any]]],
    registration: Mapping[str, Any],
    parent: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Annotate every immutable row without deleting or assigning a family."""

    target_by_id = {str(event["event_id"]): event for event in target_catalog}
    target_outputs: List[Dict[str, Any]] = []
    broader_outputs: List[Dict[str, Any]] = []
    adjudicated: List[Dict[str, Any]] = []
    for source in union_rows:
        row = dict(source)
        interval_id = str(row["interval_id"])
        if interval_id not in broader_by_interval:
            raise RuntimeError("broader catalog missing for " + interval_id)
        target_output, template_match, generic_direct = _target_association(
            row, target_catalog, registration
        )
        broader_output, broader_match = _broader_association(
            row, broader_by_interval[interval_id], registration
        )
        target_outputs.append(target_output)
        broader_outputs.append(broader_output)

        template_event = template_match.get("event") or {}
        generic_event = generic_direct.get("event") or {}
        broader_event = broader_match.get("event") or {}
        direct_ids = {
            str(event["event_id"])
            for event in (template_event, generic_event)
            if event
        }
        conflict = len(direct_ids) > 1
        direct_id = next(iter(direct_ids)) if len(direct_ids) == 1 else ""
        broader_id = str(broader_event.get("event_id", ""))
        if direct_id and broader_id and direct_id != broader_id:
            conflict = True
        generic_only_direct_without_physics = bool(
            generic_event and not template_event and not broader_event
        )
        if generic_only_direct_without_physics:
            conflict = True

        final_event: Mapping[str, Any] | None = None
        if not conflict:
            if template_event:
                final_event = template_event
            elif broader_event:
                final_event = target_by_id.get(broader_id, broader_event)
        final_id = str(final_event.get("event_id", "")) if final_event else ""
        spatial = _spatial_annotation(final_event, registration, parent)

        if conflict:
            known_class = "catalog_timing_or_identity_conflict_STOP"
            detection_status = "STOP_catalog_conflict_not_an_extension_claim"
            disposition = "retain_raw_row_for_manual_blind_adjudication"
            evaluation_id = "STOP:" + str(row["union_candidate_id"])
        elif final_event:
            known_class = str(spatial["known_event_class"])
            detection_status = "known_catalog_event_not_detection_extension"
            disposition = "retain_known_event_for_later_repeater_classification"
            evaluation_id = "catalog:" + final_id
        elif str(row.get("generic_candidate_id", "")):
            known_class = "catalog_unassociated_generic_candidate"
            detection_status = (
                "candidate_only_requires_independent_waveform_confirmation"
            )
            disposition = "retain_not_automatically_new"
            evaluation_id = "candidate:" + str(row["union_candidate_id"])
        else:
            known_class = "catalog_unassociated_template_only_inconclusive"
            detection_status = (
                "candidate_only_broader_arrival_audit_not_applicable"
            )
            disposition = "retain_not_automatically_new"
            evaluation_id = "candidate:" + str(row["union_candidate_id"])

        if conflict:
            target_status = str(target_output["target_association_status"])
        elif template_event:
            target_status = "known_event_by_template_origin"
        elif generic_event and broader_event:
            target_status = "known_event_by_generic_origin_and_physical_arrival"
        elif generic_event:
            target_status = "generic_direct_audit_without_physics_STOP"
        else:
            target_status = "none"
        broader_status = str(broader_output["association_status"])
        row.update(
            {
                "catalog_audit_stage": "post_frozen_union_catalog_only",
                "target_catalog_association": target_status,
                "target_catalog_event_id": ";".join(sorted(direct_ids)),
                "target_catalog_origin_time": str(
                    (template_event or generic_event).get("origin_time", "")
                ),
                "template_target_time_residual_s": template_match.get(
                    "residual_s", ""
                ),
                "generic_target_time_residual_s": generic_direct.get(
                    "residual_s", ""
                ),
                "broader_catalog_association": broader_status,
                "broader_catalog_event_id": broader_id,
                "broader_catalog_origin_time": str(
                    broader_event.get("origin_time", "")
                ),
                "broader_catalog_location_name": str(
                    broader_event.get("location_name", "")
                ),
                "broader_catalog_magnitude": broader_event.get("magnitude", ""),
                "broader_catalog_horizontal_distance_km": broader_match.get(
                    "horizontal_distance_km", ""
                ),
                "broader_catalog_path_distance_km": broader_match.get(
                    "path_distance_km", ""
                ),
                "broader_catalog_observed_delay_s": broader_match.get(
                    "observed_delay_s", ""
                ),
                "broader_catalog_nominal_timing_residual_s": broader_match.get(
                    "nominal_timing_residual_s", ""
                ),
                "broader_catalog_plausible_match_count": int(
                    broader_match["match_count"]
                ),
                "catalog_event_id_final": final_id,
                "known_event_class": known_class,
                "family_neighborhood_horizontal_distance_km": spatial[
                    "horizontal_distance_km"
                ],
                "family_neighborhood_depth_difference_km": spatial[
                    "depth_difference_km"
                ],
                "family_neighborhood_spatial_annotation": spatial["annotation"],
                "evaluation_unit_id": evaluation_id,
                "catalog_detection_extension_status": detection_status,
                "repeater_family_extension_status": (
                    "not_evaluated_catalog_evidence_cannot_assign_family"
                ),
                "local_extension_disposition": disposition,
                "catalog_conflict_STOP": conflict,
                "eligible_for_independent_DAS_comparison": True,
            }
        )
        adjudicated.append(row)
    assert_union_projection_unchanged(union_rows, adjudicated)
    return target_outputs, broader_outputs, adjudicated


def assert_union_projection_unchanged(
    union_rows: Sequence[Mapping[str, Any]],
    adjudicated: Sequence[Mapping[str, Any]],
) -> None:
    if len(union_rows) != len(adjudicated):
        raise PermissionError("catalog audit deleted a frozen union row")
    for source, result in zip(union_rows, adjudicated):
        for field, value in source.items():
            if str(result.get(field, "")) != str(value):
                raise PermissionError(
                    "catalog audit changed frozen field {}".format(field)
                )


def build_evaluation_units(
    adjudicated: Sequence[Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    groups: Dict[str, List[Mapping[str, Any]]] = defaultdict(list)
    for row in adjudicated:
        groups[str(row["evaluation_unit_id"])].append(row)
    output: List[Dict[str, Any]] = []
    for identifier, rows in groups.items():
        classes = {str(row["known_event_class"]) for row in rows}
        detection = {
            str(row["catalog_detection_extension_status"]) for row in rows
        }
        family = {str(row["repeater_family_extension_status"]) for row in rows}
        event_ids = {
            str(row["catalog_event_id_final"])
            for row in rows
            if str(row["catalog_event_id_final"])
        }
        if len(classes) != 1 or len(detection) != 1 or len(family) != 1:
            raise RuntimeError("evaluation unit has inconsistent annotations")
        if identifier.startswith("catalog:") and len(event_ids) != 1:
            raise RuntimeError("known-event evaluation unit has inconsistent IDs")
        output.append(
            {
                "evaluation_unit_id": identifier,
                "catalog_event_id": ";".join(sorted(event_ids)),
                "known_event_class": next(iter(classes)),
                "candidate_count": len(rows),
                "union_candidate_ids": ";".join(
                    str(row["union_candidate_id"]) for row in rows
                ),
                "interval_ids": ";".join(
                    sorted({str(row["interval_id"]) for row in rows})
                ),
                "branch_memberships": ";".join(
                    sorted({str(row["branch_membership"]) for row in rows})
                ),
                "catalog_detection_extension_status": next(iter(detection)),
                "repeater_family_extension_status": next(iter(family)),
                "catalog_conflict_STOP": any(
                    str(row["catalog_conflict_STOP"]).lower() == "true"
                    or row["catalog_conflict_STOP"] is True
                    for row in rows
                ),
            }
        )
    return sorted(output, key=lambda row: str(row["evaluation_unit_id"]))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "heldout_catalog_audit.json",
    )
    parser.add_argument("--timeout-s", type=float, default=90.0)
    args = parser.parse_args()

    project = project_root()
    registration = load_config(args.config)
    output = project / str(registration["output"]["catalog_audit_directory"])
    final_status_path = output / str(
        registration["output"]["final_status_json"]
    )
    if final_status_path.is_file():
        raise PermissionError("held-out catalog audit is already frozen")
    registration_output = project / str(
        registration["output"]["registration_directory"]
    )
    registration_status_path = registration_output / str(
        registration["output"]["registration_status_json"]
    )
    registration_status = _load_json(registration_status_path)
    release = load_catalog_runner_release(project)

    union_declaration = registration["frozen_inputs"]["network_time_only_union"]
    union_path = project / str(union_declaration["path"])
    if sha256_file(union_path) != str(union_declaration["sha256"]):
        raise RuntimeError("frozen network union changed before catalog audit")
    union_rows = _read_csv(union_path)
    parent = load_config(
        project / str(registration["frozen_inputs"]["parent_config"]["path"])
    )
    target_catalog, broader_by_interval, source_ledger, runner_sha = (
        acquire_registered_catalogs(
            project,
            registration,
            registration_status,
            release,
            timeout_s=float(args.timeout_s),
        )
    )
    target_rows, broader_rows, adjudicated = adjudicate_network_union(
        union_rows,
        target_catalog,
        broader_by_interval,
        registration,
        parent,
    )
    evaluation_units = build_evaluation_units(adjudicated)

    target_path = output / str(registration["output"]["target_associations_csv"])
    source_path = output / str(
        registration["output"]["broader_source_ledger_csv"]
    )
    broader_path = output / str(
        registration["output"]["broader_associations_csv"]
    )
    adjudicated_path = output / str(
        registration["output"]["adjudicated_union_csv"]
    )
    units_path = output / str(registration["output"]["evaluation_units_csv"])
    union_fields = list(union_rows[0].keys()) if union_rows else []
    _write_csv(target_path, target_rows, TARGET_ASSOCIATION_FIELDS)
    _write_csv(source_path, source_ledger, SOURCE_LEDGER_FIELDS)
    _write_csv(broader_path, broader_rows, BROADER_ASSOCIATION_FIELDS)
    _write_csv(adjudicated_path, adjudicated, union_fields + ADJUDICATION_FIELDS)
    _write_csv(units_path, evaluation_units, EVALUATION_UNIT_FIELDS)

    classes = Counter(str(row["known_event_class"]) for row in adjudicated)
    conflict_count = classes.get("catalog_timing_or_identity_conflict_STOP", 0)
    status_value = CONDITIONAL if conflict_count else PASS
    release_path = project / (
        "outputs/heldout_v2/registration/catalog_audit_runner_release.json"
    )
    status = {
        "status": status_value,
        "stage": "heldout_network_catalog_audit_complete_and_frozen",
        "generated_utc": utc_now(),
        "catalog_audit_config_sha256": registration["_config_sha256"],
        "catalog_registration_status_sha256": sha256_file(
            registration_status_path
        ),
        "catalog_runner_release_sha256": sha256_file(release_path),
        "runner_commit_sha": runner_sha,
        "network_union_input_sha256": sha256_file(union_path),
        "network_union_input_candidate_count": len(union_rows),
        "adjudicated_union_candidate_count": len(adjudicated),
        "all_raw_union_rows_retained": len(adjudicated) == len(union_rows),
        "frozen_union_fields_changed": 0,
        "candidate_rows_deleted": 0,
        "candidate_times_edited": 0,
        "family_assignments_made": 0,
        "target_catalog_event_rows_opened": len(target_catalog),
        "target_catalog_sha256": registration_status["target_catalog_sha256"],
        "broader_catalog_query_count": len(source_ledger),
        "broader_catalog_queries_PASS": sum(
            str(row["status"]) == PASS for row in source_ledger
        ),
        "broader_catalog_event_rows_opened": sum(
            int(row["event_row_count"]) for row in source_ledger
        ),
        "heldout_network_waveform_files_opened": 0,
        "heldout_DAS_HDF5_files_opened": 0,
        "heldout_DAS_HDF5_datasets_opened": 0,
        "heldout_family_label_rows_opened": 0,
        "known_event_class_counts": dict(sorted(classes.items())),
        "catalog_conflict_STOP_count": conflict_count,
        "catalog_unassociated_candidate_count": sum(
            name.startswith("catalog_unassociated") * count
            for name, count in classes.items()
        ),
        "evaluation_unit_count": len(evaluation_units),
        "evaluation_unit_duplicate_candidate_count": len(adjudicated)
        - len(evaluation_units),
        "generic_development_SNR1_STOP_preserved": True,
        "catalog_unassociated_is_not_automatically_new": True,
        "cataloged_event_can_still_extend_repeater_family": True,
        "repeater_family_membership_evaluated": False,
        "scientific_extension_claim_gate": (
            "STOP_NO_INDEPENDENT_DAS_OR_FAMILY_ADJUDICATION_YET"
        ),
        "DAS_candidate_generation_read_network_or_catalog_times": False,
        "target_associations_path": str(target_path),
        "target_associations_sha256": sha256_file(target_path),
        "broader_source_ledger_path": str(source_path),
        "broader_source_ledger_sha256": sha256_file(source_path),
        "broader_associations_path": str(broader_path),
        "broader_associations_sha256": sha256_file(broader_path),
        "adjudicated_union_path": str(adjudicated_path),
        "adjudicated_union_sha256": sha256_file(adjudicated_path),
        "evaluation_units_path": str(units_path),
        "evaluation_units_sha256": sha256_file(units_path),
        "next_stage_gate": (
            "PASS_REGISTER_INDEPENDENT_HELDOUT_DAS_REPLAY"
            if not conflict_count
            else "CONDITIONAL_RETAIN_CONFLICTS_AND_REGISTER_INDEPENDENT_DAS_REPLAY"
        ),
        "heldout_DAS_access_gate": (
            "STOP_PENDING_SEPARATE_INDEPENDENT_DAS_REPLAY_REGISTRATION"
        ),
        "heldout_family_label_access_gate": "STOP_PENDING_SEPARATE_REGISTRATION",
    }
    write_json(final_status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
