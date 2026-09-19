"""Serve one bounded coffee engine session on loopback."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections import deque
import json
import multiprocessing as mp
import os
import signal
from pathlib import Path
from queue import Empty, Full
import time
import traceback
import uuid

from aiohttp import WSMsgType, web

HERE = Path(__file__).resolve().parent
MAX_COMMANDS = 64
MAX_CLIENTS = 4


def worker(preset, states, acknowledgments, commands, stop, out):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        os.environ[name] = '1'
    engine = None
    command_results = {}
    sim_limit, wall_limit = 0, 0
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    def publish(status, error=None):
        state = engine.snapshot() if engine else {}
        state.update(status=status, error=error, limits={
            'sim_seconds': sim_limit, 'wall_seconds': wall_limit,
            'clients': MAX_CLIENTS, 'commands': MAX_COMMANDS,
        })
        if status in ('completed', 'failed'):
            state['command_results'] = list(command_results.values())
        # A stale pose packet can be discarded. Acknowledgments have a separate queue.
        while not stop.is_set():
            try:
                states.put(state, timeout=0.02)
                return
            except Full:
                with contextlib.suppress(Empty):
                    states.get(timeout=0.02)

    try:
        from engine import Engine
        engine = Engine(Path(preset))
        sim_limit = float(engine.preset['limits']['max_sim_seconds'])
        wall_limit = float(engine.preset['limits']['max_wall_seconds'])
        if not (0.6 < sim_limit <= 10 and 0 < wall_limit <= 300):
            raise ValueError('Session limits must allow 0.6 to 10 simulation seconds and at most 300 wall seconds.')
        publish('ready')
        running = False
        started = None
        last_publish = time.monotonic()
        reason = 'stopped'
        with (out / 'commands.jsonl').open('a') as log:
            while not stop.is_set():
                try:
                    command = commands.get(timeout=0.05) if not running else commands.get_nowait()
                except Empty:
                    command = None
                if command:
                    ack = dict(type='ack', command_id=command['command_id'], session_id=engine.session_id)
                    try:
                        if command['session_id'] != engine.session_id:
                            raise ValueError('The session changed. Reload the page.')
                        if running and engine.sim.data.time >= sim_limit - 0.6:
                            raise ValueError('The session is ending. Restart the service before another injection.')
                        object_id = engine.inject(command['class_name'])
                        ack.update(ok=True, object_id=object_id, sim_time_s=float(engine.sim.data.time))
                        if not running:
                            running, started = True, time.monotonic()
                    except ValueError as exc:
                        ack.update(ok=False, error=str(exc))
                    command_results[ack['command_id']] = ack
                    acknowledgments.put(ack, timeout=1)
                    log.write(json.dumps({**command, **ack}) + '\n')
                    log.flush()
                    publish('running' if running else 'ready')
                if not running:
                    continue
                engine.step()
                now = time.monotonic()
                if now - last_publish >= 0.1:
                    publish('running')
                    last_publish = now
                if engine.sim.data.time >= sim_limit or now - started >= wall_limit:
                    reason = 'simulation limit' if engine.sim.data.time >= sim_limit else 'wall-time limit'
                    break
        report = engine.report()
        report['completion_reason'] = reason
        (out / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False))
        final = engine.snapshot()
        (out / 'final-state.json').write_text(json.dumps(final, indent=2, allow_nan=False))
        publish('completed')
    except Exception as exc:
        traceback.print_exc()
        (out / 'error.txt').write_text(traceback.format_exc())
        publish('failed', str(exc))
    finally:
        if engine is not None:
            engine.close()
        # The HTTP process drains the final packet before joining this worker.
        acknowledgments.close()
        states.close()


class LiveService:
    def __init__(self, preset, out):
        ctx = mp.get_context('spawn')
        self.states = ctx.Queue(maxsize=2)
        self.acks = ctx.Queue(maxsize=MAX_COMMANDS)
        self.commands = ctx.Queue(maxsize=16)
        self.stop = ctx.Event()
        self.process = ctx.Process(target=worker, args=(str(preset), self.states, self.acks,
                                                       self.commands, self.stop, str(out)))
        self.state = {'status': 'starting'}
        self.clients = set()
        self.requests = {}
        self.task = None
        self.out = Path(out)
        self.service_timings = {'snapshot_reads': 0, 'broadcasts': 0, 'json_encode_ms': 0.0, 'send_wait_ms': 0.0}
        self.service_samples = {key: deque(maxlen=4096) for key in ('json_encode_ms', 'send_wait_ms')}

    async def lifecycle(self, app):
        self.process.start()
        self.task = asyncio.create_task(self.pump())
        yield
        self.stop.set()
        await asyncio.to_thread(self.process.join, 5)
        if self.process.is_alive():
            self.process.terminate()
            await asyncio.to_thread(self.process.join, 2)
        if self.process.is_alive():
            self.process.kill()
            await asyncio.to_thread(self.process.join, 2)
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self.task
        for queue in (self.states, self.acks, self.commands):
            queue.cancel_join_thread()
            queue.close()

    async def shutdown(self, app):
        self.stop.set()
        async def close(client):
            with contextlib.suppress(TimeoutError, ConnectionError):
                await asyncio.wait_for(client.close(code=1001, message=b'Service stopped'), timeout=1)
        await asyncio.gather(*(close(client) for client in tuple(self.clients)))

    async def broadcast(self, packet):
        started = time.perf_counter()
        encoded = json.dumps(packet, allow_nan=False)
        elapsed = (time.perf_counter() - started) * 1000
        self.service_timings['json_encode_ms'] += elapsed
        self.service_samples['json_encode_ms'].append(elapsed)
        self.service_timings['broadcasts'] += 1
        async def send(client):
            try:
                await asyncio.wait_for(client.send_str(encoded), timeout=0.5)
            except (TimeoutError, ConnectionError, RuntimeError):
                self.clients.discard(client)
                await client.close()
        started = time.perf_counter()
        await asyncio.gather(*(send(client) for client in tuple(self.clients)))
        elapsed = (time.perf_counter() - started) * 1000
        self.service_timings['send_wait_ms'] += elapsed
        self.service_samples['send_wait_ms'].append(elapsed)

    async def pump(self):
        while True:
            changed = False
            while True:
                try:
                    self.state = self.states.get_nowait()
                    self.service_timings['snapshot_reads'] += 1
                    changed = True
                except Empty:
                    break
            while True:
                try:
                    ack = self.acks.get_nowait()
                except Empty:
                    break
                entry = self.requests.get(ack['command_id'])
                if entry:
                    entry['ack'] = ack
                await self.broadcast(ack)
            if not self.process.is_alive() and self.state['status'] not in ('completed', 'failed'):
                # Allow the multiprocessing feeder to deliver its terminal state.
                await asyncio.sleep(0.05)
                try:
                    self.state = self.states.get_nowait()
                except Empty:
                    self.state = {**self.state, 'status': 'failed', 'error': 'The engine worker stopped unexpectedly.'}
                changed = True
            if changed and self.state['status'] in ('completed', 'failed'):
                # Terminal results resolve cross-queue delivery order before rejecting unprocessed commands.
                for ack in self.state.get('command_results', []):
                    entry = self.requests.get(ack['command_id'])
                    if entry:
                        entry['ack'] = ack
                for command_id, entry in self.requests.items():
                    if not entry.get('ack'):
                        entry['ack'] = {'type': 'ack', 'command_id': command_id, 'ok': False,
                                        'error': 'The engine stopped before this injection completed.'}
                    await self.broadcast(entry['ack'])
            if changed:
                await self.broadcast({'type': 'state', **self.state})
                if self.state['status'] in ('completed', 'failed'):
                    self.out.mkdir(parents=True, exist_ok=True)
                    profile = {**self.service_timings, 'session_id': self.state.get('session_id'),
                               'scope': 'HTTP process through terminal state broadcast',
                               'note': 'Send wait includes backpressure. Worker IPC and scheduling remain in unattributed_wall_ms.'}
                    profile['distributions'] = {}
                    for name, samples in self.service_samples.items():
                        ordered = sorted(samples)
                        profile['distributions'][name] = {
                            'retained_samples': len(ordered), 'retention_limit': 4096,
                            'p50_ms': ordered[int((len(ordered)-1)*0.5)] if ordered else None,
                            'p95_ms': ordered[int((len(ordered)-1)*0.95)] if ordered else None,
                            'max_ms': max(ordered, default=None),
                            'over_100ms_update_interval': sum(value > 100 for value in ordered)}
                    (self.out / 'service-profile.json').write_text(json.dumps(profile, indent=2))
            await asyncio.sleep(0.025)

    async def health(self, request):
        status = self.state['status']
        return web.json_response({'status': status, 'session_id': self.state.get('session_id'),
                                  'error': self.state.get('error')}, status=503 if status == 'failed' else 200)

    async def state_handler(self, request):
        return web.json_response(self.state)

    async def websocket(self, request):
        if len(self.clients) >= MAX_CLIENTS:
            raise web.HTTPServiceUnavailable(text='Four browsers already share this session.')
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2048)
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_json({'type': 'state', **self.state})
        # Retain acknowledgments across reconnects for this bounded session.
        for entry in self.requests.values():
            if entry.get('ack'):
                await ws.send_json(entry['ack'])
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                command_id = None
                try:
                    command = json.loads(message.data)
                    if not isinstance(command, dict):
                        raise ValueError('Send a JSON object.')
                    command_id = str(uuid.UUID(command.get('command_id', '')))
                    if command.get('type') != 'inject':
                        raise ValueError('Only injection commands are supported.')
                    if command.get('session_id') != self.state.get('session_id'):
                        raise ValueError('The session changed. Reload the page.')
                    if not isinstance(command.get('class_name'), str) or len(command['class_name']) > 32:
                        raise ValueError('Select a supported class.')
                    payload = {'command_id': command_id, 'session_id': command['session_id'],
                               'class_name': command['class_name']}
                    previous = self.requests.get(command_id)
                    if previous:
                        if previous['payload'] != payload:
                            raise ValueError('This command ID already belongs to another request.')
                        await ws.send_json(previous.get('ack') or {'type': 'pending', 'command_id': command_id})
                        continue
                    if self.state['status'] not in ('ready', 'running'):
                        raise ValueError('The session is unavailable. Restart the service.')
                    if len(self.requests) >= MAX_COMMANDS:
                        raise ValueError('This session reached its command limit. Restart the service.')
                    self.commands.put_nowait(payload)
                    self.requests[command_id] = {'payload': payload}
                except (ValueError, TypeError, AttributeError, Full) as exc:
                    error = 'The command queue is full. Retry shortly.' if isinstance(exc, Full) else str(exc)
                    await ws.send_json({'type': 'ack', 'command_id': command_id, 'ok': False, 'error': error})
        finally:
            self.clients.discard(ws)
        return ws


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=['127.0.0.1'], default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8890)
    parser.add_argument('--preset', type=Path, default=HERE / 'configs/default_demo.json')
    parser.add_argument('--out', type=Path, default=HERE / 'runs' / time.strftime('live-%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    service = LiveService(args.preset.resolve(), args.out.resolve())
    allowed_hosts = {f'127.0.0.1:{args.port}', f'localhost:{args.port}'}
    allowed_origins = {'http://' + host for host in allowed_hosts}

    @web.middleware
    async def local_access(request, handler):
        if request.host not in allowed_hosts:
            raise web.HTTPForbidden(text='Use the loopback service URL.')
        origin = request.headers.get('Origin')
        if (origin and origin not in allowed_origins) or (request.path == '/ws' and not origin):
            raise web.HTTPForbidden(text='Use the page served by this loopback service.')
        response = await handler(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    async def page(request):
        return web.FileResponse(HERE / 'live_web/index.html')

    async def script(request):
        return web.FileResponse(HERE / 'live_web/live.js')

    app = web.Application(middlewares=[local_access], client_max_size=2048)
    app.cleanup_ctx.append(service.lifecycle)
    app.on_shutdown.append(service.shutdown)
    app.add_routes([web.get('/', page), web.get('/live.js', script), web.get('/health', service.health),
                    web.get('/state', service.state_handler), web.get('/ws', service.websocket)])
    print(f'Evidence directory: {args.out.resolve()}', flush=True)
    web.run_app(app, host=args.host, port=args.port, access_log=None, shutdown_timeout=3)


if __name__ == '__main__':
    main()
