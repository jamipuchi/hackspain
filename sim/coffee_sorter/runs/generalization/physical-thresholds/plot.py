"""Plot measured reject-bin outcomes from the three frozen threshold runs."""
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

root = Path(__file__).resolve().parent
names = ('disabled', 'trained', 'low')
labels = ('Anomaly disabled', 'Trained: 18.49', 'Candidate: 8.27')
runs = [json.loads((root / name / 'physics_metrics.json').read_text()) for name in names]
for key in ('seed', 'rate_beans_per_s', 'jet_force_n', 'controller_policy'):
    if any(run[key] != runs[0][key] for run in runs):
        raise ValueError(f'Unmatched physical runs: {key}')
rows = []
for name, label, run in zip(names, labels, runs):
    groups = run['decision_attribution']
    row = {'name': name, 'label': label, 'threshold': run['anomaly_threshold']}
    for group in ('good', 'unknown'):
        r = groups[group]
        n = r['eligible_physical_objects']
        if n <= 0 or r['actual_rejected'] > n:
            raise ValueError(f'Invalid {group} denominator in {name}')
        row[group] = {k: r[k] for k in ('eligible_physical_objects', 'actual_rejected',
                                        'actual_rejection_rate', 'combined_command_rate',
                                        'spilled', 'unresolved')}
        interval_key = 'good_false_eject_rate' if group == 'good' else 'physical_reject_recall'
        row[group]['interval_95'] = run['physical_metrics']['intervals_95'][interval_key]
    rows.append(row)
fig, axes = plt.subplots(1, 2, figsize=(12, 5), constrained_layout=True)
for group, color in (('unknown', '#17618c'), ('good', '#c76938')):
    values = [100*r[group]['actual_rejection_rate'] for r in rows]
    errors = [[values[i] - 100*r[group]['interval_95'][0] for i,r in enumerate(rows)],
              [100*r[group]['interval_95'][1] - values[i] for i,r in enumerate(rows)]]
    axes[0].errorbar(range(3), values, yerr=errors, fmt='o-', capsize=4,
                     color=color, label=f'{group} physically rejected')
    for i, r in enumerate(rows):
        y = 100*r[group]['actual_rejection_rate']
        axes[0].annotate(f"{r[group]['actual_rejected']}/{r[group]['eligible_physical_objects']}",
                         (i, 100*r[group]['interval_95'][1]), xytext=(0, 9), textcoords='offset points', ha='center')
axes[0].set_xticks(range(3), labels)
axes[0].set(ylabel='Eligible objects reaching reject bin (%)', ylim=(-3, 105),
            title='Reject-bin capture (95% Wilson intervals)', xlim=(-.3, 2.3))
axes[0].legend(loc='upper left')
x = [100*r['good']['actual_rejection_rate'] for r in rows]
y = [100*r['unknown']['actual_rejection_rate'] for r in rows]
axes[1].plot(x, y, 'o-', color='#17618c')
for i, (xv, yv) in enumerate(zip(x, y)):
    axes[1].annotate(labels[i], (xv, yv), xytext=(8, 6 + i*3), textcoords='offset points')
axes[1].set(xlabel='Good objects physically rejected (%)', ylabel='Unknown objects physically rejected (%)',
            title='Measured ejection tradeoff', xlim=(-0.2, max(x)+2), ylim=(0, max(y)+12))
for ax in axes:
    ax.grid(alpha=.2)
fig.suptitle('Independent physics seed 42 | 100 objects/s | 8 s | 0.06 N | 60 ms minimum latency')
fig.savefig(root / 'physical_tradeoff.png', dpi=160)
plt.close(fig)
(root / 'summary.json').write_text(json.dumps({'rows': rows, 'selection':
    '8.27 selected from offline seed-41 sweep; seed 42 evaluates physics. Each setting changes later trajectories, so runs are not identical object replays. Anomaly-disabled threshold is 1e9.'}, indent=2))
print(json.dumps(rows, indent=1))
