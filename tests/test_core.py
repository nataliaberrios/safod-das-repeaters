"""Small deterministic tests for timestamps, HDF5 boundaries, and source gates."""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from faultzone.repeaters_v2.src.catalog import _normalize_ncedc_utc, local_offsets_m
from faultzone.repeaters_v2.src.external_catalog import (
    build_shortlist_crosswalk,
    parse_waldhauser_schaff,
)
from faultzone.repeaters_v2.src.h5io import read_header, read_window
from faultzone.repeaters_v2.src.network_family import (
    classification_summary,
    leave_one_event_out_classification,
)
from faultzone.repeaters_v2.src.source_physics import (
    brune_ratio,
    fit_brune_ratio_fixed_egf,
    relative_stress_drop,
)


def _write_h5(path: Path, start_s: float, start_value: int) -> None:
    sample_rate = 100.0
    sample_count = 100
    times_us = np.round(
        (start_s + np.arange(sample_count) / sample_rate) * 1.0e6
    ).astype(np.int64)
    with h5py.File(path, "w") as handle:
        acquisition = handle.create_group("Acquisition")
        acquisition.attrs["SpatialSamplingInterval"] = 2.0
        acquisition.attrs["GaugeLength"] = 10.0
        acquisition.attrs["StartLocusIndex"] = 1800
        custom = acquisition.create_group("Custom")
        custom.attrs["GPS Enabled"] = 1
        custom.attrs["GPS Sync Guaranteed"] = 0
        raw = acquisition.create_group("Raw[0]")
        raw.attrs["OutputDataRate"] = sample_rate
        raw.attrs["StartLocusIndex"] = 1800
        raw.attrs["RawDataUnit"] = "rad * 2PI/2^16"
        raw.create_dataset(
            "RawData",
            data=np.arange(start_value, start_value + 200, dtype=np.int32).reshape(100, 2),
        )
        raw.create_dataset("RawDataTime", data=times_us)


