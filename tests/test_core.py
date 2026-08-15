"""Small deterministic tests for timestamps, HDF5 boundaries, and source gates."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from src.background_catalog import associate_background_arrivals
from src.catalog import _normalize_ncedc_utc, local_offsets_m
from src.external_catalog import (
    build_shortlist_crosswalk,
    parse_waldhauser_schaff,
)
from src.archive_population import (
    CoverageSegment,
    ManifestRecord,
    merge_coverage,
    select_heldout_intervals,
)
from src.common import sha256_file
from src.continuous_network import validate_development_access
from src.h5io import read_header, read_window
from src.injection_recovery import select_injection_positions
from src.michel_catalog import (
    exact_id_crosswalk,
    parse_michel_catalog,
    partition_conflicts,
    sequence_overlap_matrix,
)
from src.network_continuous_detection import (
    coincidence_score,
    detect_generic_candidates,
    higher_quantile,
    rolling_normalized_correlation,
    station_energy_ratio_matrix,
)
from src.network_baseline import (
    apply_frozen_thresholds,
    fit_frozen_thresholds,
    pair_feature_rows,
)
from src.network_family import (
    classification_summary,
    leave_one_event_out_classification,
)
from src.network_union import (
    adjudicate_time_only_union,
    build_time_only_union,
    ordered_time_pairs,
)
from src.source_physics import (
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

    def test_michel_exact_id_partition_conflict(self) -> None:
        text = (
            "0 2010 1 1 0 0 0.000 36.0 -120.0 0.0 0.0 4000.0 1.0 00413 1 101\n"
            "1 2010 1 1 0 0 1.250 36.0 -120.0 0.0 0.0 4001.0 1.1 00414 1 102\n"
            "2 2010 1 1 0 0 2.500 36.0 -120.0 0.0 0.0 4002.0 1.2 00414 2 201\n"
        )
        published = [
            {
                "event_id": "101",
                "sequence_id": "R.target",
                "validation_role": "target_positive",
                "origin_time": "2010-01-01T00:00:00.000Z",
            },
            {
                "event_id": "102",
                "sequence_id": "R.target",
                "validation_role": "target_positive",
                "origin_time": "2010-01-01T00:00:01.250Z",
            },
            {
                "event_id": "201",
                "sequence_id": "R.neighbor",
                "validation_role": "neighbor_family_negative",
                "origin_time": "2010-01-01T00:00:02.500Z",
            },
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "michel.txt"
            path.write_text(text, encoding="utf-8")
            events = parse_michel_catalog(path)
        self.assertEqual(
            [row["event_id"] for row in events],
            ["101", "102", "201"],
        )
        self.assertEqual(
            events[1]["origin_time"],
            "2010-01-01T00:00:01.250Z",
        )
        crosswalk = exact_id_crosswalk(published, events)
        overlap = sequence_overlap_matrix(crosswalk)
        conflicts = partition_conflicts(overlap)
        conflict_types = {row["conflict_type"] for row in conflicts}
        self.assertEqual(
            conflict_types,
            {
                "waldhauser_schaff_family_split_by_michel",
                "michel_family_merges_waldhauser_schaff_families",
            },
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

    def test_manifest_coverage_merge_and_seeded_interval_selection(self) -> None:
        records = [
            ManifestRecord("a", 0.0, 60.0, 500.0, 900, 16.335237503051758),
            ManifestRecord("b", 60.005, 120.0, 500.0, 900, 16.335237503051758),
            ManifestRecord("c", 200.0, 400.0, 500.0, 900, 16.335237503051758),
        ]
        segments = merge_coverage(records, maximum_gap_s=0.01)
        self.assertEqual(len(segments), 2)
        self.assertAlmostEqual(segments[0].duration_s, 120.0)
        long_segments = [CoverageSegment(0, 1000.0, 10000.0, 150)]
        first = select_heldout_intervals(
            long_segments,
            duration_s=3600.0,
            count=1,
            seed=20260815,
            exclusions=[(1000.0, 5000.0)],
        )
        second = select_heldout_intervals(
            long_segments,
            duration_s=3600.0,
            count=1,
            seed=20260815,
            exclusions=[(1000.0, 5000.0)],
        )
        self.assertEqual(first, second)
        self.assertGreaterEqual(
            datetime.fromisoformat(first[0]["start_utc"].replace("Z", "+00:00")).timestamp(),
            5000.0,
        )

    def test_network_differential_lag_feature_and_freeze(self) -> None:
        metrics = []
        for station, lag_s, correlation in (
            ("S1", 0.008, 0.96),
            ("S2", 0.010, 0.97),
            ("S3", 0.012, 0.95),
        ):
            metrics.append(
                {
                    "reference_event_id": "a",
                    "comparison_event_id": "b",
                    "trace_id": "BP.{}.DP1".format(station),
                    "station": station,
                    "band_low_hz": 5.0,
                    "band_high_hz": 20.0,
                    "correlation": correlation,
                    "lag_s": lag_s,
                    "usable": True,
                }
            )
        settings = {
            "model_band_hz": [5.0, 20.0],
            "minimum_pair_components": 3,
            "minimum_pair_stations": 3,
        }
        features = pair_feature_rows(metrics, settings)
        self.assertEqual(features[0]["status"], "PASS")
        self.assertAlmostEqual(features[0]["common_lag_s"], 0.010)
        self.assertAlmostEqual(
            features[0]["differential_lag_rms_s"],
            np.sqrt((0.002 ** 2 + 0.002 ** 2) / 3.0),
        )
        scores = [
            {
                "event_id": "t1",
                "is_published_target": True,
                "score_status": "PASS",
                "median_target_correlation": 0.98,
                "median_target_differential_lag_rms_s": 0.001,
                "score_reason": "eligible",
            },
            {
                "event_id": "t2",
                "is_published_target": True,
                "score_status": "PASS",
                "median_target_correlation": 0.97,
                "median_target_differential_lag_rms_s": 0.002,
                "score_reason": "eligible",
            },
            {
                "event_id": "n1",
                "is_published_target": False,
                "score_status": "PASS",
                "median_target_correlation": 0.98,
                "median_target_differential_lag_rms_s": 0.015,
                "score_reason": "eligible",
            },
            {
                "event_id": "n2",
                "is_published_target": False,
                "score_status": "PASS",
                "median_target_correlation": 0.80,
                "median_target_differential_lag_rms_s": 0.002,
                "score_reason": "eligible",
            },
        ]
        model = fit_frozen_thresholds(scores)
        self.assertAlmostEqual(model["balanced_accuracy"], 1.0)
        decisions = apply_frozen_thresholds(scores, model)
        predicted = {row["event_id"]: row["frozen_decision"] for row in decisions}
        self.assertEqual(predicted["t1"], "target_family")
        self.assertEqual(predicted["n1"], "not_target_family")
        self.assertEqual(predicted["n2"], "not_target_family")


    def test_continuous_development_access_guard(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parent_path = root / "parent.json"
            parent_path.write_text(
                json.dumps(
                    {
                        "development_interval": {
                            "start_utc": "2025-01-20T04:55:00Z",
                            "end_utc": "2025-01-20T05:45:00Z",
                        }
                    }
                ),
                encoding="utf-8",
            )
            parent_hash = sha256_file(parent_path)
            parent = {
                "_config_sha256": parent_hash,
                "development_interval": {
                    "start_utc": "2025-01-20T04:55:00Z",
                    "end_utc": "2025-01-20T05:45:00Z",
                },
            }
            development = {
                "parent_config_path": str(parent_path),
                "parent_config_sha256": parent_hash,
                "interval": {
                    "start_utc": "2025-01-20T04:55:00Z",
                    "end_utc": "2025-01-20T05:45:00Z",
                    "duration_s": 3000,
                    "role": "nonblind_development_only",
                    "heldout_access": "FORBIDDEN",
                },
            }
            start_s, end_s = validate_development_access(
                parent, development
            )
            self.assertEqual(end_s - start_s, 3000.0)
            unsafe = copy.deepcopy(development)
            unsafe["interval"]["heldout_access"] = "ALLOWED"
            with self.assertRaises(PermissionError):
                validate_development_access(parent, unsafe)

    def test_injection_positions_are_reproducible_and_clean(self) -> None:
        start_s = datetime(
            2025, 1, 1, tzinfo=timezone.utc
        ).timestamp()
        development = {
            "interval": {
                "start_utc": "2025-01-01T00:00:00.000Z",
                "end_utc": "2025-01-01T00:10:00.000Z",
            },
            "injection_recovery": {
                "local_window_s": [-12.0, 14.0],
                "position_grid_interval_s": 1.0,
                "event_exclusion_half_width_s": 25.0,
                "noise_position_count": 4,
                "minimum_position_separation_s": 60.0,
                "random_seed": 17,
            },
        }
        excluded = [start_s + 300.0]
        first = select_injection_positions(development, excluded)
        second = select_injection_positions(development, excluded)
        np.testing.assert_array_equal(first, second)
        self.assertEqual(len(first), 4)
        self.assertTrue(np.all(first >= start_s + 12.0))
        self.assertTrue(np.all(first < start_s + 600.0 - 14.0))
        self.assertTrue(np.all(np.abs(first - excluded[0]) > 25.0))
        differences = np.diff(first)
        self.assertTrue(np.all(differences >= 60.0))

    def test_rolling_correlation_recovers_injected_template(self) -> None:
        rng = np.random.default_rng(7)
        template = rng.normal(size=200)
        continuous = 0.05 * rng.normal(size=2000)
        injection_index = 731
        continuous[
            injection_index : injection_index + len(template)
        ] += template
        correlation = rolling_normalized_correlation(
            continuous, template
        )
        self.assertEqual(int(np.nanargmax(correlation)), injection_index)
        self.assertGreater(float(np.nanmax(correlation)), 0.99)
        self.assertEqual(higher_quantile([0.1, 0.2, 0.3, 0.4], 0.75), 0.3)

    def test_background_catalog_selects_physical_arrival(self) -> None:
        origin_s = datetime(
            2025, 1, 1, tzinfo=timezone.utc
        ).timestamp()
        candidates = [{"trigger_epoch_s": origin_s + 30.0}]
        catalog = [
            {
                "event_id": "regional",
                "origin_time": "2025-01-01T00:00:00.000Z",
                "latitude": 0.0,
                "longitude": 1.62,
                "depth_km": 0.0,
                "magnitude": 2.0,
                "location_name": "synthetic regional",
            },
            {
                "event_id": "future",
                "origin_time": "2025-01-01T00:00:40.000Z",
                "latitude": 0.0,
                "longitude": 0.0,
                "depth_km": 4.0,
                "magnitude": 1.0,
                "location_name": "synthetic future",
            },
        ]
        parent = {
            "family_neighborhood": {
                "reference_latitude": 0.0,
                "reference_longitude": 0.0,
            }
        }
        development = {
            "background_catalog": {
                "array_reference_depth_km": 0.0,
                "minimum_plausible_phase_velocity_km_s": 2.5,
                "maximum_plausible_phase_velocity_km_s": 8.0,
                "nominal_ranking_phase_velocity_km_s": 6.0,
                "early_arrival_slack_s": 2.0,
                "late_arrival_slack_s": 5.0,
            }
        }
        associate_background_arrivals(
            candidates, catalog, parent, development
        )
        self.assertEqual(
            candidates[0]["background_catalog_event_id"], "regional"
        )
        self.assertAlmostEqual(
            candidates[0]["background_catalog_observed_delay_s"], 30.0
        )
        self.assertEqual(
            candidates[0]["background_catalog_plausible_match_count"], 1
        )

    def test_station_energy_ratio_is_scale_invariant(self) -> None:
        rng = np.random.default_rng(11)
        base = 0.05 * rng.normal(size=4000)
        phase = np.linspace(0.0, 8.0 * np.pi, 100, endpoint=False)
        base[2000:2100] += np.sin(phase)
        matrix, stations, component_count = station_energy_ratio_matrix(
            {
                "BP.A.DP1": base,
                "BP.B.DP1": 7.0 * base,
            },
            sample_rate_hz=100.0,
            sta_window_s=0.5,
            lta_window_s=10.0,
        )
        self.assertEqual(stations, ["BP.A", "BP.B"])
        self.assertEqual(component_count, 2)
        usable = np.isfinite(matrix[0]) & np.isfinite(matrix[1])
        np.testing.assert_allclose(
            matrix[0, usable], matrix[1, usable], rtol=2.0e-6
        )

    def test_generic_coincidence_rejects_one_station_spike(self) -> None:
        station_matrix = np.ones((5, 200), dtype=np.float32)
        station_matrix[0, 50] = 9.0
        station_matrix[:4, 120] = 4.0
        score = coincidence_score(station_matrix, station_count=4)
        self.assertEqual(int(np.argmax(score)), 120)
        self.assertEqual(float(score[50]), 1.0)
        development = {
            "generic_network_trigger": {
                "coincidence_station_count": 4,
                "candidate_minimum_separation_s": 2.0,
                "score_sample_rate_hz": 20.0,
                "station_characteristic_support_threshold": 2.0,
            }
        }
        epochs = np.arange(200, dtype=float) / 20.0
        candidates, detected_score = detect_generic_candidates(
            epochs,
            station_matrix,
            threshold=3.0,
            development=development,
        )
        self.assertEqual(len(candidates), 1)
        self.assertAlmostEqual(candidates[0]["trigger_epoch_s"], 6.0)
        self.assertEqual(
            candidates[0]["station_support_count_at_declared_ratio"], 4
        )
        np.testing.assert_array_equal(score, detected_score)

    def test_network_union_matching_maximizes_pair_count(self) -> None:
        pairs = ordered_time_pairs(
            template_times=[0.0, 10.0],
            generic_times=[6.0, 15.0],
            maximum_difference_s=6.0,
        )
        self.assertEqual(pairs, ((0, 0), (1, 1)))

    def test_network_union_is_catalog_blind_then_adjudicated(self) -> None:
        template = [
            {
                "candidate_id": "template_1",
                "origin_time": "1970-01-01T00:01:40.000Z",
                "origin_epoch_s": 100.0,
                "bank_score": 0.3,
                "threshold": 0.2,
                "catalog_association": "within_tolerance",
                "nearest_catalog_event_id": "local",
                "nearest_catalog_origin_time": (
                    "1970-01-01T00:01:40.000Z"
                ),
            }
        ]
        generic = [
            {
                "candidate_id": "generic_1",
                "trigger_time": "1970-01-01T00:01:42.000Z",
                "trigger_epoch_s": 102.0,
                "coincidence_score": 4.0,
                "threshold": 2.0,
                "station_support_count_at_declared_ratio": 5,
                "catalog_association": "within_tolerance",
                "nearest_catalog_event_id": "local",
                "nearest_catalog_origin_time": (
                    "1970-01-01T00:01:40.000Z"
                ),
                "background_catalog_association": (
                    "physically_plausible_known_event_arrival"
                ),
                "background_catalog_event_id": "local",
                "background_catalog_origin_time": (
                    "1970-01-01T00:01:40.000Z"
                ),
                "background_catalog_horizontal_distance_km": 0.1,
            },
            {
                "candidate_id": "generic_2",
                "trigger_time": "1970-01-01T00:03:20.000Z",
                "trigger_epoch_s": 200.0,
                "coincidence_score": 5.0,
                "threshold": 2.0,
                "station_support_count_at_declared_ratio": 6,
                "catalog_association": "outside_tolerance",
                "nearest_catalog_event_id": "local",
                "nearest_catalog_origin_time": (
                    "1970-01-01T00:01:40.000Z"
                ),
                "background_catalog_association": (
                    "physically_plausible_known_event_arrival"
                ),
                "background_catalog_event_id": "regional",
                "background_catalog_origin_time": (
                    "1970-01-01T00:02:50.000Z"
                ),
                "background_catalog_horizontal_distance_km": 100.0,
            },
        ]
        union = build_time_only_union(template, generic, 8.0)
        mutated_template = copy.deepcopy(template)
        mutated_generic = copy.deepcopy(generic)
        mutated_template[0]["nearest_catalog_event_id"] = "changed"
        mutated_generic[0]["catalog_association"] = "outside_tolerance"
        mutated_generic[1]["background_catalog_event_id"] = "changed"
        mutated_union = build_time_only_union(
            mutated_template, mutated_generic, 8.0
        )
        self.assertEqual(union, mutated_union)
        self.assertTrue(
            all(row["catalog_fields_used_in_grouping"] == 0 for row in union)
        )
        self.assertTrue(
            all("nearest_catalog_event_id" not in row for row in union)
        )

        adjudicated = adjudicate_time_only_union(
            union,
            template,
            generic,
            maximum_target_horizontal_distance_km=0.75,
        )
        by_id = {
            row["union_candidate_id"]: row for row in adjudicated
        }
        self.assertEqual(
            by_id["network_union_dev_0001"]["known_event_class"],
            "known_target_region_event",
        )
        self.assertEqual(
            by_id["network_union_dev_0002"]["known_event_class"],
            "known_regional_arrival_outside_target_region",
        )
        self.assertTrue(
            all(row["family_assignment"] == "not_assigned" for row in union)
        )


if __name__ == "__main__":
    unittest.main()
