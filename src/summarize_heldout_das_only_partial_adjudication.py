#!/usr/bin/env python
"""Combine fixed network scores and cached regional-catalog checks.

This creates a descriptive partial-adjudication table.  It never assigns an
earthquake or repeater family and never changes the frozen candidate set.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Dict, List

from .common import parse_utc, project_root, sha256_file, utc_now, write_json


FIELDS = [
    "comparison_candidate_id",
    "interval_id",
    "DAS_candidate_id",
    "DAS_trigger_time",
    "DAS_coincidence_score",
    "DAS_strong_block_count",
    "generic_network_above_frozen_threshold",
    "template_network_above_frozen_threshold",
    "nearest_cached_regional_event_id_within_30s",
    "nearest_cached_regional_event_time_within_30s",
    "nearest_cached_regional_time_difference_s",
    "cached_regional_catalog_status",
    "DAS_artifact_status",
    "waveform_review_status",
    "repeater_family_assignment",
]


def _read_csv(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=FIELDS, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    project = project_root()
    output = project / "outputs" / "heldout_v2" / "adjudication"
    partial_path = output / "partial_adjudication.csv"
    status_path = output / "partial_adjudication_status.json"
    if partial_path.exists() or status_path.exists():
        raise PermissionError("partial adjudication output already exists")

    generic = {
        row["DAS_candidate_id"]: row
        for row in _read_csv(output / "forced_generic_network_scores.csv")
    }
    template = {
        row["DAS_candidate_id"]: row
        for row in _read_csv(output / "forced_template_network_scores.csv")
    }
    context = _read_csv(
        project / "outputs" / "heldout_v2" / "comparison" / "das_network_network_context.csv"
    )
    das_only = [row for row in context if row["comparison_membership"] == "DAS_only"]
    if len(das_only) != 21 or set(generic) != {row["DAS_candidate_id"] for row in das_only}:
        raise RuntimeError("partial adjudication populations do not agree")
    if set(template) != set(generic):
        raise RuntimeError("generic/template adjudication IDs differ")

    catalog_rows: Dict[str, List[Dict[str, str]]] = {}
    catalog_hashes: Dict[str, str] = {}
    for row in das_only:
        interval_id = str(row["interval_id"])
        if interval_id in catalog_rows:
            continue
        path = project / "cached_catalogs" / "heldout_v2" / (interval_id + ".csv")
        catalog_hashes[interval_id] = sha256_file(path)
        catalog_rows[interval_id] = _read_csv(path)

    results: List[Dict[str, Any]] = []
    for row in das_only:
        epoch = parse_utc(row["DAS_trigger_time"]).timestamp()
        candidates = []
        for event in catalog_rows[row["interval_id"]]:
            event_epoch = parse_utc(event["origin_time"]).timestamp()
            candidates.append((abs(event_epoch - epoch), event))
        candidates.sort(key=lambda item: item[0])
        if candidates and candidates[0][0] <= 30.0:
            difference, event = candidates[0]
            event_id = event["event_id"]
            event_time = event["origin_time"]
            status = "CACHED_BROAD_REGIONAL_EVENT_WITHIN_30S"
        else:
            difference = ""
            event_id = ""
            event_time = ""
            status = "NO_CACHED_BROAD_REGIONAL_EVENT_WITHIN_30S"
        results.append(
            {
                "comparison_candidate_id": row["comparison_candidate_id"],
                "interval_id": row["interval_id"],
                "DAS_candidate_id": row["DAS_candidate_id"],
                "DAS_trigger_time": row["DAS_trigger_time"],
                "DAS_coincidence_score": row["DAS_coincidence_score"],
                "DAS_strong_block_count": row["DAS_strong_block_count"],
                "generic_network_above_frozen_threshold": generic[row["DAS_candidate_id"]][
                    "generic_network_above_frozen_threshold"
                ],
                "template_network_above_frozen_threshold": template[row["DAS_candidate_id"]][
                    "template_network_above_frozen_threshold"
                ],
                "nearest_cached_regional_event_id_within_30s": event_id,
                "nearest_cached_regional_event_time_within_30s": event_time,
                "nearest_cached_regional_time_difference_s": difference,
                "cached_regional_catalog_status": status,
                "DAS_artifact_status": "PENDING",
                "waveform_review_status": "PENDING",
                "repeater_family_assignment": "not_assigned",
            }
        )

    _write_csv(partial_path, results)
    status = {
        "status": "PARTIAL",
        "stage": "heldout_DAS_only_fixed_network_scores_and_cached_regional_catalog_check_complete",
        "generated_utc": utc_now(),
        "DAS_only_candidate_count": len(results),
        "generic_network_threshold_crossings": sum(
            str(row["generic_network_above_frozen_threshold"]).lower() == "true"
            for row in results
        ),
        "template_network_threshold_crossings": sum(
            str(row["template_network_above_frozen_threshold"]).lower() == "true"
            for row in results
        ),
        "cached_regional_events_within_30s": sum(
            row["cached_regional_catalog_status"]
            == "CACHED_BROAD_REGIONAL_EVENT_WITHIN_30S"
            for row in results
        ),
        "DAS_artifact_review_status": "PENDING",
        "waveform_review_status": "PENDING",
        "family_assignments_made": 0,
        "candidate_deletion_performed": False,
        "threshold_repair_performed": False,
        "catalog_query_mode": "already_cached_broad_regional_products_only_no_new_query",
        "cached_catalog_sha256": catalog_hashes,
        "partial_result_sha256": sha256_file(partial_path),
        "next_stage_gate": "PASS_DAS_ARTIFACT_AND_WAVEFORM_REVIEW",
        "scientific_extension_claim_gate": "STOP",
    }
    write_json(status_path, status)
    print(json.dumps(status, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
