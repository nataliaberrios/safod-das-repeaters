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

The network union, immutable 65-row DAS-v1 table, comparison audit, and DAS-v2
rule are frozen for the nonblind 50-minute interval. The DAS-v2 implementation
checkpoint is verified on the private remote. A separate registration now pins
all 12 held-out interval identities, both fixed network thresholds, ten
historical templates, strict QC, and an exact network-first access order. No
held-out network waveform, catalog row, DAS HDF5, or family label was opened by
that registration.

The multi-interval runner and its failure paths are implemented, pass 52 project
tests, and reproduce the frozen development candidate counts. Commit
`08099bea76899f7afc82193eef56b4faf330d947` was verified equal to the private
remote before a release artifact was written. Registration and release opened
zero held-out network waveform, catalog-event, DAS HDF5, or family-label rows.
The runner may now execute both frozen branches on all 12 intervals. It must
preserve the generic branch's development SNR-1 STOP, retain failed-QC intervals
without relaxing station/component rules, and materialize and checksum the
complete time-only network union before catalog adjudication or DAS access.
This release is an implementation checkpoint, not evidence of performance.

Held-out DAS-v2 generation then runs independently of network candidate times,
catalog times, and family labels. Every v2 candidate is retained before
cross-pipeline comparison. No held-out result may repair either network
threshold, the v1 DAS score threshold, the four-block support gate, or any
preprocessing setting. Scientific extension remains STOP until independent
adjudication and event-level uncertainty show an increment beyond the full
network union.
