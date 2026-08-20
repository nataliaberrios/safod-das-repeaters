"""Regional catalog adjudication for generic network triggers.

This module never generates candidates.  It asks only whether an already
generated generic trigger has a physically plausible arrival from a broader
official NCEDC event catalog, preventing regional earthquakes outside the
Parkfield target box from being mislabeled as uncataloged local events.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .catalog import (
    catalog_query_url,
    fetch_catalog,
    local_offsets_m,
    read_catalog,
    write_catalog,
)
from .common import iso_utc, parse_utc, sha256_file


def _query_config(
    development: Mapping[str, Any],
) -> Dict[str, Dict[str, Any]]:
    settings = development["background_catalog"]
    start_s = parse_utc(
        development["interval"]["start_utc"]
    ).timestamp() - float(settings["origin_time_padding_before_s"])
    end_s = parse_utc(
        development["interval"]["end_utc"]
    ).timestamp() + float(settings["origin_time_padding_after_s"])
    return {
        "catalog": {
            "event_service": settings["event_service"],
            "catalog": settings["catalog"],
            "starttime": iso_utc(start_s),
            "endtime": iso_utc(end_s),
            "bounds": dict(settings["bounds"]),
        }
    }


def obtain_background_catalog(
    project: Path,
    development: Mapping[str, Any],
    force: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load or fetch the declared regional catalog and return provenance."""

    settings = development["background_catalog"]
    cache = project / str(settings["cache_path"])
    query = _query_config(development)
    if cache.exists() and cache.stat().st_size > 0 and not force:
        events = read_catalog(cache)
        retrieval_status = "cache_hit"
    else:
        events = fetch_catalog(query)
        write_catalog(cache, events)
        retrieval_status = "downloaded"
    if not events:
        raise RuntimeError("background catalog is empty")
    provenance = {
        "status": settings["status"],
        "retrieval_status": retrieval_status,
        "catalog_name": settings["catalog"],
        "catalog_event_count": len(events),
        "query_url": catalog_query_url(query),
        "cache_path": str(cache),
        "cache_sha256": sha256_file(cache),
        "retrieved_utc": str(events[0].get("retrieved_utc", "")),
        "association_role": settings["association_role"],
    }
    return events, provenance


def associate_background_arrivals(
    candidates: List[Dict[str, Any]],
    catalog: Sequence[Mapping[str, Any]],
    parent: Mapping[str, Any],
    development: Mapping[str, Any],
    epoch_field: str = "trigger_epoch_s",
) -> None:
    """Attach the best physically plausible catalog arrival to each trigger."""

    settings = development["background_catalog"]
    neighborhood = parent["family_neighborhood"]
    reference = {
        "latitude": float(neighborhood["reference_latitude"]),
        "longitude": float(neighborhood["reference_longitude"]),
        "depth_km": float(settings["array_reference_depth_km"]),
    }
    minimum_velocity = float(
        settings["minimum_plausible_phase_velocity_km_s"]
    )
    maximum_velocity = float(
        settings["maximum_plausible_phase_velocity_km_s"]
    )
    nominal_velocity = float(
        settings["nominal_ranking_phase_velocity_km_s"]
    )
    if not 0.0 < minimum_velocity <= nominal_velocity <= maximum_velocity:
        raise ValueError("invalid background-catalog phase velocities")
    early_slack = float(settings["early_arrival_slack_s"])
    late_slack = float(settings["late_arrival_slack_s"])

    for candidate in candidates:
        trigger_s = float(candidate[epoch_field])
        plausible: List[Tuple[float, Dict[str, Any]]] = []
        for event in catalog:
            origin_s = parse_utc(str(event["origin_time"])).timestamp()
            delay_s = trigger_s - origin_s
            offsets = local_offsets_m(event, reference)
            path_km = float(offsets["distance_3d_m"]) / 1000.0
            earliest_s = path_km / maximum_velocity - early_slack
            latest_s = path_km / minimum_velocity + late_slack
            nominal_s = path_km / nominal_velocity
            if earliest_s <= delay_s <= latest_s:
                row = {
                    "event": event,
                    "horizontal_distance_km": (
                        float(offsets["horizontal_distance_m"]) / 1000.0
                    ),
                    "path_distance_km": path_km,
                    "observed_delay_s": delay_s,
                    "earliest_plausible_arrival_s": earliest_s,
                    "latest_plausible_arrival_s": latest_s,
                    "nominal_arrival_s": nominal_s,
                    "nominal_timing_residual_s": delay_s - nominal_s,
                }
                plausible.append(
                    (abs(float(row["nominal_timing_residual_s"])), row)
                )

        if not plausible:
            candidate.update(
                {
                    "background_catalog_association": "none",
                    "background_catalog_event_id": "",
                    "background_catalog_origin_time": "",
                    "background_catalog_location_name": "",
                    "background_catalog_magnitude": "",
                    "background_catalog_horizontal_distance_km": "",
                    "background_catalog_path_distance_km": "",
                    "background_catalog_observed_delay_s": "",
                    "background_catalog_nominal_arrival_s": "",
                    "background_catalog_nominal_timing_residual_s": "",
                    "background_catalog_plausible_match_count": 0,
                }
            )
            continue

        _, best = min(plausible, key=lambda item: item[0])
        event = best["event"]
        candidate.update(
            {
                "background_catalog_association": (
                    "physically_plausible_known_event_arrival"
                ),
                "background_catalog_event_id": str(event["event_id"]),
                "background_catalog_origin_time": str(event["origin_time"]),
                "background_catalog_location_name": str(
                    event.get("location_name", "")
                ),
                "background_catalog_magnitude": event.get("magnitude", ""),
                "background_catalog_horizontal_distance_km": best[
                    "horizontal_distance_km"
                ],
                "background_catalog_path_distance_km": best[
                    "path_distance_km"
                ],
                "background_catalog_observed_delay_s": best[
                    "observed_delay_s"
                ],
                "background_catalog_nominal_arrival_s": best[
                    "nominal_arrival_s"
                ],
                "background_catalog_nominal_timing_residual_s": best[
                    "nominal_timing_residual_s"
                ],
                "background_catalog_plausible_match_count": len(plausible),
            }
        )
