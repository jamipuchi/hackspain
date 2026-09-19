# Quality delivery integration

Commit `90dffc1` merges quality delivery `1944538` with the existing live restart interface.
The merge changed no production files beyond the quality delivery's classifier, controller, bootstrap, and default preset.

The exact selected model and adjacent manifest were restored from their tracked archives.
The former local model remains in `/tmp/coffee-model-before-quality-9bc9bf9`.
Frozen source, assets, model, policy, preset, and native-thread values matched `freeze.json`.
The bootstrap reuse check also confirmed exact provenance and class order.
The selected model SHA-256 is `89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`.

The browser check used port 8892. It showed the selected model and integration source revision.
The engine classified the injected object as stone, scheduled rejection, recorded its own pulse contact, and recorded a reject outcome.
The object resolved after 1.470 wall seconds. This single result does not establish general sorting quality.
Restart created a fresh Ready session with the same selected model and no visible error.
The temporary service and dedicated browser session were stopped afterward.

The first click after an idle interval remained pending. No command reached the worker.
Reloading and injecting again succeeded. This reproduces the earlier quality-task observation.
The continuous-operation plan includes idle reconnect and acknowledgment recovery. This integration does not fix that issue.
The initial automated wait also used an absent object field. A corrected predicate checked the actual object ID and outcome.

The compact record is `integration.json`. Compressed worker reports, command records, and final state accompany it.
The browser screenshot remains local at `/tmp/coffee-quality-integrated-stone.png`.

## Reviews

The Spec review found stale integration and acceptance wording. The run guide and shared plan now identify the integrated revision and exact failed metrics.
The Standards review found the same documentation issue and three minor maintenance concerns.
The concerns cover private classifier fallback fields, unused bootstrap defaults, and a repeated pulse-floor constant.
The pinned configuration has no identified correctness failure from those concerns. Preserve the frozen source for this integration.
Review those concerns when changing dependencies, training interfaces, or pulse policy.

Capture passed the frozen acceptance bounds. Good loss and engine speed remain below their requirements.
See the quality delivery's `ACCEPTANCE.md` and `REPORT.md` for those measurements.
Continuous operation and rolling scores are not implemented in this integration.
Taras retains functional QA and acceptance.
