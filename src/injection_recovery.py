"""Leakage-safe injection recovery on real development-interval noise.

Historical event templates are amplitude-normalized per component and injected
into independently selected quiet windows.  The repeater-template detector
must recover each injected waveform with that exact event removed from its
bank.  The generic detector receives the same injections.  These experiments
measure algorithmic waveform-shape recovery, not magnitude completeness.
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Dict, List, Mapping, Sequence, Tuple

import numpy as np

from .common import iso_utc, parse_utc
from .network_continuous_detection import (
    detect_candidates,
    detect_generic_candidates,
    station_energy_ratio_matrix,
    template_station_score_matrix,
)


def select_injection_positions(
    development: Mapping[str, Any],
    excluded_epoch_s: Sequence[float],
) -> np.ndarray:
    """Select deterministic, separated, event-avoiding injection origins."""

    settings = development["injection_recovery"]
    local_window = list(map(float, settings["local_window_s"]))
    start_s = parse_utc(development["interval"]["start_utc"]).timestamp()
    end_s = parse_utc(development["interval"]["end_utc"]).timestamp()
    lower = start_s - local_window[0]
    upper = end_s - local_window[1]
    grid_s = float(settings["position_grid_interval_s"])
    first = math.ceil(lower / grid_s) * grid_s
    grid = np.arange(first, upper, grid_s, dtype=np.float64)
    exclusion = float(settings["event_exclusion_half_width_s"])
    for epoch in excluded_epoch_s:
        grid = grid[np.abs(grid - float(epoch)) > exclusion]
    if grid.size == 0:
        raise RuntimeError("no clean injection position remains")

    count = int(settings["noise_position_count"])
    minimum_separation = float(settings["minimum_position_separation_s"])
    rng = np.random.default_rng(int(settings["random_seed"]))
    selected: List[float] = []
    for index in rng.permutation(len(grid)):
        candidate = float(grid[index])
        if all(
            abs(candidate - existing) >= minimum_separation
            for existing in selected
        ):
            selected.append(candidate)
            if len(selected) == count:
                break
    if len(selected) != count:
        raise RuntimeError("could not select declared injection positions")
    return np.asarray(sorted(selected), dtype=np.float64)


def _extract_local(
    continuous: Mapping[str, np.ndarray],
    request_start_s: float,
    origin_s: float,
    development: Mapping[str, Any],
) -> Tuple[Dict[str, np.ndarray], float]:
    settings = development["injection_recovery"]
    sample_rate = float(
        development["repeater_template_bank"]["target_sample_rate_hz"]
    )
    local_window = list(map(float, settings["local_window_s"]))
    local_start = float(origin_s) + local_window[0]
    local_count = int(
        round((local_window[1] - local_window[0]) * sample_rate)
    )
    first = int(round((local_start - request_start_s) * sample_rate))
    last = first + local_count
    if first < 0:
        raise ValueError("injection window begins before continuous data")
    extracted: Dict[str, np.ndarray] = {}
    for key, values in continuous.items():
        if last > len(values):
            raise ValueError("injection window ends after continuous data")
        extracted[key] = np.asarray(
            values[first:last], dtype=np.float32
        ).copy()
    return extracted, local_start


def _inject_template(
    local: Dict[str, np.ndarray],
    template: Mapping[str, np.ndarray],
    amplitude_snr: float,
    development: Mapping[str, Any],
) -> int:
    settings = development["injection_recovery"]
    bank = development["repeater_template_bank"]
    sample_rate = float(bank["target_sample_rate_hz"])
    local_window = list(map(float, settings["local_window_s"]))
    signal_window = list(map(float, bank["template_window_s"]))
    noise_window = list(map(float, settings["noise_window_s"]))
    origin_index = int(round(-local_window[0] * sample_rate))
    signal_first = origin_index + int(
        round(signal_window[0] * sample_rate)
    )
    noise_first = origin_index + int(
        round(noise_window[0] * sample_rate)
    )
    noise_last = origin_index + int(
        round(noise_window[1] * sample_rate)
    )

    injected_count = 0
    for key in sorted(set(local).intersection(template)):
        pattern = np.asarray(template[key], dtype=np.float64)
        signal_last = signal_first + len(pattern)
        if (
            noise_first < 0
            or noise_last > len(local[key])
            or signal_first < 0
            or signal_last > len(local[key])
        ):
            raise ValueError("declared injection window exceeds local data")
        noise = np.asarray(
            local[key][noise_first:noise_last], dtype=np.float64
        )
        noise_rms = float(np.sqrt(np.mean(noise ** 2)))
        template_rms = float(np.sqrt(np.mean(pattern ** 2)))
        if (
            not np.isfinite(noise_rms)
            or not np.isfinite(template_rms)
            or noise_rms <= np.finfo(float).eps
            or template_rms <= np.finfo(float).eps
        ):
            continue
        scale = float(amplitude_snr) * noise_rms / template_rms
        local[key][signal_first:signal_last] += (
            pattern * scale
        ).astype(np.float32)
        injected_count += 1
    return injected_count


def _score_step(development: Mapping[str, Any], section: str) -> int:
    sample_rate = float(
        development["repeater_template_bank"]["target_sample_rate_hz"]
    )
    score_rate = float(development[section]["score_sample_rate_hz"])
    ratio = sample_rate / score_rate
    step = int(round(ratio))
    if not math.isclose(ratio, step, rel_tol=0.0, abs_tol=1.0e-9):
        raise ValueError("target and score rates are not integer-related")
    return step


def _template_trial(
    local: Mapping[str, np.ndarray],
    local_start_s: float,
    origin_s: float,
    injected_event_id: str,
    templates: Sequence[Mapping[str, np.ndarray]],
    template_rows: Sequence[Mapping[str, Any]],
    threshold: float,
    development: Mapping[str, Any],
) -> Dict[str, Any]:
    station_matrices: List[np.ndarray] = []
    station_names: List[List[str]] = []
    detection_rows: List[Mapping[str, Any]] = []
    step = _score_step(development, "repeater_template_bank")
    curve_indices: np.ndarray | None = None
    for template, row in zip(templates, template_rows):
        if str(row["event_id"]) == str(injected_event_id):
            continue
        matrix, names, _ = template_station_score_matrix(local, template)
        if matrix.size == 0:
            continue
        if curve_indices is None:
            curve_indices = np.arange(
                0, matrix.shape[1], step, dtype=np.int64
            )
        station_matrices.append(
            matrix[:, curve_indices].astype(np.float32)
        )
        station_names.append(names)
        detection_rows.append(row)
    if not station_matrices or curve_indices is None:
        raise RuntimeError("leave-one-event-out template bank is empty")

    template_scores = np.stack(
        [
            np.mean(matrix, axis=0, dtype=np.float64).astype(np.float32)
            for matrix in station_matrices
        ],
        axis=0,
    )
    sample_rate = float(
        development["repeater_template_bank"]["target_sample_rate_hz"]
    )
    template_start = float(
        development["repeater_template_bank"]["template_window_s"][0]
    )
    candidate_origins = (
        float(local_start_s)
        + curve_indices / sample_rate
        - template_start
    )
    candidates, bank_score, best_template = detect_candidates(
        candidate_origins,
        template_scores,
        station_matrices,
        station_names,
        detection_rows,
        threshold,
        development,
    )
    tolerance = float(
        development["injection_recovery"]["template_peak_tolerance_s"]
    )
    local_indices = np.flatnonzero(
        np.abs(candidate_origins - float(origin_s)) <= tolerance
    )
    if local_indices.size == 0:
        raise RuntimeError("template injection has no evaluation samples")
    peak = int(
        local_indices[np.nanargmax(bank_score[local_indices])]
    )
    residuals = [
        float(row["origin_epoch_s"]) - float(origin_s)
        for row in candidates
        if abs(float(row["origin_epoch_s"]) - float(origin_s))
        <= tolerance
    ]
    best_index = int(best_template[peak])
    return {
        "template_recovered": bool(residuals),
        "template_peak_score": float(bank_score[peak]),
        "template_threshold": float(threshold),
        "template_peak_origin_residual_s": float(
            candidate_origins[peak] - float(origin_s)
        ),
        "template_candidate_origin_residual_s": (
            min(residuals, key=abs) if residuals else ""
        ),
        "template_best_event_id": str(
            detection_rows[best_index]["event_id"]
        ),
        "template_best_sequence_id": str(
            detection_rows[best_index]["sequence_id"]
        ),
    }


def _generic_trial(
    local: Mapping[str, np.ndarray],
    local_start_s: float,
    origin_s: float,
    threshold: float,
    development: Mapping[str, Any],
) -> Dict[str, Any]:
    settings = development["generic_network_trigger"]
    sample_rate = float(
        development["repeater_template_bank"]["target_sample_rate_hz"]
    )
    matrix_full, _, _ = station_energy_ratio_matrix(
        local,
        sample_rate,
        float(settings["sta_window_s"]),
        float(settings["lta_window_s"]),
    )
    step = _score_step(development, "generic_network_trigger")
    indices = np.arange(0, matrix_full.shape[1], step, dtype=np.int64)
    finite = np.all(np.isfinite(matrix_full[:, indices]), axis=0)
    indices = indices[finite]
    matrix = matrix_full[:, indices].astype(np.float32)
    trigger_epochs = float(local_start_s) + indices / sample_rate
    candidates, score = detect_generic_candidates(
        trigger_epochs,
        matrix,
        threshold,
        development,
    )
    search_window = list(
        map(
            float,
            development["injection_recovery"][
                "generic_peak_search_window_s"
            ],
        )
    )
    first = float(origin_s) + search_window[0]
    last = float(origin_s) + search_window[1]
    local_indices = np.flatnonzero(
        (trigger_epochs >= first) & (trigger_epochs <= last)
    )
    if local_indices.size == 0:
        raise RuntimeError("generic injection has no evaluation samples")
    peak = int(local_indices[np.nanargmax(score[local_indices])])
    residuals = [
        float(row["trigger_epoch_s"]) - float(origin_s)
        for row in candidates
        if first <= float(row["trigger_epoch_s"]) <= last
    ]
    return {
        "generic_recovered": bool(residuals),
        "generic_peak_score": float(score[peak]),
        "generic_threshold": float(threshold),
        "generic_peak_trigger_residual_s": float(
            trigger_epochs[peak] - float(origin_s)
        ),
        "generic_candidate_trigger_residual_s": (
            min(residuals, key=abs) if residuals else ""
        ),
    }


def run_injection_recovery(
    continuous: Mapping[str, np.ndarray],
    request_start_s: float,
    templates: Sequence[Mapping[str, np.ndarray]],
    template_rows: Sequence[Mapping[str, Any]],
    template_threshold: float,
    generic_threshold: float,
    excluded_epoch_s: Sequence[float],
    development: Mapping[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Run all declared leave-one-event-out injections."""

    if len(templates) != len(template_rows):
        raise ValueError("template waveforms and rows are not aligned")
    settings = development["injection_recovery"]
    positions = select_injection_positions(
        development, excluded_epoch_s
    )
    position_rows = [
        {
            "position_index": index + 1,
            "injection_origin_time": iso_utc(float(epoch)),
            "injection_origin_epoch_s": float(epoch),
        }
        for index, epoch in enumerate(positions)
    ]
    base_by_position = {
        float(origin): _extract_local(
            continuous, request_start_s, float(origin), development
        )
        for origin in positions
    }

    trials: List[Dict[str, Any]] = []
    levels = list(map(float, settings["amplitude_snr_levels"]))
    for template, event in zip(templates, template_rows):
        for position_index, origin in enumerate(positions, start=1):
            base, local_start = base_by_position[float(origin)]
            for amplitude_snr in levels:
                local = {
                    key: values.copy() for key, values in base.items()
                }
                injected_count = _inject_template(
                    local, template, amplitude_snr, development
                )
                if injected_count == 0:
                    raise RuntimeError("injection used no component")
                trial = {
                    "trial_id": "network_injection_{:05d}".format(
                        len(trials) + 1
                    ),
                    "injected_event_id": str(event["event_id"]),
                    "injected_sequence_id": str(event["sequence_id"]),
                    "injected_validation_role": str(
                        event["validation_role"]
                    ),
                    "position_index": position_index,
                    "injection_origin_time": iso_utc(float(origin)),
                    "injection_origin_epoch_s": float(origin),
                    "amplitude_snr": amplitude_snr,
                    "zero_amplitude_control": amplitude_snr == 0.0,
                    "injected_component_count": injected_count,
                    "template_self_excluded": True,
                }
                trial.update(
                    _template_trial(
                        local,
                        local_start,
                        float(origin),
                        str(event["event_id"]),
                        templates,
                        template_rows,
                        template_threshold,
                        development,
                    )
                )
                trial.update(
                    _generic_trial(
                        local,
                        local_start,
                        float(origin),
                        generic_threshold,
                        development,
                    )
                )
                trials.append(trial)
    return trials, position_rows


