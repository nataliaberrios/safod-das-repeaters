"""Executable source-physics formulas with conservative decision gates.

This module provides machinery, not a current SAFOD stress-drop result.  A Brune
ratio is fit only over a caller-supplied usable band established from signal and
noise.  The EGF corner frequency is fixed because fitting two corners and a level
from a narrow ratio is commonly non-identifiable.  Model/EGF sensitivity and
synthetic recovery must be evaluated by the caller before reporting stress drop.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from scipy.optimize import least_squares
from scipy.signal.windows import dpss

from .common import PASS, STOP


@dataclass
class CornerFit:
    status: str
    target_corner_hz: Optional[float]
    target_corner_std_hz: Optional[float]
    omega_ratio: Optional[float]
    usable_min_hz: float
    usable_max_hz: float
    egf_corner_hz: float
    point_count: int
    reduced_log_misfit: Optional[float]
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def multitaper_amplitude(
    data: np.ndarray,
    sample_rate_hz: float,
    time_bandwidth: float = 3.5,
    taper_count: int = 5,
) -> Tuple[np.ndarray, np.ndarray]:
    """Return a DPSS multitaper RMS amplitude spectrum for a 1-D window."""

    values = np.asarray(data, dtype=float)
    if values.ndim != 1 or values.size < 32:
        raise ValueError("multitaper input must be a 1-D array with >=32 samples")
    values = values - np.mean(values)
    tapers = dpss(values.size, time_bandwidth, Kmax=taper_count, sym=False)
    spectra = np.fft.rfft(tapers * values[None, :], axis=1)
    amplitude = np.sqrt(np.mean(np.abs(spectra) ** 2, axis=0))
    frequency = np.fft.rfftfreq(values.size, d=1.0 / float(sample_rate_hz))
    return frequency, amplitude


def brune_ratio(
    frequency_hz: np.ndarray,
    omega_ratio: float,
    target_corner_hz: float,
    egf_corner_hz: float,
) -> np.ndarray:
    """Circular Brune target/EGF displacement spectral ratio."""

    frequency = np.asarray(frequency_hz, dtype=float)
    return float(omega_ratio) * (
        1.0 + (frequency / float(egf_corner_hz)) ** 2
    ) / (1.0 + (frequency / float(target_corner_hz)) ** 2)


def fit_brune_ratio_fixed_egf(
    frequency_hz: np.ndarray,
    observed_ratio: np.ndarray,
    usable_mask: np.ndarray,
    egf_corner_hz: float,
    minimum_points: int = 20,
    edge_factor: float = 1.25,
) -> CornerFit:
    """Fit level and target corner, then enforce an in-band resolution gate."""

    frequency = np.asarray(frequency_hz, dtype=float)
    ratio = np.asarray(observed_ratio, dtype=float)
    mask = (
        np.asarray(usable_mask, dtype=bool)
        & np.isfinite(frequency)
        & np.isfinite(ratio)
        & (frequency > 0.0)
        & (ratio > 0.0)
    )
    if np.count_nonzero(mask) < minimum_points:
        return CornerFit(
            STOP,
            None,
            None,
            None,
            float(np.min(frequency[mask])) if np.any(mask) else float("nan"),
            float(np.max(frequency[mask])) if np.any(mask) else float("nan"),
            float(egf_corner_hz),
            int(np.count_nonzero(mask)),
            None,
            "too few usable spectral points",
        )
    used_frequency = frequency[mask]
    used_log_ratio = np.log(ratio[mask])
    usable_min = float(np.min(used_frequency))
    usable_max = float(np.max(used_frequency))

    def residual(parameters: np.ndarray) -> np.ndarray:
        omega = np.exp(parameters[0])
        corner = np.exp(parameters[1])
        prediction = brune_ratio(used_frequency, omega, corner, egf_corner_hz)
        return np.log(prediction) - used_log_ratio

    initial = np.log([float(np.median(ratio[mask])), np.sqrt(usable_min * usable_max)])
    lower = np.log([1.0e-6, usable_min / 10.0])
    upper = np.log([1.0e6, usable_max * 10.0])
    result = least_squares(residual, initial, bounds=(lower, upper), method="trf")
    omega, corner = np.exp(result.x)
    degrees = max(1, len(used_frequency) - len(result.x))
    variance = float(np.sum(result.fun ** 2) / degrees)
    corner_std = None
    try:
        covariance = variance * np.linalg.inv(result.jac.T @ result.jac)
        corner_std = float(corner * np.sqrt(max(0.0, covariance[1, 1])))
    except np.linalg.LinAlgError:
        pass
    in_band = bool(
        corner >= edge_factor * usable_min
        and corner <= usable_max / edge_factor
        and corner_std is not None
        and corner_std / corner <= 0.5
    )
    return CornerFit(
        PASS if in_band else STOP,
        float(corner),
        corner_std,
        float(omega),
        usable_min,
        usable_max,
        float(egf_corner_hz),
        int(np.count_nonzero(mask)),
        variance,
        "resolved inside usable band" if in_band else "corner is edge-limited or uncertainty exceeds 50%",
    )


def relative_stress_drop(
    moment_ratio: float, target_corner_hz: float, reference_corner_hz: float
) -> float:
    """Return Brune relative stress drop, with no absolute material constant."""

    if min(moment_ratio, target_corner_hz, reference_corner_hz) <= 0.0:
        raise ValueError("moments and corner frequencies must be positive")
    return float(moment_ratio) * (
        float(target_corner_hz) / float(reference_corner_hz)
    ) ** 3


def stress_drop_gate(
    corner_fit: CornerFit,
    synthetic_fractional_error: float,
    egf_model_spread_fraction: float,
    maximum_fractional_error: float = 0.5,
) -> Dict[str, Any]:
    """Combine spectral, synthetic-recovery, and EGF/model sensitivity gates."""

    passed = bool(
        corner_fit.status == PASS
        and synthetic_fractional_error <= maximum_fractional_error
        and egf_model_spread_fraction <= maximum_fractional_error
    )
    return {
        "status": PASS if passed else STOP,
        "corner_gate": corner_fit.status,
        "synthetic_fractional_error": float(synthetic_fractional_error),
        "egf_model_spread_fraction": float(egf_model_spread_fraction),
        "maximum_fractional_error": float(maximum_fractional_error),
        "reason": (
            "all source-physics gates passed"
            if passed
            else "corner, synthetic recovery, or EGF/model sensitivity failed"
        ),
    }

