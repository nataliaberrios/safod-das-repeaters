# Scientific specification: DAS incremental value for SAFOD repeaters

## Primary question and project shape

The paper-sized objective is to quantify what the SAFOD DAS arrays add beyond a
strong conventional seismic-network workflow for:

1. detecting small events in identical UTC intervals;
2. assigning events without silently merging or splitting nearby repeating
   sequences; and
3. resolving near-source wavefield differences that point sensors cannot.

The deep cable is scientifically unusual because it samples a dense wavefield
close to known repeater hypocenters. That makes a positive result plausible and
potentially novel. It does not waive controls: DAS earns an incremental-value
claim only by outperforming a fair network baseline.

Evidence now supports two linked primary aims and one downstream aim.

- Aim 1, family-partition resolution: test whether deep DAS supports the Michel
  M00413/M00414 split or their merger better than the conventional array.
- Aim 2, catalog extension: test whether DAS-only or joint continuous detection
  recovers independently adjudicated events missed by a same-interval
  network-only detector at matched false-discovery rate.
- Aim 3, source physics and creep: attempt relative source-size, stress-drop,
  recurrence, or slip-rate inference only after membership, completeness,
  geometry, response, and source-model gates pass.

The 14-month shallow record carries the detection/completeness comparison
because it supplies duration and many blind intervals. The deep record carries
the strongest classification and near-source resolution opportunity. The
Mar--Apr pilot fiber is supplementary.

## Clean-room and label policy

This tree is independent of faultzone/repeaters/. It must not import legacy
labels, cached correlations, figures, or source estimates.

No single published catalog is unconditional family truth. Label evidence is
handled as follows:

1. Exact NCSN event IDs crosswalk the Waldhauser--Schaff and Michel published
   catalogs; location-only transfer is forbidden.
2. Their shared IDs define agreement or a documented partition conflict.
3. NCEDC DD locations and magnitudes may nominate candidates but never assign
   family membership.
4. Conventional-network and DAS waveforms are independent features subject to
   the frozen access order.
5. Post-publication events remain prospective or catalog-continuation
   candidates until independently adjudicated.

The current exact-ID conflict is scientifically material. The six
Waldhauser--Schaff target events map to Michel M00413 and M00414. Michel M00414
also contains exact IDs that Waldhauser--Schaff assigns to a neighboring family
previously used as a hard negative. The binary target-versus-hard-negative
interpretation is therefore stopped; neither catalog is silently rewritten.

The checksummed Waldhauser--Schaff source file has 7,713 sequence headers whose
declared counts sum to 27,674, while its paper/Zenodo description advertises
27,675. That source discrepancy remains in provenance. The Michel Data Set S3
checksum is independently verified before parsing.

## Frozen populations and access order

Historical conventional-model population:

- Waldhauser--Schaff target R1.2900.11955.0: six events.
- Three Waldhauser--Schaff neighbor sequences: 12 events.
- Twelve of the 18 events satisfy the frozen full-array waveform eligibility
  rules: six target and six neighbor events.

Independent catalog reconciliation population:

- 14 exact IDs shared by the 18-event population and Michel Data Set S3.
- Waldhauser--Schaff target overlap: two M00413 and four M00414 events.
- Four post-2014 Michel M00413/M00414 continuation events.
- Eight mapped historical events support the M00413/M00414 conventional
  partition diagnostic.

Archive population:

- 404 official NCSS events within the frozen 2024--2025 query.
- 266 events with complete primary-DAS event windows.
- Five location-proximity nominees; proximity supplies no family labels.
- One nonblind 2025-01-20 50-minute development interval.
- Twelve one-hour held-out intervals selected from the DAS manifest only.

Named 2026 population:

- Event 75336682: prospective deep-DAS candidate.
- Event 75343317: same-fiber hard control.
- Family names remain provisional because the published partitions conflict.

The enforced access order is:

1. build archive coverage and select held-out intervals from the manifest only;
2. fit and freeze the conventional model on historical exact-ID labels;
3. release prospective network waveforms and write network-only scores;
4. develop network-only then independently triggered DAS-only continuous
   detectors on the nonblind 50-minute interval;
