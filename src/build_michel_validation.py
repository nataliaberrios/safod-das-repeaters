#!/usr/bin/env python
"""Build the independent Michel exact-ID crosswalk and continuation population."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import STOP, load_config, project_root, utc_now, write_json
from .coverage import write_rows
from .external_catalog import (
    md5_file,
    obtain_catalog,
    read_csv_rows,
)
from .michel_catalog import (
    continuation_population,
    exact_id_crosswalk,
    parse_michel_catalog,
    partition_conflicts,
    sequence_overlap_matrix,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=project_root() / "config" / "external_michel.json",
    )
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    root = project_root()
    catalog_path = root / "cached_catalogs" / config["filename"]
    obtain_catalog(
        catalog_path,
        config["download_url"],
        config["md5"],
        force=args.refresh,
    )
    events = parse_michel_catalog(catalog_path)
    population_path = (
        root
        / "outputs"
        / "external_validation"
        / "published_validation_population.csv"
    )
    population = read_csv_rows(population_path)
    crosswalk = exact_id_crosswalk(population, events)
    overlap = sequence_overlap_matrix(crosswalk)
    conflicts = partition_conflicts(overlap)
    continuations = continuation_population(
        events,
        crosswalk,
        config["waldhauser_schaff_target_sequence_id"],
        int(config["minimum_exact_overlap_to_map_sequence"]),
        config["waldhauser_schaff_catalog_end_utc"],
    )

    output = root / "outputs" / "michel_validation"
    write_rows(output / "exact_id_crosswalk.csv", crosswalk)
    write_rows(output / "sequence_overlap_matrix.csv", overlap)
    write_rows(output / "catalog_partition_conflicts.csv", conflicts)
    write_rows(output / "post_2014_continuations.csv", continuations)
    sequence_ids = sorted(
        {str(event["michel_sequence_id"]) for event in events}
    )
    exact_matches = [
        row for row in crosswalk if bool(row["michel_exact_id_match"])
    ]
    target_michel_sequences = sorted(
        {
            str(row["michel_sequence_id"])
            for row in exact_matches
            if str(row["waldhauser_schaff_sequence_id"])
            == config["waldhauser_schaff_target_sequence_id"]
        }
    )
    provenance = {
        "catalog_name": config["catalog_name"],
        "citation": config["citation"],
        "paper_doi": config["paper_doi"],
        "repository_record_url": config["repository_record_url"],
        "download_url": config["download_url"],
        "catalog_path": str(catalog_path),
        "catalog_md5": md5_file(catalog_path),
        "expected_md5": config["md5"],
        "catalog_event_count": len(events),
        "catalog_sequence_count": len(sequence_ids),
        "catalog_start_utc": min(event["origin_time"] for event in events),
        "catalog_end_utc": max(event["origin_time"] for event in events),
        "mapping_rule": config["mapping_rule"],
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    status = {
        "status": STOP,
        "classification_label_gate": STOP,
        "reason": "independent exact-ID catalogs disagree on the target-family partition",
        "catalog_event_count": len(events),
        "catalog_sequence_count": len(sequence_ids),
        "waldhauser_schaff_population_event_count": len(population),
        "exact_id_match_count": len(exact_matches),
        "target_maps_to_michel_sequence_ids": target_michel_sequences,
        "partition_conflict_count": len(conflicts),
        "partition_conflict_types": [
            row["conflict_type"] for row in conflicts
        ],
        "post_2014_continuation_event_count": len(continuations),
        "post_2014_continuation_event_ids": [
            row["event_id"] for row in continuations
        ],
        "frozen_network_model_status": "MECHANICALLY_FROZEN_BUT_LABEL_INTERPRETATION_SUPERSEDED_BY_CONFLICT",
        "das_opportunity": "test whether dense near-source moveout supports the split or merge partition",
        "config_sha256": config["_config_sha256"],
        "generated_utc": utc_now(),
    }
    write_json(output / "catalog_provenance.json", provenance)
    write_json(output / "status.json", status)
    print(json.dumps(status, indent=2))


if __name__ == "__main__":
    main()
