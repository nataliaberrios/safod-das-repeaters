# Initial provenance ledger

Inspected on 2026-08-14 (America/Los_Angeles).

- Event metadata: NCEDC FDSN event service, `catalog=DD`, queried directly by
  event ID for 72154411, 75336682, and 75343317.
- HRSN metadata/waveforms: NCEDC FDSN station and dataselect services, network
  BP, location 40, DP1/DP2/DP3.  Candidate epochs span 2014 and 2026.
- Deep HDF5: external read-only paths in `config/pilot.json`; data dataset is
  `/Acquisition/Raw[0]/RawData`, timestamps are microseconds since Unix epoch in
  `RawDataTime`, and columns map to locus indices beginning at `StartLocusIndex`.
- Shot log: repository-root file
  `SAFOD_ActiveSrc_ShotLocs&Times_20260617.csv`; the date is inferred from the
  filename and must be independently confirmed.
- Node deployment table: `ActiveJune2026/Nodes/p26.fdt.txt`.
- Long shallow archive manifest: despite its `.csv` suffix, the file is
  whitespace-delimited and contains placeholder rows as well as valid headers.

Generated products add exact URLs, paths, UTC intervals, and a configuration hash.

