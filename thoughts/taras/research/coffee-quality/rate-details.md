# Development feed-rate comparison

These are development comparisons. They are not acceptance results.

Classification uses approximate object associations. It excludes unseen objects and objects with ambiguous associated predicted classes.
Wilson intervals show uncertainty. Small denominators produce wider intervals.

| Requested rate | Admitted rate | Capture | Capture 95% | Good loss | Good loss 95% | Physical sorting | Class accuracy | Class coverage | Late decisions | Wall s | Engine rate | Max control compute ms |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 10 | 10.000 | 7/7 | 64.6% to 100.0% | 0/39 | 0.0% to 9.0% | 100.0% | 97.8% | 100.0% | 0 | 11.937 | 0.503 | 40.870 |
| 50 | 50.000 | 34/35 | 85.5% to 99.5% | 3/195 | 0.5% to 4.4% | 98.3% | 97.8% | 100.0% | 0 | 12.640 | 0.475 | 33.149 |
| 100 | 99.833 | 45/51 | 76.6% to 94.5% | 9/410 | 1.2% to 4.1% | 96.7% | 96.1% | 99.3% | 0 | 14.401 | 0.417 | 35.145 |
| 200 | 200.000 | 126/135 | 87.8% to 96.5% | 22/785 | 1.9% to 4.2% | 96.6% | 97.8% | 99.2% | 0 | 17.958 | 0.334 | 37.164 |
| 250 | 250.000 | 145/157 | 87.1% to 95.6% | 42/993 | 3.1% to 5.7% | 95.3% | 94.6% | 99.6% | 0 | 19.751 | 0.304 | 35.918 |
| 400 | 400.000 | 237/266 | 84.8% to 92.3% | 82/1574 | 4.2% to 6.4% | 94.0% | 94.0% | 99.6% | 0 | 24.908 | 0.241 | 36.143 |
| 500 | 500.000 | 293/346 | 80.5% to 88.1% | 99/1954 | 4.2% to 6.1% | 93.4% | 94.4% | 99.4% | 0 | 28.180 | 0.213 | 48.560 |

Low-count uncertainty:

- 10 objects/s: capture n=7, good-loss n=39.
- 50 objects/s: capture n=35, good-loss n=195.
- 100 objects/s: capture n=51, good-loss n=410.
