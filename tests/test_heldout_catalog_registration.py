"""Regression tests for the post-union held-out catalog registration."""

from __future__ import annotations

import ast
import copy
import csv
import json
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from src.common import load_config, parse_utc, project_root
from src.register_heldout_catalog_audit import (
    build_query_manifest,
    validate_catalog_rules,
    validate_network_freeze,
)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class HeldoutCatalogRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project_root()
        cls.registration = load_config(
            cls.root / "config" / "heldout_catalog_audit.json"
        )
        cls.parent = load_config(
            cls.root / "config" / "incremental_value.json"
        )
        cls.development = load_config(
            cls.root / "config" / "development_detection.json"
        )
        cls.network_registration = load_config(
            cls.root / "config" / "heldout_network_validation.json"
        )
        cls.network_status = _load_json(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "network"
            / "candidate_generation_status.json"
        )
        cls.intervals = _read_csv(
            cls.root
            / "outputs"
            / "incremental_value"
            / "heldout_intervals.csv"
        )
        cls.interval_rows = _read_csv(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "network"
            / "interval_status.csv"
        )
        cls.union_rows = _read_csv(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "network"
            / "network_union_time_only.csv"
        )

    def test_rules_exactly_inherit_frozen_development_semantics(self):
        validate_catalog_rules(
            self.registration,
            self.parent,
            self.development,
            self.network_registration,
        )

    def test_exact_33_row_union_passes_without_family_assignment(self):
        interval_ids, membership = validate_network_freeze(
            self.registration,
            self.network_status,
            self.union_rows,
            self.interval_rows,
            self.intervals,
        )
        self.assertEqual(len(interval_ids), 12)
        self.assertEqual(membership["template_bank"], 12)
        self.assertEqual(membership["generic_trigger"], 21)
        self.assertEqual(membership["template_bank+generic_trigger"], 0)

    def test_candidate_deletion_is_rejected(self):
        with self.assertRaises(RuntimeError):
            validate_network_freeze(
                self.registration,
                self.network_status,
                self.union_rows[:-1],
                self.interval_rows,
                self.intervals,
            )

    def test_family_assignment_is_rejected(self):
        rows = copy.deepcopy(self.union_rows)
        rows[0]["family_assignment"] = "R1.2900.11955.0"
        with self.assertRaises(PermissionError):
            validate_network_freeze(
                self.registration,
                self.network_status,
                rows,
                self.interval_rows,
                self.intervals,
            )

    def test_catalog_tolerance_repair_is_rejected(self):
        registration = copy.deepcopy(self.registration)
        registration["target_region_catalog"]["template_origin_association"][
            "maximum_absolute_residual_s"
        ] = 4.0
        with self.assertRaises(RuntimeError):
            validate_catalog_rules(
                registration,
                self.parent,
                self.development,
                self.network_registration,
            )

    def test_query_manifest_pins_all_twelve_exact_urls_without_fetching(self):
        rows = build_query_manifest(self.registration, self.intervals)
        self.assertEqual(len(rows), 12)
        first = rows[0]
        query = parse_qs(urlparse(first["request_url"]).query)
        settings = self.registration["broader_regional_catalog"]
        expected_start = parse_utc(self.intervals[0]["start_utc"]).timestamp()
        expected_start -= float(settings["origin_time_padding_before_s"])
        self.assertEqual(
            parse_utc(query["starttime"][0]).timestamp(), expected_start
        )
        self.assertEqual(
            query["endtime"], [self.intervals[0]["end_utc"]]
        )
        self.assertEqual(query["catalog"], ["NCSS"])
        self.assertTrue(
            all(
                int(row["catalog_event_rows_opened_at_registration"]) == 0
                for row in rows
            )
        )

    def test_DAS_generation_remains_independent(self):
        guards = self.registration["independence_guards"]
        self.assertFalse(
            guards["DAS_candidate_generation_may_read_catalog_audit_outputs"]
        )
        self.assertFalse(
            guards["DAS_candidate_generation_may_read_network_candidate_times"]
        )
        self.assertEqual(
            guards["DAS_waveform_window_selection_from_network_or_catalog_times"],
            "FORBIDDEN",
        )

    def test_registrar_contains_no_catalog_fetch_or_waveform_reader(self):
        path = self.root / "src" / "register_heldout_catalog_audit.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                imported.add(module)
        self.assertTrue(
            imported.isdisjoint(
                {
                    "h5py",
                    "obspy",
                    "src.catalog",
                    "src.background_catalog",
                    "src.external_catalog",
                    "src.michel_catalog",
                }
            )
        )
        self.assertNotIn("urlopen", source)


if __name__ == "__main__":
    unittest.main()
