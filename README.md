# SAFOD DAS/repeater incremental-value experiment

This is a standalone clean-room project for one deliberately demanding
question:

> What does near-fault DAS add beyond a fair conventional seismic-network
> workflow for detecting and assigning SAFOD repeating earthquakes?

The current answer is: **the project is worth pursuing, but DAS catalog
extension is not yet demonstrated.** The strongest opportunity is now a real
published-catalog partition disagreement near the deep fiber, not a
correlation-defined binary label that both catalogs support.

Read PROJECT_SPEC.md before interpreting an output. Fiber distance is not depth
or source distance, a high waveform correlation is not a family label, and a
DAS-visible event is not automatically a DAS catalog extension.

## Advisor checkpoint

Open notebooks/00_advisor_checkpoint.ipynb and run all cells. It reads compact
tracked products by default and does not open raw DAS or download network data.
The first control cell lets an advisor vary correlation and differential-lag
thresholds as an explicitly exploratory sandbox; it never overwrites the frozen
model.

The checkpoint currently records five decisive facts:

1. A 12-event conventional model was frozen before prospective waveform access.
2. All five 2024--2025 routine-catalog proximity candidates fail that frozen
   target-family verifier; no DAS waveforms were opened to obtain the result.
3. Exact event IDs show that Waldhauser--Schaff R1.2900.11955.0 is split between
   Michel M00413 and M00414, while Michel M00414 merges the former target and
   hard-negative populations.
4. Conventional array correlation does not separate the Michel partition
   (within-versus-between pair AUC 0.417); differential lag is only modestly
   better (AUC 0.630) and cannot support event-bootstrap uncertainty here.
5. The 2026 deep-DAS event 75336682 passes the frozen network verifier, while
   same-fiber control 75343317 fails. The candidate is also strongly visible on
   deep DAS. Its family name remains provisional because the published
   partitions disagree.

Those facts reshape the work into two linked primary aims:

- **Family-partition resolution:** test whether dense near-source DAS supports
  the M00413/M00414 split or merger better than the conventional array.
- **Catalog extension:** compare independently triggered network-only and
  DAS-only continuous detectors on identical UTC intervals at matched
  event-level false-discovery rate.

Stress drop and repeater-derived creep rate remain downstream branches.

## Reproduce compact products

Run from this repository root in the das conda environment. The order preserves
the access embargo: held-out intervals are selected first, the historical
network model is frozen second, and only then are prospective network windows
released.

    conda activate das
    python -m src.build_archive_population
    python -m src.run_incremental_network_baseline
    python -m src.build_archive_population
    python -m src.score_prospective_network
    python -m src.build_michel_validation
    python -m src.score_michel_continuations
    python -m src.build_partition_diagnostic
    python -m src.score_deep_named_network
    python -m src.build_checkpoint
    python -m unittest discover -s tests -v
    python ../../run_nb_cells.py notebooks/00_advisor_checkpoint.ipynb

The older clean-room pilot inputs can be rebuilt explicitly when needed:

    python -m src.build_coverage_ledger
    python -m src.run_hrsn_pilot --max-family-candidates 38
    python -m src.run_deep_das_pilot
    python -m src.run_active_source_calibration
    python -m src.build_external_validation
    python -m src.run_network_family_benchmark

These commands may read shared read-only data or retrieve official waveform
windows. The advisor notebook does neither by default.

## Canonical products

- config/incremental_value.json freezes the archive population, development
  interval, 12 held-out hours, conventional array, feature definition, and
  access order.
- outputs/incremental_value/network_baseline/network_model_frozen.json is the
  version-1 conventional verifier. Its thresholds may not be repaired after
  seeing prospective outcomes.
- outputs/incremental_value/prospective_network/frozen_network_decisions.csv
  holds the five blind network-only decisions for routine-catalog proximity
  candidates.
- outputs/michel_validation/catalog_partition_conflicts.csv and
  sequence_overlap_matrix.csv preserve the exact-ID disagreement between the
  two independent published catalogs.
- outputs/michel_validation/partition_diagnostic/ contains the conventional
  M00413/M00414 comparator that DAS must improve.
- outputs/incremental_value/deep_named_network/decisions.csv holds the frozen
  network decisions for the named 2026 deep candidate and hard control.
- outputs/incremental_value/heldout_intervals.csv contains the sealed,
  manifest-only one-hour intervals.
- outputs/checkpoint/advisor_checkpoint.json is the current claim and next-gate
  ledger.

Downloaded public catalog text and miniSEED waveform caches are ignored by Git.
Small request provenance, checksums, event IDs, UTC windows, source paths, and
configuration hashes remain reproducible.

## Data and geometry cautions

The archive manifest contains 378,050 time-valid rows in the primary
configuration. Sixteen rows are placeholders or numerically invalid, and one
otherwise primary row has invalid timestamps. This reconciles the earlier
378,051 numeric-primary count without silently changing it.

The deep-fiber audit found channel spacing and a channel-1702 hairpin convention,
but no surveyed channel-to-MD/TVD/XYZ/cable-leg/tangent mapping. Until that
mapping exists, plots and features may use channel or fiber-distance
coordinates, but not absolute depth, source distance, radiation direction, or
stress-drop geometry.

## Next registered computation

Use the frozen nonblind 2025-01-20 04:55--05:45 UTC development interval
(50 minutes) only:

1. build and freeze a best-effort continuous network-only detector;
2. run an independently triggered DAS-only detector on exactly that interval;
3. adjudicate the union without showing pipeline identity;
4. compare recall at matched event-level false-discovery rate; and
5. freeze both pipelines before opening any of the 12 held-out hours.

A positive DAS result requires additional independently adjudicated events or
better held-out family resolution. A visually striking record section is not
the pass criterion.
