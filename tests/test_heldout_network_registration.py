"""Regression tests for the network-first held-out registration."""

from __future__ import annotations

import copy
import csv
import json
import unittest

from src.common import load_config, project_root
from src.register_heldout_network import (
    build_template_input_inventory,
    validate_detector_inheritance,
    validate_heldout_interval_rows,
)


def _load_json(path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class HeldoutNetworkRegistrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project_root()
        cls.registration = load_config(
            cls.root / "config" / "heldout_network_validation.json"
        )
        cls.parent = load_config(
            cls.root / "config" / "incremental_value.json"
        )
        cls.development = load_config(
            cls.root / "config" / "development_detection.json"
        )
        cls.development_status = _load_json(
            cls.root / "outputs" / "development_network" / "status.json"
        )
        cls.union = load_config(cls.root / "config" / "network_union.json")
        cls.union_status = _load_json(
            cls.root
            / "outputs"
            / "development_network"
            / "network_union_status.json"
        )
        cls.das_v2 = load_config(
            cls.root / "config" / "das_v2_validation.json"
        )
        cls.das_v2_status = _load_json(
            cls.root / "outputs" / "development_das_v2" / "status.json"
        )
        cls.heldout_rows = _read_csv(
            cls.root
            / "outputs"
            / "incremental_value"
            / "heldout_intervals.csv"
        )

    def test_registered_intervals_are_the_twelve_sealed_hours(self):
        hours, identifiers = validate_heldout_interval_rows(
            self.heldout_rows, self.registration
        )
        self.assertEqual(hours, 12.0)
        self.assertEqual(
            identifiers,
            ["heldout_{:02d}".format(index) for index in range(1, 13)],
        )

    def test_unsealed_interval_is_rejected(self):
        rows = copy.deepcopy(self.heldout_rows)
        rows[0]["analysis_status"] = "OPENED"
        with self.assertRaises(PermissionError):
            validate_heldout_interval_rows(rows, self.registration)

    def test_detector_settings_exactly_inherit_the_development_freeze(self):
        validate_detector_inheritance(
            self.registration,
            self.parent,
            self.development,
            self.development_status,
            self.union,
            self.union_status,
            self.das_v2,
            self.das_v2_status,
        )

    def test_threshold_repair_is_rejected(self):
        registration = copy.deepcopy(self.registration)
        registration["template_branch"]["threshold"] += 0.001
        with self.assertRaises(RuntimeError):
            validate_detector_inheritance(
                registration,
                self.parent,
                self.development,
                self.development_status,
                self.union,
                self.union_status,
                self.das_v2,
                self.das_v2_status,
            )

    def test_historical_inventory_contains_no_heldout_waveform(self):
        rows = build_template_input_inventory(
            self.root, self.registration, self.parent
        )
        self.assertEqual(len(rows), 30)
        self.assertEqual(
            sum(row["availability"] == "available" for row in rows),
            27,
        )
        self.assertEqual(
            sum(row["availability"] == "missing" for row in rows),
            3,
        )
        self.assertTrue(
            all("heldout" not in row["waveform_path"] for row in rows)
        )
        self.assertTrue(
            all(
                not row["waveform_sha256"]
                or len(row["waveform_sha256"]) == 64
                for row in rows
            )
        )

    def test_registrar_has_no_waveform_parser_import(self):
        source = (
            self.root / "src" / "register_heldout_network.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("import obspy", source)
        self.assertNotIn("from obspy", source)
        self.assertNotIn("import h5py", source)


if __name__ == "__main__":
    unittest.main()
