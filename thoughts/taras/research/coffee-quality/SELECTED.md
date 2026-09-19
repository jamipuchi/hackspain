# Selected configuration before acceptance

Taras owns functional QA and acceptance. The reserved seeds remain untouched at this checkpoint.

The selected configuration uses 1 ms physics, 250 Hz inspection, and 500 requested objects/s.
It reduces force for light predicted objects and uses equivalent numeric inference.
The rejection threshold is 0.8. Anomaly rejection remains enabled.
All native thread limits remain one. All session limits remain unchanged.

## Model change

The bootstrap now consumes the selected physical preset and records its hash.
Training uses seed 7. Holdout uses seed 9. Training still uses synthetic, isolated object observations.
The model learns from 23 image features. It does not learn from physical acceptance seeds.

Retraining improved holdout observation accuracy from 93.64% to 95.29%.
Holdout good false-defect predictions worsened from 2.41% to 3.31%.
The physical development comparison also exposed a regression in good loss.

| Four-second development run, seed 8 | Capture | Good loss | Unresolved |
|---|---:|---:|---:|
| Fixed baseline model, selected physics and force | 156/190 (82.11%) | 50/1110 (4.50%) | 0 |
| Retrained model, threshold 0.5 | 161/190 (84.74%) | 67/1110 (6.04%) | 0 |
| Retrained model, threshold 0.8 | 165/190 (86.84%) | 62/1110 (5.59%) | 0 |

The fixed-model physical improvement and retraining are separate changes.
The final model does not preserve the best observed good-loss result.

## Threshold evidence

The pulse evaluator recorded rejection scores before the threshold comparison.
Seven lost keep objects had a score below 0.8 without an anomaly trigger.
One captured defect had a score below 0.8 without an anomaly trigger.
This supported one comparison of threshold 0.5 against 0.8 with a fixed model.
The actual comparison saved five keep objects and captured four additional defects.
Changed jet activity also changes subsequent motion. Score suppression counts do not predict complete physical outcomes.

The selected run admitted 500 objects/s without pool starvation.
It took 18.200 wall seconds for four simulated seconds, or 0.220 times real time.
It had zero late decisions. Capture's Wilson lower bound was 81.30%.
Good loss's Wilson upper bound was 7.10%.
The cohort contained only 1,300 eligible objects. This is development evidence, not acceptance.

Most false rejection scores remain high. Raising the threshold cannot resolve every remaining perception or tracking failure.
No jet width or maximum force increased.

## Exact selected model

Final bootstrap SHA-256:
`89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`.

The final bootstrap used the selected preset, including threshold 0.8.
Its tree nodes, baseline logits, and anomaly parameters exactly match the preceding physical-preset bootstrap.
Only model metadata changed because threshold does not participate in training.
The measured threshold comparison therefore uses the same prediction function as the selected artifact.

The repository preserves compressed baseline, retrained, and selected model bytes beside this document.
It also preserves the selected training manifest and recorded camera features for inference reproduction.
Bootstrap can reproduce training, but the archived artifact provides exact bytes for the frozen evaluation.

## Verification and limits

Numeric inference matched scikit-learn probabilities and anomaly output exactly on 1,000 recorded camera batches.
Checks included empty, single-row, NaN, large-batch, and unsupported-model fallback paths.
The retrained model's isolated replay took 2.826 seconds versus 8.652 seconds through scikit-learn.
The complete engine remains slower than real time. Replay speed does not establish complete engine speed.

Separate Standards and Spec reviews found no remaining actionable issues before this final threshold comparison.
Syntax checks and bounded diagnostics provide implementation verification. Taras retains functional acceptance.

```bash
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/bootstrap_model.py sim/coffee_sorter/classifier.py sim/coffee_sorter/controller.py sim/coffee_sorter/engine.py
.venv-coffee/bin/python sim/coffee_sorter/bootstrap_model.py
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/evaluate_frozen.py --freeze thoughts/taras/research/coffee-quality/freeze.json
.venv-coffee/bin/python thoughts/taras/research/coffee-quality/evaluate_frozen.py --evaluate thoughts/taras/research/coffee-quality/freeze.json --out /tmp/coffee-quality-acceptance
```

The evaluator refuses repeated reserved-seed evaluation with the same exposure record.
Do not remove that record to treat exposed seeds as untouched.
