# Metadata policy

Files here are inputs or schemas whose provenance can be audited independently.
They are not conclusions copied from the legacy repeater workflow.

- `seed_events.csv` records three official NCEDC DD events and their role in the
  pilot.  `candidate_pair` is a hypothesis, not a family label.
- `reference_family_members_template.csv` is deliberately empty except for its
  header.  Verified rows are generated only by the v2 HRSN workflow.
- `MISSING_METADATA.md` lists information that blocks particular claims.
- Normalized shot and catalog tables produced by scripts are written to
  `outputs/` with source URLs/paths and retrieval timestamps.

The source shot CSV and all waveform archives are read-only external inputs.

