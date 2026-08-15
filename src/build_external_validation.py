#!/usr/bin/env python
"""Build the published-family crosswalk and frozen incremental-value design."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .common import PASS, load_config, project_root, write_json
from .coverage import write_rows
from .external_catalog import (
    build_shortlist_crosswalk,
    build_validation_population,
    catalog_provenance,
    obtain_catalog,
    parse_waldhauser_schaff,
    read_csv_rows,
)
from .incremental_value import (
    claim_status,
    diagnostic_confusion,
    metric_manifest,
    pipeline_manifest,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--catalog-path", type=Path, default=None)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    root = project_root()
    settings = config["external_validation"]
    catalog_path = args.catalog_path
    if catalog_path is None:
        catalog_path = root / "cached_catalogs" / settings["filename"]
        obtain_catalog(
            catalog_path,
            settings["download_url"],
            settings["md5"],
            force=args.refresh,
        )
    sequences, published_events = parse_waldhauser_schaff(catalog_path)
    shortlist = read_csv_rows(
        root / "outputs" / "hrsn" / "similarity_shortlist_v2.csv"
    )
    ncedc = read_csv_rows(root / "outputs" / "catalog" / "ncedc_dd_catalog.csv")
    crosswalk = build_shortlist_crosswalk(
        shortlist,
        ncedc,
        published_events,
        settings["target_sequence_id"],
        settings["hard_negative_sequence_ids"],
        settings["catalog_end_utc"],
    )
    population = build_validation_population(
        sequences,
        crosswalk,
        settings["target_sequence_id"],
        settings["hard_negative_sequence_ids"],
    )
    confusion = diagnostic_confusion(crosswalk)
    target_population = [
        row for row in population if row["validation_role"] == "target_positive"
    ]
    hard_population = [
        row
        for row in population
        if row["validation_role"] == "neighbor_family_negative"
    ]
    exact_matches = [row for row in crosswalk if row["external_match_type"] == "exact_event_id"]
    target_overlap = [
        row for row in crosswalk if row["external_role"] == "published_target_positive"
    ]
    hard_overlap = [
        row
        for row in crosswalk
        if row["external_role"] == "published_neighbor_family_negative"
    ]
    overmerges = [
        row
        for row in crosswalk
        if row["diagnostic_outcome"] == "discordant_single_anchor_overmerge"
    ]
    provenance = catalog_provenance(
        catalog_path, settings, len(sequences), len(published_events)
    )
    status = {
        "external_population_gate": PASS
        if len(target_population) >= 3 and len(settings["hard_negative_sequence_ids"]) >= 2
        else "STOP",
        "published_catalog_sequence_count": len(sequences),
        "published_catalog_event_count": len(published_events),
        "record_advertised_event_count": settings[
            "record_advertised_event_count"
        ],
        "record_minus_file_event_count": int(
            settings["record_advertised_event_count"]
        )
        - len(published_events),
        "frozen_shortlist_events": len(crosswalk),
        "shortlist_exact_id_matches": len(exact_matches),
        "published_target_population_events": len(target_population),
        "published_target_shortlist_overlap": len(target_overlap),
        "published_neighbor_population_events": len(hard_population),
        "published_neighbor_family_count": len(settings["hard_negative_sequence_ids"]),
        "published_neighbor_shortlist_overlap": len(hard_overlap),
        "discordant_single_anchor_overmerges": len(overmerges),
        "single_anchor_catalog_claim": "STOP",
        "single_anchor_catalog_reason": "external exact-ID labels show cross-family merges",
        "target_sequence_id": settings["target_sequence_id"],
        "hard_negative_sequence_ids": settings["hard_negative_sequence_ids"],
        "catalog_provenance": provenance,
        "config_sha256": config["_config_sha256"],
    }
    deep_status = json.load(
        (root / "outputs" / "deep_das" / "status.json").open(encoding="utf-8")
    )
    output = root / "outputs" / "external_validation"
    write_rows(output / "shortlist_external_crosswalk.csv", crosswalk)
    write_rows(output / "published_validation_population.csv", population)
    write_rows(output / "single_anchor_repair_diagnostic.csv", confusion)
    write_rows(output / "pipeline_manifest.csv", pipeline_manifest())
    write_rows(output / "metric_gates.csv", metric_manifest())
    write_rows(output / "claim_status.csv", claim_status(status, deep_status))
    write_json(output / "catalog_provenance.json", provenance)
    write_json(output / "status.json", status)
    print(
        json.dumps(
            {
                "external_population_gate": status["external_population_gate"],
                "shortlist_exact_id_matches": status["shortlist_exact_id_matches"],
                "target_population": status["published_target_population_events"],
                "neighbor_population": status["published_neighbor_population_events"],
                "overmerges": status["discordant_single_anchor_overmerges"],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
