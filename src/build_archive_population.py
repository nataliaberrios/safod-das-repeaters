#!/usr/bin/env python
"""Build the 2024-2025 archive population without reading candidate waveforms."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from .archive_population import (
    archive_overlap_rows,
    heldout_catalog_counts,
    merge_coverage,
    read_primary_manifest,
    segment_rows,
    select_heldout_intervals,
)
from .catalog import catalog_query_url, fetch_catalog, write_catalog
from .common import (
    CONDITIONAL,
    load_config,
    parse_utc,
    project_root,
    sha256_file,
    utc_now,
    write_json,
)
from .coverage import write_rows


def _catalog_config(config: Dict[str, Any]) -> Dict[str, Any]:
    return {"catalog": dict(config["archive_catalog"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "incremental_value.json",
    )
    args = parser.parse_args()

    config = load_config(args.config)
    archive = config["archive"]
    manifest_path = Path(archive["manifest"])
    records, manifest_stats = read_primary_manifest(manifest_path, archive)
    segments = merge_coverage(
        records, float(archive["maximum_internal_gap_s"])
    )
    development = config["development_interval"]
    development_bounds = [
        (
            parse_utc(development["start_utc"]).timestamp(),
            parse_utc(development["end_utc"]).timestamp(),
        )
    ]
    heldout_settings = config["heldout_intervals"]
    heldout = select_heldout_intervals(
        segments,
        float(heldout_settings["interval_duration_s"]),
        int(heldout_settings["count"]),
        int(heldout_settings["selection_seed"]),
        exclusions=development_bounds,
    )

    query_config = _catalog_config(config)
    events = fetch_catalog(query_config)
    overlap, candidates = archive_overlap_rows(
        events,
        records,
        segments,
        archive,
        config["family_neighborhood"],
    )
    heldout_counts = heldout_catalog_counts(heldout, events)
    development_events: List[str] = []
    development_start, development_end = development_bounds[0]
    for event in events:
        origin_s = parse_utc(event["origin_time"]).timestamp()
        if development_start <= origin_s < development_end:
            development_events.append(str(event["event_id"]))

    output = project_root() / "outputs" / "incremental_value"
    write_catalog(output / "ncedc_archive_catalog.csv", events)
    write_rows(output / "archive_das_overlap.csv", overlap)
    write_rows(output / "prospective_candidates.csv", candidates)
    write_rows(output / "coverage_segments.csv", segment_rows(segments))
    write_rows(output / "heldout_intervals.csv", heldout)
    write_rows(
        output / "heldout_interval_catalog_counts.csv", heldout_counts
    )
    provenance = {
        "catalog_query_url": catalog_query_url(query_config),
        "catalog_event_count": len(events),
        "catalog_retrieved_utc": events[0].get("retrieved_utc", ""),
        "catalog_role": "routine_event_nomination_and_later_adjudication_not_family_labels",
        "manifest_path": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "manifest_stats": manifest_stats,
        "path_rewrites": archive.get("path_rewrites", []),
        "config_path": config["_config_path"],
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    model_path = (
        project_root()
        / "outputs"
        / "incremental_value"
        / "network_baseline"
        / "network_model_frozen.json"
    )
    model_release = False
    if model_path.exists():
        with model_path.open("r", encoding="utf-8") as handle:
            model = json.load(handle)
        model_release = (
            model.get("freeze_status")
            == "FROZEN_FOR_PROSPECTIVE_SCORING"
            and model.get("config_sha256") == config["_config_sha256"]
        )
    status = {
        "status": CONDITIONAL,
        "stage": (
            "prospective_population_constructed_waveform_access_released"
            if model_release
            else "prospective_population_constructed_waveform_embargo_active"
        ),
        "catalog_event_count": len(events),
        "catalog_events_with_complete_primary_das_window": sum(
            bool(row["coverage_complete"]) for row in overlap
        ),
        "prospective_candidate_count": len(candidates),
        "candidate_event_ids": [row["event_id"] for row in candidates],
        "family_label_count": 0,
        "family_label_rule": "catalog_proximity_never_assigns_family_membership",
        "primary_manifest_record_count": len(records),
        "contiguous_coverage_segment_count": len(segments),
        "contiguous_coverage_hours": sum(
            segment.duration_s for segment in segments
        )
        / 3600.0,
        "longest_contiguous_segment_hours": max(
            (segment.duration_s for segment in segments), default=0.0
        )
        / 3600.0,
        "heldout_interval_count": len(heldout),
        "heldout_total_hours": sum(
            float(row["duration_s"]) for row in heldout
        )
        / 3600.0,
        "heldout_selection_input": heldout_settings["selection_inputs"],
        "development_interval_event_ids": development_events,
        "prospective_network_waveform_access": (
            "RELEASED_FOR_BLIND_NETWORK_SCORING"
            if model_release
            else "EMBARGOED"
        ),
        "embargo_release_gate": "network_model_frozen.json exists and its hashes verify",
        "release_model_path": str(model_path) if model_release else "",
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    write_json(output / "catalog_provenance.json", provenance)
    write_json(output / "population_status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
