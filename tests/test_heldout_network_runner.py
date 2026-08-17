"""Regression tests for the sealed held-out network runner."""

from __future__ import annotations

import ast
import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from src.common import iso_utc, load_config, parse_utc, project_root
from src.freeze_heldout_network_candidates import (
    _validate_interval_status,
    aggregate_execution_status,
    build_interval_scoped_unions,
    validate_candidate_rows,
)
from src.heldout_network_access import (
    RUNNER_IMPLEMENTATION_PATHS,
    download_heldout_source,
    interval_development_view,
    load_runner_release,
    load_template_metadata,
    source_request_url,
)
from src.release_heldout_network_runner import current_branch
from src.run_heldout_network_interval import (
    finalize_generic_candidates,
    finalize_template_candidates,
)


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _read_csv(path: Path):
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


class HeldoutNetworkRunnerTests(unittest.TestCase):
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
        cls.registration_status = _load_json(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "registration"
            / "network_registration_status.json"
        )
        cls.intervals = _read_csv(
            cls.root
            / "outputs"
            / "incremental_value"
            / "heldout_intervals.csv"
        )

    def test_request_url_uses_exact_registered_interval_and_padding(self):
        interval = self.intervals[0]
        source = self.parent["network_array"]["sources"][0]
        url = source_request_url(
            self.parent, self.registration, interval, source
        )
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        padding = float(
            self.registration["waveform_acquisition"]["request_padding_s"]
        )
        expected_start = iso_utc(
            parse_utc(interval["start_utc"]).timestamp() - padding
        )
        expected_end = iso_utc(
            parse_utc(interval["end_utc"]).timestamp() + padding
        )
        self.assertEqual(query["start"], [expected_start])
        self.assertEqual(query["end"], [expected_end])
        self.assertEqual(query["net"], [source["network"]])
        self.assertEqual(query["sta"], [",".join(source["stations"])])
        self.assertEqual(query["cha"], [",".join(source["channels"])])

    def test_interval_view_changes_time_not_detector_mechanics(self):
        interval = self.intervals[3]
        view = interval_development_view(
            self.development, self.registration, interval
        )
        self.assertEqual(view["interval"]["start_utc"], interval["start_utc"])
        self.assertEqual(view["interval"]["end_utc"], interval["end_utc"])
        self.assertEqual(
            view["repeater_template_bank"],
            self.development["repeater_template_bank"],
        )
        self.assertEqual(
            view["generic_network_trigger"],
            self.development["generic_network_trigger"],
        )
        self.assertEqual(
            view["continuous_network"]["merge_maximum_gap_s"], 0.05
        )

    def _template_candidate(self, interval_index=0):
        interval = self.intervals[interval_index]
        epoch_s = parse_utc(interval["start_utc"]).timestamp() + 30.0
        raw = {
            "origin_time": iso_utc(epoch_s),
            "origin_epoch_s": epoch_s,
            "bank_score": self.registration["template_branch"]["threshold"]
            + 0.01,
            "threshold": self.registration["template_branch"]["threshold"],
            "best_template_event_id": self.registration[
                "historical_template_bank"
            ]["event_ids"][0],
            "best_template_sequence_id": "must_not_escape",
            "best_template_score": 0.2,
            "best_template_station_count": 4,
            "station_support_count_at_0p2": 2,
        }
        return interval, finalize_template_candidates(
            [raw], interval["interval_id"]
        )[0]

    def _generic_candidate(self, interval_index=0):
        interval = self.intervals[interval_index]
        epoch_s = parse_utc(interval["start_utc"]).timestamp() + 31.0
        raw = {
            "trigger_time": iso_utc(epoch_s),
            "trigger_epoch_s": epoch_s,
            "coincidence_score": self.registration["generic_branch"][
                "threshold"
            ]
            + 0.01,
            "threshold": self.registration["generic_branch"]["threshold"],
            "coincidence_station_count": 4,
            "station_support_count_at_declared_ratio": 4,
            "station_characteristic_support_threshold": 2.0,
        }
        return interval, finalize_generic_candidates(
            [raw], interval["interval_id"]
        )[0]

    def test_finalized_template_candidate_strips_family_bearing_sequence(self):
        _, row = self._template_candidate()
        self.assertNotIn("best_template_sequence_id", row)
        self.assertEqual(row["catalog_fields_used_in_candidate_generation"], 0)
        self.assertEqual(row["DAS_fields_used_in_candidate_generation"], 0)
        self.assertEqual(row["family_assignment"], "not_assigned")

    def test_finalized_candidates_pass_fixed_blind_validation(self):
        template_interval, template = self._template_candidate()
        generic_interval, generic = self._generic_candidate()
        validate_candidate_rows(
            [template], template_interval, self.registration, "template"
        )
        validate_candidate_rows(
            [generic], generic_interval, self.registration, "generic"
        )

    def test_candidate_threshold_repair_is_rejected(self):
        interval, row = self._template_candidate()
        changed = copy.deepcopy(row)
        changed["threshold"] = float(changed["threshold"]) + 0.001
        with self.assertRaises(PermissionError):
            validate_candidate_rows(
                [changed], interval, self.registration, "template"
            )

    def test_candidate_catalog_leakage_is_rejected(self):
        interval, row = self._generic_candidate()
        changed = copy.deepcopy(row)
        changed["catalog_fields_used_in_candidate_generation"] = 1
        with self.assertRaises(PermissionError):
            validate_candidate_rows(
                [changed], interval, self.registration, "generic"
            )

    def test_time_union_never_pairs_candidates_across_intervals(self):
        _, template = self._template_candidate(interval_index=0)
        _, generic = self._generic_candidate(interval_index=1)
        generic["trigger_epoch_s"] = float(template["origin_epoch_s"]) + 1.0
        generic["trigger_time"] = iso_utc(generic["trigger_epoch_s"])
        rows = build_interval_scoped_unions(
            [template],
            [generic],
            ["heldout_01", "heldout_02"],
            maximum_difference_s=8.0,
        )
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(int(row["branch_count"]) == 1 for row in rows))

    def test_time_union_pairs_branches_within_one_interval(self):
        _, template = self._template_candidate(interval_index=0)
        _, generic = self._generic_candidate(interval_index=0)
        rows = build_interval_scoped_unions(
            [template],
            [generic],
            ["heldout_01"],
            maximum_difference_s=8.0,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["branch_count"]), 2)
        self.assertEqual(rows[0]["interval_id"], "heldout_01")

    def test_interval_status_validator_enforces_fixed_thresholds(self):
        interval = self.intervals[0]
        release = {"runner_commit_sha": "a" * 40}
        release_sha256 = "b" * 64
        status = {
            "status": "PASS",
            "stage": "heldout_network_interval_candidate_generation_complete",
            "interval_id": interval["interval_id"],
            "interval_start_utc": interval["start_utc"],
            "interval_end_utc": interval["end_utc"],
            "heldout_network_config_sha256": self.registration[
                "_config_sha256"
            ],
            "runner_commit_sha": release["runner_commit_sha"],
            "network_runner_release_sha256": release_sha256,
            "template_threshold": self.registration["template_branch"][
                "threshold"
            ],
            "generic_threshold": self.registration["generic_branch"][
                "threshold"
            ],
            "threshold_recalibration_performed": False,
            "generic_development_SNR1_STOP_preserved": True,
            "template_branch_status": "PASS",
            "generic_branch_status": "PASS",
            "heldout_catalog_event_rows_opened": 0,
            "heldout_DAS_HDF5_files_opened": 0,
            "heldout_DAS_HDF5_datasets_opened": 0,
            "heldout_family_label_rows_opened": 0,
            "candidate_family_assignments_made": 0,
        }
        _validate_interval_status(
            status,
            interval,
            self.registration,
            release,
            release_sha256,
        )
        changed = copy.deepcopy(status)
        changed["template_threshold"] += 0.001
        with self.assertRaises(PermissionError):
            _validate_interval_status(
                changed,
                interval,
                self.registration,
                release,
                release_sha256,
            )

    def test_aggregate_preserves_interval_stop_and_conditional_states(self):
        self.assertEqual(
            aggregate_execution_status(
                [{"interval_status": "PASS"}, {"interval_status": "PASS"}]
            ),
            "PASS",
        )
        self.assertEqual(
            aggregate_execution_status(
                [
                    {"interval_status": "PASS"},
                    {"interval_status": "CONDITIONAL"},
                ]
            ),
            "CONDITIONAL",
        )
        self.assertEqual(
            aggregate_execution_status(
                [
                    {"interval_status": "CONDITIONAL"},
                    {"interval_status": "STOP"},
                ]
            ),
            "STOP",
        )

    def test_historical_template_labels_are_opaque_to_runner(self):
        rows = load_template_metadata(self.root, self.registration)
        self.assertEqual(
            [row["event_id"] for row in rows],
            self.registration["historical_template_bank"]["event_ids"],
        )
        self.assertTrue(
            all(row["sequence_id"] == "opaque_historical_template" for row in rows)
        )

    def test_absent_release_stops_before_any_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                load_runner_release(Path(directory))

    def test_bad_release_stops_downloader_before_urlopen(self):
        interval = self.intervals[0]
        source = self.parent["network_array"]["sources"][0]
        with patch(
            "src.heldout_network_access.urllib.request.urlopen"
        ) as urlopen:
            with self.assertRaises(PermissionError):
                download_heldout_source(
                    self.root,
                    self.parent,
                    self.registration,
                    self.registration_status,
                    {"status": "STOP"},
                    interval,
                    source,
                )
            urlopen.assert_not_called()

    def test_runner_modules_import_no_catalog_or_DAS_reader(self):
        paths = [
            "src/heldout_network_access.py",
            "src/run_heldout_network_interval.py",
            "src/freeze_heldout_network_candidates.py",
        ]
        banned_modules = {
            "h5py",
            "src.catalog",
            "src.external_catalog",
            "src.michel_catalog",
            "src.das_analysis",
            "src.das_continuous_detection",
            "src.das_v2",
        }
        for relative in paths:
            source = (self.root / relative).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom):
                    prefix = "src" if node.level else ""
                    module = node.module or ""
                    imported.add(".".join(part for part in (prefix, module) if part))
            self.assertTrue(imported.isdisjoint(banned_modules), relative)
            self.assertNotIn("ncedc_archive_catalog.csv", source)
        runner_source = (
            self.root / "src" / "run_heldout_network_interval.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("null_bank_maxima", runner_source)
        self.assertNotIn("null_coincidence_maxima", runner_source)

    def test_release_branch_lookup_supports_cluster_git(self):
        self.assertEqual(
            current_branch(self.root),
            "agent/freeze-incremental-value-checkpoint",
        )

    def test_release_hash_set_covers_runner_aggregate_and_tests(self):
        self.assertEqual(
            set(RUNNER_IMPLEMENTATION_PATHS),
            {
                "src/heldout_network_access.py",
                "src/run_heldout_network_interval.py",
                "src/freeze_heldout_network_candidates.py",
                "src/release_heldout_network_runner.py",
                "heldout_network_interval_job.sh",
                "tests/test_heldout_network_runner.py",
            },
        )


if __name__ == "__main__":
    unittest.main()