5. freeze both detectors and adjudication rules; and
6. open held-out intervals only after those freezes.

No number discovered during prospective or test evaluation may repair version 1.
A methodological improvement requires a version 2 and a new split.

## Fair pipeline comparison

All pipelines use the same predeclared UTC/configuration intervals and
event-level adjudication rules.

| Pipeline | Candidate generation | Family evidence | Information embargo |
|---|---|---|---|
| Network only | Conventional multi-template bank plus independent multi-station energy trigger | Multi-anchor waveform shape plus robust differential timing/relocation | Blind to DAS triggers and scores |
| DAS only | Array-coherent picking or multi-channel template matching | Withheld DAS templates and spatial wavefield features | Blind to network triggers and scores |
| Joint | Union of the two frozen candidate/score tables | Predeclared evidence fusion with abstention | Blind to held-out labels |

Comparison against only the routine NCEDC catalog is forbidden because a fair
network matched-filter baseline can recover events absent from the routine
catalog. DAS-only candidate generation may not inherit network event times.

Development version 4 preserves a mixed network result. The template bank
recovered both local catalog events and 30/30 target-family injections at
component SNR 1.0 with the injected event excluded. Both branches recovered
0/50 zero-amplitude controls. The independent
four-station trigger found both local arrivals and a known M2.3 Carpinteria
arrival, but recovered only 22/30 target injections at the predeclared SNR 1.0
gate (30/30 at SNR 2.0). Its SNR 1.0 STOP may not be erased by threshold
repair. The next development version must explicitly improve/replace this
branch or register it as an auxiliary detector with its measured sensitivity.
A broader official-catalog physical-arrival veto is part of adjudication, not
candidate generation.

Network-union version 1 registers the template bank as the primary
target-sensitive branch and retains the generic trigger as an auxiliary
non-template safety net without repairing its failed SNR-1 gate. An ordered,
one-to-one 8-second time-only rule converts five branch detections into three
event groups. Catalog information is attached only after that table is written
and checksummed: two groups are known local events and one is a known regional
arrival from NC event 75120096. No group is unassociated and no family label is
assigned. This is a comparator-rule freeze for independent DAS development,
not a claim of held-out detector performance.

DAS-development version 1 was registered before raw HDF5 access. Manifest
times and acquisition configuration alone selected 52 files spanning the
shared interval plus 15-second filter padding. The candidate generator used 180
sampled columns in ten contiguous blocks, 5--20 Hz per-channel energy ratios,
fourth-highest block coincidence, and 199 independent block-shift nulls. It
materialized and checksummed 65 raw DAS triggers while recording zero network-
candidate, catalog-time, or held-out table access.

A separately committed comparison design then used ordered, one-to-one,
time-only matching before catalog adjudication. DAS recovered both known local
network events within 1.7--1.9 seconds and missed the known regional network
arrival. The local events are score ranks 1 and 2 (4.43 and 3.34 versus 1.73 for
the strongest other trigger) and have 10/10 and 8/10 blocks at the declared
characteristic ratio of 2; every other trigger has at most one. This is
promising development evidence for spatially coherent local sensitivity, but
there are only two local positives, 54 catalog-unassociated raw triggers, zero
validated DAS-only extensions, and no family assignments. Version 1 is not
extension-ready and may not be repaired retrospectively.

DAS version 2 was registered after that comparison and before any held-out
waveform access. It adds only a minimum support of four of ten blocks at the
existing characteristic ratio of 2; every v1 preprocessing, channel, timing,
null, and score-threshold setting is inherited. Its disclosed development
replay retains the two known local events and rejects the other 63 v1 triggers.
That result verifies implementation fidelity only. It is post-hoc tuning and
provides no independent specificity estimate.

