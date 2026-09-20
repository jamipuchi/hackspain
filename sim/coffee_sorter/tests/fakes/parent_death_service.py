"""A service-like parent that starts the REAL live.worker and then hard-exits.

The test process is the grandparent, so it survives and can observe the worker. This
parent never drains the states queue, which is what the HTTP pump normally does.
"""
import json
import multiprocessing as mp
import os
import queue as queue_module
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
# Append, so the fake engine directory that PYTHONPATH puts earlier still wins.
sys.path.append(str(HERE.parents[1]))


def main():
    marker, out = Path(sys.argv[1]), Path(sys.argv[2])
    graceful = len(sys.argv) > 3 and sys.argv[3] == 'graceful'
    import live

    context = mp.get_context('spawn')
    states = context.Queue(maxsize=2)
    acknowledgments = context.Queue(maxsize=64)
    commands = context.Queue(maxsize=16)
    stop = context.Event()
    process = context.Process(target=live.worker,
                              args=('unused.json', states, acknowledgments, commands,
                                    stop, str(out)))
    process.start()
    deadline = time.monotonic() + 30
    # Never drain states: wait until the feeder has certainly blocked on a full pipe.
    while time.monotonic() < deadline and not (out / 'engine-filled').is_file():
        time.sleep(0.02)
    marker.write_text(json.dumps({
        'worker_pid': process.pid, 'parent_pid': os.getpid(),
        'engine_started': (out / 'engine-started').is_file(),
        'engine_filled': (out / 'engine-filled').is_file(),
    }))
    if graceful:
        # The real service drains state packets in its pump, so the graceful model must
        # drain them too. Without a reader the worker cannot close its queues.
        draining = threading.Event()

        def drain():
            while not draining.is_set():
                try:
                    states.get(timeout=0.05)
                except (queue_module.Empty, OSError, ValueError):
                    continue

        reader = threading.Thread(target=drain, name='state-drain', daemon=True)
        reader.start()
        commands.put({'type': 'inject', 'command_id': 'graceful-command',
                      'session_id': 'parent-death-session', 'command_epoch': 'epoch',
                      'class_name': 'stone'})
        acknowledgment = acknowledgments.get(timeout=30)
        stop.set()
        process.join(30)
        draining.set()
        (out / 'graceful.json').write_text(json.dumps({
            'acknowledgment': acknowledgment, 'exitcode': process.exitcode}))
        raise SystemExit(0)
    os._exit(17)


if __name__ == '__main__':
    main()
