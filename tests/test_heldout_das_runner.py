"""Regression tests for the sealed, independently triggered DAS runner."""

from __future__ import annotations

import ast
import copy
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.common import iso_utc, load_config, parse_utc, project_root
from src.das_v2 import select_v2_candidates
from src.freeze_heldout_das_candidates import _validate_interval_status
from src.heldout_das_runner import (
    BASE_V1_CANDIDATE_FIELDS,
    V2_CANDIDATE_FIELDS,
    apply_frozen_v2_gate,
    finalize_base_candidates,
    interval_v1_view,
    validate_candidate_tables,
)
from src.heldout_das_runner_access import (
    RUNNER_IMPLEMENTATION_PATHS,
    interval_output_paths,
    load_runner_release,
    registered_interval_rows,
    registered_manifest_rows,
)
from src.release_heldout_das_runner import (
    configured_remote_url,
    current_branch,
    heldout_product_files,
)
from src.run_heldout_das_interval import main as run_interval_main


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows, fields):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class HeldoutDASRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project_root()
        cls.registration = load_config(
            cls.root / "config" / "heldout_das_replay.json"
        )
        cls.v1 = load_config(cls.root / "config" / "das_development.json")
        cls.v2 = load_config(cls.root / "config" / "das_v2_validation.json")
        cls.registration_status = _load_json(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "registration"
            / "das_registration_status.json"
        )
        cls.intervals = _read_csv(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "registration"
            / "das_interval_selection.csv"
        )

    def _raw(self, seconds, support, score=2.5, threshold=1.5):
        epoch_s = (
            parse_utc(self.intervals[0]["interval_start_utc"]).timestamp()
            + float(seconds)
        )
        return {
            "candidate_id": "raw_{:04d}".format(int(seconds)),
            "trigger_time": iso_utc(epoch_s),
            "trigger_epoch_s": epoch_s,
            "coincidence_score": score,
            "threshold": threshold,
            "coincidence_block_count": 4,
            "block_support_count_at_declared_ratio": support,
            "block_characteristic_support_threshold": 2.0,
            "network_candidate_time_fields_read": 0,
            "catalog_event_time_fields_read": 0,
            "family_assignment": "not_assigned",
        }

    def _tables(self):
        raw = [
            self._raw(10, 3, score=2.0),
            self._raw(20, 4, score=2.4),
            self._raw(30, 8, score=3.0),
        ]
        base = finalize_base_candidates(
            raw, "heldout_01", 10, self.registration
        )
        v2 = apply_frozen_v2_gate(base, "heldout_01", self.registration)
        return base, v2

    def test_interval_view_changes_only_bounds_and_role(self):
        view = interval_v1_view(
            self.v1, self.registration, self.intervals[0]
        )
        self.assertEqual(
            view["interval"]["start_utc"],
            self.intervals[0]["interval_start_utc"],
        )
        self.assertEqual(
            view["interval"]["end_utc"],
            self.intervals[0]["interval_end_utc"],
        )
        for field in (
            "channel_sampling",
            "preprocessing",
            "channel_qc",
            "generic_array_trigger",
            "null_calibration",
        ):
            self.assertEqual(view[field], self.v1[field])

    def test_registered_selection_is_complete_without_HDF5_access(self):
        intervals = registered_interval_rows(
            self.root, self.registration, self.registration_status
        )
        rows = registered_manifest_rows(
            self.root,
            self.registration,
            self.registration_status,
            intervals[0],
        )
        self.assertEqual(len(intervals), 12)
        self.assertEqual(len(rows), 61)
        self.assertTrue(
            all(row["hdf5_file_opened"].lower() == "false" for row in rows)
        )

    def test_base_table_retains_all_and_v2_is_exact_support_subset(self):
        base, v2 = self._tables()
        self.assertEqual(len(base), 3)
        self.assertEqual(len(v2), 2)
        self.assertEqual(
            [row["parent_v1_candidate_id"] for row in v2],
            [base[1]["candidate_id"], base[2]["candidate_id"]],
        )
        validate_candidate_tables(
            base,
            v2,
            self.intervals[0],
            self.registration,
            1.5,
        )

    def test_candidate_validation_survives_csv_roundtrip(self):
        base, v2 = self._tables()
        with tempfile.TemporaryDirectory() as directory:
            base_path = Path(directory) / "base.csv"
            v2_path = Path(directory) / "v2.csv"
            _write_csv(base_path, base, BASE_V1_CANDIDATE_FIELDS)
            _write_csv(v2_path, v2, V2_CANDIDATE_FIELDS)
            validate_candidate_tables(
                _read_csv(base_path),
                _read_csv(v2_path),
                self.intervals[0],
                self.registration,
                1.5,
            )

    def test_threshold_repair_and_comparison_leak_are_rejected(self):
        base, v2 = self._tables()
        changed = copy.deepcopy(base)
        changed[0]["threshold"] = 1.6
        with self.assertRaises(PermissionError):
            validate_candidate_tables(
                changed,
                v2,
                self.intervals[0],
                self.registration,
                1.5,
            )
        changed = copy.deepcopy(base)
        changed[0]["network_candidate_time_fields_read"] = 1
        with self.assertRaises(PermissionError):
            validate_candidate_tables(
                changed,
                v2,
                self.intervals[0],
                self.registration,
                1.5,
            )

    def test_heldout_gate_matches_disclosed_development_v2_membership(self):
        raw = _read_csv(
            self.root
            / "outputs"
            / "development_das"
            / "candidate_detections_raw.csv"
        )
        base = finalize_base_candidates(
            raw, "development_equivalence", 10, self.registration
        )
        observed = apply_frozen_v2_gate(
            base, "development_equivalence", self.registration
        )
        expected = select_v2_candidates(raw, self.v2)
        self.assertEqual(len(observed), 2)
        self.assertEqual(
            [row["trigger_epoch_s"] for row in observed],
            [row["trigger_epoch_s"] for row in expected],
        )
        self.assertEqual(
            [row["strong_block_count"] for row in observed],
            [row["strong_block_count"] for row in expected],
        )

    def test_absent_release_stops_before_any_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                load_runner_release(Path(directory))

    def test_bad_release_stops_interval_before_waveform_reader(self):
        argv = [
            "run_heldout_das_interval",
            "--interval-id",
            "heldout_01",
        ]
        with patch.object(sys, "argv", argv), patch(
            "src.run_heldout_das_interval.load_runner_release",
            return_value={"status": "STOP"},
        ), patch(
            "src.run_heldout_das_interval.preprocess_registered_chunks"
        ) as preprocess:
            with self.assertRaises(PermissionError):
                run_interval_main()
            preprocess.assert_not_called()

    def test_interval_status_rejects_rule_repair_and_leakage(self):
        interval = self.intervals[0]
        release = {"runner_commit_sha": "a" * 40}
        status = {
            "status": "PASS",
            "stage": "heldout_DAS_interval_candidate_generation_complete",
            "interval_id": interval["interval_id"],
            "interval_start_utc": interval["interval_start_utc"],
            "interval_end_utc": interval["interval_end_utc"],
            "heldout_DAS_config_sha256": self.registration["_config_sha256"],
            "runner_commit_sha": release["runner_commit_sha"],
            "DAS_runner_release_sha256": "b" * 64,
            "threshold_recalibration_or_repair_performed": False,
            "v2_threshold_or_support_sweep_performed": False,
            "candidate_deletion_after_review_performed": False,
            "v2_minimum_strong_block_count": 4,
            "v2_strong_block_characteristic_ratio": 2.0,
            "network_candidate_table_rows_opened": 0,
            "catalog_association_table_rows_opened": 0,
            "network_or_catalog_candidate_time_fields_read": 0,
            "heldout_family_label_rows_opened": 0,
            "candidate_family_assignments_made": 0,
        }
        _validate_interval_status(
            status, interval, self.registration, release, "b" * 64
        )
        changed = copy.deepcopy(status)
        changed["v2_minimum_strong_block_count"] = 3
        with self.assertRaises(PermissionError):
            _validate_interval_status(
                changed, interval, self.registration, release, "b" * 64
            )
        changed = copy.deepcopy(status)
        changed["network_candidate_table_rows_opened"] = 1
        with self.assertRaises(PermissionError):
            _validate_interval_status(
                changed, interval, self.registration, release, "b" * 64
            )

    def test_runner_modules_import_no_network_catalog_or_family_reader(self):
        paths = [
            "src/heldout_das_runner.py",
            "src/heldout_das_runner_access.py",
            "src/run_heldout_das_interval.py",
            "src/freeze_heldout_das_candidates.py",
        ]
        banned_fragments = (
            "network_continuous_detection",
            "network_union",
            "heldout_catalog_access",
            "ncedc_archive_catalog.csv",
            "network_union_time_only.csv",
        )
        for relative in paths:
            source = (self.root / relative).read_text(encoding="utf-8")
            ast.parse(source)
            for fragment in banned_fragments:
                self.assertNotIn(fragment, source, relative)

    def test_release_hash_set_covers_all_scientific_runtime_dependencies(self):
        self.assertEqual(
            set(RUNNER_IMPLEMENTATION_PATHS),
            {
                "src/common.py",
                "src/h5io.py",
                "src/das_continuous_detection.py",
                "src/das_v2.py",
                "src/heldout_das_runner.py",
                "src/heldout_das_runner_access.py",
                "src/run_heldout_das_interval.py",
                "src/freeze_heldout_das_candidates.py",
                "src/release_heldout_das_runner.py",
                "heldout_das_interval_job.sh",
                "tests/test_heldout_das_runner.py",
            },
        )

    def test_release_helpers_support_cluster_git_and_empty_temp_project(self):
        self.assertEqual(
            current_branch(self.root),
            "agent/freeze-incremental-value-checkpoint",
        )
        self.assertEqual(
            configured_remote_url(self.root, "origin"),
            "git@github.com:nataliaberrios/safod-das-repeaters.git",
        )
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                heldout_product_files(Path(directory), self.registration), []
            )

    def test_interval_output_paths_reject_unsafe_identifier(self):
        with self.assertRaises(ValueError):
            interval_output_paths(self.root, self.registration, "../escape")


if __name__ == "__main__":
    unittest.main()
