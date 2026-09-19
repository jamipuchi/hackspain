# Independent continuous score audit

Run the script with one capture file. It uses only exported evaluator rows.
It does not import `rolling_scores.py`.

```bash
.venv-coffee/bin/python thoughts/taras/research/coffee-score-audit/audit_rolling_scores.py capture.json
```

For one score snapshot, put `rows` and the score context at the capture root.
Each row needs `object_id`, `spawn_time_s`, `required_reject`, and
`score_epoch_id`. `outcome` can be `accept`, `reject`, `spilled`, or `null`.
Mark manual objects with `manual_injection: true`.

For multiple score snapshots, use `snapshots`. Each snapshot needs its own
`rows` captured at its exact `as_of_sim_time_s`. It also needs
`score_epoch_id`, `versions.model`, `versions.policy`,
`versions.source_revision`, and the complete engine aggregate in
`rolling_scores`. The defaults are a 60-second window and 0.6-second settling
period. One epoch cannot span model, policy, or source revisions.

The script exits zero only when every engine aggregate matches. It exits one
after it reports a mismatch. It exits two for an invalid or incomplete export.

The script uses `T - 0.6 - 60 < spawn_time_s <= T - 0.6`. It leaves pending
outcomes in every relevant denominator. It excludes manual rows. It keeps
epochs separate. Empty denominators return a `null` value with zero counts.

Run the durable fixtures with:

```bash
python3 -m unittest thoughts/taras/research/coffee-score-audit/test_audit_rolling_scores.py -v
```
