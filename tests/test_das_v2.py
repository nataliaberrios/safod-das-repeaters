"""Regression tests for the frozen DAS version-2 support gate."""

from __future__ import annotations

import unittest

from src.common import load_config, project_root
from src.das_v2 import select_v2_candidates, validate_v2_inheritance


def _v1_candidate(
    candidate_id: str,
    epoch_s: float,
    score: float,
    support_count: int,
):
    return {
        "candidate_id": candidate_id,
        "trigger_time": "1970-01-01T00:00:00Z",
        "trigger_epoch_s": epoch_s,
        "coincidence_score": score,
        "threshold": 1.4,
        "coincidence_block_count": 4,
        "block_support_count_at_declared_ratio": support_count,
        "block_characteristic_support_threshold": 2.0,
        "network_candidate_time_fields_read": 0,
        "catalog_event_time_fields_read": 0,
        "heldout_interval_fields_read": 0,
        "family_assignment": "not_assigned",
    }


class DasV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = project_root()
        cls.v2 = load_config(root / "config" / "das_v2_validation.json")
        cls.v1 = load_config(root / "config" / "das_development.json")

    def test_v2_inherits_every_v1_detector_setting_except_support_gate(self):
        validate_v2_inheritance(self.v2, self.v1)
        self.assertEqual(
            self.v2["v2_candidate_rule"]["minimum_strong_block_count"],
            4,
        )
        self.assertEqual(
            self.v2["v2_candidate_rule"][
                "strong_block_characteristic_ratio"
            ],
            2.0,
        )

    def test_v2_gate_is_a_chronological_subset_with_frozen_ranks(self):
        selected = select_v2_candidates(
            [
                _v1_candidate("v1_a", 100.0, 1.5, 3),
                _v1_candidate("v1_b", 110.0, 2.0, 4),
                _v1_candidate("v1_c", 120.0, 4.0, 10),
            ],
            self.v2,
            identifier_prefix="test_v2",
        )
        self.assertEqual(
            [row["parent_v1_candidate_id"] for row in selected],
            ["v1_b", "v1_c"],
        )
        self.assertEqual(
            [row["candidate_id"] for row in selected],
            ["test_v2_0001", "test_v2_0002"],
        )
        self.assertEqual(
            [row["v1_score_rank"] for row in selected],
            [2, 1],
        )
        self.assertTrue(
            all(
                row["strong_block_count"]
                >= row["minimum_required_strong_block_count"]
                for row in selected
            )
        )
        self.assertTrue(
            all(
                row["family_assignment"] == "not_assigned"
                for row in selected
            )
        )

    def test_v2_rejects_a_candidate_with_leaked_network_input(self):
        row = _v1_candidate("v1_leaked", 100.0, 3.0, 8)
        row["network_candidate_time_fields_read"] = 1
        with self.assertRaises(PermissionError):
            select_v2_candidates([row], self.v2)


if __name__ == "__main__":
    unittest.main()
