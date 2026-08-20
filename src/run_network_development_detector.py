#!/usr/bin/env python
"""Run both network-only detectors on the nonblind development interval."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

import numpy as np
from obspy import Stream, read

from .background_catalog import (
    associate_background_arrivals,
    obtain_background_catalog,
)
from .catalog import CATALOG_FIELDS
from .common import (
    CONDITIONAL,
    iso_utc,
    load_config,
    parse_utc,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .continuous_network import (
    download_development_stream,
    validate_development_access,
)
from .injection_recovery import (
    run_injection_recovery,
    summarize_injection_recovery,
)
from .network_continuous_detection import (
    detect_candidates,
    detect_generic_candidates,
    generic_scan_indices_and_times,
    higher_quantile,
    null_bank_maxima,
    null_coincidence_maxima,
    prepare_continuous_traces,
    prepare_template,
    scan_indices_and_times,
    station_energy_ratio_matrix,
    template_station_score_matrix,
)


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


def _event_template_stream(
    project: Path,
    event_id: str,
    parent: Mapping[str, Any],
) -> Stream:
    stream = Stream()
    cache = project / "cached_event_windows" / "network_array" / event_id
    for source in parent["network_array"]["sources"]:
        path = cache / (str(source["name"]) + ".mseed")
        if path.exists() and path.stat().st_size > 0:
            stream += read(str(path))
    stream.sort()
    return stream


def _eligible_events(
    project: Path,
    development: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    settings = development["repeater_template_bank"]
    population = _read_csv(project / str(settings["population_path"]))
    eligibility = _read_csv(project / str(settings["eligibility_path"]))
    population_by_id = {
        str(row["event_id"]): row for row in population
    }
    events: List[Dict[str, Any]] = []
    for row in eligibility:
        if str(row["score_status"]) != "PASS":
            continue
        event_id = str(row["event_id"])
        published = population_by_id.get(event_id)
        if published is None:
            raise ValueError(
                "eligible event {} is absent from published population".format(
                    event_id
                )
            )
        events.append(
            {
                "event_id": event_id,
                "origin_time": published["origin_time"],
                "sequence_id": published["sequence_id"],
                "validation_role": published["validation_role"],
            }
        )
    return sorted(events, key=lambda row: row["event_id"])


def _associate_catalog(
    candidates: List[Dict[str, Any]],
    catalog: Sequence[Mapping[str, Any]],
    tolerance_s: float,
    epoch_field: str = "origin_epoch_s",
    scope: str = "parkfield_target_region_archive_catalog",
) -> None:
    for row in candidates:
        row["catalog_association_scope"] = scope
    if not catalog:
        for row in candidates:
            row["nearest_catalog_event_id"] = ""
            row["nearest_catalog_origin_time"] = ""
            row["catalog_time_residual_s"] = ""
            row["catalog_association"] = "none"
        return
    epochs = np.asarray(
        [
            parse_utc(str(event["origin_time"])).timestamp()
            for event in catalog
        ],
        dtype=float,
    )
    for row in candidates:
        residuals = float(row[epoch_field]) - epochs
        index = int(np.argmin(np.abs(residuals)))
        within = abs(float(residuals[index])) <= float(tolerance_s)
        row["nearest_catalog_event_id"] = str(catalog[index]["event_id"])
        row["nearest_catalog_origin_time"] = str(
            catalog[index]["origin_time"]
        )
        row["catalog_time_residual_s"] = float(residuals[index])
        row["catalog_association"] = (
            "within_tolerance" if within else "outside_tolerance"
        )


def _catalog_score_rows(
    catalog: Sequence[Mapping[str, Any]],
    origin_epoch_s: np.ndarray,
    bank_score: np.ndarray,
    best_template: np.ndarray,
    template_rows: Sequence[Mapping[str, Any]],
    threshold: float,
    candidates: Sequence[Mapping[str, Any]],
    tolerance_s: float,
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    candidate_epochs = np.asarray(
        [float(row["origin_epoch_s"]) for row in candidates], dtype=float
    )
    for event in catalog:
        epoch = parse_utc(str(event["origin_time"])).timestamp()
        local = np.flatnonzero(
            np.abs(origin_epoch_s - epoch) <= float(tolerance_s)
        )
        if local.size == 0:
            raise ValueError(
                "catalog event has no score sample inside association window"
            )
        index = int(local[np.nanargmax(bank_score[local])])
        template_index = int(best_template[index])
        detected = bool(
            candidate_epochs.size
            and np.min(np.abs(candidate_epochs - epoch))
            <= float(tolerance_s)
        )
        rows.append(
            {
                "event_id": str(event["event_id"]),
                "origin_time": str(event["origin_time"]),
                "magnitude": event.get("magnitude", ""),
                "peak_score_time_within_tolerance": iso_utc(
                    float(origin_epoch_s[index])
                ),
                "peak_score_time_residual_s": float(
                    origin_epoch_s[index] - epoch
                ),
                "peak_bank_score_within_tolerance": float(
                    bank_score[index]
                ),
                "threshold": float(threshold),
                "best_template_event_id": str(
                    template_rows[template_index]["event_id"]
                ),
                "best_template_sequence_id": str(
                    template_rows[template_index]["sequence_id"]
                ),
                "candidate_within_tolerance": detected,
            }
        )
    return rows


def _generic_catalog_score_rows(
    catalog: Sequence[Mapping[str, Any]],
    trigger_epoch_s: np.ndarray,
    generic_score: np.ndarray,
    threshold: float,
    candidates: Sequence[Mapping[str, Any]],
    tolerance_s: float,
) -> List[Dict[str, Any]]:
    """Audit the largest generic-trigger score near each catalog origin."""

    rows: List[Dict[str, Any]] = []
    candidate_epochs = np.asarray(
        [float(row["trigger_epoch_s"]) for row in candidates], dtype=float
    )
    for event in catalog:
        epoch = parse_utc(str(event["origin_time"])).timestamp()
        local = np.flatnonzero(
            np.abs(trigger_epoch_s - epoch) <= float(tolerance_s)
        )
        if local.size == 0:
            raise ValueError(
                "catalog event has no generic score inside association window"
            )
        index = int(local[np.nanargmax(generic_score[local])])
        detected = bool(
            candidate_epochs.size
            and np.min(np.abs(candidate_epochs - epoch))
            <= float(tolerance_s)
        )
        rows.append(
            {
                "event_id": str(event["event_id"]),
                "origin_time": str(event["origin_time"]),
                "magnitude": event.get("magnitude", ""),
                "peak_trigger_time_within_tolerance": iso_utc(
                    float(trigger_epoch_s[index])
                ),
                "peak_trigger_time_residual_s": float(
                    trigger_epoch_s[index] - epoch
                ),
                "peak_generic_score_within_tolerance": float(
                    generic_score[index]
                ),
                "threshold": float(threshold),
                "candidate_within_tolerance": detected,
            }
        )
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "development_detection.json",
    )
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument(
        "--force-background-catalog", action="store_true"
    )
    args = parser.parse_args()

    project = project_root()
    development = load_config(args.config)
    parent_path = project / str(development["parent_config_path"])
    parent = load_config(parent_path)
    interval_start, interval_end = validate_development_access(
        parent, development
    )

    continuous_stream, source_rows = download_development_stream(
        project,
        parent,
        development,
        force=args.force_download,
    )
    if not continuous_stream:
        raise RuntimeError(
            "no continuous network source was available: {}".format(
                source_rows
            )
        )
    continuous, trace_rows, request_start = prepare_continuous_traces(
        continuous_stream, parent, development
    )
    if not continuous:
        raise RuntimeError("no continuous trace passed strict QC")

    eligible_events = _eligible_events(project, development)
    template_rows: List[Dict[str, Any]] = []
    station_matrices_full: List[np.ndarray] = []
    station_names_by_template: List[List[str]] = []
    eligible_template_rows: List[Dict[str, Any]] = []
    eligible_template_waveforms: List[Dict[str, np.ndarray]] = []
    first_curve_length: int | None = None
    for event in eligible_events:
        event_stream = _event_template_stream(
            project, str(event["event_id"]), parent
        )
        templates, _, row = prepare_template(
            event,
            event_stream,
            list(continuous),
            development,
        )
        row["validation_role"] = event["validation_role"]
        template_rows.append(row)
        if row["status"] != "PASS":
            continue
        station_matrix, station_names, component_count = (
            template_station_score_matrix(continuous, templates)
        )
        row["scored_component_count"] = component_count
        row["scored_station_count"] = len(station_names)
        minimum_components = int(
            development["repeater_template_bank"][
                "minimum_components_per_template"
            ]
        )
        minimum_stations = int(
            development["repeater_template_bank"][
                "minimum_stations_per_template"
            ]
        )
        if (
            component_count < minimum_components
            or len(station_names) < minimum_stations
        ):
            row["status"] = "STOP"
            row["reason"] = "insufficient_scored_components_or_stations"
            continue
        if first_curve_length is None:
            first_curve_length = station_matrix.shape[1]
        elif station_matrix.shape[1] != first_curve_length:
            raise ValueError("template station curves have unequal lengths")
        station_matrices_full.append(station_matrix)
        station_names_by_template.append(station_names)
        eligible_template_rows.append(row)
        eligible_template_waveforms.append(templates)

    if not station_matrices_full or first_curve_length is None:
        raise RuntimeError("no historical template passed continuous scoring QC")
    scan_indices, origin_epoch_s = scan_indices_and_times(
        request_start,
        first_curve_length,
        development,
    )
    station_matrices = [
        matrix[:, scan_indices].astype(np.float32)
        for matrix in station_matrices_full
    ]
    template_scores = np.stack(
        [
            np.mean(matrix, axis=0, dtype=np.float64).astype(np.float32)
            for matrix in station_matrices
        ],
        axis=0,
    )

    null_maxima = null_bank_maxima(station_matrices, development)
    quantile = float(
        development["null_calibration"][
            "familywise_threshold_quantile"
        ]
    )
    threshold = higher_quantile(null_maxima, quantile)
    candidates, bank_score, best_template = detect_candidates(
        origin_epoch_s,
        template_scores,
        station_matrices,
        station_names_by_template,
        eligible_template_rows,
        threshold,
        development,
    )

    catalog = _read_csv(
        project
        / "outputs"
        / "incremental_value"
        / "ncedc_archive_catalog.csv"
    )
    development_catalog = [
        row
        for row in catalog
        if interval_start
        <= parse_utc(str(row["origin_time"])).timestamp()
        < interval_end
    ]
    association_tolerance = float(
        development["repeater_template_bank"][
            "catalog_association_tolerance_s"
        ]
    )
    _associate_catalog(
        candidates,
        development_catalog,
        association_tolerance,
    )
    catalog_scores = _catalog_score_rows(
        development_catalog,
        origin_epoch_s,
        bank_score,
        best_template,
        eligible_template_rows,
        threshold,
        candidates,
        association_tolerance,
    )

    generic_settings = development["generic_network_trigger"]
    generic_station_matrix_full, generic_station_names, generic_components = (
        station_energy_ratio_matrix(
            continuous,
            float(
                development["repeater_template_bank"][
                    "target_sample_rate_hz"
                ]
            ),
            float(generic_settings["sta_window_s"]),
            float(generic_settings["lta_window_s"]),
        )
    )
    generic_indices, generic_trigger_epoch_s = (
        generic_scan_indices_and_times(
            request_start,
            generic_station_matrix_full.shape[1],
            development,
        )
    )
    generic_station_matrix = generic_station_matrix_full[
        :, generic_indices
    ].astype(np.float32)
    generic_null_maxima = null_coincidence_maxima(
        generic_station_matrix, development
    )
    generic_quantile = float(
        generic_settings["familywise_threshold_quantile"]
    )
    generic_threshold = higher_quantile(
        generic_null_maxima, generic_quantile
    )
    generic_candidates, generic_score = detect_generic_candidates(
        generic_trigger_epoch_s,
        generic_station_matrix,
        generic_threshold,
        development,
    )
    background_catalog, background_catalog_provenance = (
        obtain_background_catalog(
            project,
            development,
            force=args.force_background_catalog,
        )
    )
    associate_background_arrivals(
        generic_candidates,
        background_catalog,
        parent,
        development,
    )

    generic_association_tolerance = float(
        generic_settings["catalog_association_tolerance_s"]
    )
    _associate_catalog(
        generic_candidates,
        development_catalog,
        generic_association_tolerance,
        epoch_field="trigger_epoch_s",
    )
    generic_catalog_scores = _generic_catalog_score_rows(
        development_catalog,
        generic_trigger_epoch_s,
        generic_score,
        generic_threshold,
        generic_candidates,
        generic_association_tolerance,
    )

    injection_excluded_epochs = sorted(
        {
            *[
                float(row["origin_epoch_s"]) for row in candidates
            ],
            *[
                float(row["trigger_epoch_s"])
                for row in generic_candidates
            ],
            *[
                parse_utc(str(row["origin_time"])).timestamp()
                for row in development_catalog
            ],
        }
    )
    injection_trials, injection_positions = run_injection_recovery(
        continuous,
        request_start,
        eligible_template_waveforms,
        eligible_template_rows,
        threshold,
        generic_threshold,
        injection_excluded_epochs,
        development,
    )
    injection_summary = summarize_injection_recovery(
        injection_trials, development
    )
    injection_primary_rows = [
        row
        for row in injection_summary
        if bool(row["is_primary_acceptance_row"])
    ]
    injection_zero_control_rows = [
        row
        for row in injection_summary
        if row["population"] == "all"
        and float(row["amplitude_snr"]) == 0.0
    ]
    injection_zero_control_status = (
        "PASS"
        if len(injection_zero_control_rows) == 2
        and all(
            row["recovered_count"] == 0
            for row in injection_zero_control_rows
        )
        else "STOP"
    )

    injection_acceptance_status = (
        "PASS"
        if len(injection_primary_rows) == 2
        and injection_zero_control_status == "PASS"
        and all(
            row["acceptance_status"] == "PASS"
            for row in injection_primary_rows
        )
        else "STOP"
    )

    output = project / str(development["output"]["directory"])
    _write_csv(
        output / "background_catalog.csv",
        background_catalog,
        list(CATALOG_FIELDS) + ["source_url", "retrieved_utc"],
    )
    write_json(
        output / "background_catalog_provenance.json",
        background_catalog_provenance,
    )
    injection_trial_fields = [
        "trial_id",
        "injected_event_id",
        "injected_sequence_id",
        "injected_validation_role",
        "position_index",
        "injection_origin_time",
        "injection_origin_epoch_s",
        "amplitude_snr",
        "zero_amplitude_control",
        "injected_component_count",
        "template_self_excluded",
        "template_recovered",
        "template_peak_score",
        "template_threshold",
        "template_peak_origin_residual_s",
        "template_candidate_origin_residual_s",
        "template_best_event_id",
        "template_best_sequence_id",
        "generic_recovered",
        "generic_peak_score",
        "generic_threshold",
        "generic_peak_trigger_residual_s",
        "generic_candidate_trigger_residual_s",
    ]
    _write_csv(
        output / "injection_recovery_trials.csv",
        injection_trials,
        injection_trial_fields,
    )
    _write_csv(
        output / "injection_positions.csv",
        injection_positions,
        [
            "position_index",
            "injection_origin_time",
            "injection_origin_epoch_s",
        ],
    )
    _write_csv(
        output / "injection_recovery_summary.csv",
        injection_summary,
        [
            "detector",
            "population",
            "amplitude_snr",
            "trial_count",
            "recovered_count",
            "recovery_fraction",
            "wilson_95_low",
            "wilson_95_high",
            "is_primary_acceptance_row",
            "minimum_primary_recovery_fraction",
            "acceptance_status",
        ],
    )
    _write_csv(
        output / "source_availability.csv",
        source_rows,
        ["source_name", "network", "status", "trace_count", "error"],
    )
    trace_fields = [
        "trace_id",
        "status",
        "reason",
        "maximum_positive_gap_s",
        "native_sample_rate_hz",
        "target_sample_rate_hz",
        "sample_count",
        "request_start_utc",
        "request_end_utc",
    ]
    _write_csv(output / "continuous_trace_qc.csv", trace_rows, trace_fields)
    template_fields = [
        "event_id",
        "origin_time",
        "sequence_id",
        "validation_role",
        "usable_component_count",
        "usable_station_count",
        "median_template_trace_snr",
        "scored_component_count",
        "scored_station_count",
        "status",
        "reason",
    ]
    _write_csv(
        output / "template_inventory.csv", template_rows, template_fields
    )
    null_rows = [
        {"replicate": index + 1, "bank_maximum_score": value}
        for index, value in enumerate(null_maxima)
    ]
    _write_csv(
        output / "null_bank_maxima.csv",
        null_rows,
        ["replicate", "bank_maximum_score"],
    )
    candidate_fields = [
        "candidate_id",
        "origin_time",
        "origin_epoch_s",
        "bank_score",
        "threshold",
        "best_template_event_id",
        "best_template_sequence_id",
        "best_template_score",
        "best_template_station_count",
        "station_support_count_at_0p2",
        "candidate_generation_label",
        "catalog_association_scope",
        "nearest_catalog_event_id",
        "nearest_catalog_origin_time",
        "catalog_time_residual_s",
        "catalog_association",
    ]
    _write_csv(
        output / "candidate_detections.csv",
        candidates,
        candidate_fields,
    )
    catalog_fields = [
        "event_id",
        "origin_time",
        "magnitude",
        "peak_score_time_within_tolerance",
        "peak_score_time_residual_s",
        "peak_bank_score_within_tolerance",
        "threshold",
        "best_template_event_id",
        "best_template_sequence_id",
        "candidate_within_tolerance",
    ]
    _write_csv(
        output / "development_catalog_scores.csv",
        catalog_scores,
        catalog_fields,
    )

    generic_null_rows = [
        {
            "replicate": index + 1,
            "generic_maximum_coincidence_score": value,
        }
        for index, value in enumerate(generic_null_maxima)
    ]
    _write_csv(
        output / "generic_null_maxima.csv",
        generic_null_rows,
        ["replicate", "generic_maximum_coincidence_score"],
    )
    generic_candidate_fields = [
        "candidate_id",
        "trigger_time",
        "trigger_epoch_s",
        "coincidence_score",
        "threshold",
        "coincidence_station_count",
        "station_support_count_at_declared_ratio",
        "station_characteristic_support_threshold",
        "candidate_generation_label",
        "catalog_association_scope",
        "nearest_catalog_event_id",
        "nearest_catalog_origin_time",
        "catalog_time_residual_s",
        "catalog_association",
        "background_catalog_association",
        "background_catalog_event_id",
        "background_catalog_origin_time",
        "background_catalog_location_name",
        "background_catalog_magnitude",
        "background_catalog_horizontal_distance_km",
        "background_catalog_path_distance_km",
        "background_catalog_observed_delay_s",
        "background_catalog_nominal_arrival_s",
        "background_catalog_nominal_timing_residual_s",
        "background_catalog_plausible_match_count",
    ]
    _write_csv(
        output / "generic_candidate_detections.csv",
        generic_candidates,
        generic_candidate_fields,
    )
    generic_catalog_fields = [
        "event_id",
        "origin_time",
        "magnitude",
        "peak_trigger_time_within_tolerance",
        "peak_trigger_time_residual_s",
        "peak_generic_score_within_tolerance",
        "threshold",
        "candidate_within_tolerance",
    ]
    _write_csv(
        output / "generic_development_catalog_scores.csv",
        generic_catalog_scores,
        generic_catalog_fields,
    )

    preview_step = max(
        1,
        int(
            round(
                float(development["output"]["preview_sample_interval_s"])
                * float(
                    development["repeater_template_bank"][
                        "score_sample_rate_hz"
                    ]
                )
            )
        ),
    )
    preview_rows = []
    for index in range(0, len(origin_epoch_s), preview_step):
        template_index = int(best_template[index])
        preview_rows.append(
            {
                "origin_time": iso_utc(float(origin_epoch_s[index])),
                "bank_score": float(bank_score[index]),
                "threshold": threshold,
                "best_template_event_id": str(
                    eligible_template_rows[template_index]["event_id"]
                ),
                "best_template_sequence_id": str(
                    eligible_template_rows[template_index]["sequence_id"]
                ),
            }
        )
    _write_csv(
        output / "score_preview.csv",
        preview_rows,
        [
            "origin_time",
            "bank_score",
            "threshold",
            "best_template_event_id",
            "best_template_sequence_id",
        ],
    )

    generic_preview_step = max(
        1,
        int(
            round(
                float(development["output"]["preview_sample_interval_s"])
                * float(generic_settings["score_sample_rate_hz"])
            )
        ),
    )
    generic_preview_rows = [
        {
            "trigger_time": iso_utc(float(generic_trigger_epoch_s[index])),
            "coincidence_score": float(generic_score[index]),
            "threshold": generic_threshold,
        }
        for index in range(
            0, len(generic_trigger_epoch_s), generic_preview_step
        )
    ]
    _write_csv(
        output / "generic_score_preview.csv",
        generic_preview_rows,
        ["trigger_time", "coincidence_score", "threshold"],
    )

    full_cache = project / str(
        development["output"]["full_score_cache"]
    )
    full_cache.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        full_cache,
        origin_epoch_s=origin_epoch_s,
        bank_score=bank_score,
        best_template_index=best_template,
        template_scores=template_scores,
        template_event_ids=np.asarray(
            [row["event_id"] for row in eligible_template_rows]
        ),
        generic_trigger_epoch_s=generic_trigger_epoch_s,
        generic_score=generic_score,
        generic_station_names=np.asarray(
            generic_station_names
        ),
    )

    model_path = (
        project
        / "outputs"
        / "incremental_value"
        / "network_baseline"
        / "network_model_frozen.json"
    )
    detected_catalog_count = sum(
        bool(row["candidate_within_tolerance"])
        for row in catalog_scores
    )
    generic_detected_catalog_count = sum(
        bool(row["candidate_within_tolerance"])
        for row in generic_catalog_scores
    )
    background_associated_count = sum(
        row["background_catalog_association"]
        == "physically_plausible_known_event_arrival"
        for row in generic_candidates
    )
    status = {
        "status": CONDITIONAL,
        "stage": "network_detection_and_injection_development_complete",
        "freeze_status": "DEVELOPMENT_ONLY_NOT_FROZEN",
        "overall_network_detector_status": (
            "READY_TO_FREEZE_BLIND_UNION_RULES"
            if injection_acceptance_status == "PASS"
            else "STOP_INJECTION_RECOVERY_ACCEPTANCE_FAILED"
        ),
        "development_interval_start_utc": development["interval"][
            "start_utc"
        ],
        "development_interval_end_utc": development["interval"]["end_utc"],
        "development_interval_duration_s": (
            float(interval_end - interval_start)
        ),
        "continuous_source_available_count": sum(
            row["status"] == "available" for row in source_rows
        ),
        "continuous_trace_usable_count": len(continuous),
        "historical_model_eligible_event_count": len(eligible_events),
        "template_bank_event_count": len(eligible_template_rows),
        "template_bank_event_ids": [
            row["event_id"] for row in eligible_template_rows
        ],
        "template_null_replicate_count": len(null_maxima),
        "template_null_familywise_threshold_quantile": quantile,
        "template_detection_threshold": threshold,
        "template_observed_maximum_bank_score": float(np.max(bank_score)),
        "template_candidate_count": len(candidates),
        "routine_catalog_event_count": len(development_catalog),
        "template_routine_catalog_events_with_candidate": (
            detected_catalog_count
        ),
        "routine_catalog_event_ids": [
            row["event_id"] for row in development_catalog
        ],
        "template_candidate_ids": [
            row["candidate_id"] for row in candidates
        ],
        "generic_network_trigger_status": (
            "DEVELOPMENT_COMPLETE_NOT_FROZEN"
        ),
        "generic_config_status": generic_settings["status"],
        "generic_component_count": generic_components,
        "generic_station_count": len(generic_station_names),
        "generic_station_names": generic_station_names,
        "generic_null_replicate_count": len(generic_null_maxima),
        "generic_null_familywise_threshold_quantile": generic_quantile,
        "generic_detection_threshold": generic_threshold,
        "generic_observed_maximum_score": float(np.max(generic_score)),
        "generic_candidate_count": len(generic_candidates),
        "generic_routine_catalog_events_with_candidate": (
            generic_detected_catalog_count
        ),
        "generic_candidate_ids": [
            row["candidate_id"] for row in generic_candidates
        ],
        "background_catalog_event_count": len(background_catalog),
        "background_catalog_retrieval_status": (
            background_catalog_provenance["retrieval_status"]
        ),
        "background_catalog_sha256": background_catalog_provenance[
            "cache_sha256"
        ],
        "background_catalog_query_url": background_catalog_provenance[
            "query_url"
        ],
        "generic_candidate_background_catalog_associated_count": (
            background_associated_count
        ),
        "generic_candidate_background_catalog_unassociated_count": (
            len(generic_candidates) - background_associated_count
        ),
        "injection_recovery_status": development[
            "injection_recovery"
        ]["status"],
        "injection_trial_count": len(injection_trials),
        "injection_position_count": len(injection_positions),
        "injection_amplitude_snr_levels": development[
            "injection_recovery"
        ]["amplitude_snr_levels"],
        "injection_template_self_excluded": development[
            "injection_recovery"
        ]["template_detector_excludes_injected_event"],
        "injection_detector_thresholds_fixed": development[
            "injection_recovery"
        ]["detector_thresholds_fixed_before_injection"],
        "injection_primary_acceptance_rows": injection_primary_rows,
        "injection_zero_control_rows": injection_zero_control_rows,
        "injection_zero_control_status": injection_zero_control_status,
        "injection_acceptance_status": injection_acceptance_status,
        "injection_interpretation": development[
            "injection_recovery"
        ]["interpretation"],
        "network_only_stage_das_waveforms_opened": 0,
        "heldout_intervals_opened": 0,
        "template_threshold_interpretation": development[
            "null_calibration"
        ]["interpretation"],
        "generic_threshold_interpretation": generic_settings[
            "interpretation"
        ],
        "parent_config_sha256": parent["_config_sha256"],
        "development_config_sha256": development["_config_sha256"],
        "frozen_network_model_sha256": sha256_file(model_path),
        "full_score_cache_path": str(full_cache),
        "full_score_cache_sha256": sha256_file(full_cache),
        "generated_utc": utc_now(),
    }
    write_json(output / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
