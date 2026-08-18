# SAFOD DAS/repeater incremental-value experiment

This is a standalone clean-room project for one deliberately demanding
question:

> What does near-fault DAS add beyond a fair conventional seismic-network
> workflow for detecting and assigning SAFOD repeating earthquakes?

The current answer is: **the project is worth pursuing, the independent
12-hour network and DAS candidate tables are now frozen, but DAS catalog
extension is not yet demonstrated.** Both known local development earthquakes
were the two strongest and most spatially coherent DAS triggers. The disclosed
four-of-ten-block rule retains 22 of 724 base triggers in the held-out replay,
concentrated in four hours. A separately registered comparison and independent
adjudication must now determine whether those triggers include events that the
full network workflow missed.

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

Open notebooks/06_heldout_network_freeze_checkpoint.ipynb for the 12-hour
network-only result. It verifies every compact hash, plots branch and interval
counts, records source/QC availability, and provides display-only timing and
interval controls. It never opens miniSEED, full score arrays, a catalog, family
labels, or DAS HDF5, and it cannot retune a held-out threshold.

Open notebooks/07_heldout_catalog_audit_checkpoint.ipynb for the frozen
post-union catalog audit. It proves all 33 rows were retained, shows the five
known earthquakes explaining six triggers, separates the 27 unresolved
candidates by branch, and exposes only non-writing display filters.

Open notebooks/08_heldout_das_registration_checkpoint.ipynb for the independent
held-out DAS preregistration. It verifies the manifest-selection hashes, shows
the 61--62 files and byte footprint registered for each interval, and provides
display-only interval/file controls. It opens no HDF5 file or comparison table.

Open notebooks/09_heldout_das_freeze_checkpoint.ipynb for the frozen DAS-only
result. It verifies all aggregate hashes and access counters, shows the
724-to-22 fixed-rule reduction and per-interval timing, and exposes only
non-writing display filters. It opens no network/catalog candidate table,
family label, raw HDF5, or full score cache.

Open notebooks/10_heldout_comparison_registration_checkpoint.ipynb for the
pre-result comparison contract. It verifies all frozen input hashes and exact
schemas, shows the 22 DAS rows, 33 raw network rows, and 32 network event units,
and exposes only registration-metadata controls. It parses no candidate row or
time and reveals no match result.

The checkpoint currently records thirteen decisive facts:

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
9. The frozen 12-hour network scan has 12 template and 21 auxiliary generic
   candidates, zero cross-branch pairs within 8 seconds, and 33 time-only union
   rows. All intervals passed fixed QC; 35/36 sources were available.
10. The frozen catalog audit retains all 33 rows as 32 event units. Five known
    earthquakes explain six triggers; 12 template-only and 15 generic triggers
    remain catalog unassociated, not automatically new events or repeaters.
11. Manifest-only DAS preregistration selected 738 distinct files (26.0 GB)
    across all 12 padded intervals. The fail-closed runner passed its tests and
    was committed, pushed, and verified equal to the private remote before HDF5
    access was released; no comparison time or family label was opened.
12. Slurm array 39362290 completed 12/12 intervals with exit code 0 and read all
    738 selected files. The fixed null thresholds retain 724 base-v1 triggers;
    the unchanged four-of-ten rule retains 22 candidates in four intervals
    (6, 2, 13, and 1). These are arrival candidates, not yet earthquakes,
    repeaters, or extensions, and all comparison/label access counters remain
    zero.
13. The held-out comparison is registered against exact hashes and schemas
    without parsing any candidate row or time. It inherits the untuned 8-second
    deterministic one-to-one rule within intervals, retains all 22 DAS and 33
    network rows, and preserves the 32-unit network ledger. Candidate-time
    access remains locked pending a tested, committed, pushed, remotely
    released runner.

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
release. This release was permission to execute the network baseline, not a
performance result; the separately frozen result is recorded below.

## Held-out network runner release checkpoint

The release hashes the downloader, interval detector, aggregate freezer,
release helper, SLURM launcher, and regression test file. It preserves the
fixed template threshold 0.10883580148220062, generic threshold
1.5937319993972778, and the generic branch's development SNR-1 STOP. It forbids
held-out null recalibration, threshold repair, cross-interval matching, family
assignment, and interval reruns after materialization. Cached miniSEED and its
sidecar are re-hashed, while failed-QC intervals and unavailable sources remain
explicit rather than being silently dropped.

That release condition has now been satisfied and the complete network-only
union is frozen. The released runner cannot rerun an interval after its status
exists, and the freezer refuses to overwrite its final status.

## Held-out network-only freeze checkpoint

SLURM array 39345238 completed all 12 tasks with exit code 0. All 12 intervals
passed both fixed-threshold branch mechanics; 35 of 36 source requests were
available, with an explicit empty NC.PSM response in heldout_07. The aggregate
contains 12 primary template-bank candidates and 21 auxiliary generic
candidates. There are no pairs within the frozen 8-second window, so the
catalog-blind union retains all 33 rows. An independent timing audit confirms
that heldout_03 is the only hour containing both branches and its nearest times
are 250.55 seconds apart.