class CoreTests(unittest.TestCase):
    def test_ncedc_time_normalization(self) -> None:
        self.assertEqual(
            _normalize_ncedc_utc("2014-02-02T21:41:40.5500"),
            "2014-02-02T21:41:40.550Z",
        )

    def test_local_distance(self) -> None:
        reference = {"latitude": 36.0, "longitude": -120.0, "depth_km": 5.0}
        event = {"latitude": 36.0, "longitude": -120.0, "depth_km": 5.010}
        self.assertAlmostEqual(local_offsets_m(event, reference)["distance_3d_m"], 10.0)

    def test_h5_boundary_and_locus_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            start = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
            first = root / "test_2026-01-01T000000Z.h5"
            second = root / "test_2026-01-01T000001Z.h5"
            _write_h5(first, start, 0)
            _write_h5(second, start + 1.0, 1000)
            header = read_header(first)
            self.assertEqual(header.status, "ok")
            self.assertEqual(header.channel_count, 2)
            window = read_window([first, second], start + 0.5, start + 1.5)
            self.assertEqual(window.data.shape, (100, 2))
            np.testing.assert_array_equal(window.column_indices, [0, 1])
            np.testing.assert_array_equal(window.locus_indices, [1800, 1801])
            self.assertAlmostEqual(window.missing_fraction, 0.0)
            self.assertLess(window.maximum_gap_s, 0.011)
            self.assertEqual(len(window.source_files), 2)

    def test_synthetic_corner_recovery_and_boundary_stop(self) -> None:
        frequency = np.linspace(2.0, 80.0, 400)
        ratio = brune_ratio(frequency, 1.7, 18.0, 45.0)
        usable = (frequency >= 5.0) & (frequency <= 60.0)
        fit = fit_brune_ratio_fixed_egf(frequency, ratio, usable, 45.0)
        self.assertEqual(fit.status, "PASS")
        self.assertAlmostEqual(fit.target_corner_hz, 18.0, delta=0.2)
        boundary_ratio = brune_ratio(frequency, 1.7, 90.0, 45.0)
        boundary = fit_brune_ratio_fixed_egf(
            frequency, boundary_ratio, usable, 45.0
        )
        self.assertEqual(boundary.status, "STOP")

    def test_relative_stress_drop(self) -> None:
        self.assertAlmostEqual(relative_stress_drop(2.0, 20.0, 10.0), 16.0)

    def test_external_catalog_exact_id_crosswalk(self) -> None:
        text = (
            "# 2 36.0 -120.0 4.0 0.1 0.1 10 1 0.1 10 1 0.1 0.95 R.target\n"
            "2009 1 1 0 0 0.000 1 36.0 -120.0 4.0 1 1 1 1.0 0.0 0.1 0.95 101\n"
            "2010 1 1 0 0 0.000 366 36.0 -120.0 4.0 1 1 1 1.0 0.0 0.1 0.95 102\n"
            "# 2 36.1 -120.1 4.1 0.1 0.1 10 1 0.1 10 1 0.1 0.94 R.neighbor\n"
            "2009 2 1 0 0 0.000 32 36.1 -120.1 4.1 1 1 1 1.0 0.0 0.1 0.94 201\n"
            "2010 2 1 0 0 0.000 397 36.1 -120.1 4.1 1 1 1 1.0 0.0 0.1 0.94 202\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "catalog.txt"
            path.write_text(text, encoding="utf-8")
            sequences, events = parse_waldhauser_schaff(path)
        self.assertEqual(len(sequences), 2)
        self.assertEqual(len(events), 4)
        shortlist = [
            {
                "event_id": "101",
                "origin_time": "2009-01-01T00:00:00.000Z",
                "magnitude": 1.0,
                "decision": "family",
                "membership_status": "single_anchor_high_similarity",
            },
            {
                "event_id": "201",
                "origin_time": "2009-02-01T00:00:00.000Z",
                "magnitude": 1.0,
                "decision": "family",
                "membership_status": "single_anchor_high_similarity",
            },
        ]
        ncedc = [
            {
                "event_id": row["event_id"],
                "origin_time": row["origin_time"],
                "latitude": 36.0,
                "longitude": -120.0,
                "depth_km": 4.5,
            }
            for row in shortlist
        ]
        crosswalk = build_shortlist_crosswalk(
            shortlist,
            ncedc,
            events,
            "R.target",
            ["R.neighbor"],
            "2014-12-31T23:59:59Z",
        )
        by_id = {row["event_id"]: row for row in crosswalk}
        self.assertEqual(
            by_id["101"]["external_role"], "published_target_positive"
        )
        self.assertEqual(
            by_id["201"]["diagnostic_outcome"],
            "discordant_single_anchor_overmerge",
        )

    def test_multi_anchor_leave_one_event_out_classifier(self) -> None:
        population = []
        for family, prefix in (("family_a", "a"), ("family_b", "b")):
            for index in range(3):
                population.append(
                    {
                        "event_id": "{}{}".format(prefix, index),
                        "origin_time": "2010-01-01T00:00:00.000Z",
                        "sequence_id": family,
                        "validation_role": "target_positive"
                        if family == "family_a"
                        else "neighbor_family_negative",
                    }
                )
        summaries = []
        for first_index, first in enumerate(population):
            for second in population[first_index + 1 :]:
                same = first["sequence_id"] == second["sequence_id"]
                summaries.append(
                    {
                        "reference_event_id": first["event_id"],
                        "comparison_event_id": second["event_id"],
                        "status": "PASS",
                        "overall_median_correlation": 0.96 if same else 0.55,
                    }
                )
        scores, classifications = leave_one_event_out_classification(
            population,
            summaries,
            [row["event_id"] for row in population],
            maximum_anchors_per_family=3,
            minimum_score=0.8,
            minimum_margin=0.2,
        )
        self.assertEqual(len(scores), 12)
        self.assertTrue(all(row["correct"] for row in classifications))
        summary = classification_summary(classifications, "family_a")
        self.assertAlmostEqual(summary["macro_f1_with_abstention_as_error"], 1.0)
        self.assertAlmostEqual(summary["target_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
