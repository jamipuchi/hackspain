# OpenRouter vision and TypeSafe Jev

Taras requested TypeSafe for decisions and OpenRouter for LLM APIs.
This experiment adds that route above the same robot contract.

```text
Camera crop
    -> OpenRouter vision: describe the visible object
    -> Jev: compare the observation with demonstrated groups
    -> Local validation: accept a destination or defer
    -> SortDecision: pick_at, then place_in
    -> Jaume's controller and simulated Arduino
```

## Responsibilities

OpenRouter serves `google/gemini-3-flash-preview` for visual descriptions.
The request contains image bytes and a fixed prompt.
It excludes filenames, simulator labels, intended destinations, and fixture coordinates.

TypeSafe serves the pinned `jev-1.13.0` model for destination choices.
Its state contains observed visual properties and demonstration pairs.
Each pair contains an observation and an opaque destination ID.
It returns probabilities for `bin_A`, `bin_B`, `bin_C`, and `defer`.

Local code checks the response shape and returned model.
It requires one clearly visible object, a top probability of at least 0.8, and a probability lead of at least 0.2.
Those thresholds are fixed experimental settings, without measured calibration on real parts.
Missing observations, provider errors, or invalid choices cause deferral.

The policy changes by accumulating demonstrations in its context.
The experiment does not retrain model weights or generate new firmware.
Arduino execution remains behind Jaume's existing controller.

## Measured results

With three demonstrations, Jev assigned 28 test images correctly and deferred two.
With 21 demonstrations, it assigned all 30 correctly.
All 30 predictions followed a control that changed the demonstrated destination labels.

Three separate robot trials then captured new images and called both providers.
The screw, nut, and washer reached their expected bin footprints in MuJoCo.
The contract kept physical task verification unknown.

The combined provider cost was approximately $0.049, including connectivity checks and robot trials.
OpenRouter reported its cost. The TypeSafe cost uses its published price and recorded token usage.

Read the [full results and limits](results/v2/report.md).

## Evaluation design

The experiment reuses v1's rendered images for an exploratory comparison.
OpenRouter describes three initial demonstrations, 18 additional demonstrations, and 30 test images.
Each test image represents one pose of one of three M4 models.
Those 30 images produced 20 distinct descriptions, with cached results for identical decision requests.

The first Jev policy receives three examples.
The second receives 21 examples.
A control changes the demonstrated destinations while preserving the observed descriptions.
The test labels reach the evaluator only.

We retain the prompts, image hashes, responses, model identifiers, usage, latency, and decision probabilities.
Response caching avoids repeated charges for identical requests.
The runner uses at most four concurrent calls and makes no automatic retries.
Credentials are read from the environment or `.env` and are never saved in results.

The v1 comparison changes both visual representation and the decision model.
It cannot isolate Jev's contribution.
The second Jev policy is an experimental candidate, with no automatic promotion based on test results.

## Commands

Use the environment from [the experiment README](README.md).
The provider clients use Python's standard library.
Set `OPENROUTER_API_KEY` and `TYPESAFE_API_KEY` in the environment or repository `.env`.

```bash
PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python contracts/learning/run_jev_experiment.py \
  --dataset contracts/learning/results/v1/dataset/records.json \
  --out contracts/learning/results/v2 --env-file .env --live

PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python contracts/learning/run_jev_smoke.py \
  --policy contracts/learning/results/v2/policy.json \
  --out contracts/learning/results/v2/smoke --env-file .env --live
```

The comparison requires network access.
The robot simulation also requires host graphics access.
The simulation uses a fixed pickup station and checks final positions from simulator state.
These checks do not establish physical sorting or visual destination verification.

## Provider references

- [TypeSafe model versions and text input](https://docs.typesafe.ai/models)
- [TypeSafe HTTP request and response schema](https://docs.typesafe.ai/api)
- [Choice probabilities](https://docs.typesafe.ai/primitives/choice)
- [Confidence semantics](https://docs.typesafe.ai/confidence)
- [OpenRouter image inputs](https://openrouter.ai/docs/guides/overview/multimodal/image-understanding)
- [OpenRouter structured outputs](https://openrouter.ai/docs/guides/features/structured-outputs)
