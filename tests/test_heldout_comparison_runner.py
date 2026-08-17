"""Tests for the remotely gated held-out DAS/network comparison runner."""

from __future__ import annotations

import ast
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.common import iso_utc, load_config, project_root
from src.heldout_comparison_runner import (
    MATCHING_RULE,
    attach_network_context,
    build_interval_summary,
    build_time_only_comparison,
    validate_candidate_rows,
)
from src.heldout_comparison_runner_access import (
    RUNNER_IMPLEMENTATION_PATHS,
    comparison_output_paths,
    load_runner_release,
)
from src.release_heldout_comparison_runner import (
    configured_remote_url,
    current_branch,
)
from src.run_heldout_das_network_comparison import main as run_comparison_main


def _load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _das(interval_id: str, candidate_id: str, epoch_s: float):
    return {
        "interval_id": interval_id,
        "candidate_id": candidate_id,
        "parent_v1_candidate_id": "parent_" + candidate_id,
        "trigger_time": iso_utc(epoch_s),
        "trigger_epoch_s": epoch_s,
        "coincidence_score": 2.5,
        "v1_interval_null_threshold": 1.5,
        "strong_block_characteristic_ratio": 2.0,
        "strong_block_count": 5,
        "usable_block_count": 10,
        "total_registered_block_count": 10,
        "strong_block_fraction_of_registered": 0.5,
        "minimum_required_strong_block_count": 4,
        "v1_score_rank_within_interval": 1,
        "candidate_generation_label": "synthetic_arrival_not_truth",
        "network_candidate_time_fields_read": 0,
        "catalog_event_time_fields_read": 0,
        "family_label_fields_read": 0,
        "family_assignment": "not_assigned",
    }


def _network(interval_id: str, candidate_id: str, epoch_s: float):
    return {
        "interval_id": interval_id,
        "union_candidate_id": candidate_id,
        "representative_time": iso_utc(epoch_s),
        "representative_epoch_s": epoch_s,
        "representative_time_basis": "generic_trigger_arrival",
        "branch_membership": "generic_trigger",
        "branch_count": 1,
        "template_candidate_id": "",
        "template_origin_time": "",
        "template_origin_epoch_s": "",
        "template_bank_score": "",
        "template_threshold": "",
        "generic_candidate_id": "generic_" + candidate_id,
        "generic_trigger_time": iso_utc(epoch_s),
        "generic_trigger_epoch_s": epoch_s,
        "generic_coincidence_score": 2.0,
        "generic_threshold": 1.5,
        "generic_station_support_count": 4,
        "cross_branch_time_difference_s": "",
        "union_generation_rule": "synthetic_time_only",
        "catalog_fields_used_in_grouping": 0,
        "family_assignment": "not_assigned",
    }


def _adjudicated(network, evaluation_unit_id: str):
    return {
        **network,
        "evaluation_unit_id": evaluation_unit_id,
        "catalog_event_id_final": "event_" + evaluation_unit_id,
        "known_event_class": "catalog_unassociated_generic_candidate",
        "catalog_detection_extension_status": "not_established",
        "repeater_family_extension_status": "not_evaluated",
        "local_extension_disposition": "pending_independent_DAS_comparison",
        "catalog_conflict_STOP": False,
        "eligible_for_independent_DAS_comparison": True,
    }


def _unit(evaluation_unit_id: str, candidate_ids: str):
    return {
        "evaluation_unit_id": evaluation_unit_id,
        "catalog_event_id": "event_" + evaluation_unit_id,
        "known_event_class": "catalog_unassociated_generic_candidate",
        "candidate_count": len(candidate_ids.split(";")),
        "union_candidate_ids": candidate_ids,
        "interval_ids": "heldout_01",
        "branch_memberships": "generic_trigger",
        "catalog_detection_extension_status": "not_established",
        "repeater_family_extension_status": "not_evaluated",
        "catalog_conflict_STOP": False,
    }


class HeldoutComparisonRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = project_root()
        cls.registration = load_config(
            cls.root
            / "config"
            / "heldout_das_network_comparison.json"
        )
        cls.registration_status = _load_json(
            cls.root
            / "outputs"
            / "heldout_v2"
            / "registration"
            / "comparison_registration_status.json"
        )

    def test_matching_retains_every_source_row_once(self):
        das = [
            _das("heldout_01", "d1", 100.0),
            _das("heldout_01", "d2", 200.0),
        ]
        network = [
            _network("heldout_01", "n1", 105.0),
            _network("heldout_01", "n2", 300.0),
        ]
        rows = build_time_only_comparison(das, network, 8.0)
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row["comparison_membership"] for row in rows],
            ["DAS+network", "DAS_only", "network_only"],
        )
        self.assertAlmostEqual(rows[0]["DAS_minus_network_time_s"], -5.0)
        self.assertTrue(all(row["matching_rule"] == MATCHING_RULE for row in rows))
        self.assertTrue(
            all(row["catalog_fields_used_in_matching"] == 0 for row in rows)
        )

    def test_matching_never_crosses_intervals(self):
        rows = build_time_only_comparison(
            [_das("heldout_01", "d1", 100.0)],
            [_network("heldout_02", "n1", 100.0)],
            8.0,
        )
        self.assertEqual(
            [row["comparison_membership"] for row in rows],
            ["DAS_only", "network_only"],
        )

    def test_ordered_assignment_maximizes_pairs_then_minimizes_distance(self):
        rows = build_time_only_comparison(
            [
                _das("heldout_01", "d1", 100.0),
                _das("heldout_01", "d2", 109.0),
            ],
            [
                _network("heldout_01", "n1", 101.0),
                _network("heldout_01", "n2", 108.0),
            ],
            8.0,
        )
        matched = [
            row for row in rows if row["comparison_membership"] == "DAS+network"
        ]
        self.assertEqual(len(matched), 2)
        self.assertEqual(
            [(row["DAS_candidate_id"], row["network_union_candidate_id"]) for row in matched],
            [("d1", "n1"), ("d2", "n2")],
        )

    def test_leakage_and_support_repair_are_rejected(self):
        das = [_das("heldout_01", "d1", 100.0)]
        network = [_network("heldout_01", "n1", 100.0)]
        changed = copy.deepcopy(das)
        changed[0]["network_candidate_time_fields_read"] = 1
        with self.assertRaises(PermissionError):
            validate_candidate_rows(changed, network)
        changed = copy.deepcopy(das)
        changed[0]["minimum_required_strong_block_count"] = 3
        with self.assertRaises(PermissionError):
            validate_candidate_rows(changed, network)

    def test_network_context_preserves_matches_and_collapses_only_units(self):
        das = [_das("heldout_01", "d1", 100.0)]
        network = [
            _network("heldout_01", "n1", 100.5),
            _network("heldout_01", "n2", 300.0),
        ]
        time_only = build_time_only_comparison(das, network, 8.0)
        adjudicated = [
            _adjudicated(network[0], "unit_1"),
            _adjudicated(network[1], "unit_1"),
        ]
        context = attach_network_context(
            time_only,
            adjudicated,
            [_unit("unit_1", "n1;n2")],
        )
        for before, after in zip(time_only, context):
            self.assertEqual(
                {field: before[field] for field in before},
                {field: after[field] for field in before},
            )
        summary = build_interval_summary(
            context, ["heldout_01", "heldout_02"]
        )
        self.assertEqual(summary[0]["network_raw_candidate_count"], 2)
        self.assertEqual(summary[0]["network_event_unit_count"], 1)
        self.assertEqual(summary[0]["network_duplicate_raw_candidate_count"], 1)
        self.assertEqual(summary[0]["network_event_units_with_DAS_match"], 1)
        self.assertEqual(summary[1]["DAS_v2_candidate_count"], 0)
        self.assertEqual(summary[1]["network_raw_candidate_count"], 0)

    def test_DAS_only_context_remains_pending_not_extension(self):
        time_only = build_time_only_comparison(
            [_das("heldout_01", "d1", 100.0)], [], 8.0
        )
        context = attach_network_context(time_only, [], [])
        self.assertEqual(
            context[0]["DAS_detection_increment_status"],
            "PENDING_NOT_AN_EXTENSION_CLAIM",
        )
        self.assertEqual(context[0]["repeater_family_assignment"], "not_assigned")

    def test_absent_release_stops_before_any_access(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(PermissionError):
                load_runner_release(Path(directory))

    def test_bad_release_stops_before_candidate_reader(self):
        argv = ["run_heldout_das_network_comparison"]
        with patch.object(sys, "argv", argv), patch(
            "src.run_heldout_das_network_comparison.load_runner_release",
            return_value={"status": "STOP"},
        ), patch(
            "src.run_heldout_das_network_comparison._read_csv"
        ) as reader:
            with self.assertRaises(PermissionError):
                run_comparison_main()
            reader.assert_not_called()

    def test_runner_source_writes_hash_before_context_row_access(self):
        source = (
            self.root / "src" / "run_heldout_das_network_comparison.py"
        ).read_text(encoding="utf-8")
        write_index = source.index(
            '_write_csv(output["time_only"], time_only_rows, TIME_ONLY_FIELDS)'
        )
        hash_index = source.index(
            'time_only_sha256 = sha256_file(output["time_only"])'
        )
        context_index = source.index(
            'network_adjudicated_rows = _read_csv(paths["network_union_adjudicated"])'
        )
        self.assertLess(write_index, hash_index)
        self.assertLess(hash_index, context_index)

    def test_runner_modules_import_no_waveform_HDF5_or_family_reader(self):
        paths = [
            "src/heldout_comparison_runner.py",
            "src/heldout_comparison_runner_access.py",
            "src/run_heldout_das_network_comparison.py",
            "src/release_heldout_comparison_runner.py",
        ]
        banned = (
            "h5py",
            "src.h5io",
            "obspy",
            "network_continuous_detection",
            "das_continuous_detection",
            "michel_catalog",
        )
        for relative in paths:
            source = (self.root / relative).read_text(encoding="utf-8")
            ast.parse(source)
            for fragment in banned:
                self.assertNotIn(fragment, source, relative)

    def test_release_hash_set_covers_every_runtime_dependency(self):
        self.assertEqual(
            set(RUNNER_IMPLEMENTATION_PATHS),
            {
                "src/common.py",
                "src/network_union.py",
                "src/heldout_comparison_access.py",
                "src/register_heldout_das_network_comparison.py",
                "src/heldout_comparison_runner.py",
                "src/heldout_comparison_runner_access.py",
                "src/run_heldout_das_network_comparison.py",
                "src/release_heldout_comparison_runner.py",
                "tests/test_heldout_comparison_registration.py",
                "tests/test_heldout_comparison_runner.py",
            },
        )

    def test_release_helpers_and_output_paths_match_private_project(self):
        self.assertEqual(
            current_branch(self.root),
            "agent/freeze-incremental-value-checkpoint",
        )
        self.assertEqual(
            configured_remote_url(self.root, "origin"),
            "git@github.com:nataliaberrios/safod-das-repeaters.git",
        )
        paths = comparison_output_paths(self.root, self.registration)
        self.assertEqual(paths["root"].name, "comparison")
        self.assertEqual(paths["time_only"].name, "das_network_time_only.csv")


if __name__ == "__main__":
    unittest.main()
