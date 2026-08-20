"""Tests for the frozen DAS/network post-hoc comparison."""

from __future__ import annotations

import unittest

from src.das_network_comparison import (
    adjudicate_comparison,
    build_time_only_comparison,
)


def _das(candidate_id: str, epoch_s: float, score: float = 2.0):
    return {
        "candidate_id": candidate_id,
        "trigger_time": "1970-01-01T00:00:{:06.3f}Z".format(epoch_s),
        "trigger_epoch_s": epoch_s,
        "coincidence_score": score,
        "threshold": 1.4,
        "block_support_count_at_declared_ratio": 4,
    }


def _network(union_id: str, epoch_s: float):
    return {
        "union_candidate_id": union_id,
        "representative_time": "1970-01-01T00:00:{:06.3f}Z".format(
            epoch_s
        ),
        "representative_epoch_s": epoch_s,
        "representative_time_basis": "template_origin",
        "branch_membership": "template_bank+generic_trigger",
    }


def _associate(
    row,
    event_id: str,
    residual_s: float,
    distance_km: float,
):
    row.update(
        {
            "background_catalog_association": (
                "physically_plausible_known_event_arrival"
            ),
            "background_catalog_event_id": event_id,
            "background_catalog_origin_time": "1970-01-01T00:00:00Z",
            "background_catalog_location_name": "synthetic",
            "background_catalog_magnitude": 1.0,
            "background_catalog_horizontal_distance_km": distance_km,
            "background_catalog_path_distance_km": distance_km,
            "background_catalog_observed_delay_s": 1.0,
            "background_catalog_nominal_arrival_s": 1.0 - residual_s,
            "background_catalog_nominal_timing_residual_s": residual_s,
            "background_catalog_plausible_match_count": 1,
        }
    )
    return row


class DasNetworkComparisonTests(unittest.TestCase):
    def test_time_only_matching_retains_both_unmatched_sides(self):
        rows = build_time_only_comparison(
            [_das("d1", 100.0), _das("d2", 200.0)],
            [_network("n1", 105.0), _network("n2", 300.0)],
            8.0,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [row["comparison_membership"] for row in rows],
            ["DAS+network", "DAS_only", "network_only"],
        )
        self.assertAlmostEqual(rows[0]["DAS_minus_network_time_s"], -5.0)
        self.assertTrue(
            all(row["catalog_fields_used_in_matching"] == 0 for row in rows)
        )
        self.assertTrue(
            all(row["family_assignment"] == "not_assigned" for row in rows)
        )

    def test_catalog_dedup_prefers_same_network_event(self):
        d1 = _associate(_das("d1", 100.0, 1.5), "event_a", 5.0, 0.1)
        d2 = _associate(_das("d2", 102.0, 3.0), "event_a", 1.0, 0.1)
        d3 = _associate(_das("d3", 400.0, 2.0), "event_b", 1.0, 0.1)
        time_only = build_time_only_comparison(
            [d1, d2, d3],
            [_network("n1", 100.0)],
            8.0,
        )
        network_adjudicated = [
            {
                **_network("n1", 100.0),
                "known_event_class": "known_target_region_event",
                "broader_catalog_event_id": "event_a",
                "broader_catalog_origin_time": (
                    "1970-01-01T00:00:00Z"
                ),
                "target_region_catalog_event_id": "event_a",
                "target_region_catalog_origin_time": (
                    "1970-01-01T00:00:00Z"
                ),
            }
        ]
        adjudicated = adjudicate_comparison(
            time_only,
            [d1, d2, d3],
            network_adjudicated,
            0.75,
        )
        by_das = {
            row["DAS_candidate_id"]: row
            for row in adjudicated
            if row["DAS_candidate_id"]
        }
        self.assertEqual(
            by_das["d1"]["comparison_class"],
            "matched_frozen_network_known_target_event",
        )
        self.assertIs(by_das["d1"]["catalog_event_representative"], True)
        self.assertEqual(
            by_das["d2"]["comparison_class"],
            "secondary_trigger_for_same_catalog_event",
        )
        self.assertIs(by_das["d2"]["catalog_event_representative"], False)
        self.assertEqual(
            by_das["d3"]["comparison_class"],
            "DAS_only_catalog_compatible_target_event",
        )
        self.assertIs(
            by_das["d3"][
                "eligible_DAS_network_detection_increment_candidate"
            ],
            True,
        )
        self.assertIs(
            by_das["d3"]["eligible_catalog_extension_candidate"],
            False,
        )


if __name__ == "__main__":
    unittest.main()
