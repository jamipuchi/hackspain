"""Run a sequential, paired simulation sweep, preserving every negative result.

Usage: python sim/magnet_sorter/evaluate_adaptive.py --output <new-directory>
Source the headless bootstrap env first. Credentials are inherited by name only.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def pin_manifest(output, manifest, source):
    path = output / 'manifest.json'
    if path.exists():
        if json.loads(path.read_text()) != manifest:
            raise ValueError("resume requires the original policy, seed matrix and ledger")
        if (output / 'policy-source.py').read_bytes() != source:
            raise ValueError("saved policy source does not match its manifest")
        return
    if (output / 'results.json').exists():
        raise ValueError("existing results have no manifest; do not mix an unpinned sweep")
    (output / 'policy-source.py').write_bytes(source)
    path.write_text(json.dumps(manifest, indent=2))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output', type=Path, required=True)
    ap.add_argument('--seeds', type=int, nargs='+', default=[4, 17, 29])
    ap.add_argument('--configs', nargs='+', choices=['cheap', 'strong', 'adaptive'], default=['cheap', 'strong', 'adaptive'])
    ap.add_argument('--resume', action='store_true', help='skip runs already recorded in results.json; retain ledger')
    ap.add_argument('--ledger', type=Path, help='reuse one ledger across successive sweeps ($5 cap covers all of them)')
    args = ap.parse_args()
    args.output.mkdir(parents=True, exist_ok=args.resume)
    ledger = (args.ledger or args.output / 'ledger.json').resolve()
    results_path = args.output / 'results.json'
    results = json.loads(results_path.read_text()) if args.resume and results_path.exists() else []
    source = Path(__file__).with_name('adaptive_brain.py').read_bytes()
    manifest = {'seeds': args.seeds, 'configs': args.configs, 'policy_sha256': hashlib.sha256(source).hexdigest(), 'ledger': str(ledger)}
    pin_manifest(args.output, manifest, source)
    # Same seed order, rendering, controller, geometry and accounting for all configs.
    for seed in args.seeds:
        for config in args.configs:
            if any(r['seed'] == seed and r['config'] == config for r in results):
                continue
            dest = args.output.resolve() / f'{config}-seed-{seed}'
            if dest.exists():
                raise SystemExit(f'unrecorded run exists at {dest}; reconcile its result and ledger before resuming')
            command = [sys.executable, str(Path(__file__).with_name('run_demo.py')), '--build', 'theker_v1', '--brain', 'agent', '--phone', 'mujoco', '--cameras', 'A,B', '--routing', config, '--ledger', str(ledger), '--seed', str(seed), '--max-steps', '60', '--budget', '1.5', '--no-record', '--run-dir', str(dest)]
            with (args.output / f'{dest.name}.log').open('w') as log:
                try:
                    returncode = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=600).returncode
                except subprocess.TimeoutExpired:
                    returncode = -1
                except OSError as exc:
                    log.write(f'process launch failed: {type(exc).__name__}\n')
                    returncode = -2
            result = json.loads((dest / 'result.json').read_text()) if (dest / 'result.json').exists() else {'summary': 'process failed before result', 'cost_complete': False}
            record = dict(config=config, seed=seed, returncode=returncode, result=result)
            results.append(record)
            (args.output / 'results.json').write_text(json.dumps(results, indent=2))
            print(json.dumps({k: v for k, v in record.items() if k != 'result'}), result.get('correct'), result.get('expected'), result.get('summary'), flush=True)
            if ledger.exists():
                rows = json.loads(ledger.read_text())['requests']
                unresolved = [r for r in rows if r['status'] not in ('accounted', 'rejected')]
                spend = sum(r['cost_usd'] or 0 for r in rows)
                ceiling = sum(r['cost_usd'] if r['cost_usd'] is not None else r['reserved_usd'] for r in rows)
                print(f'known spend ${spend:.8f}; ceiling ${ceiling:.8f}; unresolved {len(unresolved)}', flush=True)
                if unresolved or ceiling >= 5:
                    print('STOP: accounting unresolved or hard cap reached', flush=True)
                    return


if __name__ == '__main__':
    main()
