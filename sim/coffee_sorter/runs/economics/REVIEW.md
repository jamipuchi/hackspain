# Economics review

Scope: new economics module, tests, config and outputs relative to 6515364.
Independent Standards and Spec reviews ran in separate Codex contexts. Main
verified the findings against source and regenerated the outputs after fixes.

## Standards

- Important: zero eligible input divided by zero; derived finite inputs could
  overflow. Fixed with controlled rejection and regression tests.
- Minor: reproduction commands lacked shell quoting and portable paths. Fixed
  with shlex serialization and a separate checkout-relative command.
- Minor: redundant mass-key whitelist. Removed.

## Spec

- Important: good spills and defect spills were not consistently separated in
  output and the plot called only good rejects “Good lost.” Fixed across JSON,
  CSV, chart and prose; regression assertions cover each category.
- Minor: human-readable break-even omitted. Added.
- Minor: empty cohort handling. Fixed as above.

Main verdict: all reported findings resolved. Arithmetic reproduced independently;
43 tests pass for the committed baseline plus economics. Values remain conditional
on the stated mass, duty and price assumptions, not evidence of a buyer premium.