The released network-only runner then processed all 12 held-out hours before
any held-out catalog or DAS access. All intervals passed fixed QC, 35/36 source
requests were available, and the immutable catalog-blind output contains 12
historical-template candidates plus 21 auxiliary generic candidates. No
cross-branch times match within the registered 8 seconds, so the union has 33
rows. This is a completed comparator-generation step, not evidence that all 33
rows are earthquakes or repeaters. The generic branch's development SNR-1 STOP
remains part of interpretation.

A separately registered catalog audit retained all 33 rows as 32 event units.
Five cataloged earthquakes explain six generic triggers; 12 template-only and
15 generic triggers remain catalog unassociated. No candidate was deleted and
no family was assigned. Catalog absence is not earthquake or repeater truth.

The independently released DAS runner then completed all 12 held-out hours with
zero network-candidate, catalog-time, association-row, or family-label access.
All 738 registered HDF5 files were read; every interval retained 180 usable
sampled channels and ten usable blocks. The fixed interval nulls retain all 724
base-v1 candidates, and the unchanged four-of-ten rule retains the exact 22-row
v2 subset. The v2 candidates occur in four intervals with counts 6, 2, 13, and
1. Their uneven clustering and the single 10/10 high-score trigger are targets
for preregistered comparison and adjudication, not evidence by themselves of an
earthquake, repeater, or catalog extension.

The separate held-out comparison is now registered against exact hashes and
schemas with zero candidate/evaluation rows or candidate-time fields parsed.
It inherits the pre-held-out 8-second window, forbids cross-interval matching,
uses deterministic one-to-one maximum-cardinality/minimum-distance assignment,
and retains every one of the 22 DAS and 33 network rows. The 32-unit network
ledger may collapse only one known duplicate for event-level metrics after the
time-only output is checksummed. This registration is a pre-result contract,
not evidence of a DAS increment.

## Primary metrics and pass gates

| Claim | Primary comparison | Pass requirement |
|---|---|---|
| Detection extension | DAS-only and joint versus network-only | Positive interval-bootstrap lower bound for recall difference at matched event-level FDR |
| Completeness extension | Detection probability versus magnitude, noise, and configuration | Gain survives stratification and yields a defensible completeness change |
| Classification extension | DAS or joint versus network-only on event-held-out labels/partitions | Positive macro-F1 or partition-resolution change without increased cross-family merging |
| Near-source resolution | Same-sequence versus competing-partition spatial DAS features | Effect survives channel, band, polarity, and block-bootstrap sensitivity |

The frozen conventional verifier uses median positive-peak correlation and
station-centered differential-lag RMS over the full BP array plus NC.PSM and
BK.PKD. Its historical values are apparent training performance, not
prospective accuracy. One post-freeze continuation exposed cycle-skip
sensitivity in the lag statistic. That outcome is retained; version 1 is not
silently repaired.

## Observable-to-inference ladder

1. Header and manifest audits establish UTC, configuration epochs, sampling,
   units, channel/locus mapping, and gaps.
2. SNR, active shots, controls, and nulls establish usable channels and bands.
3. Exact-ID crosswalks establish agreement and published partition conflicts.
4. A frozen network-only verifier and continuous detector establish the
   baseline that DAS must beat.
5. Blinded DAS-only and joint scans test detection and classification extension.
6. At least two independently adjudicated events on one DAS configuration
   enable direct repeatability and near-source spatial comparisons.
7. Response-corrected same-path EGF ensembles with in-band corners may enable
   relative source-size or stress-drop work.
8. A complete extended family plus a defensible moment/area/slip model may
   enable a repeater-derived creep rate.

## Hard decision gates

| Branch | PASS requirement | Current interpretation if absent |
|---|---|---|
| Catalog partition | Independent evidence supports a split, merger, or explicit probabilistic assignment | STOP single binary truth label |
| Geometry | Surveyed channel-to-MD/TVD/XYZ/cable-leg/tangent table | STOP depth, source-distance, and directivity claims |
| Continuous best-network baseline | Same-interval scan with frozen event-level FDR controls | STOP DAS extension claim |
| DAS incremental value | Improvement at matched FDR with interval uncertainty | STOP DAS extension claim |
| DAS partition value | Event-held-out improvement without increased merge rate | STOP DAS classification-extension claim |
| Direct DAS repeatability | At least two independently adjudicated members on one configuration | STOP repeatability/source claim |
| Corner frequency | Corner and uncertainty inside empirical usable bandwidth | STOP stress-drop claim |
| Stress drop | Response, EGF/model sensitivity, geometry, and synthetic recovery pass | STOP |
| Creep rate | Validated complete sequence, at least three complete intervals, constrained slip | STOP |

