# Independent continuous score audit

Run the script with one capture file. It uses only exported evaluator rows.
It does not import `rolling_scores.py`.

```bash
.venv-coffee/bin/python thoughts/taras/research/coffee-score-audit/audit_rolling_scores.py capture.json
```

The capture must include `rows`. Each row needs `object_id`, `spawn_time_s`,
`required_reject`, and `score_epoch_id`. `outcome` can be `accept`, `reject`,
`spilled`, or `null`. Mark manual objects with `manual_injection: true`.

Supply one or more score contexts in `snapshots`. Each needs
`score_epoch_id`, `as_of_sim_time_s`, `versions.model`, `versions.policy`,
and `versions.source_revision`. The defaults are a 60-second window and
0.6-second settling period. Each snapshot also needs the complete engine
aggregate in `rolling_scores`. One epoch cannot span model or policy versions.

The script exits zero only when every engine aggregate matches. It exits one
after it reports a mismatch. It exits two for an invalid or incomplete export.

The script uses `T - 0.6 - 60 < spawn_time_s <= T - 0.6`. It leaves pending
outcomes in every relevant denominator. It excludes manual rows. It keeps
epochs separate. Empty denominators return a `null` value with zero counts.
