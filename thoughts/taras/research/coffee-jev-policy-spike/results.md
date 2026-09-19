# Coffee Jev typed-policy micro-spike

Exploratory evidence only. No policy was activated and no simulator was called.

## Original six requests

These raw results use a product question that also included unsupported-class detection. Do not interpret its product answer as a clean capability result.

| Case | Original outcome | Latency | Interpretation limit |
| --- | --- | ---: | --- |
| english_policy | green_arabica; policy_change; clear | 0.879s | Raw actions differ from effective policy when unchanged preserves current action. |
| spanish_policy | green_arabica; policy_change; ambiguous | 0.746s | Raw actions differ from effective policy when unchanged preserves current action. |
| unsupported_immature | green_arabica; unsupported_recognition; clear | 0.721s | Original question structure applies. |
| contradictory_black | green_arabica; ambiguous; ambiguous | 0.804s | Original question structure applies. |
| irrelevant | unsupported; irrelevant; clear | 0.745s | Original question structure applies. |
| switch_roasted | roasted; policy_change; clear | 0.731s | Original question structure applies. |

## Refined policy requests

`unchanged` compiles to the current local action. The table reports raw model choices and the effective compiled policy separately.

| Case | Product, capability, intent, ambiguity | Raw foreign actions | Effective foreign actions | Effective policy match | Local control gate | Model | Latency |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| english_policy_refined | green_arabica; supported; policy_change; ambiguous | husk=unchanged, stone=unchanged, stick=unchanged | husk=reject, stone=reject, stick=reject | pass | defer: ambiguity | jev-1.13.0 | 0.786s |
| spanish_policy_refined | green_arabica; supported; policy_change; ambiguous | husk=unchanged, stone=unchanged, stick=unchanged | husk=reject, stone=reject, stick=reject | pass | defer: ambiguity | jev-1.13.0 | 0.726s |

Combined usage: 14593 input tokens and 4166 output tokens across eight serial requests. The API returned no cost field.

See [the original responses](results.json) and [the refined responses](refined_results.json).
The original `matches` fields preserve the first rubric. They compare raw actions, so they are not a reliable policy-accuracy score.
The refined report compiles `unchanged` into the existing action before comparing policies.

## Reproduction and limits

The [original script](jev_policy_spike.py) makes six serial requests. The [refined script](refined_policy_spike.py) makes two serial requests.
Neither script retries failed requests automatically. Each execution uses the authenticated TypeSafe account and incurs provider usage.
Run the original script before the refined script. The refined script reads the original result JSON from the configured output directory.
Script outputs use the system temporary directory by default. Preserve these recorded files when evaluating later variants.
Set `TYPESAFE_API_KEY` in the environment. See each script for optional credential and output path overrides.

These scripts preserve exploratory prompts, not a production policy contract. They do not change the running coffee sorter.
The eight requests cannot establish a reliable accuracy estimate, calibrated confidence, or production latency distribution.
The API returned no billed cost. No additional requests were made while preparing this evidence for commit.
