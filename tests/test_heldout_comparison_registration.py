"""Regression tests for held-out DAS/network comparison registration."""

from __future__ import annotations

import ast
import copy
import json
import unittest
from pathlib import Path

from src.common import load_config, project_root
from src.heldout_comparison_access import (
    read_csv_header,
    validate_frozen_inputs,
    validate_stage_gates,
)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


class HeldoutComparisonRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project_root()
        cls.registration = load_config(
            cls.root
            / "config"
            / "heldout_das_network_comparison.json"
        )
        cls.paths = validate_frozen_inputs(cls.root, cls.registration)
        cls.network_status = _load_json(
            cls.paths["network_candidate_generation_status"]
        )
        cls.catalog_status = _load_json(cls.paths["catalog_audit_status"])
        cls.das_status = _load_json(
            cls.paths["DAS_candidate_generation_status"]
        )
        cls.heldout_das_config = load_config(
            cls.paths["heldout_DAS_config"]
        )

    def _validate(
        self,
        registration=None,
        network=None,
        catalog=None,
        das=None,
    ):
        validate_stage_gates(
            registration or self.registration,
            network or self.network_status,
            catalog or self.catalog_status,
            das or self.das_status,
            self.heldout_das_config,
        )

    def test_exact_complete_freezes_and_inherited_rule_pass(self):
        self._validate()

    def test_matching_window_repair_is_rejected(self):
        registration = copy.deepcopy(self.registration)
        registration["time_only_matching"][
            "maximum_absolute_time_difference_s"
        ] = 9.0
        with self.assertRaises(PermissionError):
            self._validate(registration=registration)

    def test_matching_algorithm_repair_is_rejected(self):
        registration = copy.deepcopy(self.registration)
        registration["time_only_matching"]["matching_algorithm"] = (
            "greedy_nearest"
        )
        with self.assertRaises(PermissionError):
            self._validate(registration=registration)

    def test_cross_interval_matching_is_rejected(self):
        registration = copy.deepcopy(self.registration)
        registration["time_only_matching"]["cross_interval_matching"] = (
            "ALLOWED"
        )
        with self.assertRaises(PermissionError):
            self._validate(registration=registration)

    def test_incomplete_DAS_freeze_is_rejected(self):
        das = copy.deepcopy(self.das_status)
        das["all_12_intervals_PASS"] = False
        with self.assertRaises(PermissionError):
            self._validate(das=das)

    def test_DAS_generation_comparison_leak_is_rejected(self):
        das = copy.deepcopy(self.das_status)
        das["network_candidate_table_rows_opened"] = 1
        with self.assertRaises(PermissionError):
            self._validate(das=das)

    def test_network_threshold_repair_is_rejected(self):
        network = copy.deepcopy(self.network_status)
        network["threshold_recalibration_performed"] = True
        with self.assertRaises(PermissionError):
            self._validate(network=network)

    def test_catalog_candidate_deletion_is_rejected(self):
        catalog = copy.deepcopy(self.catalog_status)
        catalog["candidate_rows_deleted"] = 1
        with self.assertRaises(PermissionError):
            self._validate(catalog=catalog)

    def test_headers_match_without_reading_candidate_rows(self):
        names = (
            "network_union_time_only",
            "DAS_v2_candidates_time_only",
            "network_evaluation_units",
            "network_union_adjudicated",
        )
        for name in names:
            self.assertEqual(
                read_csv_header(self.paths[name]),
                self.registration["frozen_schema_headers"][name],
            )

    def test_registrar_has_no_candidate_row_or_comparison_reader(self):
        path = self.root / "src" / "register_heldout_das_network_comparison.py"
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertTrue(
            imported.isdisjoint(
                {
                    "pandas",
                    "src.das_network_comparison",
                    "src.network_union",
                    "src.heldout_catalog_access",
                }
            )
        )
        self.assertNotIn("DictReader", source)

    def test_materialized_registration_reports_zero_row_access(self):
        status = _load_json(
            self.root
            / "outputs"
            / "heldout_v2"
            / "registration"
            / "comparison_registration_status.json"
        )
        self.assertEqual(status["status"], "PASS")
        self.assertEqual(
            status["comparison_config_sha256"],
            self.registration["_config_sha256"],
        )
        self.assertEqual(status["network_raw_candidate_count"], 33)
        self.assertEqual(status["network_evaluation_unit_count"], 32)
        self.assertEqual(status["DAS_v2_candidate_count"], 22)
        self.assertTrue(status["schema_headers_verified_exact"])
        for field in (
            "network_candidate_rows_opened",
            "DAS_candidate_rows_opened",
            "network_evaluation_rows_opened",
            "network_adjudication_rows_opened",
            "network_candidate_time_fields_read",
            "DAS_candidate_time_fields_read",
            "catalog_association_rows_opened",
            "family_label_rows_opened",
            "comparison_output_products_present_before_registration",
        ):
            self.assertEqual(status[field], 0)

    def test_comparison_outputs_remain_absent_at_registration(self):
        output = self.registration["output"]
        root = self.root / output["comparison_directory"]
        for field in (
            "time_only_comparison_csv",
            "network_context_csv",
            "interval_summary_csv",
            "comparison_status_json",
        ):
            self.assertFalse((root / output[field]).exists())


if __name__ == "__main__":
    unittest.main()
