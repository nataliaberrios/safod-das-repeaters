"""Regression tests for the remotely released held-out catalog audit."""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.common import iso_utc, load_config, parse_utc, project_root
from src.heldout_catalog_access import (
    CATALOG_RUNNER_IMPLEMENTATION_PATHS,
    acquire_registered_catalogs,
    load_catalog_runner_release,
    parse_ncedc_text,
)
from src.run_heldout_catalog_audit import (
    adjudicate_network_union,
    build_evaluation_units,
    nearest_origin_event,
    physical_arrival_event,
)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class HeldoutCatalogRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project_root()
        cls.registration = load_config(
            cls.root / "config" / "heldout_catalog_audit.json"
        )
        cls.parent = load_config(
            cls.root / "config" / "incremental_value.json"
        )
        cls.registration_status = _load_json(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "registration"
            / "catalog_audit_registration_status.json"
        )
        cls.base_s = parse_utc("2025-01-01T00:00:00Z").timestamp()

    def event(
        self,
        event_id,
        origin_s,
        latitude=36.02009,
        longitude=-120.57759,
        depth_km=4.831,
        magnitude=1.0,
    ):
        return {
            "event_id": str(event_id),
            "origin_time": iso_utc(origin_s),
            "latitude": latitude,
            "longitude": longitude,
            "depth_km": depth_km,
            "magnitude": magnitude,
            "location_name": "synthetic",
        }

    def template_row(self, identifier, epoch_s):
        return {
            "interval_id": "heldout_01",
            "union_candidate_id": identifier,
            "representative_time": iso_utc(epoch_s),
            "representative_epoch_s": epoch_s,
            "representative_time_basis": "template_origin",
            "branch_membership": "template_bank",
            "branch_count": 1,
            "template_candidate_id": "template_" + identifier,
            "template_origin_time": iso_utc(epoch_s),
            "template_origin_epoch_s": epoch_s,
            "generic_candidate_id": "",
            "generic_trigger_time": "",
            "generic_trigger_epoch_s": "",
            "catalog_fields_used_in_grouping": 0,
            "family_assignment": "not_assigned",
        }

    def generic_row(self, identifier, epoch_s):
        return {
            "interval_id": "heldout_01",
            "union_candidate_id": identifier,
            "representative_time": iso_utc(epoch_s),
            "representative_epoch_s": epoch_s,
            "representative_time_basis": "generic_trigger_arrival",
            "branch_membership": "generic_trigger",
            "branch_count": 1,
            "template_candidate_id": "",
            "template_origin_time": "",
            "template_origin_epoch_s": "",
            "generic_candidate_id": "generic_" + identifier,
            "generic_trigger_time": iso_utc(epoch_s),
            "generic_trigger_epoch_s": epoch_s,
            "catalog_fields_used_in_grouping": 0,
            "family_assignment": "not_assigned",
        }

    def test_absent_release_stops_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                load_catalog_runner_release(Path(directory))

    def test_bad_release_stops_before_catalog_read_or_urlopen(self):
        with patch(
            "src.heldout_catalog_access._target_catalog_after_release"
        ) as target_read, patch(
            "src.heldout_catalog_access.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(PermissionError):
                acquire_registered_catalogs(
                    self.root,
                    self.registration,
                    self.registration_status,
                    {"status": "STOP"},
                )
            target_read.assert_not_called()
            urlopen.assert_not_called()

    def test_direct_origin_ranking_is_deterministic(self):
        first = self.event("b", self.base_s - 1.0)
        second = self.event("a", self.base_s + 1.0)
        result = nearest_origin_event(
            self.base_s, [first, second], tolerance_s=3.0
        )
        self.assertEqual(result["match_count"], 2)
        self.assertEqual(result["event"]["event_id"], "a")

    def test_physical_arrival_selects_regional_event(self):
        event = self.event(
            "regional",
            self.base_s,
            latitude=36.52,
            longitude=-120.57759,
            depth_km=5.0,
        )
        result = physical_arrival_event(
            self.base_s + 10.0,
            [event],
            self.registration["broader_regional_catalog"],
        )
        self.assertEqual(result["match_count"], 1)
        self.assertEqual(result["event"]["event_id"], "regional")

    def test_template_match_is_known_but_never_a_family_assignment(self):
        event = self.event("local", self.base_s + 0.5)
        source = self.template_row("u1", self.base_s)
        _, _, rows = adjudicate_network_union(
            [source],
            [event],
            {"heldout_01": []},
            self.registration,
            self.parent,
        )
        result = rows[0]
        self.assertEqual(
            result["known_event_class"],
            "known_family_neighborhood_catalog_event",
        )
        self.assertEqual(result["family_assignment"], "not_assigned")
        self.assertEqual(
            result["repeater_family_extension_status"],
            "not_evaluated_catalog_evidence_cannot_assign_family",
        )
        for field, value in source.items():
            self.assertEqual(str(result[field]), str(value))

    def test_generic_regional_arrival_is_not_detection_extension(self):
        event = self.event(
            "regional",
            self.base_s,
            latitude=36.52,
            longitude=-120.57759,
            depth_km=5.0,
        )
        source = self.generic_row("u2", self.base_s + 10.0)
        _, _, rows = adjudicate_network_union(
            [source],
            [],
            {"heldout_01": [event]},
            self.registration,
            self.parent,
        )
        self.assertEqual(
            rows[0]["known_event_class"],
            "known_regional_arrival_outside_target_box",
        )
        self.assertEqual(
            rows[0]["catalog_detection_extension_status"],
            "known_catalog_event_not_detection_extension",
        )

    def test_unassociated_template_is_inconclusive_not_new(self):
        source = self.template_row("u3", self.base_s)
        _, broader, rows = adjudicate_network_union(
            [source],
            [],
            {"heldout_01": []},
            self.registration,
            self.parent,
        )
        self.assertEqual(
            broader[0]["association_status"],
            "not_applicable_template_time_is_origin_not_array_arrival",
        )
        self.assertEqual(
            rows[0]["known_event_class"],
            "catalog_unassociated_template_only_inconclusive",
        )
        self.assertEqual(
            rows[0]["local_extension_disposition"],
            "retain_not_automatically_new",
        )

    def test_direct_generic_without_physics_is_STOP(self):
        event = self.event("direct", self.base_s)
        source = self.generic_row("u4", self.base_s + 1.0)
        _, _, rows = adjudicate_network_union(
            [source],
            [event],
            {"heldout_01": []},
            self.registration,
            self.parent,
        )
        self.assertEqual(
            rows[0]["known_event_class"],
            "catalog_timing_or_identity_conflict_STOP",
        )
        self.assertTrue(rows[0]["catalog_conflict_STOP"])

    def test_known_duplicate_candidates_collapse_only_at_evaluation_level(self):
        event = self.event("local", self.base_s)
        rows = [
            self.template_row("u5", self.base_s),
            self.template_row("u6", self.base_s + 0.1),
        ]
        _, _, adjudicated = adjudicate_network_union(
            rows,
            [event],
            {"heldout_01": []},
            self.registration,
            self.parent,
        )
        units = build_evaluation_units(adjudicated)
        self.assertEqual(len(adjudicated), 2)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["candidate_count"], 2)

    def test_empty_ncedc_response_is_valid_zero_event_catalog(self):
        self.assertEqual(
            parse_ncedc_text("# no events\n", "https://example"), []
        )

    def test_release_hash_set_covers_runner_and_tests(self):
        self.assertEqual(
            set(CATALOG_RUNNER_IMPLEMENTATION_PATHS),
            {
                "src/heldout_catalog_access.py",
                "src/run_heldout_catalog_audit.py",
                "src/release_heldout_catalog_runner.py",
                "tests/test_heldout_catalog_runner.py",
            },
        )

    def test_runner_imports_no_waveform_DAS_or_family_reader(self):
        banned = {
            "h5py",
            "obspy",
            "src.das_analysis",
            "src.das_continuous_detection",
            "src.das_v2",
            "src.external_catalog",
            "src.michel_catalog",
            "src.network_family",
        }
        for relative in CATALOG_RUNNER_IMPLEMENTATION_PATHS[:3]:
            source = (self.root / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    if node.level:
                        module = "src." + module
                    imported.add(module)
            self.assertTrue(imported.isdisjoint(banned), relative)


if __name__ == "__main__":
    unittest.main()