def _wilson_interval(successes: int, count: int) -> Tuple[float, float]:
    if count <= 0:
        return float("nan"), float("nan")
    z = 1.959963984540054
    fraction = successes / float(count)
    denominator = 1.0 + z ** 2 / count
    center = (fraction + z ** 2 / (2.0 * count)) / denominator
    radius = (
        z
        * math.sqrt(
            fraction * (1.0 - fraction) / count
            + z ** 2 / (4.0 * count ** 2)
        )
        / denominator
    )
    return center - radius, center + radius


def summarize_injection_recovery(
    trials: Sequence[Mapping[str, Any]],
    development: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """Summarize both detectors by amplitude and validation population."""

    settings = development["injection_recovery"]
    primary_role = str(settings["primary_acceptance_validation_role"])
    roles = sorted(
        {str(row["injected_validation_role"]) for row in trials}
    )
    populations = ["all"] + roles
    grouped: Dict[
        Tuple[str, str, float], List[bool]
    ] = defaultdict(list)
    for row in trials:
        role = str(row["injected_validation_role"])
        level = float(row["amplitude_snr"])
        for population in ("all", role):
            grouped[("template_bank", population, level)].append(
                bool(row["template_recovered"])
            )
            grouped[("generic_trigger", population, level)].append(
                bool(row["generic_recovered"])
            )

    reference = float(settings["reference_amplitude_snr"])
    minimum = float(settings["minimum_primary_recovery_fraction"])
    rows: List[Dict[str, Any]] = []
    levels = sorted({float(row["amplitude_snr"]) for row in trials})
    for detector in ("template_bank", "generic_trigger"):
        for population in populations:
            for level in levels:
                values = grouped[(detector, population, level)]
                recovered = sum(values)
                fraction = recovered / float(len(values))
                low, high = _wilson_interval(recovered, len(values))
                is_acceptance = (
                    population == primary_role
                    and math.isclose(level, reference)
                )
                rows.append(
                    {
                        "detector": detector,
                        "population": population,
                        "amplitude_snr": level,
                        "trial_count": len(values),
                        "recovered_count": recovered,
                        "recovery_fraction": fraction,
                        "wilson_95_low": low,
                        "wilson_95_high": high,
                        "is_primary_acceptance_row": is_acceptance,
                        "minimum_primary_recovery_fraction": (
                            minimum if is_acceptance else ""
                        ),
                        "acceptance_status": (
                            "PASS"
                            if is_acceptance and fraction >= minimum
                            else "STOP"
                            if is_acceptance
                            else "diagnostic"
                        ),
                    }
                )
    return rows
