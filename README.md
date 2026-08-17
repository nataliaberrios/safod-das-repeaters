# SAFOD DAS/repeater incremental-value experiment

This is a standalone clean-room project for one deliberately demanding
question:

> What does near-fault DAS add beyond a fair conventional seismic-network
> workflow for detecting and assigning SAFOD repeating earthquakes?

The current answer is: **the project is worth pursuing, DAS version 2 is now
procedurally frozen, but DAS catalog extension is not yet demonstrated.** Both
known local earthquakes are the two strongest and most spatially coherent DAS
triggers. A transparently post-hoc four-of-ten-block rule retains those 2 of 65
development triggers; only held-out performance can show whether it generalizes.

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

Open notebooks/04_das_development_checkpoint.ipynb for the independent DAS
checkpoint. It plots the frozen score and spatial-support separation, summarizes
the time-only network comparison, and provides non-writing display and match-
window controls.

Open notebooks/05_das_v2_freeze_checkpoint.ipynb for the version-2 freeze. It
checks config and product hashes, shows exactly how 65 v1 triggers become two v2
candidates, and provides a non-writing support-count sandbox. It does not open
raw or held-out waveforms.

The checkpoint currently records eight decisive facts:

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
6. The frozen development network union contains two known local events and one
   known regional arrival, with zero unassociated local candidates; catalog
   evidence was attached only after time-only grouping.
7. The independent DAS-only scan recovered both local events at score ranks 1
   and 2 with 8/10 and 10/10 strong blocks, while missing the regional arrival.
   Version 1 retained 65 raw triggers and yielded zero validated extensions.
8. Version 2 adds only the registered four-of-ten-block gate at the existing
   ratio of 2. Its development replay retains those two known events and rejects
   the other 63 v1 triggers. This is disclosed tuning, not held-out validation.

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

## DAS-only pre-waveform registration

DAS-development version 1 was specified before any raw HDF5 dataset access. A
manifest-only registrar selected 52 one-minute primary-configuration files
(1.83 GB) covering the 50-minute interval plus 15 seconds of filter padding on
each side. The maximum manifest gap is 2.0 ms. The registration status records
zero HDF5 file or dataset opens, zero network-candidate or catalog-time table
opens, and zero held-out table opens.

The initial detector was predeclared: columns 0--899 sampled every five
columns (180 channels), ten 90-column blocks, 5--20 Hz processing at 100 Hz,
per-channel 0.5/10 s energy STA/LTA, fourth-highest block coincidence, an
8-second candidate separation, and 199 independent-block circular-shift nulls.

## DAS development comparison checkpoint

The raw detector materialized 65 triggers before any network or catalog event
time was read. The comparison design was then committed separately. Under the
frozen 8-second, one-to-one time-only rule, DAS matches both known local network
events within 1.7--1.9 seconds and misses the known Carpinteria regional
arrival. The two local events rank first and second by DAS score (4.43 and 3.34
versus 1.73 for the strongest other trigger) and have 10/10 and 8/10 blocks at
the declared ratio of 2; every other trigger has at most one such block.

This is meaningful near-fiber selectivity evidence, but the development sample
contains only two local positives. Fifty-four raw triggers are catalog-
unassociated, broad travel-time compatibility links several weak triggers to
distant events, and there are zero validated DAS-only catalog extensions.
Version 1 is therefore preserved as promising but not extension-ready. Any hard
spatial-support gate is a transparently development-tuned version 2 that must be
frozen before held-out access.

## DAS version-2 freeze checkpoint

Version 2 changes one thing: a v1 candidate must have at least four of the ten
registered blocks at the already declared characteristic ratio of 2. The v1
band, channels, STA/LTA, candidate spacing, null seed, 199 null replicates,
familywise quantile, and interval-specific score threshold are unchanged. Score
threshold repair, amplitude-selected channels, family labels, and held-out
feedback are forbidden.

The development replay retains 2 of 65 candidates, with parent v1 ranks 2 and 1
and strong-block counts 8 and 10. It rejects 63 of 65. Because both labels and
the support separation were seen before registration, this is a mechanical
regression check of disclosed post-hoc tuning—not an estimate of specificity.
All 12 held-out hours remained sealed, and held-out network and DAS waveform
files opened at this stage remain zero.

The next stage is intentionally asymmetric. The full 12-hour network-only
operating point is registered with exact thresholds, inputs, QC, and access
order. Its one-shot runner and failure paths passed 52 project tests and
reproduced the frozen development counts (two template and three generic
candidates). Runner commit `08099bea76899f7afc82193eef56b4faf330d947` was
verified equal to the private remote before the release artifact was written.
No held-out waveform, catalog row, DAS HDF5, or family label was opened before
release. This is permission to execute the network baseline, not held-out
performance. The complete network union must still be frozen before held-out
DAS candidate generation.