This is a technically successful comparator freeze, not a claim that 33
earthquakes occurred. Template detections are concentrated in heldout_01 and
heldout_03; generic detections are concentrated later, including eight in
heldout_11. The generic branch still carries its development SNR-1 STOP, and
neither branch has yet been checked against local or regional catalogs. Catalog,
family-label, and DAS access remained zero through the union freeze.

## Held-out catalog-audit checkpoint

The separately registered catalog audit preserves all 33 network rows and
represents them as 32 event units. Five cataloged earthquakes explain six
generic triggers: one target-box event outside the family neighborhood and
four physically plausible regional arrivals, one represented by two triggers.
The other 27 candidates comprise 12 template-only catalog misses and 15 generic
catalog-unassociated triggers. No row was deleted, no family was assigned, and
catalog-unassociated does not mean uncataloged earthquake or repeater.

## Held-out DAS runner and freeze checkpoint

The DAS runner was committed at
`ed94600f8ee34be32680902b20ac332cbcbae94e`, pushed, and verified exactly equal
to the private GitHub remote before its release file permitted HDF5 reads.
Slurm array 39362290 then completed all 12 registered tasks with exit code 0.
Every selected file was read: 738 unique HDF5 files over 12 hours. All sampled
180 channels and all ten registered blocks were usable in every interval.

The interval-specific 95th-percentile block-shift nulls retain 724 complete
base-v1 triggers. The frozen development-derived four-of-ten spatial-support
gate retains 22 DAS-v2 arrival candidates: six in heldout_01, two in
heldout_03, 13 in heldout_04, and one in heldout_08. The heldout_08 candidate
has 10/10 strong blocks and the highest score-to-null ratio; that is a priority
for later adjudication, not proof of an earthquake or repeater. The clustering
in heldout_04 likewise requires explicit artifact/regional-arrival checks.

Both aggregate tables and all interval products were checksummed before the
final status was written. No network or catalog candidate time, association
row, or family label was read; no threshold was repaired, no support sweep was
run, and no candidate was deleted. The freezer now refuses overwrite. This is
successful independent candidate generation, not yet incremental value.

## Held-out comparison registration checkpoint

The comparison contract pins exact byte hashes and headers for the 22-row
DAS-v2 table, 33-row catalog-blind network union, 33-row adjudicated union, and
32-row network evaluation-unit ledger. Registration opened four headers and
three status JSON files but parsed zero candidate/evaluation rows, zero
candidate-time fields, zero association rows, and zero family labels.

The time-only stage inherits the 8-second window already declared before
held-out DAS access. It matches only within each interval and deterministically
maximizes pair count before minimizing total absolute time difference. Every
matched and unmatched row must remain. Network branch roles remain visible;
the one duplicate network candidate is collapsed only for event-level metrics,
never deleted. Catalog/evaluation rows cannot open until the time-only output
is written and checksummed.

This registration does not contain or imply a match result. Candidate-time
access remains STOP until the runner and failure-path tests are committed,
pushed, and verified against the private remote. DAS-only rows will remain
pending independent catalog, forced-network-score, artifact, and waveform
adjudication after matching.

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
    python -m src.register_heldout_catalog_audit
    # Commit/push the registered audit runner before catalog access.
    python -m src.release_heldout_catalog_runner
    python -m src.run_heldout_catalog_audit
    python -m src.register_heldout_das_replay
    # Commit/push the tested DAS runner; release only succeeds when remote == HEAD.
    python -m src.release_heldout_das_runner
    sbatch heldout_das_interval_job.sh
    # Run only after all 12 DAS array tasks complete successfully.
    python -m src.freeze_heldout_das_candidates
    python -m src.register_heldout_das_network_comparison
    # Commit/push the tested comparison runner before candidate-time access.
    python -m src.build_checkpoint
    python -m unittest discover -s tests -v
    python ../../run_nb_cells.py notebooks/00_advisor_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/02_network_development_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/03_network_union_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/04_das_development_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/05_das_v2_freeze_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/06_heldout_network_freeze_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/07_heldout_catalog_audit_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/08_heldout_das_registration_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/09_heldout_das_freeze_checkpoint.ipynb
    python ../../run_nb_cells.py notebooks/10_heldout_comparison_registration_checkpoint.ipynb

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
- config/heldout_das_replay.json freezes the same 12 interval identities, DAS
  manifest selection, inherited v1 null mechanics, unchanged four-of-ten v2
  rule, output schemas, and comparison embargo.
- config/heldout_das_network_comparison.json pins both frozen candidate tables,
  network evaluation units, exact schemas, the inherited 8-second within-
  interval matching rule, full row retention, and the post-time-only access
  order before any match result is parsed.
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
- outputs/heldout_v2/network/ contains the 12 immutable interval products,
  source/QC ledgers, SLURM execution ledger, and the 33-row catalog-blind
  time-only union. candidate_generation_status.json pins every aggregate hash
  and records zero catalog, family-label, and DAS access through the freeze.
