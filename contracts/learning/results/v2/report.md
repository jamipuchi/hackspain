# OpenRouter and Jev experiment results

The pipeline uses OpenRouter vision, TypeSafe Jev, and the existing high-level robot contract.
Vision model: `google/gemini-3-flash-preview`. Decision model: `jev-1.13.0`.

| Policy | Demonstrations | Correct | Wrong | Deferred |
| --- | ---: | ---: | ---: | ---: |
| Jev baseline | 3 | 28 | 0 | 2 |
| Jev candidate | 21 | 30 | 0 | 0 |
| Jev with changed destinations | 21 | 30 | 0 | 0 |

The test contains ten rendered poses of each of three unseen M4 part models.
The three models and camera setup are fixed, so these results do not establish broad generalization.
The 30 images produced 20 distinct observations. Ten candidate decisions reused identical cached requests.
The score counts images, not independent model calls.
All 30 accepted predictions followed the changed demonstrated destinations.
The policy accumulates examples in context. Neither model undergoes weight training.
Every Jev decision receives its complete demonstration set. This experiment does not establish persistent learning inside Jev.

## Comparison with v1

The earlier local feature policy made 19 correct assignments, two wrong assignments, and nine deferrals on these images.
Both perception and decision models changed for v2. This comparison cannot isolate the contribution from Jev.
The test images were already used in v1, so v2 remains an exploratory comparison.
No v2 prompt or threshold was tuned after inspecting the test results.

## Robot integration

All three isolated MuJoCo trials reached their expected bin footprints: screw, nut, and washer.
Each trial captured a new image and called OpenRouter and Jev before sending robot actions.
The actions use Jaume's `RobotAPI`, `ArmController`, and `SimArduino`.
Pickup coordinates come from a fixed taught station. Simulator state supplies the final footprint check.
Action receipts retain `task_verified: null`. Physical hardware and human video capture remain untested.

## Usage and latency

OpenRouter reported $0.044706 across 54 saved requests.
TypeSafe cost is approximately $0.004466 for 64 saved requests.
The TypeSafe estimate uses its published input price. The API did not return a billed cost.
Estimated combined cost, including connectivity and robot trials: $0.0492.
Median observed request latency: OpenRouter 2.294 seconds, TypeSafe 1.022 seconds.
These measurements include network overhead. Exact duplicate requests reuse saved responses.

## Artifacts

- [Evaluation results](results.json)
- [Exported demonstration policy](policy.json)
- [Observed descriptions](observations.json)
- [Decision traces](decisions.json)
- [Robot trial results](smoke/results.json)
- [Usage details](usage.json)
- [Architecture and reproducible commands](../../JEV.md)
