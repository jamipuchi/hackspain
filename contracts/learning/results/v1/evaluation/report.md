# Learned sorting experiment

The policy uses opaque teacher destinations and numeric features only.
Demonstrated destinations define the groups. The learner does not discover an unknown number of groups.

The baseline uses 3 demonstrations. The candidate uses 21 demonstrations.
Validation uses separate poses of the three training fixtures.
The test contains ten poses of each of three unseen M4 fixtures.

| Policy | Validation total accuracy | Test total accuracy |
| --- | ---: | ---: |
| Baseline | 0.556 | 0.000 |
| Candidate (centroid) | 0.889 | 0.633 |

Candidate promoted: True.
Test assignments: 19 correct, 2 wrong, 9 deferred.
Deployed test total accuracy: 0.633.
Deployed selective accuracy: 0.905.
Deployed coverage: 0.700.
Deployed abstentions: 9.
Deployed ungated nearest accuracy: 0.933 across 30 valid records.
Baseline ungated nearest accuracy: 0.800.
The baseline defers most items. Its zero total accuracy does not imply zero recognition ability.
Ungated accuracy is a diagnostic. It ignores the acceptance threshold and does not measure accepted robot actions.
This comparison measures additional demonstrations. It does not isolate an improvement from changing the learning method.

## Demonstrated destination control

The control changes the demonstrated destinations and reuses identical test features.
Accepted predictions checked: 21.
Predictions follow the changed destinations: True.

## Deployed test confusion matrix

```json
{
  "bin_A": {
    "abstain": 1,
    "bin_A": 9
  },
  "bin_B": {
    "abstain": 8,
    "bin_B": 2
  },
  "bin_C": {
    "bin_A": 2,
    "bin_C": 8
  }
}
```

## Limits

This experiment evaluates a simulated manifest. It does not establish human video learning or physical robot success.
