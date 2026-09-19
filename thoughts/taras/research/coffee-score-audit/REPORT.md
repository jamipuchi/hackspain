# Independent continuous score audit

Date: 2026-09-19

## Verdict

**Not yet independently validated.**

The published long-run evidence proves bounded retention. It does not provide
the evaluator rows needed to recalculate any rolling score.

`thoughts/taras/research/coffee-continuous-live/REPORT.md` reports 30,205 to
30,300 score rows during the endurance run. It does not save their spawn
times, truth classes, outcomes, manual flags, or a matching aggregate.

`thoughts/taras/research/coffee-core-live/live.json` contains a bounded
diagnostic summary. It contains one manual injection. It contains no
continuous score epoch, rolling score snapshot, or feed-row export. This audit
does not convert that historical diagnostic cohort into a rolling cohort.

## Required export

At one score snapshot time `T`, save the following evaluator-truth data:

```json
{
  "as_of_sim_time_s": 120.0,
  "window_seconds": 60.0,
  "settling_seconds": 0.6,
  "score_epoch_id": "session-uuid",
  "versions": {"model": "...", "policy": "..."},
  "rows": [
    {
      "object_id": 17,
      "spawn_time_s": 59.4,
      "required_reject": true,
      "outcome": "reject",
      "manual_injection": false,
      "score_epoch_id": "session-uuid"
    }
  ],
  "rolling_scores": {"...": "matching engine aggregate"}
}
```

Include every feed and manual row with `T - 60.6 < spawn_time_s <= T`.
Include rows with a null outcome. Keep manual rows so the audit can prove their
exclusion. Include the exact boundary times. Keep each epoch separate. Save
the aggregate from the same engine snapshot.

## Independent checker

`audit_rolling_scores.py` uses only the exported rows. It does not import the
production ledger or simulator. It calculates this cohort:

```text
T - 0.6 - 60 < spawn_time_s <= T - 0.6
```

It reports these measures:

- Capture: required rejected divided by required.
- Good loss: keep rejected or spilled divided by keep.
- Sorting accuracy: required rejected plus keep accepted, divided by all cohort rows.
- Unresolved: pending outcomes divided by all cohort rows.

The checker returns null values for empty denominators. It excludes manual
rows. It prevents duplicate object IDs within an epoch. It calculates each
epoch separately. It compares each available engine snapshot exactly.

## Edge-case check

The fixture passed these cases:

- A row at the left boundary was excluded.
- A row at the right boundary was included.
- A settling row was excluded from the cohort and counted as settling.
- A spilled keep row increased good loss.
- A pending row remained in the denominator.
- A manual row was excluded.
- A second epoch used a separate cohort.
- An empty required denominator returned `0/0` and null.

The fixture checks calculation rules. It does not provide physical acceptance evidence.

## Commands

```bash
cd /private/tmp/hackspain-coffee-score-audit
python3 -m py_compile thoughts/taras/research/coffee-score-audit/audit_rolling_scores.py
python3 thoughts/taras/research/coffee-score-audit/audit_rolling_scores.py capture.json
git diff --check
```

## Limitation

This audit awaits a capture with complete evaluator rows and a matching engine
snapshot. No current published artifact can establish a measured pass or fail
for continuous capture, good loss, or sorting accuracy.
