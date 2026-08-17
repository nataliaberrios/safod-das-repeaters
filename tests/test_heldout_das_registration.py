"""Regression tests for the independent held-out DAS preregistration."""

from __future__ import annotations

import ast
import copy
import csv
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from src.common import load_config, parse_utc, project_root, sha256_file
from src.heldout_das_access import (
    select_manifest_records,
    validate_detector_and_stage_gates,
    validate_heldout_intervals,
)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class HeldoutDASRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project_root()
        cls.registration = load_config(
            cls.root / "config" / "heldout_das_replay.json"
        )
        cls.parent = load_config(
            cls.root / "config" / "incremental_value.json"
        )
        cls.v1 = load_config(cls.root / "config" / "das_development.json")
        cls.v2 = load_config(cls.root / "config" / "das_v2_validation.json")
        cls.v2_status = _load_json(
            cls.root / "outputs" / "development_das_v2" / "status.json"
        )
        cls.network_status = _load_json(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "network"
            / "candidate_generation_status.json"
        )
        cls.catalog_status = _load_json(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "network_catalog_audit"
            / "network_catalog_audit_status.json"
        )
        cls.population_status = _load_json(
            cls.root / "outputs" / "incremental_value" / "population_status.json"
        )
        cls.intervals = _read_csv(
            cls.root / "outputs" / "incremental_value" / "heldout_intervals.csv"
        )

    def _validate(self, registration=None, network=None, catalog=None):
        validate_detector_and_stage_gates(
            registration or self.registration,
            self.parent,
            self.v1,
            self.v2,
            self.v2_status,
            network or self.network_status,
            catalog or self.catalog_status,
            self.population_status,
        )

    def test_exact_frozen_detector_and_stage_gates_pass(self):
        self._validate()

    def test_all_twelve_manifest_selected_hours_remain_sealed(self):
        hours, interval_ids = validate_heldout_intervals(
            self.intervals, self.registration
        )
        self.assertEqual(hours, 12.0)
        self.assertEqual(
            interval_ids,
            ["heldout_{:02d}".format(index) for index in range(1, 13)],
        )

    def test_unsealed_interval_is_rejected(self):
        rows = copy.deepcopy(self.intervals)
        rows[0]["analysis_status"] = "OPENED"
        with self.assertRaises(PermissionError):
            validate_heldout_intervals(rows, self.registration)

    def test_null_seed_repair_is_rejected(self):
        registration = copy.deepcopy(self.registration)
        registration["detector_inheritance"]["DAS_v1_null_random_seed"] += 1
        with self.assertRaises(RuntimeError):
            self._validate(registration=registration)

    def test_network_stage_premature_DAS_access_is_rejected(self):
        network = copy.deepcopy(self.network_status)
        network["heldout_DAS_HDF5_files_opened"] = 1
        with self.assertRaises(PermissionError):
            self._validate(network=network)

    def test_catalog_stage_time_leak_is_rejected(self):
        catalog = copy.deepcopy(self.catalog_status)
        catalog["DAS_candidate_generation_read_network_or_catalog_times"] = True
        with self.assertRaises(PermissionError):
            self._validate(catalog=catalog)

    def test_manifest_selection_uses_only_extent_and_detects_gaps(self):
        records = [
            SimpleNamespace(path="a.h5", start_s=0.0, end_s=60.0),
            SimpleNamespace(path="b.h5", start_s=60.0, end_s=120.0),
        ]
        selected, summary = select_manifest_records(records, 10.0, 110.0, 0.0)
        self.assertEqual([record.path for record in selected], ["a.h5", "b.h5"])
        self.assertEqual(summary["maximum_manifest_gap_s"], 0.0)
        broken = [
            SimpleNamespace(path="a.h5", start_s=0.0, end_s=50.0),
            SimpleNamespace(path="b.h5", start_s=60.0, end_s=120.0),
        ]
        with self.assertRaises(RuntimeError):
            select_manifest_records(broken, 10.0, 110.0, 0.01)

    def test_candidate_generation_allowlist_has_no_comparison_input(self):
        guards = self.registration["independence_guards"]
        allowed = "\n".join(guards["candidate_generation_allowed_inputs"]).lower()
        self.assertNotIn("network", allowed)
        self.assertNotIn("catalog", allowed)
        self.assertNotIn("family", allowed)
        self.assertEqual(
            self.registration["candidate_materialization"][
                "waveform_window_selection_from_network_or_catalog_times"
            ],
            "FORBIDDEN",
        )

    def test_registrar_imports_no_HDF5_or_network_catalog_reader(self):
        path = self.root / "src" / "register_heldout_das_replay.py"
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
                    "h5py",
                    "src.h5io",
                    "src.network_continuous_detection",
                    "src.network_union",
                    "src.heldout_catalog_access",
                }
            )
        )

    def test_materialized_registration_reports_zero_waveform_access(self):
        output = self.root / "outputs" / "heldout_v2" / "registration"
        status_path = output / "das_registration_status.json"
        selection_path = output / "das_manifest_selection.csv"
        interval_path = output / "das_interval_selection.csv"
        status = _load_json(status_path)
        selection = _read_csv(selection_path)
        interval_rows = _read_csv(interval_path)
        self.assertEqual(status["heldout_DAS_config_sha256"], self.registration["_config_sha256"])
        self.assertEqual(status["manifest_selection_sha256"], sha256_file(selection_path))
        self.assertEqual(status["interval_selection_sha256"], sha256_file(interval_path))
        self.assertEqual(len(interval_rows), 12)
        self.assertGreater(len(selection), 12)
        self.assertTrue(all(row["status"] == "PASS" for row in interval_rows))
        self.assertTrue(
            all(
                row["hdf5_file_opened"].lower() == "false"
                and row["hdf5_header_opened"].lower() == "false"
                and row["hdf5_dataset_opened"].lower() == "false"
                and int(row["network_candidate_time_fields_read"]) == 0
                and int(row["catalog_event_time_fields_read"]) == 0
                for row in selection
            )
        )
        for row in interval_rows:
            self.assertLessEqual(
                parse_utc(row["selected_coverage_start_utc"]),
                parse_utc(row["padded_request_start_utc"]),
            )
            self.assertGreaterEqual(
                parse_utc(row["selected_coverage_end_utc"]),
                parse_utc(row["padded_request_end_utc"]),
            )
        for field in (
            "heldout_DAS_HDF5_files_opened",
            "heldout_DAS_HDF5_headers_opened",
            "heldout_DAS_HDF5_datasets_opened",
            "network_candidate_table_rows_opened",
            "catalog_association_table_rows_opened",
            "network_or_catalog_candidate_time_fields_read",
            "heldout_family_label_rows_opened",
        ):
            self.assertEqual(status[field], 0)


if __name__ == "__main__":
    unittest.main()