The deep geometry audit currently supports channel spacing and a channel-1702
hairpin convention only. It found no surveyed absolute trajectory. Channel or
fiber-distance features are allowed; depth, source distance, radiation
direction, and stress-drop geometry are not.

## Falsifiable hypotheses

- H1: A best-effort network-only continuous scan recovers most catalog-sized
  events and therefore sets a demanding baseline.
- H2: DAS-only or joint processing recovers additional independently
  adjudicated events at the same event-level false-discovery rate.
- H3: Dense near-source DAS features resolve the M00413/M00414 partition better
  than conventional correlation and differential-lag features.
- H4: Any DAS effect is concentrated on physically coherent channel regions and
  persists across reasonable bands, configuration epochs, and block
  bootstraps.
- H5: If no incremental gain survives these controls, the extension claim fails
  even if individual DAS earthquakes are visually striking.

## Immediate next access constraint

The network union, network catalog audit, and DAS-only candidate generation are
all frozen and checksummed. The DAS runner was committed, pushed, and verified
equal to the private remote before waveform access. Slurm array 39362290 passed
12/12 tasks, the freezer verified every interval-product and ignored-score-cache
hash, and its overwrite guard now refuses a second freeze. Neither detector's
threshold, the four-block support rule, nor any candidate was changed after
held-out access.

The time-only comparison is now separately registered. It pins the exact
22-row DAS table, 33-row network union, 33-row adjudicated network table, and
32-unit network ledger by byte hash and exact schema. Registration opened only
four CSV headers and three status JSON files: candidate/evaluation rows,
candidate-time fields, association rows, and family labels all remain zero.
The 8-second tolerance is inherited from the pre-held-out contract, matching is
one-to-one and confined within intervals, and all matched/unmatched rows must be
retained.

Candidate-time access remains STOP until a fail-closed runner and its failure-
path tests are committed, pushed, and remotely verified. Once released, the
runner must:

1. parse only the frozen 22 DAS and 33 network rows for the time-only stage;
2. write and checksum the complete matched/unmatched table before opening any
   adjudicated network or evaluation-unit row;
3. attach network context by immutable ID without changing a match; and
4. refuse overwrite, threshold/window repair, rank selection, row deletion,
   cross-interval matching, and family assignment.

Any DAS-only candidate must then pass independent forced-network-score,
waveform, catalog/regional-arrival, and DAS-artifact adjudication. A network or
catalog miss does not automatically establish an earthquake, repeater, or DAS
extension.

Scientific extension remains STOP until independently adjudicated event units
show an increment beyond the full network union with interval-level uncertainty
and the auxiliary generic branch's development SNR-1 limitation retained.
Family classification, stress drop, and creep-rate inference remain downstream
of their separate evidence gates.


## Held-out comparison result checkpoint

The release-gated runner completed the fixed time-only comparison and retained
all 22 DAS and 33 network rows. It produced one within-interval DAS+network
match (0.1004 s apart in heldout_08), 21 DAS-only rows, and 32 network-only
rows. No cross-interval match, threshold/support repair, rank selection,
candidate deletion, or family assignment occurred. Network context was
attached only after the time-only table was written and checksummed.

The 21 DAS-only rows are not yet detections or repeater-family extensions. The
next checkpoint is independent adjudication: force the frozen network score at
each DAS-only time, test catalog/regional-arrival associations, review DAS
persistence and morphology plus waveform evidence, and quantify interval-level
uncertainty. Family labels remain withheld until independent evidence supports
them.
