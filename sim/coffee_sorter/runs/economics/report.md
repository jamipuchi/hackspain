# Economics ledger

This is a rerunnable, hypothetical ledger from preserved physical metrics; it is not a market, premium, certification, or cost claim.

Headline (rate-1000 when present): rate-1000: requested 1000; effective 1000 beans/s.
Input 576.000 kg/h; accepted 497.575 kg/h; defects rejected 36.332 kg/h.
Good falsely ejected 28.357 kg/h, spilled 3.323 kg/h, unresolved 0.000 kg/h. Defects spilled 10.412 kg/h, unresolved 0.000 kg/h; neither earns a rejection credit.
Baseline EUR 3456.00/h; sorted EUR 3234.24/h; uplift EUR -221.76/h; zero-premium value EUR -470.55/h.
Good false-ejection cost EUR 170.14/h; spill cost EUR 82.41/h; unresolved cost EUR 0.00/h.
Incoming policy-defect count 14.192%; residual accepted policy-defect count 7.035%.
Break-even accepted-stream premium: EUR 0.946/kg, before costs.
All discovered scenarios, including negative uplift results, are retained in summary.json and summary.csv.

## Assumptions

- Equal mass: 0.2 g for every object, including fragments and foreign matter; simulator feed_kg_per_h is deliberately unused.
- Duty cycle: 0.8; Q uses measured throughput, never requested rate.
- Base unsorted feed opportunity price: EUR 6.0/kg. Accepted-stream premium: EUR 0.5/kg only if a buyer pays it; it is unverified.
- Rejected salvage, spill/unresolved sale value, operating, capital, and labor costs are EUR 0 by assumption. No spilled defect receives a benefit credit.

## Reproduce

`.venv/bin/python economics.py --output runs/economics`

Source SHA-256 values, controller configurations, class counts, denominators, and formulas are in summary.json.
