# Metadata requests and the claims they block

## Priority 1: deep-fiber physical trajectory

Request the surveyed relation among HDF5 column, OptaSense locus index, measured
depth, true vertical depth, XYZ/latitude-longitude-elevation, cable leg/fold, and
local tangent orientation.  Also request the channel-zero convention, launch
point, interrogator-to-fiber direction, splices, slack loops, and polarity.

Until supplied and validated, depth-localized source, radiation-pattern,
directivity, and source-to-channel distance claims are **STOP**.  Plots use array
column or locus index only.

## Priority 2: active-source trigger metadata

The available CSV gives four local times, all on round minutes.  Request the
actual shot date confirmation, detonator/GPS trigger UTC, timing uncertainty,
source type and charge/hammer details, elevation datum, repeated-shot IDs, and
any failed/misfired shots.  Until then, the shots can calibrate detectability and
relative channel/frequency response, while absolute timing is **CONDITIONAL**.

## Priority 3: interrogator response and clock QC

Request phase-to-strain/strain-rate calibration, laser/pulse configuration
changes, instrument response, GPS-lock logs, clock corrections, dropped packets,
and known compromised intervals.  The HDF5 files state raw processed phase and
`GPS Sync Guaranteed = 0`; source-time and spectral-amplitude claims therefore
remain conditional on empirical calibration.

## Priority 4: published family membership tables

Request event-ID/time tables behind the Nadeau SAFOD target sequences and later
updates.  They are for post-freeze external validation, not for training the v2
family reconstruction.

## Priority 5: archive completeness

Request authoritative acquisition/outage logs for the 14-month and deep epochs,
including duplicate copies and configuration transitions.  A file manifest alone
does not establish a complete recurrence interval.

## Priority 6: source-parameter priors

For stress-drop work, request or reconstruct station responses, phase picks,
moment estimates, velocity/density models, attenuation tests, and published EGF
choices.  No stress drop is reported until bandwidth, model, and synthetic gates
pass.