## Held-out network runner release checkpoint

The release hashes the downloader, interval detector, aggregate freezer,
release helper, SLURM launcher, and regression test file. It preserves the
fixed template threshold 0.10883580148220062, generic threshold
1.5937319993972778, and the generic branch's development SNR-1 STOP. It forbids
held-out null recalibration, threshold repair, cross-interval matching, family
assignment, and interval reruns after materialization. Cached miniSEED and its
sidecar are re-hashed, while failed-QC intervals and unavailable sources remain
explicit rather than being silently dropped.

The next scientific checkpoint is the complete 12-hour network-only time union.
Only after every interval table and score cache verifies may the catalog-blind
aggregate be checksummed. Until that happens, catalog adjudication and held-out
DAS waveform access remain stopped.

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
    python -m src.run_network_development_detector
    python -m src.freeze_network_union
    python -m src.register_das_development
    python -m src.run_das_development_detector
    python -m src.compare_das_network_development
    python -m src.register_das_v2
    python -m src.run_das_v2_development
    python -m src.register_heldout_network
    # Commit/push the tested runner; release only succeeds when remote == HEAD.
    python -m src.release_heldout_network_runner
    sbatch heldout_network_interval_job.sh
    # Run only after all 12 array tasks complete successfully.
    python -m src.freeze_heldout_network_candidates
    python -m src.build_checkpoint
    python -m unittest discover -s tests -v
    python ../../run_nb_cells.py notebooks/00_advisor_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/02_network_development_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/03_network_union_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/04_das_development_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/05_das_v2_freeze_checkpoint.ipynb

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
- config/das_development.json freezes the raw-access boundary, channel/block
  sampling, preprocessing, detector, null, and candidate-materialization order
  before HDF5 waveform access.
- config/das_network_comparison.json pins the raw DAS table and frozen network
  union, then predeclares one-to-one matching, catalog-event deduplication,
  conflict handling, and claim limits before comparison rows are parsed.
- config/das_v2_validation.json discloses the post-hoc development inputs,
  freezes the single spatial-support gate, pins every parent checksum, and
  specifies held-out sequencing and event-level pass criteria.
- config/heldout_network_validation.json freezes all 12 interval identities,
  both network thresholds, the ten-event historical template bank, strict QC,
  network-first access order, and catalog-after-union adjudication.
- outputs/development_network/status.json preserves the v4 generic-trigger STOP;
  candidate tables and injection_recovery_summary.csv provide its evidence.
  Full-rate scores, miniSEED, and the downloaded catalog cache remain ignored
  local products.
- outputs/development_network/network_candidate_union_time_only.csv is the
  catalog-blind event union. network_candidate_union_adjudicated.csv attaches
  known-event evidence afterward, and network_union_status.json records the
  development-only access gate.
- outputs/development_das/manifest_selection.csv and registration_status.json
  prove the manifest-only 52-file selection and the zero-waveform-access
  registration state.
- outputs/development_das/candidate_detections_raw.csv is the immutable 65-row
  DAS-only table. network_comparison_time_only.csv precedes catalog access;
  network_comparison_adjudicated.csv and comparison_status.json preserve the
  cautious post-hoc audit and the held-out STOP.
- outputs/development_das_v2/ records the pre-held-out registration and the
  exact two-row development replay; it is a tuning audit, not validation.
- outputs/heldout_v2/registration/ records the zero-waveform-access network
  registration, a 30-row source inventory for the historical templates (27
  available waveform/sidecar pairs and three explicit missing sources), and
  the remotely verified runner release. The release is not a performance
  result.
- outputs/heldout_v2/network/ is reserved for the 12 immutable interval
  products and complete catalog-blind time-only union. It remains empty at the
  runner-release checkpoint.
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

The 12-interval network-only operating point and its runner are now released.
They inherit the fixed development thresholds (template 0.1088358; generic
1.593732), the same ten development-passing templates, strict
station/component QC, and the 8-second time-only union. The generic SNR-1
injection STOP remains explicit. Registration and release opened zero held-out
network, catalog, or DAS waveform rows.

The remaining order is fixed:

1. run both network branches on all 12 hours and retain every candidate,
   unavailable source, and failed-QC interval without repair;
2. checksum and freeze the complete time-only network union before catalog
   adjudication and before opening any held-out DAS HDF5;
3. audit the immutable network union against known-event catalogs without
   deleting raw candidates; and
4. only then run frozen DAS v2 independently and compare unique events at the
   registered operating point.

A positive DAS result must add independently adjudicated events beyond the full
network union or improve held-out family resolution. Neither registration nor
the development two-of-65 replay passes that scientific gate.
