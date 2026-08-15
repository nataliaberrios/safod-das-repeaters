#!/usr/bin/env python
"""Fetch the DD shortlist and run a controlled HRSN similarity diagnostic.

The single 2026 seed can nominate high-similarity events, but it cannot assign
published-family membership. Historical membership labels are added only by
the exact-event-ID external-catalog crosswalk.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from .catalog import (
    check_deep_overlap,
    fetch_catalog,
    find_event,
    nominate_candidates,
    read_catalog,
    write_catalog,
)
from .common import PASS, STOP, load_config, project_root, write_json
from .coverage import write_rows
from .hrsn import compare_events, download_event, load_stream, write_metrics


def _configured_event(config: Dict[str, Any], key: str) -> Dict[str, Any]:
    event = dict(config["events"][key])
    event["origin_time"] = event.pop("origin_time")
    return event


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--refresh-catalog", action="store_true")
    parser.add_argument("--refresh-waveforms", action="store_true")
    parser.add_argument(
        "--max-family-candidates",
        type=int,
        default=38,
        help="Number of nearest DD nominees (including seed) to test; 38 is the frozen pilot shortlist.",
    )
    args = parser.parse_args()
    config = load_config(args.config)
    root = project_root()
    catalog_dir = root / "outputs" / "catalog"
    hrsn_dir = root / "outputs" / "hrsn"
    cache_dir = root / "cached_event_windows" / "hrsn"
    catalog_path = catalog_dir / "ncedc_dd_catalog.csv"
    if catalog_path.exists() and not args.refresh_catalog:
        events = read_catalog(catalog_path)
    else:
        events = fetch_catalog(config)
        write_catalog(catalog_path, events)
    shortlist = nominate_candidates(events, config)
    write_catalog(catalog_dir / "nominated_shortlist.csv", shortlist)

    configured = {
        key: _configured_event(config, key)
        for key in ("historical_candidate", "deep_template", "hard_spatial_control")
    }
    # Exact event-ID queries established the three configured rows.  Replace a
    # configured row with the bounded catalog version when present, preserving
    # operation if the hard control lies outside the shortlist bounds.
    by_id = {str(event["event_id"]): event for event in events}
    for key, event in list(configured.items()):
        configured[key] = dict(by_id.get(str(event["event_id"]), event))

    streams = {}
    for key, event in configured.items():
        path = download_event(
            event, config, cache_dir, force=args.refresh_waveforms
        )
        streams[key] = load_stream(path)

    all_metrics: List[Dict[str, Any]] = []
    pair_summaries: List[Dict[str, Any]] = []
    candidate_metrics, candidate_summary = compare_events(
        configured["deep_template"],
        configured["historical_candidate"],
        streams["deep_template"],
        streams["historical_candidate"],
        config,
        pair_name="blind_A",
    )
    control_metrics, control_summary = compare_events(
        configured["deep_template"],
        configured["hard_spatial_control"],
        streams["deep_template"],
        streams["hard_spatial_control"],
        config,
        pair_name="blind_B",
    )
    all_metrics.extend(candidate_metrics)
    all_metrics.extend(control_metrics)
    candidate_summary["revealed_role"] = "DD-nearest historical candidate"
    control_summary["revealed_role"] = "magnitude/depth-matched spatial control"
    pair_summaries.extend([candidate_summary, control_summary])

    target_control_pair_gate = bool(
        candidate_summary["decision"] == "family"
        and control_summary["decision"] == "not_family"
        and control_summary["overall_median_correlation"]
        <= float(config["hrsn"]["hard_negative_max_median_correlation"])
    )
    write_metrics(hrsn_dir / "pair_metrics.csv", all_metrics)
    write_json(
        hrsn_dir / "pair_summaries.json",
        {"pairs": pair_summaries, "config_sha256": config["_config_sha256"]},
    )

    # Score the bounded, frozen nearest-neighbour shortlist against one seed.
    # This is a nomination diagnostic, not a family catalog: a high score can
    # occur for members of distinct nearby published families.
    seed_id = str(config["catalog"]["seed_event_id"])
    seed_event = configured["deep_template"]
    seed_stream = streams["deep_template"]
    reconstruction_rows: List[Dict[str, Any]] = []
    reconstruction_metrics: List[Dict[str, Any]] = []
    tested = 0
    for nominee in shortlist:
        if str(nominee["event_id"]) == seed_id:
            reconstruction_rows.append(
                {
                    "event_id": seed_id,
                    "origin_time": seed_event["origin_time"],
                    "distance_3d_m": 0.0,
                    "magnitude": seed_event["magnitude"],
                    "decision": "seed",
                    "membership_status": "unlabeled_deep_seed",
                }
            )
            continue
        if tested >= max(0, args.max_family_candidates - 1):
            break
        tested += 1
        try:
            path = download_event(
                nominee, config, cache_dir, force=args.refresh_waveforms
            )
            nominee_stream = load_stream(path)
            metrics, summary = compare_events(
                seed_event,
                nominee,
                seed_stream,
                nominee_stream,
                config,
                pair_name="reconstruction_{}".format(nominee["event_id"]),
            )
            reconstruction_metrics.extend(metrics)
            decision = summary["decision"]
            membership = (
                "single_anchor_high_similarity"
                if decision == "family"
                else (
                    "single_anchor_dissimilar_neighbor"
                    if decision == "not_family"
                    else "insufficient_data"
                )
            )
            reconstruction_rows.append(
                {
                    "event_id": nominee["event_id"],
                    "origin_time": nominee["origin_time"],
                    "distance_3d_m": nominee["distance_3d_m"],
                    "magnitude": nominee["magnitude"],
                    "decision": decision,
                    "membership_status": membership,
                    "overall_median_correlation": summary["overall_median_correlation"],
                    "weakest_band_median_correlation": summary["weakest_band_median_correlation"],
                    "minimum_components_per_band": summary["minimum_components_per_band"],
                    "minimum_stations_per_band": summary["minimum_stations_per_band"],
                }
            )
        except Exception as exc:
            reconstruction_rows.append(
                {
                    "event_id": nominee["event_id"],
                    "origin_time": nominee["origin_time"],
                    "distance_3d_m": nominee["distance_3d_m"],
                    "magnitude": nominee["magnitude"],
                    "decision": "download_or_data_error",
                    "membership_status": "insufficient_data",
                    "error": "{}: {}".format(type(exc).__name__, exc),
                }
            )
    diagnostic_scope = "frozen_{}_event_spatial_magnitude_shortlist".format(
        min(args.max_family_candidates, len(shortlist))
    )
    for row in reconstruction_rows:
        row.update(
            {
                "catalog_source": "NCEDC_DD",
                "scoring_method": "HRSN_multistation_multiband_single_anchor_v2",
                "diagnostic_scope": diagnostic_scope,
                "catalog_status": "candidate_only_not_validated",
                "config_sha256": config["_config_sha256"],
            }
        )
    write_rows(hrsn_dir / "similarity_shortlist_v2.csv", reconstruction_rows)
    write_rows(
        hrsn_dir / "high_similarity_candidates_v2.csv",
        [
            row
            for row in reconstruction_rows
            if row["membership_status"]
            in ("unlabeled_deep_seed", "single_anchor_high_similarity")
        ],
    )
    provisional_rows = [
        row
        for row in reconstruction_rows
        if row["decision"] == "insufficient_data"
    ]
    if provisional_rows:
        write_rows(
            hrsn_dir / "insufficient_data_similarity_candidates_v2.csv",
            provisional_rows,
        )
    no_data_rows = [
        row
        for row in reconstruction_rows
        if row["decision"] == "download_or_data_error"
    ]
    if no_data_rows:
        write_rows(hrsn_dir / "no_data_similarity_candidates_v2.csv", no_data_rows)
    if reconstruction_metrics:
        write_metrics(
            hrsn_dir / "single_anchor_similarity_metrics.csv",
            reconstruction_metrics,
        )

    overlap_events = [configured["deep_template"], configured["hard_spatial_control"]]
    overlap = check_deep_overlap(
        overlap_events,
        [Path(config["paths"]["deep_mar_apr"]), Path(config["paths"]["deep_may_jun"])],
        config["deep_das"]["window_s"],
    )
    write_rows(catalog_dir / "deep_event_overlap.csv", overlap)
    high_similarity_count = sum(
        row["membership_status"]
        in ("unlabeled_deep_seed", "single_anchor_high_similarity")
        for row in reconstruction_rows
    )
    status = {
        "target_control_pair_gate": PASS if target_control_pair_gate else STOP,
        "candidate_pair_decision": candidate_summary["decision"],
        "hard_control_decision": control_summary["decision"],
        "diagnostic_scope": diagnostic_scope,
        "events_in_frozen_shortlist": len(reconstruction_rows),
        "high_similarity_candidates_in_pilot": high_similarity_count,
        "provisional_events_in_pilot": len(provisional_rows),
        "no_data_events_in_pilot": len(no_data_rows),
        "single_anchor_catalog_claim": STOP,
        "classifier_claim": STOP,
        "classifier_reason": "one-seed thresholding is only a similarity diagnostic; exact-ID external labels show cross-family overmerging",
        "config_sha256": config["_config_sha256"],
    }
    write_json(hrsn_dir / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