- outputs/heldout_v2/das/ contains all 12 compact DAS interval ledgers, the
  724-row frozen base-v1 table, the exact 22-row v2 subset, and the aggregate
  freeze status. Full score arrays remain ignored; all comparison and
  family-label access counters are zero.
- outputs/heldout_v2/registration/comparison_registration_status.json records
  exact schema verification and zero candidate-row/time access. It releases
  only implementation of a tested runner; it is not a comparison result.
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

The full 12-hour network comparator, catalog audit, and independently triggered
DAS tables are now immutable. The 33 network rows become 32 event-level units:
six rows are explained by five cataloged earthquakes, while 12 template-only
and 15 generic candidates remain catalog unassociated. The DAS replay read all
738 registered files and retained 724 base-v1 triggers plus the exact 22-row
four-of-ten v2 subset. No network/catalog candidate time or family label was
opened during DAS generation.

The separate comparison is now registered without parsing candidate rows. It
pins both candidate-table hashes and schemas, the inherited 8-second tolerance,
within-interval deterministic one-to-one assignment, complete row retention,
and the 32-unit network ledger.

The released comparison runner has now completed the fixed first comparison.
It retained all 22 DAS and 33 network rows, producing 54 time-only rows: one
within-interval DAS+network match (0.1004 s apart), 21 DAS-only rows, and 32
network-only rows. No cross-interval match, threshold/support repair, rank
selection, candidate deletion, or family assignment occurred. Network context
was attached only after the time-only table was written and checksummed. The
21 DAS-only rows are explicitly pending independent catalog, forced-network-
score, waveform, and DAS-artifact adjudication; they are not yet a detection or
repeater-family extension.

The next registered computation is that independent DAS-only adjudication with
interval-level uncertainty and the generic network branch development STOP
retained. It must predeclare waveform-review and network-score access, artifact
rejection, catalog/regional-arrival checks, and the rule that family assignment
is withheld without independent evidence.

A positive DAS result must add independently adjudicated events beyond the full
network union or improve held-out family resolution. The 33 raw network rows do
not yet establish the baseline false-discovery rate, and neither the
development two-of-65 replay nor the held-out 22-of-724 DAS table alone passes
the scientific extension gate.

The completed audit used the pinned target NCSS catalog, inherited 3 s
template-origin and 12 s generic-origin tolerances, physical 2.5--8 km/s
regional-arrival rule, and 12 exact interval queries. The catalog vetoes are
audit evidence, not family truth. A cataloged earthquake can still be a new
repeater-family member; a catalog-unassociated candidate still needs
independent waveform confirmation before it can extend the detection catalog.

## Positive-control and adjudication registration

The next stage is now registered in
config/heldout_das_adjudication.json. The development detector recovered 2/2
known local catalog events, with 8/10 and 10/10 strong-block support and score
ranks 2 and 1. This is a capability sanity check, not a repeater-family recall
estimate. The published-family reference contains 18 events across four
families, but the independent exact-ID partition remains STOP because Michel
M00413/M00414 and Waldhauser--Schaff do not agree.

All 21 heldout DAS-only rows are retained for independent adjudication. The
protocol requires frozen-network scores, catalog/regional checks, DAS
artifact/morphology review, waveform review, and interval-level uncertainty.
Threshold repair, row deletion, matching-window sweeps, and family assignment
remain forbidden. A positive extension claim requires at least one validated
local DAS-only event beyond the full network union.

## Held-out DAS-only adjudication checkpoint

Open `notebooks/13_heldout_adjudication_waveform_checkpoint.ipynb` for the compact partial adjudication. The fixed generic and template network branches were sampled at all 21 DAS-only times, and the registered broad-regional catalog files were checked without threshold repair or family labels. None of the 21 rows crossed either frozen network threshold or had a cached regional association within 30 seconds.

The targeted DAS review read only the registered raw HDF5 windows and reproduced the frozen DAS preprocessing. All 21 windows had complete finite coverage; the table records score persistence, four-block support duration, spatial support, and a common-mode variance ratio. The raw payload is int32 and has no declared full-scale limit, so saturation is explicitly not assessed. The saved PNG is an automated review aid, not an event classifier.

The scientific state is **PARTIAL / STOP**: no DAS-only row is yet a validated event or catalog extension, no candidate was deleted, and no family was assigned. The next gate is manual waveform/artifact review followed by interval-stratified adjudication.

## Advisor dashboard

Open `notebooks/14_advisor_das_adjudication_dashboard.ipynb` for the presentation dashboard. It provides an evidence funnel, ranked DAS-support overview, interval-stratified counts, and a candidate browser controlled by the `candidate_number` variable. It reads compact products only and writes the overview figure to `outputs/heldout_v2/adjudication/advisor_dashboard_overview.png`; it cannot modify the frozen candidate set or assign families.
