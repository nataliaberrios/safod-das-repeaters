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

Open notebooks/02_network_development_checkpoint.ipynb for the newer
network-continuous STOP checkpoint. It plots fixed-threshold injection recovery
and exposes only a non-writing SNR/acceptance sandbox.

Open notebooks/03_network_union_checkpoint.ipynb for the frozen network-union
checkpoint. It shows the time-only union and post-union catalog audit, and lets
an advisor vary the cross-branch matching window in memory without changing the
registered result.

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

## Network development STOP checkpoint

On the registered 50-minute nonblind interval, 29 components from 10 stations
passed strict continuous-data QC. The frozen-style repeater-template bank found
both Parkfield catalog events. An independent four-station energy trigger found
those two plus a third trigger; a broader official NCEDC query shows that the
third is a physically plausible arrival from event 75120096, M2.3 near
Carpinteria, 197.2 km away. It is not evidence for an uncataloged local event.

In 300 injections including zero-amplitude controls, the exact injected event
was removed from the template bank and neither detector threshold was changed. At
the registered component SNR of 1.0, the template bank recovered 30/30 target
injections, while the generic trigger recovered 22/30 and therefore failed its
predeclared 90% gate. At SNR 2.0 the generic trigger recovered 30/30. Preserve
this STOP when deciding whether that branch should be improved, replaced, or
retained only as an auxiliary safety net. No DAS or held-out waveform was
opened.

## Frozen network-union checkpoint

Network-union version 1 preserves both v4 thresholds and the generic SNR-1
STOP. The template bank is registered as the primary target-sensitive branch;
the generic trigger is retained as an auxiliary non-template safety net with
its measured recovery curve. It is not promoted to a passing sensitivity
branch.

The five branch-level detections become three event groups under an ordered,
one-to-one, time-only 8-second matching rule. Catalog fields and family labels
are absent from that grouping step. A separate audit then identifies two known
local earthquakes and one known regional arrival from event 75120096 near
Carpinteria. All three rows remain in the raw union; the regional arrival is
excluded only from an uncataloged-local-extension count. There are zero
unassociated local candidates in this 50-minute development interval.

This freezes the comparator for independent DAS-only development. It does not
authorize held-out access or demonstrate held-out network performance.

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
    python -m src.run_network_development_detector
    python -m src.freeze_network_union
    python -m unittest discover -s tests -v
    python ../../run_nb_cells.py notebooks/00_advisor_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/02_network_development_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/03_network_union_checkpoint.ipynb

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
- config/development_detection.json records the nonblind continuous-network
  detector, null, regional-catalog veto, and leakage-safe injection design.
- config/network_union.json pins all v4 input checksums, declares branch roles,
  and freezes time-only matching and post-union catalog-adjudication rules.
- outputs/development_network/status.json preserves the v4 generic-trigger STOP;
  candidate tables and injection_recovery_summary.csv provide its evidence.
  Full-rate scores, miniSEED, and the downloaded catalog cache remain ignored
  local products.
- outputs/development_network/network_candidate_union_time_only.csv is the
  catalog-blind event union. network_candidate_union_adjudicated.csv attaches
  known-event evidence afterward, and network_union_status.json records the
  development-only access gate.
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

The network union is now frozen on the nonblind 2025-01-20 04:55--05:45 UTC
interval. Continue on that same interval only:

1. register a DAS-only configuration and an access guard that forbids imports
   of network candidate times, catalog event times, and held-out intervals;
2. develop candidate generation from continuous DAS data alone, using array
   coherence and/or DAS template evidence with empirical nulls;
3. materialize the DAS-only candidate table before comparing it with the frozen
   network union;
4. freeze cross-pipeline event matching, blinded adjudication, and matched-FDR
   rules; and
5. only then open any of the 12 held-out hours.

A positive DAS result must add independently adjudicated events beyond the full
network union or improve held-out family resolution. A visually striking record
section is not the pass criterion.
