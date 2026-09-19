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
        if engine:
            state.update(source_revision=engine.source_revision, source_hashes=engine.source_hashes)
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
                            raise ValueError('The session is ending. Restart the session before another injection.')
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
        self.ctx = mp.get_context('spawn')
        self.preset = Path(preset)
        self.out = Path(out)
        self.session_out = self.out
        self.restart_count = 0
        self.restarting = False
        self.recovery_session_id = str(uuid.uuid4())
        self.state = {'status': 'starting'}
        self.clients = set()
        self.requests = {}
        self.task = None
        self.state_ready = asyncio.Event()
        self.service_timings = {'snapshot_reads': 0, 'broadcasts': 0, 'json_encode_ms': 0.0, 'send_wait_ms': 0.0}
        self.service_samples = {key: deque(maxlen=4096) for key in ('json_encode_ms', 'send_wait_ms')}
        self.profile_written = False
        self._create_worker(self.session_out)

    def _create_worker(self, out):
        if getattr(self, 'process', None) is not None:
            raise RuntimeError('The previous engine worker handle is still open.')
        self.states = self.ctx.Queue(maxsize=2)
        self.acks = self.ctx.Queue(maxsize=MAX_COMMANDS)
        self.commands = self.ctx.Queue(maxsize=16)
        self.stop = self.ctx.Event()
        self.process = self.ctx.Process(
            target=worker,
            args=(str(self.preset), self.states, self.acks, self.commands, self.stop, str(out)),
        )

    def _record_worker_ack(self, ack):
        entry = self.requests.get(ack['command_id'])
        if entry and not entry.get('ack'):
            entry['ack'] = ack

    async def _broadcast_unsent_acks(self):
        for entry in self.requests.values():
            if entry.get('ack') and not entry.get('ack_sent'):
                await self.broadcast(entry['ack'])
                entry['ack_sent'] = True

    async def _drain_worker_queues(self, resolve_acks):
        if self.states is not None:
            while True:
                try:
                    packet = self.states.get_nowait()
                except Empty:
                    break
                if resolve_acks:
                    for ack in packet.get('command_results', []):
                        self._record_worker_ack(ack)
        if self.acks is not None:
            while True:
                try:
                    ack = self.acks.get_nowait()
                except Empty:
                    break
                if resolve_acks:
                    self._record_worker_ack(ack)

    async def _join_worker(self, timeout, resolve_acks):
        deadline = asyncio.get_running_loop().time() + timeout
        while self.process.is_alive() and asyncio.get_running_loop().time() < deadline:
            await self._drain_worker_queues(resolve_acks)
            remaining = deadline - asyncio.get_running_loop().time()
            await asyncio.to_thread(self.process.join, min(0.05, max(0, remaining)))
        await self._drain_worker_queues(resolve_acks)

    async def _stop_worker(self, resolve_acks=False):
        if self.process is None:
            return
        self.stop.set()
        if self.process.pid is None:
            return
        await self._join_worker(5, resolve_acks)
        if self.process.is_alive():
            self.process.terminate()
            await self._join_worker(2, resolve_acks)
        if self.process.is_alive():
            self.process.kill()
            await self._join_worker(2, resolve_acks)
        if resolve_acks:
            await self._broadcast_unsent_acks()
        if self.process.is_alive():
            raise RuntimeError('The engine worker did not stop.')

    async def _cancel_pump(self):
        if self.task is None:
            return
        task = self.task
        self.task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    def _close_queues(self):
        for queue in (self.states, self.acks, self.commands):
            if queue is None:
                continue
            with contextlib.suppress(Exception):
                queue.cancel_join_thread()
            with contextlib.suppress(Exception):
                queue.close()
        self.states = self.acks = self.commands = None
        if self.process is not None:
            try:
                self.process.close()
            except Exception:
                return
        self.stop = self.process = None

    def _reset_profile(self):
        self.service_timings = {
            'snapshot_reads': 0, 'broadcasts': 0, 'json_encode_ms': 0.0, 'send_wait_ms': 0.0,
        }
        self.service_samples = {key: deque(maxlen=4096) for key in ('json_encode_ms', 'send_wait_ms')}
        self.profile_written = False

    def _write_service_profile(self):
        if self.profile_written:
            return
        self.session_out.mkdir(parents=True, exist_ok=True)
        scope = ('HTTP process through terminal state broadcast'
                 if self.state.get('status') in ('completed', 'failed')
                 else 'HTTP process through restart request')
        profile = {**self.service_timings, 'session_id': self.state.get('session_id'),
                   'scope': scope,
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
        (self.session_out / 'service-profile.json').write_text(json.dumps(profile, indent=2))
        self.profile_written = True

    async def lifecycle(self, app):
        self.process.start()
        self.task = asyncio.create_task(self.pump())
        try:
            yield
        finally:
            try:
                await self._cancel_pump()
            finally:
                try:
                    await self._stop_worker()
                finally:
                    try:
                        self._write_service_profile()
                    finally:
                        self._close_queues()

    async def shutdown(self, app):
        if self.stop is not None:
            self.stop.set()
        async def close(client):
            with contextlib.suppress(TimeoutError, ConnectionError):
                await asyncio.wait_for(client.close(code=1001, message=b'Service stopped'), timeout=1)
        await asyncio.gather(*(close(client) for client in tuple(self.clients)))

    async def broadcast(self, packet):
        if packet.get('type') == 'state':
            packet = {**packet, 'restart_supported': True}
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
                with contextlib.suppress(TimeoutError, ConnectionError, RuntimeError):
                    await asyncio.wait_for(client.close(), timeout=0.5)
        started = time.perf_counter()
        await asyncio.gather(*(send(client) for client in tuple(self.clients)))
        elapsed = (time.perf_counter() - started) * 1000
        self.service_timings['send_wait_ms'] += elapsed
        self.service_samples['send_wait_ms'].append(elapsed)

    def _set_worker_state(self, state):
        if state.get('session_id'):
            self.recovery_session_id = state['session_id']
        elif state.get('status') == 'failed':
            state = {**state, 'session_id': self.recovery_session_id}
        self.state = state

    async def pump(self):
        while True:
            changed = False
            while True:
                try:
                    self._set_worker_state(self.states.get_nowait())
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
                if entry:
                    entry['ack_sent'] = True
            if not self.process.is_alive() and self.state['status'] not in ('completed', 'failed'):
                # Allow the multiprocessing feeder to deliver its terminal state.
                await asyncio.sleep(0.05)
                try:
                    self._set_worker_state(self.states.get_nowait())
                except Empty:
                    self.state = {**self.state, 'status': 'failed',
                                  'session_id': self.recovery_session_id,
                                  'error': 'The engine worker stopped unexpectedly.'}
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
                    entry['ack_sent'] = True
            if changed:
                await self.broadcast({'type': 'state', **self.state})
                if self.state['status'] in ('ready', 'failed'):
                    self.state_ready.set()
                if self.state['status'] in ('completed', 'failed'):
                    self._write_service_profile()
            await asyncio.sleep(0.025)

    async def _resolve_pending(self, error):
        await self._broadcast_unsent_acks()
        for command_id, entry in self.requests.items():
            if entry.get('ack'):
                continue
            entry['ack'] = {'type': 'ack', 'command_id': command_id,
                            'session_id': self.recovery_session_id,
                            'ok': False, 'error': error}
            await self.broadcast(entry['ack'])
            entry['ack_sent'] = True

    async def _cleanup_restart_failure(self, error):
        cleanup_errors = []
        try:
            await self._cancel_pump()
        except Exception as exc:
            cleanup_errors.append(f'pump cleanup failed: {exc}')
        try:
            await self._stop_worker(resolve_acks=True)
        except Exception as exc:
            cleanup_errors.append(f'worker cleanup failed: {exc}')
        try:
            await self._resolve_pending('The session stopped before this injection completed.')
        except Exception as exc:
            cleanup_errors.append(f'command cleanup failed: {exc}')
        try:
            self._write_service_profile()
        except Exception as exc:
            cleanup_errors.append(f'profile write failed: {exc}')
        finally:
            self._close_queues()
        if self.process is not None:
            cleanup_errors.append('the worker handle remains open')
        if cleanup_errors:
            error = f"{error} Cleanup errors: {'. '.join(cleanup_errors)}"
        retained = {key: self.state[key] for key in
                    ('source_revision', 'source_hashes', 'limits', 'previous_session_id')
                    if key in self.state}
        self.state = {**retained, 'status': 'failed',
                      'session_id': self.recovery_session_id, 'error': error}
        with contextlib.suppress(Exception):
            await self.broadcast({'type': 'state', **self.state})

    async def restart(self, request):
        try:
            body = json.loads(await request.text())
            if not isinstance(body, dict):
                raise ValueError('Send a JSON object.')
            session_id = str(uuid.UUID(body.get('session_id', '')))
        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            return web.json_response({'status': 'invalid', 'error': 'Send the current UUID session_id.'}, status=400)
        if self.restarting:
            return web.json_response({'status': 'restarting', 'error': 'A restart is already in progress.'}, status=409)
        current_session_id = self.state.get('session_id')
        if session_id != current_session_id:
            return web.json_response({'status': 'stale', 'error': 'The session changed.',
                                      'session_id': current_session_id}, status=409)

        self.restarting = True
        try:
            self.state = {**self.state, 'status': 'restarting', 'error': None}
            await self.broadcast({'type': 'state', **self.state})
            await self._cancel_pump()
            await self._stop_worker(resolve_acks=True)
            await self._resolve_pending('The session restarted before this injection completed.')
            self._write_service_profile()
            self._close_queues()
            self.requests = {}
            self.restart_count += 1
            suffix = f'restart-{self.restart_count:03d}-{uuid.uuid4().hex[:8]}'
            self.session_out = self.out.parent / f'{self.out.name}-{suffix}'
            self._reset_profile()
            self.state = {'status': 'starting', 'previous_session_id': session_id}
            self.state_ready = asyncio.Event()
            self._create_worker(self.session_out)
            await self.broadcast({'type': 'state', **self.state})
            self.process.start()
            self.task = asyncio.create_task(self.pump())
            await asyncio.wait_for(self.state_ready.wait(), timeout=30)
            if self.state['status'] == 'failed':
                await self._cleanup_restart_failure(self.state.get('error') or 'The engine failed to start.')
                return web.json_response(self.state, status=500)
            return web.json_response({'status': self.state['status'],
                                      'previous_session_id': session_id,
                                      'session_id': self.state.get('session_id'),
                                      'evidence_directory': str(self.session_out)})
        except asyncio.CancelledError:
            cleanup = asyncio.create_task(self._cleanup_restart_failure('The restart request was cancelled.'))
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await cleanup
            except Exception:
                pass
            raise
        except Exception as exc:
            await self._cleanup_restart_failure(f'Restart failed: {exc}')
            return web.json_response(self.state, status=500)
        finally:
            self.restarting = False

    async def health(self, request):
        status = self.state['status']
        return web.json_response({'status': status, 'session_id': self.state.get('session_id'),
                                  'error': self.state.get('error')}, status=503 if status == 'failed' else 200)

    async def state_handler(self, request):
        return web.json_response({**self.state, 'restart_supported': True})

    async def websocket(self, request):
        if len(self.clients) >= MAX_CLIENTS:
            raise web.HTTPServiceUnavailable(text='Four browsers already share this session.')
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2048)
        await ws.prepare(request)
        self.clients.add(ws)
        await ws.send_json({'type': 'state', **self.state, 'restart_supported': True})
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
                    if self.restarting:
                        raise ValueError('The session is restarting. Retry after it starts.')
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
                        raise ValueError('The session is unavailable. Restart the session.')
                    if len(self.requests) >= MAX_COMMANDS:
                        raise ValueError('This session reached its command limit. Restart the session.')
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
        origin_required = request.path == '/ws' or (request.path == '/restart' and request.method == 'POST')
        if (origin and origin not in allowed_origins) or (origin_required and not origin):
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
                    web.get('/state', service.state_handler), web.get('/ws', service.websocket),
                    web.post('/restart', service.restart)])
    print(f'Evidence directory: {args.out.resolve()}', flush=True)
    web.run_app(app, host=args.host, port=args.port, access_log=None, shutdown_timeout=3)


if __name__ == '__main__':
    main()
