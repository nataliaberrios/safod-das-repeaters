# Scientific specification: DAS incremental value for SAFOD repeaters

## Primary question

The paper-sized objective is to quantify what the SAFOD DAS arrays add beyond a
strong conventional seismic-network workflow for:

1. detecting small events in identical UTC intervals;
2. assigning events to a known repeating-earthquake sequence without merging
   nearby sequences; and
3. resolving near-source differences that surface/borehole point sensors cannot.

The deep cable is scientifically unusual because it samples a dense wavefield
close to known repeater hypocenters. That geometry makes a positive result
plausible and potentially novel. It does not waive controls: DAS earns an
incremental-value claim only by outperforming a fair network baseline.

The two DAS archives have different primary jobs. The 14-month shallow record
carries the detection/completeness comparison because it supplies duration and
many blind intervals. The deep record carries the strongest classification and
near-source source-resolution test because of its proximity to the repeaters,
but direct repeatability there requires another independently classified event
on the same configuration. The Mar--Apr pilot fiber is supplementary.

Stress drop and repeater-derived creep rate are valuable downstream branches.
They remain stopped until classification, response, geometry, source-model, and
completeness gates pass.

## Clean-room and label policy

This tree is independent of faultzone/repeaters/. It must not import legacy
labels, cached correlations, figures, or source estimates.

Label authority is ordered:

1. Exact event IDs in the Waldhauser--Schaff (2021) published repeater catalog
   provide historical sequence labels.
2. NCEDC DD locations and magnitudes nominate candidates but do not define
   family membership.
3. HRSN/NCSN and DAS waveforms provide held-out features.
4. Post-2014 events are prospective candidates until independently classified.

Location-only matching to the published catalog is forbidden. The absolute
depths differ substantially between catalog versions even for identical IDs.

The checksummed source file has 7,713 sequence headers whose declared counts sum
to 27,674; its paper/Zenodo description advertises 27,675. Both counts are
recorded in provenance.

## Frozen validation populations

- Target sequence: R1.2900.11955.0, six published events.
- Neighbor controls: R1.0697.5199.0, R1.2172.7737.0, and
  R1.3027.11698.0, 12 published events in total.
- Repair diagnostic: 38 NCEDC DD nominees scored against the single 2026 seed.

The published families are not defined by correlation alone. Waldhauser--Schaff
also require relative co-location within modeled rupture dimensions and similar
event size. Therefore high cross-family correlation is expected to be an
insufficient label feature rather than evidence that the external labels are
interchangeable.

## Fair pipeline comparison

All three pipelines use the same predeclared UTC/configuration intervals and
event-level adjudication rules.

| Pipeline | Candidate generation | Family assignment | Information embargo |
|---|---|---|---|
| Network only | Conventional association plus HRSN/NCSN multi-template matched filtering | Multi-anchor waveform features and differential relocation | Blind to DAS triggers/scores |
| DAS only | Array-coherent picking and multi-channel template matching | Withheld DAS templates and spatial wavefield features | Blind to network triggers/scores |
| Joint | Frozen network and DAS candidate/score tables | Predeclared evidence fusion with abstention | Blind to held-out labels |

The network-only pipeline must be run first and frozen. A comparison against only
the routine NCEDC catalog would be unfair because matched filtering can recover
events absent from the routine catalog.

## Primary metrics and pass gates

| Claim | Primary comparison | Pass requirement |
|---|---|---|
| Detection extension | DAS-only and joint versus network-only | Positive interval-bootstrap lower bound for recall difference at matched event-level FDR |
| Completeness extension | Detection probability versus magnitude/noise/configuration | Gain survives stratification and yields a defensible completeness change |
| Classification extension | Joint versus network-only on event-held-out labels | Positive macro-F1 change without loss of target precision or increased cross-family merging |
| Near-source resolution | Same-family versus neighboring-family spatial DAS features | Effect survives channel, band, and block-bootstrap sensitivity |

No number discovered during the test evaluation may be used to relax a frozen
threshold. Exploratory improvements require a new held-out split.

## Observable-to-inference ladder

1. Header and sidecar audits establish UTC, configuration epochs, sampling,
   units, locus mapping, and gaps.
2. SNR, active shots, and controls establish usable channels and frequencies.
3. Exact-ID labels establish independent historical validation populations.
4. Network-only detection and differential-time classification establish the
   baseline that DAS must beat.
5. Blinded DAS-only and joint scans test detection/classification extension.
6. Two independently classified events on one DAS configuration enable direct
   repeatability and near-source spatial comparisons.
7. Response-corrected same-path EGF ensembles with in-band corners may enable
   relative source-size or stress-drop work.
8. A complete extended family plus a defensible moment/area/slip model may
   enable a repeater-derived creep rate.

## Hard decision gates

| Branch | PASS requirement | Current interpretation if absent |
|---|---|---|
| Geometry | Surveyed locus-to-MD/TVD/XYZ/cable-leg/tangent table | STOP depth/directivity claims |
| Single-anchor catalog | Independent multi-family validation with controlled merge rate | STOP; current diagnostic overmerges |
| Best network baseline | Continuous same-interval scan plus event-held-out relocation/classification | STOP DAS extension claim |
| DAS incremental value | Improvement at matched FDR with uncertainty | STOP DAS extension claim |
| Direct DAS repeatability | At least two independently classified members on one configuration | STOP repeatability/source claim |
| Corner frequency | Corner and uncertainty inside empirical usable bandwidth | STOP stress-drop claim |
| Stress drop | Response, EGF/model sensitivity, and synthetic recovery pass | STOP |
| Creep rate | Validated complete sequence, at least three complete intervals, constrained slip | STOP |

## Falsifiable hypotheses

- H1: A best-effort network-only scan recovers most catalog-sized events, setting
  a demanding baseline.
- H2: DAS-only or joint processing recovers additional adjudicated events at the
  same event-level false-discovery rate.
- H3: Dense near-source DAS features reduce target/neighbor family ambiguity
  relative to network-only features on held-out events.
- H4: The effect is concentrated on physically coherent channel regions and
  persists across reasonable bands, configuration epochs, and block bootstraps.
- H5: If no incremental gain survives those controls, the catalog-extension
  claim fails even though individual DAS earthquakes are visually striking.
