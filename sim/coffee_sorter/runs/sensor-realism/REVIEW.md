# Sensor implementation review

Independent Standards and Spec reviews examined the new experiment files against
6515364. Shared controller, detector, profile, classifier and simulation files
remain byte-identical to that base. Main verified and resolved the findings.

## Standards

- Source identity now covers behavioral dependencies and is pinned at process
  start, checked before/after execution, and checked when resuming.
- A separate completion checksum binds metrics; metrics bind decoded images,
  decisions, camera evidence and the feed manifest. Corrupt evidence is rejected.
- Config numeric values must be finite; fixed camera cadence is validated.
- Conservative bounds cover capsules and rotated boxes. Unused diagnostics were
  removed and image-write failures are checked.
- The source-resume regression reaches the intended guard instead of passing on
  an earlier artifact error whose scenario name happened to contain “source.”

## Spec

- Fractional centered kernels represent 1.2 px and 6 px exposure blur without
  shifting timestamps or silently making nominal exposure an identity transform.
- Product identity is independent of placement retries; same-rate summaries
  assert matched eligible product hashes. Admission-limited crowded cohorts are
  explicitly not claimed to be causally paired when their eligibility differs.
- Spawn attempts are bounded; blocked items persist and admission counts expose
  missed feed opportunities. Actual contact counters count timesteps once and
  use lifetime bean UIDs, excluding positive-distance proximity records.
- A tracked frozen model and archival --rerun make fresh-checkout execution and
  real recomputation possible. Software versions and exact source are preserved.
- Regression coverage now includes photometry, empirical noise variance, jitter
  range/isolation, admission recovery, geometry/mass/force parity, contact
  accounting across body reuse, cohort mismatch and artifact/source invalidation.

Main verdict: all reported Critical and Important implementation findings resolved.
65 tests pass. Final full-sweep validation is recorded in NIGHT_LOG and summary.json;
the code checks do not establish hardware performance or a real buyer premium.
