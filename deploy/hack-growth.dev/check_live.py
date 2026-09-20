#!/usr/bin/env python3
"""Require a healthy worker, one stable session, and advancing simulation time."""

import argparse
import json
import time
from urllib.request import urlopen


def read_json(url, timeout):
    with urlopen(url, timeout=timeout) as response:
        return json.load(response)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', default='http://127.0.0.1:8890')
    parser.add_argument('--window', type=float, default=12.0)
    parser.add_argument('--request-timeout', type=float, default=3.0)
    args = parser.parse_args()
    if args.window <= 0 or args.request_timeout <= 0:
        parser.error('Timeouts must be positive.')

    health = read_json(args.base + '/health', args.request_timeout)
    if health.get('status') not in ('ready', 'running'):
        raise SystemExit(f'worker is not healthy: {health!r}')
    queue = health.get('item_jobs')
    if not isinstance(queue, dict):
        raise SystemExit('health lacks item-job queue status')
    if queue.get('runner_thread_alive') is not True or queue.get('unhealthy_shutdown') is not False:
        raise SystemExit(f'item-job queue is not healthy: {queue!r}')
    if queue.get('provider_mode') != 'cached':
        raise SystemExit(f'item-job queue is not in cached mode: {queue!r}')
    if queue.get('last_fault') is not None:
        raise SystemExit(f'item-job queue has an active fault: {queue!r}')
    children = queue.get('active_children')
    required_stages = {'generation', 'render', 'physics_proposal', 'physics', 'training'}
    if not isinstance(children, dict) or not required_stages <= set(children) or any(
            not isinstance(count, int) or isinstance(count, bool) or count < 0 or count > 1
            for count in children.values()):
        raise SystemExit(f'item-job child count is invalid: {queue!r}')
    unavailable = queue.get('worker_unavailable_stages')
    if not isinstance(unavailable, list) or unavailable:
        raise SystemExit(f'an item-job stage is unavailable: {queue!r}')
    first = read_json(args.base + '/state', args.request_timeout)
    session_id = first.get('session_id')
    first_time = first.get('sim_time_s')
    if not isinstance(session_id, str) or not isinstance(first_time, (int, float)):
        raise SystemExit(f'state lacks session or simulation time: {first!r}')

    deadline = time.monotonic() + args.window
    while time.monotonic() < deadline:
        time.sleep(1)
        current = read_json(args.base + '/state', args.request_timeout)
        if current.get('session_id') != session_id:
            raise SystemExit('engine session changed during the health window')
        if current.get('sim_time_s', first_time) > first_time:
            return
    raise SystemExit('simulation time did not advance during the health window')


if __name__ == '__main__':
    main()
