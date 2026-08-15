# SAFOD DAS/repeater incremental-value pilot

This is the isolated v2 workflow for one deliberately narrow question:

> What does near-fault DAS add beyond a strong conventional seismic-network
> workflow for detecting and assigning SAFOD repeating earthquakes?

Read PROJECT_SPEC.md before interpreting an output. In particular, DAS locus
indices are not depth, a high waveform correlation is not a family label, and a
detectable DAS event is not yet a DAS catalog extension.

## Reproduce the current checkpoint

Run from the repository notebook directory in the das conda environment:

    conda activate das
    python -m faultzone.repeaters_v2.src.build_coverage_ledger
    python -m faultzone.repeaters_v2.src.run_hrsn_pilot --max-family-candidates 38
    python -m faultzone.repeaters_v2.src.run_deep_das_pilot
    python -m faultzone.repeaters_v2.src.run_active_source_calibration
    python -m faultzone.repeaters_v2.src.build_external_validation
    python -m faultzone.repeaters_v2.src.run_network_family_benchmark
    python -m faultzone.repeaters_v2.src.checkpoint
    python -m unittest discover -s faultzone/repeaters_v2/tests -v
    python run_nb_cells.py faultzone/repeaters_v2/notebooks/00_advisor_checkpoint.ipynb
    python run_nb_cells.py faultzone/repeaters_v2/notebooks/01_das_incremental_value.ipynb

The notebooks read cached products by default. Network downloads and raw HDF5
work are explicit opt-in rebuilds.

## Correction to the first pilot

The former phrase “25 verified members” was wrong. The 38-event frozen DD
shortlist contains 25 single-anchor high-similarity candidates (the seed plus 24
pairwise passes), nine insufficient-data rows, and four unavailable rows.
Exact-event-ID comparison with the Waldhauser--Schaff (2021) catalog finds two
high-similarity rows that belong to other published families. Therefore the
single-anchor family-catalog claim is STOP.

The checksummed public file contains 7,713 sequence headers whose declared event
counts sum to 27,674. The paper and Zenodo record advertise 27,675. This one-row
source discrepancy is retained in provenance rather than silently “fixed.”

## Canonical products

- outputs/hrsn/similarity_shortlist_v2.csv: all 38 single-anchor diagnostic
  rows; candidate status only.
- outputs/hrsn/high_similarity_candidates_v2.csv: the 25 high-similarity rows;
  not a family catalog.
- outputs/external_validation/shortlist_external_crosswalk.csv: exact-ID label
  crosswalk and the repair diagnostic.
- outputs/external_validation/published_validation_population.csv: six events
  in the target sequence and 12 events in three neighboring published families.
- outputs/external_validation/network_family_classification.csv: frozen
  leave-one-event-out HRSN correlation baseline with an explicit abstain class.
- outputs/external_validation/pipeline_manifest.csv and metric_gates.csv:
  network-only, DAS-only, and joint comparisons that must be run on identical
  UTC intervals.
- outputs/checkpoint/advisor_checkpoint.json: current claim and next-gate
  ledger.

Cached public catalog text, miniSEED, and NPZ windows are ignored by Git.
Checksums, URLs, event IDs, UTC windows, source paths, and configuration hashes
remain in small provenance products.

## Current interpretation

The external validation population is usable, the prospective 2026 candidate is
detectable across the deep fiber, and a same-fiber hard control is available.
The HRSN correlation-only multi-family diagnostic does not classify any of the
ten events with available HRSN data under its frozen separation margin; nearby
families are too waveform-similar. That is a reason to test DAS spatial
information, not evidence that DAS wins.

The decisive next computation is a blinded same-interval comparison:
best-network detection first, DAS-only detection second, and predeclared joint
fusion third. Catalog extension passes only if DAS improves recall or
classification at a matched event-level false-discovery rate with interval-level
uncertainty.
