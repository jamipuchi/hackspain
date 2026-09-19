# UR5e infeed code review

Target: `a59fc9a` through this task's working tree. Independent reviewers checked
Standards and Spec separately; the parent verified their findings and fixes.

## Standards

No material Standards findings.

The remaining Mesa finding is resolved under the parent’s adjudicated scope:

- `setup_mesa.sh:15-16` creates unique download and extraction directories per invocation.
- Packages are downloaded and extracted only within those fresh directories, preventing cross-run package-generation mixing.
- `runtime-packages.txt` is stored in the selected extraction root.
- The printed `LD_LIBRARY_PATH` references that same root.
- The script and generated report honestly describe the Ubuntu package recipe as runtime-resolved and unpinned.
- `bash -n sim/coffee_sorter/setup_mesa.sh` passes.
- No concrete new bug was found.

All other prior Standards findings remain resolved. No broad tests were repeated and no files or external state were changed.

The first review found stale-output mixing, weak model/source identity,
nonfinite solver handling, a test-only selection helper and stale active-target
labels. These were corrected and regression-tested. Completion inventories bind
the final files. The unpinned Ubuntu Mesa bootstrap is an explicit environment
limitation, not a claim of bit-identical OS reproduction. Fresh per-invocation
download/extraction roots prevent cached package-generation mixing.

## Spec

Resolved. No remaining Critical or Important issue within this verification scope.

Verified:

- `a-large-4` was selected at 2.48 s and missed at 5.86 s, yielding a 3.38 s queue residence.
- Burst metrics include all four selected objects in `samples` and `by_item`; maximum is 3.38 s.
- Aggregate metrics, REPORT, NIGHT_LOG, and MORNING_REVIEW all report 3.38 s.
- Tests require queue samples for every selected object; `/tmp/ur5-final-tests-queue.log` reports 72/72 passing.
- Regenerated evidence and current source hashes match `completion.json`.
- The original 18,614-byte MORNING_REVIEW prefix remains byte-exact.

No edits, commits, test reruns, or external messages were made.

The initial review also identified oracle-gated capture and incomplete collision
and bin-placement disclosures. Capture is now class-independent; assistance by
true identity/height remains explicit. Workspace exclusion is a configured
policy and makes no physical-unreachability claim. The preserved burst miss has
its full queue residence counted. Human hardware and PR acceptance remain pending.

## Verification

The final full suite passes 72 tests with the actual robot model required;
[test output](tests.log) and [exact command](test_command.txt) are saved.
[Independent validation](validation.log) checks success event sequences, finite
trajectories, configured velocity bounds, matching video frame counts and the
immutable morning-review prefix. Compile, shell syntax and scoped diff checks
pass. The fork has no configured GitHub Actions workflows. No merge or Slack
post is part of this task.

## Post-review artifact packaging

The CSV exporter now explicitly writes LF line endings after the staged diff
check exposed default CRLF output. This changes serialization only. The full
72-test suite was rerun on that exact source, all three scenarios were replayed,
and the saved-evidence audit and staged whitespace check were repeated before
commit. The final `tests.log` and completion inventory bind those outputs.
