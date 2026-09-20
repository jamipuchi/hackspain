"""Serve bounded or continuous coffee engine sessions on configured hosts."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
from collections import deque
import hashlib
import importlib.util
import ipaddress
import json
import logging
from logging.handlers import RotatingFileHandler
import multiprocessing as mp
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
import tempfile
import threading
from queue import Empty, Full
import time
import traceback
from urllib.parse import urlsplit
import uuid

from aiohttp import WSMsgType, web

# The one physical settling time. rolling_scores holds no model and no profile, so this
# import cannot load profiles before the active bundle catalog is exported.
from rolling_scores import SETTLING_SECONDS

import item_jobs
import object_catalog

HERE = Path(__file__).resolve().parent
MAX_COMMANDS = 64
MAX_CLIENTS = 4
MAX_PENDING_COMMANDS = 16
MAX_COMMANDS_PER_EPOCH = 256
COMMAND_EPOCH_SECONDS = 60
PUMP_STALE_SECONDS = 3.0
SOURCE_REVISION_PATTERN = re.compile(r'[0-9a-f]{40}')
DNS_LABEL_PATTERN = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?')
# A worker whose parent died has no reader for its results. It leaves after this bound.
ORPHAN_EXIT_SECONDS = 10.0
ORPHAN_EXIT_CODE = 3
VALIDATE_BUNDLE_TIMEOUT_S = 120.0
# Only the service sends these. The WebSocket admits inject and set_reject_policy alone.
ACTIVATION_COMMANDS = ('prepare_activation', 'commit_activation', 'cancel_activation')
DRAIN_TIMEOUT_S = 20.0
ACTIVATION_ACK_TIMEOUT_S = 30.0
# Cleanup gets its own short bound. It runs after the drain budget is already spent.
ACTIVATION_CANCEL_TIMEOUT_S = 5.0
ACTIVATION_POLL_S = 0.05
# The frozen record block of the increment C contract. Every member is always present.
# The two catalog revisions and the model artifact come from the training side, not here.
ACTIVATION_MEMBERS = ('phase', 'active_objects', 'drain_timeout_seconds', 'result',
                      'rolled_back', 'message', 'bundle_sha256', 'previous_bundle_sha256',
                      'activation_policy_version', 'session_id', 'score_epoch_id')
# The sources that bundle zero records. engine.SOURCE_FILES cannot be imported for this:
# engine imports profiles, and profiles must not load before the active catalog is exported.
BUNDLE_SOURCE_FILES = ('engine.py', 'controller.py', 'sim.py', 'rolling_scores.py', 'vision.py',
                       'classifier.py', 'profiles.py', 'scene.py', 'live.py', 'object_catalog.py')
DEFAULT_RUNTIME_LOCK = Path('/private/tmp/hackspain-coffee-runtime.lock')
# Local development replays the recorded research cache. The service never writes there.
DEFAULT_PROVIDER_CACHE = (HERE.parents[1]
                          / 'thoughts/taras/research/coffee-quality/object-generation/results')
ITEM_JOB_STATUS = {
    'invalid_request': 400, 'invalid_description': 400, 'invalid_action': 400,
    'paid_mode_disabled': 403, 'history_full': 429, 'unknown_job': 404, 'unknown_preview': 404,
    'preview_unavailable': 404, 'unsupported_job_schema': 500, 'request_conflict': 409,
    'catalog_revision_conflict': 409, 'not_available': 409, 'worker_unavailable': 409,
    'fake_provider_not_activatable': 409, 'queue_full': 429,
}


def _file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _normalize_allowed_host(value):
    value = value.strip().lower()
    if not value or value == '*' or any(character.isspace() for character in value):
        raise ValueError('Allowed hosts must name one explicit DNS host or IPv4 address.')
    parsed = urlsplit('//' + value)
    if (parsed.hostname is None or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment or ':' in parsed.hostname):
        raise ValueError('Allowed hosts must name one explicit DNS host or IPv4 address.')
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError('Allowed host ports must be between 1 and 65535.') from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Allowed host ports must be between 1 and 65535.')
    if ':' in value and port is None:
        raise ValueError('Allowed host ports must be explicit numbers.')
    hostname = parsed.hostname
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split('.')
        if (len(hostname) > 253 or re.fullmatch(r'[0-9.]+', hostname)
                or any(DNS_LABEL_PATTERN.fullmatch(label) is None for label in labels)):
            raise ValueError('Allowed hosts must name one valid DNS host or IPv4 address.')
    else:
        if address.version != 4:
            raise ValueError('Allowed hosts must name one valid DNS host or IPv4 address.')
    return f'{hostname}:{port}' if port is not None else hostname


def _normalize_allowed_origin(value):
    parsed = urlsplit(value.strip())
    if (parsed.scheme not in ('http', 'https') or parsed.hostname is None
            or parsed.username is not None or parsed.password is not None
            or parsed.path or parsed.query or parsed.fragment):
        raise ValueError('Allowed origins must be exact HTTP or HTTPS origins without paths.')
    host = _normalize_allowed_host(parsed.netloc)
    return f'{parsed.scheme}://{host}'


def access_rules(port, public_hosts=(), public_origins=()):
    hosts = {f'127.0.0.1:{port}', f'localhost:{port}'}
    origins = {'http://' + host for host in hosts}
    hosts.update(_normalize_allowed_host(value) for value in public_hosts)
    origins.update(_normalize_allowed_origin(value) for value in public_origins)
    if any(urlsplit(origin).netloc not in hosts for origin in origins):
        raise ValueError('Every allowed origin must use an allowed host.')
    return frozenset(hosts), frozenset(origins)


def configured_access_rules(bind_host, port, public_hosts=(), public_origins=()):
    if bind_host == '0.0.0.0' and (not public_hosts or not public_origins):
        raise ValueError('Public binding requires at least one allowed host and origin.')
    return access_rules(port, public_hosts, public_origins)


def _configured_source_revision():
    value = os.environ.get('CINTA_SOURCE_REVISION')
    if value is None:
        return None
    value = value.strip().lower()
    if SOURCE_REVISION_PATTERN.fullmatch(value) is None:
        raise ValueError('CINTA_SOURCE_REVISION must be a full Git SHA.')
    return value


def access_middleware(allowed_hosts, allowed_origins):
    @web.middleware
    async def validate_access(request, handler):
        if request.host.lower() not in allowed_hosts:
            raise web.HTTPForbidden(text='Use an allowed service host.')
        origin = request.headers.get('Origin')
        if origin is not None:
            try:
                origin = _normalize_allowed_origin(origin)
            except ValueError as exc:
                raise web.HTTPForbidden(text='Use an allowed page origin.') from exc
        origin_required = request.path == '/ws' or (request.path == '/restart' and request.method == 'POST')
        if (origin and origin not in allowed_origins) or (origin_required and not origin):
            raise web.HTTPForbidden(text='Use an allowed page origin.')
        response = await handler(request)
        response.headers['Cache-Control'] = 'no-store'
        return response

    return validate_access


def _item_job_response(error_code):
    return web.json_response({'ok': False, 'error_code': error_code},
                             status=ITEM_JOB_STATUS.get(error_code, 500))


_PRIVATE_WORKER_FIELDS = ('token', 'log')
_PRIVATE_ARTIFACTS = ('runtime_lock',)
# Evidence carries nested trainer and validator records, so a top-level deny-list cannot
# see every path. Any absolute path in a published value becomes this marker.
_REDACTED_PATH = '<path>'
_ABSOLUTE_PATH = re.compile(r'(?<![\w.~-])(?:/[^\s"\',;:)\]}]+)+')
# The queue builds this one URL itself from a validated request id. It is a route of this
# service, not a host path, and the Items modal loads the early preview from it.
_PREVIEW_ROUTE = re.compile(r'/item-jobs/[0-9a-f-]{36}/previews/[a-z]+\.png')


def _public_worker(worker):
    """Publish worker ownership without its fencing token or any host path."""
    if not worker:
        return None
    return {key: value for key, value in worker.items() if key not in _PRIVATE_WORKER_FIELDS}


def _without_host_paths(value):
    """Walk any published value and redact absolute paths, at any depth.

    A job record now carries trainer and validator evidence written by children, so the
    only safe rule is structural: no published string may contain an absolute path.
    """
    if isinstance(value, str):
        if _PREVIEW_ROUTE.fullmatch(value):
            return value
        return _ABSOLUTE_PATH.sub(_REDACTED_PATH, value)
    if isinstance(value, dict):
        return {key: _without_host_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_without_host_paths(item) for item in value]
    return value


def _public_record(record):
    """Redact one top-level job or summary record, and keep its request text verbatim.

    The description and the requester name were validated at admission and no child can
    write them. A URL or a slash in that text is content, never a host path.
    """
    public = _without_host_paths(record)
    public.update({key: record[key] for key in ('description', 'requester_name')
                   if key in record})
    return public


def _public_job(job):
    """One job record for a public response. No child-written value may carry a host path."""
    artifacts = {key: value for key, value in (job.get('artifacts') or {}).items()
                 if key not in _PRIVATE_ARTIFACTS}
    public = {**job, 'worker': _public_worker(job.get('worker')), 'artifacts': artifacts}
    return _public_record(public)


def _reset_state_function():
    """Load the reset helper from its repository path without adding deploy to sys.path."""
    path = HERE.parents[1] / 'deploy' / 'hack-growth.dev' / 'reset_live_state.py'
    spec = importlib.util.spec_from_file_location('_cinta_reset_live_state', path)
    if spec is None or spec.loader is None:
        raise RuntimeError('The reset helper is unavailable.')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.reset_state


def _validate_item_job_arguments(parser, args, item_jobs_root):
    """Fail fast only on a contradiction. Compatible defaults must keep starting."""
    mode = args.item_jobs_provider
    if mode == 'fake' and args.host != '127.0.0.1':
        parser.error('--item-jobs-provider fake requires the loopback host 127.0.0.1.')
    if mode == 'paid' and args.item_jobs_provider_env is None:
        parser.error('--item-jobs-provider paid requires --item-jobs-provider-env.')
    if args.item_jobs_root is not None and args.object_catalog_root is not None:
        parser.error('--object-catalog-root cannot be used with --item-jobs-root: the '
                     'active bundle under that root selects the catalog.')
    # An explicitly named path that does not exist is a contradiction, not a default.
    for name, value, check in (
            ('--item-jobs-provider-cache', args.item_jobs_provider_cache, 'is_dir'),
            ('--item-jobs-generator-root', args.item_jobs_generator_root, 'is_dir'),
            ('--item-jobs-physics-replay', args.item_jobs_physics_replay, 'is_dir'),
            ('--item-jobs-provider-env', args.item_jobs_provider_env, 'is_file')):
        if value is not None and not getattr(Path(value), check)():
            parser.error(f'{name} does not exist: {value}')
    # Reviewed replay metadata is an operator input. It may never live where the service
    # writes, or a job could plant its own replay.
    if args.item_jobs_physics_replay is not None:
        replay = args.item_jobs_physics_replay.resolve()
        if replay == item_jobs_root or item_jobs_root in replay.parents:
            parser.error('--item-jobs-physics-replay must stay outside --item-jobs-root.')
    # The local default applies only when it really exists. A packaged image without a
    # provider cache must refuse to start instead of dead-ending every job.
    if mode in ('cached', 'paid') and args.item_jobs_provider_cache is None \
            and not (DEFAULT_PROVIDER_CACHE / 'cache').is_dir():
        parser.error(f'--item-jobs-provider {mode} requires --item-jobs-provider-cache '
                     f'because no provider cache exists at the development default.')
    if args.item_jobs_provider_env is None:
        return
    credential = args.item_jobs_provider_env.resolve()
    if credential == item_jobs_root or item_jobs_root in credential.parents:
        parser.error('--item-jobs-provider-env must stay outside --item-jobs-root.')


def resolve_model_path(preset, preset_path, source_dir):
    """Resolve `model_path`. `model_path_root: preset` binds it to the preset directory.

    A bundle carries its model beside its preset, so a relative path must resolve there
    and must stay there. Without the field the behavior is unchanged: relative to the
    source directory. `engine.Engine.__init__` holds the matching resolution for the
    engine child, which must not import this service module.
    """
    raw = preset.get('model_path', '')
    model_path = Path(raw)
    root = preset.get('model_path_root')
    if root is None:
        return model_path if model_path.is_absolute() else Path(source_dir) / model_path
    if root != 'preset':
        raise ValueError('model_path_root must be "preset" when it is present.')
    if model_path.is_absolute():
        raise ValueError('model_path_root "preset" requires a relative model_path.')
    directory = Path(preset_path).resolve().parent
    resolved = (directory / model_path).resolve()
    if not resolved.is_relative_to(directory):
        raise ValueError('model_path must stay inside the preset directory.')
    return resolved


def load_preset(preset_path):
    """Load a preset and validate continuous model compatibility without training."""
    preset_path = Path(preset_path).resolve()
    preset = json.loads(preset_path.read_text())
    if preset.get('mode') != 'continuous':
        return preset

    limits = preset.get('limits', {})
    if limits.get('max_sim_seconds') is not None or limits.get('max_wall_seconds') is not None:
        raise ValueError('Continuous presets must disable simulation and wall-time limits.')
    if preset.get('score_window_seconds') != 60.0:
        raise ValueError('Continuous presets must use a 60 simulation-second score window.')

    model_path = resolve_model_path(preset, preset_path, HERE)
    manifest_path = model_path.with_suffix('.manifest.json')
    if not model_path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError('The selected model and its adjacent manifest are required.')
    manifest = json.loads(manifest_path.read_text())
    if manifest.get('artifact_sha256') != _file_hash(model_path):
        raise ValueError('The selected model bytes do not match the adjacent manifest.')

    from classifier import Model
    from profiles import PROFILES
    from vision import FEATURES

    profile = PROFILES.get(preset.get('profile'))
    if profile is None:
        raise ValueError('The continuous preset uses an unsupported profile.')
    model = Model.load(model_path)
    provenance = manifest.get('provenance')
    config = provenance.get('config', {}) if isinstance(provenance, dict) else {}
    physical = config.get('physical_preset', {})
    capture_every = preset.get('camera_every_steps')
    timestep = preset.get('layout', {}).get('timestep')
    expected_capture_hz = None
    if isinstance(capture_every, int) and not isinstance(capture_every, bool) and capture_every > 0 and timestep:
        expected_capture_hz = 1.0 / (float(timestep) * capture_every)
    mismatches = []
    if config.get('profile') != preset.get('profile') or model.meta.get('profile') != preset.get('profile'):
        mismatches.append('profile')
    if list(model.classes) != profile.names or model.meta.get('classes') != profile.names:
        mismatches.append('classes')
    if manifest.get('features') != FEATURES or model.meta.get('features') != FEATURES:
        mismatches.append('features')
    if model.meta.get('provenance') != provenance:
        mismatches.append('model provenance')
    if physical.get('layout') != preset.get('layout'):
        mismatches.append('physical layout')
    if physical.get('requested_rate') != preset.get('requested_rate') or config.get('rate') != preset.get('requested_rate'):
        mismatches.append('feed rate')
    if physical.get('camera_every_steps') != capture_every or config.get('capture_every') != capture_every:
        mismatches.append('camera cadence')
    if expected_capture_hz is None or physical.get('capture_hz') != expected_capture_hz:
        mismatches.append('camera frequency')
    if mismatches:
        raise ValueError(f"The selected model is physically incompatible: {', '.join(mismatches)}.")
    return preset


def validate_bundle_child(bundle_dir):
    """Run the one bundle validator in a fresh child. Any refusal raises CatalogError.

    The child binds the bundle's own catalog before its profiles loads, so this process
    never judges a bundle against its own cached profiles. No failure code carries a path.
    """
    try:
        result = subprocess.run(
            [sys.executable, str(HERE / 'validate_bundle.py'), '--bundle', str(bundle_dir)],
            capture_output=True, text=True, cwd=str(HERE), timeout=VALIDATE_BUNDLE_TIMEOUT_S)
        payload = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        payload = None
    if not isinstance(payload, dict):
        raise object_catalog.CatalogError('the bundle validator returned no result')
    if result.returncode != 0 or payload.get('ok') is not True:
        failures = payload.get('failures') or ['no failure code']
        raise object_catalog.CatalogError(
            'the bundle validator refused the bundle: ' + '; '.join(map(str, failures)))
    return payload


def default_reject_classes(catalog, preset):
    """The reject set an engine derives from the preset severities, in catalog order.

    Bundle zero states this set, so a seeded start equals a start without a bundle. It
    comes from catalog data, because profiles must not load before the catalog export.
    """
    severities = set(preset['policy']['reject_severities'])
    return [definition['classifier_label'] for definition in catalog['definitions']
            if definition['truth']['defect'] and definition['truth']['severity'] in severities]


def _bundle_sources():
    """What built bundle zero: the source revision and the hash of each source file.

    The revision is deployment-controlled only. The service never asks the host for one.
    """
    return {'source_revision': os.environ.get('CINTA_SOURCE_REVISION') or None,
            'files': {name: _file_hash(HERE / name) for name in BUNDLE_SOURCE_FILES}}


def activation_block(**fields):
    """One record block with exactly the frozen members. Unknown members stay null."""
    block = dict.fromkeys(ACTIVATION_MEMBERS)
    block['drain_timeout_seconds'] = DRAIN_TIMEOUT_S
    for name, value in fields.items():
        if name not in block:
            raise KeyError(f'the activation record has no member named {name}')
        block[name] = value
    return block


def _bundle_model_manifest(bundle):
    """The manifest of the model that recognized the type this bundle retires."""
    found = sorted(Path(bundle).glob('model/*.manifest.json'))
    return found[0] if found else None


def _pointer_bundle(active_root):
    """What the active pointer names RIGHT NOW. An unreadable pointer names nothing."""
    try:
        return object_catalog.read_active(Path(active_root))['active_bundle_sha256']
    except (object_catalog.CatalogError, OSError, ValueError):
        return None


def _activation_recorded(path, job_id, bundle_sha256):
    """True when this exact activation already has its history row."""
    try:
        lines = Path(path).read_text(encoding='utf-8').splitlines()
    except OSError:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get('job_id') == job_id and row.get('bundle_sha256') == bundle_sha256:
            return True
    return False


def _retired_definition(active_root, victim_id, active_sha256):
    """The victim definition, read from a published bundle that still carries it.

    Only a bundle that is NOT the active one can hold a retired type, so this never
    reads the type back out of the catalog that replaced it.
    """
    bundles = Path(active_root) / 'bundles'
    for bundle in sorted(bundles.iterdir()) if bundles.is_dir() else []:
        if bundle.name == active_sha256 or bundle.is_symlink() or not bundle.is_dir():
            continue
        path = bundle / 'catalog' / 'definitions' / f'{victim_id}.json'
        if path.is_file():
            try:
                return bundle.name, json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                return None, None
    return None, None


def _activation_message(error):
    """One short operator line. No host path ever reaches a job record."""
    return _without_host_paths(f'{type(error).__name__}: {error}'[:200])


def bind_active_bundle(preset_path, item_jobs_root):
    """Startup steps 2 and 3: resolve the one verified active bundle, then export its catalog.

    An empty root seeds bundle zero from the packaged catalog, the preset, and its model,
    after the validator child accepts that seed. profiles loads its catalog once, at its
    import, from the root exported here. It must not exist yet, and the caller loads the
    bundled preset only afterwards. The engine worker inherits the export through spawn.

    A seeded root already carries its own catalog, preset, and model. `--preset` is not
    read again there, so a missing or malformed file never breaks a plain restart.
    """
    if 'profiles' in sys.modules:
        raise RuntimeError('profiles loaded before the active bundle catalog was bound.')
    preset_path, item_jobs_root = Path(preset_path), Path(item_jobs_root)
    active_root = item_jobs_root / 'active'
    if os.path.lexists(object_catalog.active_pointer_path(active_root)):
        bundle = object_catalog.resolve_active_bundle(active_root)
    else:
        preset = json.loads(preset_path.read_text())
        model_path = resolve_model_path(preset, preset_path, HERE)
        packaged = object_catalog.load_catalog(object_catalog.PACKAGED_CATALOG_ROOT)
        bundle = object_catalog.ensure_active_bundle(
            active_root, catalog_root=object_catalog.PACKAGED_CATALOG_ROOT,
            model_path=model_path, model_manifest_path=model_path.with_suffix('.manifest.json'),
            preset=preset, policy={'reject_classes': default_reject_classes(packaged, preset)},
            sources=_bundle_sources(), validate=validate_bundle_child,
            history_root=item_jobs_root / 'history')
    os.environ[object_catalog.CATALOG_ROOT_ENV] = str(bundle / 'catalog')
    return bundle


class CommandLog:
    def __init__(self, path, rotating):
        self.stream = None
        self.logger = None
        self.handler = None
        if rotating:
            self.handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2)
            self.logger = logging.getLogger(f'coffee.commands.{uuid.uuid4()}')
            self.logger.propagate = False
            self.logger.setLevel(logging.INFO)
            self.logger.addHandler(self.handler)
        else:
            self.stream = path.open('a')

    def write(self, row):
        encoded = json.dumps(row)
        if self.logger is not None:
            self.logger.info(encoded)
        else:
            self.stream.write(encoded + '\n')
            self.stream.flush()

    def close(self):
        if self.handler is not None:
            self.logger.removeHandler(self.handler)
            self.handler.close()
        if self.stream is not None:
            self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback_value):
        self.close()


def _apply_activation_command(engine, command, activation, ack):
    """Handle one activation command inside the worker loop. Returns the activation state.

    The loop serializes these with set_reject_policy, so each one reads the LATEST policy.
    A drain only pauses NEW spawning: no live object is deleted or retyped. After a commit
    the worker holds the policy fence until it stops or a cancel arrives.
    """
    kind, job_id = command['type'], command['job_id']
    policy = engine.reject_policy()
    if kind == 'prepare_activation':
        if activation is not None and activation['job_id'] != job_id:
            ack.update(ok=False, error_code='activation_in_progress',
                       error='Another activation is in progress.')
        elif activation is None and command['expected_catalog_revision'] != \
                object_catalog.load_catalog()['catalog_revision']:
            ack.update(ok=False, error_code='activation_conflict',
                       error='The active catalog changed after this candidate was trained.')
        elif activation is None and command['victim_label'] in policy['reject_classes']:
            ack.update(ok=False, error_code='replacement_conflict', reject_policy=policy,
                       error='The replacement victim is rejected now.')
        else:
            if activation is None:
                activation = {'job_id': job_id, 'phase': 'draining',
                              'victim_label': command['victim_label'], 'rate': engine.sim.rate}
                engine.sim.rate = 0.0
            ack.update(ok=True, phase=activation['phase'],
                       active_objects=engine.sim.n_active())
        return activation
    if activation is None or activation['job_id'] != job_id:
        if kind == 'cancel_activation':
            ack.update(ok=True, cancelled=False)
        else:
            ack.update(ok=False, error_code='activation_not_prepared',
                       error='No activation is prepared for this job.')
        return activation
    if kind == 'cancel_activation':
        engine.sim.rate = activation['rate']
        ack.update(ok=True, cancelled=True)
        return None
    if activation['phase'] == 'activating':
        # An exact retry returns the same capture. The fence is already held.
        ack.update(ok=True, **activation['captured'])
    elif engine.sim.n_active() != 0:
        ack.update(ok=False, error_code='activation_not_drained',
                   error='Objects are still on the belt.', active_objects=engine.sim.n_active())
    elif activation['victim_label'] in policy['reject_classes']:
        engine.sim.rate = activation['rate']
        ack.update(ok=False, error_code='replacement_conflict', reject_policy=policy,
                   error='The replacement victim is rejected now.')
        return None
    else:
        captured = {'reject_classes': list(policy['reject_classes']),
                    'policy_version': policy['policy_version'],
                    'score_epoch_id': policy['score_epoch_id']}
        activation.update(phase='activating', captured=captured)
        ack.update(ok=True, **captured)
    return activation


def worker(preset, states, acknowledgments, commands, stop, out):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    # A hard parent exit leaves this process reparented to PID 1. Python gives a spawned
    # child a parent sentinel, so one daemon watcher sets the SAME stop event that a
    # graceful shutdown sets. A direct unit call gets None here and skips the watcher.
    parent = mp.parent_process()
    abandoned = threading.Event()
    if parent is not None:
        def watch_parent():
            parent.join()
            abandoned.set()
            stop.set()
            # Fail-safe only. Cancelling the abandoned feeders below normally ends this
            # process at once. Nothing reads its results after the parent is gone.
            time.sleep(ORPHAN_EXIT_SECONDS)
            os._exit(ORPHAN_EXIT_CODE)

        threading.Thread(target=watch_parent, name='parent-sentinel', daemon=True).start()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS'):
        os.environ[name] = '1'
    engine = None
    command_results = {}
    continuous = False
    rolling_scores = None
    last_score_publish = 0.0
    sim_limit, wall_limit = 0.0, 0.0
    # One activation at a time: None, or the draining or fenced state of one job.
    activation = None
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)

    def publish(status, error=None):
        nonlocal rolling_scores, last_score_publish
        state = engine.snapshot() if engine else {}
        if activation is not None:
            state['activation'] = {'job_id': activation['job_id'], 'phase': activation['phase'],
                                   'active_objects': engine.sim.n_active()}
        now = time.monotonic()
        if continuous and (rolling_scores is None or now - last_score_publish >= 1.0):
            rolling_scores = engine.rolling_scores()
            last_score_publish = now
        if continuous:
            state['rolling_scores'] = rolling_scores
        state.update(status=status, error=error, limits={
            'sim_seconds': sim_limit, 'wall_seconds': wall_limit,
            'clients': MAX_CLIENTS,
            'commands': MAX_COMMANDS_PER_EPOCH if continuous else MAX_COMMANDS,
        })
        if engine:
            state.update(source_revision=engine.source_revision, source_hashes=engine.source_hashes)
        if status in ('completed', 'failed'):
            state['command_results'] = (list(command_results) if continuous
                                        else list(command_results.values()))
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
        release_revision = _configured_source_revision()
        if release_revision is not None:
            engine.source_revision = release_revision
        continuous = engine.continuous
        if continuous:
            command_results = deque(maxlen=MAX_COMMANDS_PER_EPOCH * 2)
            sim_limit = wall_limit = None
        else:
            sim_limit = float(engine.preset['limits']['max_sim_seconds'])
            wall_limit = float(engine.preset['limits']['max_wall_seconds'])
            # A bounded session must be longer than one settling time, or no injected
            # object could ever resolve inside it.
            if not (SETTLING_SECONDS < sim_limit <= 10 and 0 < wall_limit <= 300):
                raise ValueError(f'Session limits must allow {SETTLING_SECONDS:g} to 10 '
                                 'simulation seconds and at most 300 wall seconds.')
        running = continuous
        started = time.monotonic() if continuous else None
        publish('running' if continuous else 'ready')
        last_publish = time.monotonic()
        reason = 'stopped'
        with CommandLog(out / 'commands.jsonl', rotating=continuous) as log:
            while not stop.is_set():
                try:
                    command = commands.get(timeout=0.05) if not running else commands.get_nowait()
                except Empty:
                    command = None
                if command:
                    command_type = command['type']
                    ack = dict(type='ack', command_id=command['command_id'],
                               command_type=command_type, session_id=engine.session_id)
                    if continuous:
                        ack['command_epoch'] = command['command_epoch']
                    try:
                        if command['session_id'] != engine.session_id:
                            raise ValueError('The session changed. Reload the page.')
                        if command_type == 'inject':
                            # One settling time is what an injected object needs to reach
                            # its outcome. A later injection could never resolve.
                            if not continuous and running and \
                                    engine.sim.data.time >= sim_limit - SETTLING_SECONDS:
                                raise ValueError('The session is ending. Restart the session before another injection.')
                            object_id = engine.inject(command['class_name'])
                            ack.update(ok=True, object_id=object_id,
                                       spawn_position=engine.injection_position(object_id),
                                       **engine.injection_expectation(object_id),
                                       sim_time_s=float(engine.sim.data.time))
                            if not running:
                                running, started = True, time.monotonic()
                        elif command_type in ACTIVATION_COMMANDS:
                            activation = _apply_activation_command(engine, command, activation, ack)
                        elif command_type == 'set_reject_policy':
                            if activation is not None and activation['phase'] == 'activating':
                                # The fence: the captured policy is already in the new bundle.
                                ack.update(ok=False, error_code='activation_in_progress',
                                           error='An activation is in progress. Retry shortly.',
                                           reject_policy=engine.reject_policy())
                            elif command['expected_policy_version'] != engine.policy_version:
                                ack.update(ok=False, error='The reject policy changed. Use the latest state.',
                                           error_code='policy_version_conflict',
                                           reject_policy=engine.reject_policy())
                            else:
                                result = engine.set_reject_classes(command['reject_classes'])
                                ack.update(ok=True, **result)
                                if result['changed']:
                                    rolling_scores = None
                        else:
                            raise ValueError('Unsupported command type.')
                    except ValueError as exc:
                        ack.update(ok=False, error=str(exc))
                    if continuous:
                        command_results.append(ack)
                    else:
                        command_results[ack['command_id']] = ack
                    acknowledgments.put(ack, timeout=1)
                    log.write({**command, **ack})
                    publish('running' if running else 'ready')
                if not running:
                    continue
                engine.step()
                now = time.monotonic()
                if now - last_publish >= 0.1:
                    publish('running')
                    last_publish = now
                if not continuous and (engine.sim.data.time >= sim_limit or now - started >= wall_limit):
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
        if abandoned.is_set():
            # Parent-death path only. Nobody drains these queues now, and with spawn this
            # process also holds a read end, so a full pipe never reports EPIPE. The
            # blocked feeder thread would otherwise keep the process alive after stop.
            for queue in (acknowledgments, states, commands):
                with contextlib.suppress(Exception):
                    queue.cancel_join_thread()
        # The HTTP process drains the final packet before joining this worker.
        acknowledgments.close()
        states.close()


class LiveService:
    def __init__(self, preset, out, *, item_jobs_root=None, item_jobs_provider='cached',
                 catalog_root=None, provider_cache=None, provider_env=None,
                 generator_root=None, runtime_lock=None, physics_replay=None,
                 active_bundle=None, record=None, quality_gate='strict'):
        self.ctx = mp.get_context('spawn')
        # The one reporting seam. The job runner injects it, so the activator never
        # touches the queue store. Without it the activation still runs and reports nothing.
        self.record = record if record is not None else (lambda job_id, **fields: None)
        self.loop = None
        # One activation at a time. The loop sets this before its first await.
        self.activating = None
        self.preset = Path(preset)
        self.preset_config = load_preset(self.preset)
        self.continuous = self.preset_config.get('mode') == 'continuous'
        # load_preset compares the model label order with the active catalog, and only for
        # a continuous preset. None means that no such check ran.
        self.catalog_model_compatible = True if self.continuous else None
        self.out = Path(out)
        self.session_out = self.out
        self.restart_count = 0
        self.restarting = False
        self.recovery_session_id = str(uuid.uuid4())
        self.state = {'status': 'starting'}
        self.clients = set()
        self.client_sends = {}
        self.requests = {}
        self.command_epoch_seconds = COMMAND_EPOCH_SECONDS
        self.command_epoch = str(uuid.uuid4()) if self.continuous else None
        self.previous_command_epoch = None
        self.command_epoch_started = time.monotonic()
        self.epoch_admissions = ({self.command_epoch: 0} if self.continuous else {})
        self.heartbeat_seq = 0
        self.last_state_broadcast = time.monotonic()
        self.task = None
        self.state_ready = asyncio.Event()
        self.service_timings = {'snapshot_reads': 0, 'broadcasts': 0, 'json_encode_ms': 0.0, 'send_wait_ms': 0.0}
        self.service_samples = {key: deque(maxlen=4096) for key in ('json_encode_ms', 'send_wait_ms')}
        self.profile_written = False
        # The item job queue owns its own store, runner thread, and child processes.
        self.item_jobs_root = Path(item_jobs_root) if item_jobs_root else self.out / 'item-jobs'
        self.item_jobs_provider = item_jobs_provider
        # The accepted demonstration switch. It is always published, so nobody can read a
        # demo run as a strict one.
        self.item_jobs_quality_gate = quality_gate
        self.provider_cache = Path(provider_cache) if provider_cache else DEFAULT_PROVIDER_CACHE
        self.provider_env = Path(provider_env) if provider_env else None
        self.generator_root = Path(generator_root) if generator_root else item_jobs.GENERATOR_ROOT
        self.runtime_lock = Path(runtime_lock) if runtime_lock else DEFAULT_RUNTIME_LOCK
        # Reviewed replay metadata. One explicit operator input, read-only, never a route.
        self.physics_replay = Path(physics_replay) if physics_replay else None
        self.catalog_root = (Path(catalog_root) if catalog_root
                             else self.item_jobs_root / 'object_catalog')
        # With an active bundle the persistent root holds two units. `active/` is restored
        # as one: a rollback repoints it. `history/` is append-only and is never restored.
        self.active_bundle = Path(active_bundle) if active_bundle else None
        self.history_root = self.item_jobs_root / 'history' if self.active_bundle else None
        self.item_jobs = None
        self.item_runner = None
        self.item_jobs_state = None
        self.item_jobs_revision = None
        self.item_jobs_unhealthy = False
        self.resetting = False
        self.catalog_revision = None
        self.active_type_ids = []
        self._create_worker(self.session_out)

    def _open_item_jobs(self):
        """Own one job store and one runner thread. Generation and rendering stay in children."""
        # Without an active bundle a fresh writable root reads the packaged catalog for
        # display and admission only, and nothing writes there.
        if self.active_bundle is None and not (self.catalog_root / 'active/catalog.json').is_file():
            print(f'Item jobs: runtime catalog root holds no active manifest, reading the '
                  f'packaged catalog read-only: {object_catalog.PACKAGED_CATALOG_ROOT}',
                  flush=True)
            self.catalog_root = object_catalog.PACKAGED_CATALOG_ROOT
        if not (self.provider_cache / 'cache').is_dir():
            print(f'Item jobs: no provider cache at {self.provider_cache}/cache. Every new job '
                  f'stops at operator_required with provider_cache_miss.', flush=True)
        catalog = self._active_catalog()
        self.catalog_revision = catalog['catalog_revision']
        self.active_type_ids = list(catalog['active_type_ids'])
        store_root = self.history_root or self.item_jobs_root
        store_root.mkdir(parents=True, exist_ok=True)
        # The heavy worker takes this lock. The service only guarantees a writable parent.
        self.runtime_lock.parent.mkdir(parents=True, exist_ok=True)
        self.item_jobs = item_jobs.ItemJobStore(store_root, provider_mode=self.item_jobs_provider)
        # Only a service that owns a verified bundle can activate. Without one the queue
        # keeps a validated candidate waiting, exactly as before.
        activation = self.active_bundle is not None
        self.item_runner = item_jobs.ItemJobRunner(
            self.item_jobs, self._item_commands(), runtime_lock_path=self.runtime_lock,
            catalog_provider=self._active_catalog, policy_provider=self._item_jobs_policy,
            activator=self.activate if activation else None,
            active_bundle_sha256=self._active_bundle_sha256 if activation else None,
            quality_gate=self.item_jobs_quality_gate)
        if activation:
            # The runner needs the service's activator and the activator needs the
            # runner's reporting path. That one cycle is closed here, after both exist.
            self.record = self.item_runner.record_activation
        self.item_runner.start()
        self.item_jobs_state = self._item_jobs_packet()
        self.item_jobs_revision = self.item_jobs.revision

    def _active_bundle_sha256(self):
        """The sha the pointer names. The queue reads it for an exact activation retry."""
        return _pointer_bundle(self.item_jobs_root / 'active')

    def _refresh_active_identity(self):
        """Republish the identity of the bundle the pointer names right now.

        The parent never rebinds its loaded profiles, so every value here comes from the
        verified bundle files. A stale revision would refuse every new job, and a stale
        class list would describe a catalog that no engine runs.
        """
        catalog = self._active_catalog()
        self.catalog_revision = catalog['catalog_revision']
        self.active_type_ids = list(catalog['active_type_ids'])
        if self.active_bundle is not None:
            # A child verified this bundle, and that check includes the model label order.
            self.catalog_model_compatible = True

    def _active_catalog(self):
        """The active catalog. With a bundle, always through the pointer and its verified bundle."""
        if self.active_bundle is not None:
            return object_catalog.read_active(self.item_jobs_root / 'active')
        return object_catalog.load_catalog(self.catalog_root)

    def _item_jobs_policy(self):
        """The policy the live engine applies right now, read from the latest state packet.

        A training baseline must bind the policy in force when its turn begins, not the
        one that held at admission. None means the engine has published no authoritative
        policy yet, and the queue then WAITS. An absent policy must never read as an empty
        reject set, because that would silently bind Keep all.
        """
        state = self.state or {}
        if state.get('status') in (None, 'starting'):
            return None
        policy = state.get('reject_policy')
        if not isinstance(policy, dict) or policy.get('policy_version') is None:
            return None
        return {'reject_classes': list(policy.get('reject_classes') or []),
                'policy_version': policy['policy_version']}

    def _item_commands(self):
        """Fake mode runs fixtures. Cached and paid modes never pass an automatic --live."""
        if self.item_jobs_provider == 'fake':
            return item_jobs.fake_commands()
        # The service passes the credential FILE PATH to the generation child only. It never
        # opens that file and never puts a provider value into its own environment.
        return item_jobs.real_commands(
            mode=self.item_jobs_provider, provider_cache=self.provider_cache,
            runtime_lock=self.runtime_lock, generator_root=self.generator_root,
            preset=self.preset, env_file=self.provider_env,
            physics_replay=self.physics_replay,
            source_revision=os.environ.get('CINTA_SOURCE_REVISION'))

    def _close_item_jobs(self):
        if self.item_runner is not None:
            if not self.item_runner.stop():
                # An unconfirmed runner thread may still write. Keep the store and the
                # writer lock, and never report a clean stop.
                self.item_jobs_unhealthy = True
                print('Item jobs: the runner thread did not confirm its exit. The job store '
                      'and its writer lock stay open.', flush=True)
                return
            self.item_runner = None
        if self.item_jobs is not None:
            self.item_jobs.close()
            self.item_jobs = None

    def _item_jobs_packet(self):
        # A summary carries free text from a child, so it takes the same redaction.
        return {'summaries': [_public_record(summary) for summary in self.item_jobs.summaries()],
                'limits': {'max_queued_jobs': item_jobs.MAX_QUEUED_JOBS,
                           'max_retained_open_jobs': item_jobs.MAX_RETAINED_OPEN_JOBS,
                           'max_retained_jobs': item_jobs.MAX_RETAINED_JOBS,
                           'max_summaries': item_jobs.MAX_SUMMARIES,
                           'max_attempts': item_jobs.MAX_ATTEMPTS},
                'provider_mode': self.item_jobs.provider_mode,
                'item_jobs_quality_gate': self.item_jobs_quality_gate,
                'catalog_revision': self.catalog_revision,
                'active_type_ids': list(self.active_type_ids)}

    def _read_preview(self, request_id, name):
        """Read one verified preview on a worker thread. The HTTP loop never touches disk."""
        job = self.item_jobs.get(request_id)
        return item_jobs.read_preview(job, self.item_jobs.job_dir(job['request_id']), name)

    @staticmethod
    def _require_origin(request):
        """Every item job POST needs a present Origin. The middleware validates its value."""
        if request.headers.get('Origin'):
            return None
        return web.json_response({'ok': False, 'error_code': 'origin_required'}, status=403)

    async def _item_body(self, request):
        """Read one bounded JSON object from an item job POST."""
        refused = self._require_origin(request)
        if refused is not None:
            return refused
        try:
            body = json.loads(await request.text())
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            return _item_job_response('invalid_request')
        return body if isinstance(body, dict) else _item_job_response('invalid_request')

    async def _item_action(self, request, action, *arguments):
        refused = self._require_origin(request)
        if refused is not None:
            return refused
        if self.resetting:
            return web.json_response({'ok': False, 'error_code': 'reset_in_progress'}, status=409)
        try:
            job = await asyncio.to_thread(action, request.match_info['request_id'], *arguments)
        except item_jobs.ItemJobError as error:
            return _item_job_response(error.code)
        return web.json_response({'ok': True, 'job': self.item_jobs.summary(job)})

    async def submit_item_job(self, request):
        body = await self._item_body(request)
        if isinstance(body, web.Response):
            return body
        if self.resetting:
            return web.json_response({'ok': False, 'error_code': 'reset_in_progress'}, status=409)
        try:
            job, created = await asyncio.to_thread(
                self.item_jobs.submit, body, self.catalog_revision)
        except item_jobs.ItemJobError as error:
            return _item_job_response(error.code)
        return web.json_response({'ok': True, 'created': created,
                                  'job': self.item_jobs.summary(job)},
                                 status=201 if created else 200)

    async def list_item_jobs(self, request):
        return web.json_response({'ok': True, **await asyncio.to_thread(self._item_jobs_packet)})

    async def get_item_job(self, request):
        try:
            job = await asyncio.to_thread(self.item_jobs.get, request.match_info['request_id'])
        except item_jobs.ItemJobError as error:
            return _item_job_response(error.code)
        return web.json_response({'ok': True, 'job': _public_job(job)})

    async def resolve_item_provider(self, request):
        body = await self._item_body(request)
        if isinstance(body, web.Response):
            return body
        return await self._item_action(request, self.item_runner.resolve_provider,
                                       body.get('action'))

    async def resolve_item_replacement(self, request):
        # Phase 4 owns replacement. The conflict stays visible and unchanged until then.
        return await self._item_action(request, self.item_runner.resolve_replacement)

    async def confirm_item_cleanup(self, request):
        return await self._item_action(request, self.item_runner.confirm_cleanup)

    async def item_job_preview(self, request):
        try:
            data, content_type = await asyncio.to_thread(
                self._read_preview, request.match_info['request_id'], request.match_info['name'])
        except item_jobs.ItemJobError as error:
            # No message repeats a host path.
            return _item_job_response(error.code)
        return web.Response(body=data, content_type=content_type)

    async def reset_defaults(self, request):
        """Return to the verified seed bundle after every queue and worker owner stops."""
        body = await self._item_body(request)
        if isinstance(body, web.Response):
            return body
        if body:
            return _item_job_response('invalid_request')
        if self.resetting:
            return web.json_response({'ok': False, 'error_code': 'reset_in_progress'}, status=409)
        if self.activating is not None:
            return web.json_response({'ok': False, 'error_code': 'activation_conflict'}, status=409)

        self.resetting = True
        previous_session_id = self.state.get('session_id')
        stopped = False
        try:
            await asyncio.to_thread(self._close_item_jobs)
            if self.item_jobs is not None or self.item_runner is not None:
                raise RuntimeError('The item queue did not release its writer lock.')
            self.item_jobs_state = None
            await self._cancel_pump()
            await self._stop_worker(resolve_acks=True)
            await self._resolve_pending('The session reset before this injection completed.')
            self._write_service_profile()
            self._close_queues()
            stopped = True

            reset_state = _reset_state_function()
            result = await asyncio.to_thread(reset_state, self.item_jobs_root, apply=True)
            bundle = object_catalog.resolve_active_bundle(self.item_jobs_root / 'active')
            self.preset, self.active_bundle = bundle / object_catalog.BUNDLE_PRESET, bundle
            os.environ[object_catalog.CATALOG_ROOT_ENV] = str(bundle / 'catalog')
            self._refresh_active_identity()
            self.requests = {}
            self.restart_count += 1
            self.session_out = self.out.parent / \
                f'{self.out.name}-reset-{self.restart_count:03d}-{uuid.uuid4().hex[:8]}'
            self._reset_profile()
            self.state = {'status': 'starting', 'previous_session_id': previous_session_id}
            self.state_ready = asyncio.Event()
            self._create_worker(self.session_out)
            await asyncio.to_thread(self._open_item_jobs)
            self.process.start()
            self.task = asyncio.create_task(self.pump())
            await asyncio.wait_for(self.state_ready.wait(), timeout=30)
            if self.state.get('status') == 'failed':
                raise RuntimeError(self.state.get('error') or 'The seed worker failed to start.')
            return web.json_response({'ok': True, 'result': result})
        except Exception:
            if stopped and self.process is None:
                with contextlib.suppress(Exception):
                    bundle = object_catalog.resolve_active_bundle(self.item_jobs_root / 'active')
                    self.preset, self.active_bundle = bundle / object_catalog.BUNDLE_PRESET, bundle
                    os.environ[object_catalog.CATALOG_ROOT_ENV] = str(bundle / 'catalog')
                    self._refresh_active_identity()
                    self._create_worker(self.session_out)
                    await asyncio.to_thread(self._open_item_jobs)
                    self.process.start()
                    self.task = asyncio.create_task(self.pump())
            return web.json_response({'ok': False, 'error_code': 'reset_failed'}, status=500)
        finally:
            self.resetting = False

    def _read_catalog_asset(self, revision, digest):
        """The GLB the ACTIVE bundle lists for this pair, or None. The caller names no path."""
        # The active revision is a lowercase sha256, so equality also proves the format.
        if self.active_bundle is None or revision != self.catalog_revision \
                or not re.fullmatch(r'[0-9a-f]{64}', digest):
            return None
        try:
            rows = [row for row in object_catalog.read_visual_registry(self.active_bundle)
                    if row.get('glb_sha256') == digest]
            if len(rows) != 1:
                return None
            data = object_catalog.read_confined(
                self.active_bundle, (object_catalog.VISUAL_ASSET_DIR, f'{digest}.glb'),
                object_catalog.MAX_GLB_BYTES)
        except object_catalog.CatalogError:
            return None
        return data if hashlib.sha256(data).hexdigest() == digest else None

    async def catalog_asset(self, request):
        digest = request.match_info['glb_sha256']
        data = await asyncio.to_thread(
            self._read_catalog_asset, request.match_info['catalog_revision'], digest)
        if data is None:
            return web.json_response({'ok': False, 'error_code': 'asset_unavailable'}, status=404)
        # The URL changes with the catalog or the bytes, so the body never changes.
        return web.Response(body=data, content_type=object_catalog.GLB_MEDIA_TYPE, headers={
            'ETag': f'"{digest}"', 'Cache-Control': 'public, max-age=31536000, immutable'})

    def _read_wall_preview(self, entry_id, name):
        """Read the PNG declared by one immutable Needs review entry."""
        if not re.fullmatch(r'needs-review\.[a-z0-9][a-z0-9._-]{0,79}', entry_id) \
                or name not in object_catalog.NEEDS_REVIEW_PREVIEWS:
            return None
        history = Path(getattr(self, 'history_root', None) or self.catalog_root)
        wall = history / 'wall-of-fame'
        try:
            record = json.loads(object_catalog.read_confined(
                wall, (entry_id, 'entry.json'), 16 << 10))
            expected_url = f'/wall-of-fame/{entry_id}/{name}'
            if not isinstance(record, dict) \
                    or record.get('entry_kind') != object_catalog.NEEDS_REVIEW_KIND \
                    or record.get('entry_id') != entry_id \
                    or record.get('preview_url') != expected_url:
                return None
            return object_catalog.read_confined(
                wall, (entry_id, name), item_jobs.MAX_PREVIEW_BYTES)
        except (object_catalog.CatalogError, UnicodeDecodeError, ValueError):
            return None

    async def wall_of_fame_preview(self, request):
        data = await asyncio.to_thread(
            self._read_wall_preview, request.match_info['entry_id'], request.match_info['name'])
        if data is None:
            return _item_job_response('preview_unavailable')
        return web.Response(body=data, content_type='image/png')

    async def wall_of_fame(self, request):
        try:
            offset = int(request.query.get('offset', 0))
            limit = int(request.query.get('limit', object_catalog.MAX_WALL_PAGE))
        except (TypeError, ValueError):
            return _item_job_response('invalid_request')
        try:
            # Archived types are history. Without an active bundle the wall stays where it was.
            page = await asyncio.to_thread(object_catalog.wall_of_fame_page,
                                           getattr(self, 'history_root', None) or self.catalog_root,
                                           offset, limit)
        except object_catalog.CatalogError:
            return _item_job_response('invalid_request')
        return web.json_response({'ok': True, **page})

    def _advance_command_epoch(self, now=None):
        if not self.continuous:
            return False
        now = time.monotonic() if now is None else now
        changed = False
        while now - self.command_epoch_started >= self.command_epoch_seconds:
            self.previous_command_epoch = self.command_epoch
            self.command_epoch = str(uuid.uuid4())
            self.command_epoch_started += self.command_epoch_seconds
            self.epoch_admissions = {
                self.previous_command_epoch: self.epoch_admissions.get(self.previous_command_epoch, 0),
                self.command_epoch: 0,
            }
            changed = True
        if changed:
            self._prune_completed_requests()
        return changed

    def _prune_completed_requests(self):
        if not self.continuous:
            return
        retained_epochs = {self.command_epoch, self.previous_command_epoch}
        expired = [command_id for command_id, entry in self.requests.items()
                   if entry.get('ack') and entry['payload'].get('command_epoch') not in retained_epochs]
        for command_id in expired:
            del self.requests[command_id]

    def _state_packet(self, state=None):
        packet = {'type': 'state', **(self.state if state is None else state),
                  'restart_supported': not self.continuous,
                  'heartbeat_seq': self.heartbeat_seq}
        if self.continuous:
            packet.update(command_epoch=self.command_epoch,
                          command_epoch_seconds=self.command_epoch_seconds)
        item_jobs_state = getattr(self, 'item_jobs_state', None)
        if item_jobs_state is not None:
            packet['item_jobs'] = item_jobs_state
        return packet

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
        # The activator runs on a job runner thread and hands every step to this loop.
        self.loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._open_item_jobs)
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
                        try:
                            # Stopping the runner thread terminates its owned process groups.
                            await asyncio.to_thread(self._close_item_jobs)
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
            self.heartbeat_seq += 1
            packet = self._state_packet(packet)
            packet['heartbeat_seq'] = self.heartbeat_seq
            self.last_state_broadcast = time.monotonic()
        started = time.perf_counter()
        encoded = json.dumps(packet, allow_nan=False)
        elapsed = (time.perf_counter() - started) * 1000
        self.service_timings['json_encode_ms'] += elapsed
        self.service_samples['json_encode_ms'].append(elapsed)
        self.service_timings['broadcasts'] += 1
        started = time.perf_counter()
        sends = {}
        for client in tuple(self.clients):
            previous = self.client_sends.get(client)
            if previous is not None and not previous.done():
                self.clients.discard(client)
                self.client_sends.pop(client, None)
                with contextlib.suppress(Exception):
                    client.force_close()
                previous.cancel()
                continue
            task = asyncio.create_task(client.send_str(encoded))
            self.client_sends[client] = task
            sends[task] = client

            def finished(completed, *, target=client):
                if self.client_sends.get(target) is completed:
                    self.client_sends.pop(target, None)
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    completed.result()

            task.add_done_callback(finished)
        if sends:
            try:
                done, pending = await asyncio.wait(sends, timeout=0.5)
                for task in done:
                    with contextlib.suppress(asyncio.CancelledError):
                        if task.exception() is not None:
                            client = sends[task]
                            self.clients.discard(client)
                            with contextlib.suppress(Exception):
                                client.force_close()
                for task in pending:
                    client = sends[task]
                    self.clients.discard(client)
                    self.client_sends.pop(client, None)
                    with contextlib.suppress(Exception):
                        client.force_close()
                    task.cancel()
            except asyncio.CancelledError:
                for task, client in sends.items():
                    self.clients.discard(client)
                    if self.client_sends.get(client) is task:
                        self.client_sends.pop(client, None)
                    with contextlib.suppress(Exception):
                        client.force_close()
                    task.cancel()
                await asyncio.gather(*sends, return_exceptions=True)
                raise
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
                self._prune_completed_requests()
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
            if self.item_jobs is not None and self.item_jobs.revision != self.item_jobs_revision:
                # A cheap counter comparison keeps the queue in this loop without a second one.
                self.item_jobs_revision = self.item_jobs.revision
                self.item_jobs_state = await asyncio.to_thread(self._item_jobs_packet)
                changed = True
            now = time.monotonic()
            epoch_changed = self._advance_command_epoch(now)
            heartbeat_due = self.continuous and now - self.last_state_broadcast >= 1.0
            if changed or epoch_changed or heartbeat_due:
                await self.broadcast(self._state_packet())
                if self.state['status'] in ('ready', 'running', 'failed'):
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

    async def _swap_worker(self, previous_session_id):
        """Stop the engine worker and start one new session. One engine exists at a time.

        The old worker is confirmed stopped before the new one is created, and the new one
        inherits the environment of this moment, so an exported catalog root reaches it
        before any profile import in that child.
        """
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
        self.state = {'status': 'starting', 'previous_session_id': previous_session_id}
        self.state_ready = asyncio.Event()
        self._create_worker(self.session_out)
        await self.broadcast({'type': 'state', **self.state})
        self.process.start()
        self.task = asyncio.create_task(self.pump())
        await asyncio.wait_for(self.state_ready.wait(), timeout=30)

    # Activation ---------------------------------------------------------

    def activate(self, job_id, candidate):
        """Thread entry for the job runner. Every queue and worker step runs on the loop.

        `candidate` names the trained artifacts: `catalog_root`, `model`,
        `model_manifest`, `preset`, `victim_id`, `expected_catalog_revision`, optional
        `evidence` (archive file name to path), and `bundle_sha256` on an exact retry.
        Returns `active`, `replacement_conflict`, `activation_conflict`, or
        `activation_failed`. It never raises a queue decision as an exception.
        """
        if self.loop is None:
            raise RuntimeError('the service loop is not running')
        return asyncio.run_coroutine_threadsafe(
            self._activate(job_id, candidate), self.loop).result()

    async def _activation_command(self, kind, job_id, deadline=None, **fields):
        """Send one internal command and wait for the worker acknowledgment.

        The envelope is the SHARED one, session id included, because the worker loop
        checks that field for every command. The entry is marked sent, so an internal
        acknowledgment never reaches a browser. `deadline` is the one shared budget, so
        no single wait can push an activation past its declared drain bound.
        """
        command_id = str(uuid.uuid4())
        payload = {'type': kind, 'command_id': command_id, 'job_id': job_id,
                   'session_id': self.state.get('session_id'), **fields}
        if self.continuous:
            payload['command_epoch'] = self.command_epoch
        self.requests[command_id] = {'payload': payload, 'ack_sent': True}
        self.commands.put_nowait(payload)
        limit = time.monotonic() + ACTIVATION_ACK_TIMEOUT_S if deadline is None else deadline
        while True:
            entry = self.requests.get(command_id)
            if entry is not None and entry.get('ack'):
                return entry['ack']
            if time.monotonic() >= limit:
                raise TimeoutError(f'the engine did not acknowledge {kind}')
            await asyncio.sleep(ACTIVATION_POLL_S)

    async def _abandon_activation(self, job_id, report, message, **fields):
        """Give the engine its rate and its policy back, then report one failure.

        This is the ONE way a prepared activation ends before the worker is replaced. A
        cancel that is not acknowledged leaves the worker state unknown, so the existing
        fatal path owns it and no report ever claims a recovered session.
        """
        confirmed = False
        try:
            ack = await self._activation_command(
                'cancel_activation', job_id,
                deadline=time.monotonic() + ACTIVATION_CANCEL_TIMEOUT_S)
            confirmed = bool(ack.get('ok'))
        except (TimeoutError, Full, OSError, ValueError) as error:
            message = f'{message} The cancel failed: {_activation_message(error)}'
        if not confirmed:
            message = f'{message} The engine state is unconfirmed.'
            await self._cleanup_restart_failure(
                'An activation could not be cancelled, so the engine state is unknown.')
        with contextlib.suppress(Exception):
            report(phase='failed', result='activation_failed', message=message, **fields)
        return 'activation_failed'

    def _candidate_bundle_files(self, candidate, reject_classes):
        """The candidate bundle and the visual assets that it serves.

        `candidate['assets']` names the GLB and the draft evidence of the new type, in the
        `seed_bundle_files` form. A surviving generated type keeps the row and the GLB of
        the active bundle. A type with neither gets no row, and the UI shows its proxy.
        """
        carried = (object_catalog.read_visual_registry(self.active_bundle)
                   if self.active_bundle else [])
        assets = {row['object_type_id']: {'glb': self.active_bundle / row['path'],
                                          'evidence': row} for row in carried}
        assets.update(candidate.get('assets') or {})
        return object_catalog.seed_bundle_files(
            Path(candidate['catalog_root']), Path(candidate['model']),
            Path(candidate['model_manifest']),
            json.loads(Path(candidate['preset']).read_text()),
            {'reject_classes': list(reject_classes)}, _bundle_sources(), assets=assets)

    async def _activate(self, job_id, candidate):
        """One activation at a time. A second call never starts a second drain."""
        if self.activating is not None or self.resetting:
            self.record(job_id, activation=activation_block(
                phase='failed', result='activation_conflict',
                message=('A reset is in progress.' if self.resetting
                         else 'Another activation is in progress.')))
            return 'activation_conflict'
        self.activating = job_id
        try:
            return await self._run_activation(job_id, candidate)
        finally:
            self.activating = None

    async def _run_activation(self, job_id, candidate):
        """Spec section 2 in order. Nothing is rewritten after the verification."""
        active_root = self.item_jobs_root / 'active'
        catalog = object_catalog.read_active(active_root)
        previous_sha256 = catalog['active_bundle_sha256']

        def report(**fields):
            self.record(job_id, activation=activation_block(
                previous_bundle_sha256=previous_sha256, **fields))

        def epoch():
            return (self.state.get('reject_policy') or {}).get('score_epoch_id')

        # An exact retry: the pointer already carries this bundle. No second restart and
        # no second score epoch. The commit step is COMPLETED instead, because a first
        # attempt that died after the pointer write would otherwise lose its audit trail
        # forever. Every repair step is idempotent.
        if candidate.get('bundle_sha256') == previous_sha256 and previous_sha256 is not None:
            audit = self._repair_commit(job_id, candidate, active_root, previous_sha256)
            self._refresh_active_identity()
            report(phase='active', result='active', active_objects=0, rolled_back=False,
                   bundle_sha256=previous_sha256, session_id=self.state.get('session_id'),
                   score_epoch_id=epoch(), message=audit)
            return 'active'

        victim = next((item for item in catalog['definitions']
                       if item['object_type_id'] == candidate['victim_id']), None)
        if victim is None:
            report(phase='failed', result='replacement_conflict',
                   message='The replacement victim is not active.')
            return 'replacement_conflict'
        victim_label = victim['classifier_label']

        # Step 0: a fresh child judges the candidate before anything pauses. The parent
        # never judges a bundle against its own cached profiles.
        provisional = [name for name in ((self.state.get('reject_policy') or {})
                                         .get('reject_classes') or []) if name != victim_label]
        evidence = {name: Path(path) for name, path in (candidate.get('evidence') or {}).items()}
        try:
            # Every archive input is read here, before anything pauses or stops, so a
            # missing evidence file can never strand a committed activation.
            for path in evidence.values():
                if not path.is_file():
                    raise OSError(f'the archive evidence file is missing: {path.name}')
            files = self._candidate_bundle_files(candidate, provisional)
            with tempfile.TemporaryDirectory() as scratch:
                draft = object_catalog.publish_bundle(scratch, files)
                await asyncio.to_thread(validate_bundle_child, Path(scratch) / draft)
        except (object_catalog.CatalogError, OSError, ValueError, KeyError) as error:
            report(phase='failed', result='activation_failed', message=_activation_message(error))
            return 'activation_failed'

        # One budget bounds the prepare, the whole drain, and every acknowledgment, so an
        # activation can never run past the drain bound it declares.
        deadline = time.monotonic() + DRAIN_TIMEOUT_S
        # Step 1: prepare. A conflict pauses nothing, so it never needs a cancel.
        try:
            ack = await self._activation_command(
                'prepare_activation', job_id, deadline=deadline, victim_label=victim_label,
                expected_catalog_revision=candidate['expected_catalog_revision'])
        except (TimeoutError, Full, OSError, ValueError) as error:
            # The prepare may have been applied, so the rate may already be zero.
            return await self._abandon_activation(job_id, report, _activation_message(error))
        if not ack.get('ok'):
            code = ack.get('error_code')
            result = code if code in ('activation_conflict', 'replacement_conflict') \
                else 'activation_failed'
            report(phase='failed', result=result, message=ack.get('error'))
            return result

        # The engine is paused from this line on, so EVERY statement below sits inside the
        # one cleanup boundary that gives the rate and the policy back. A reporting
        # failure aborts the activation: an activation nobody can report is not one, and
        # the engine must never be left paused with nobody to cancel it.
        try:
            report(phase='draining', active_objects=ack.get('active_objects'))
            # Steps 2 and 3: drain, then commit under the fence. The worker owns both
            # truths: only it knows the belt is empty, and only a commit sets the fence.
            while True:
                ack = await self._activation_command('commit_activation', job_id,
                                                     deadline=deadline)
                if ack.get('ok'):
                    break
                code = ack.get('error_code')
                if code != 'activation_not_drained':
                    result = code if code in ('activation_conflict', 'replacement_conflict') \
                        else 'activation_failed'
                    report(phase='failed', result=result,
                           active_objects=ack.get('active_objects'), message=ack.get('error'))
                    return result
                report(phase='draining', active_objects=ack.get('active_objects'))
                if time.monotonic() >= deadline:
                    return await self._abandon_activation(
                        job_id, report, 'The belt did not drain before the timeout.',
                        active_objects=ack.get('active_objects'))
                await asyncio.sleep(ACTIVATION_POLL_S)

            # Steps 4 and 5: the captured survivors minus the label that leaves, written
            # once by the staging of the bundle, then verified in a child.
            captured = [name for name in (ack.get('reject_classes') or [])
                        if name != victim_label]
            policy_version = ack.get('policy_version')
            report(phase='activating', active_objects=0,
                   activation_policy_version=policy_version)
            files = self._candidate_bundle_files(candidate, captured)
            bundle_sha256 = object_catalog.publish_bundle(active_root / 'bundles', files)
            bundle = active_root / 'bundles' / bundle_sha256
            await asyncio.to_thread(validate_bundle_child, bundle)
        except Exception as error:
            return await self._abandon_activation(job_id, report, _activation_message(error))

        # Step 6: the swap. From here the old session is gone, so a failure can never
        # claim score continuity.
        previous_preset, previous_bundle = self.preset, self.active_bundle
        previous_session_id = self.state.get('session_id')
        os.environ[object_catalog.CATALOG_ROOT_ENV] = str(bundle / 'catalog')
        self.preset, self.active_bundle = bundle / object_catalog.BUNDLE_PRESET, bundle
        try:
            await self._swap_worker(previous_session_id)
            if self.state.get('status') == 'failed':
                raise RuntimeError(self.state.get('error') or 'The engine failed to start.')
        except Exception as error:
            return await self._rollback_activation(
                report, previous_preset, previous_bundle, policy_version,
                _activation_message(error))

        # Step 7: the pointer IS the activation. The write replaces the name first and
        # makes it durable afterwards, so an exception here proves nothing. The POINTER
        # decides what actually happened, never the exception.
        durability = None
        try:
            object_catalog.write_active_pointer(active_root, bundle_sha256)
        except Exception as error:
            durability = _activation_message(error)
        if _pointer_bundle(active_root) != bundle_sha256:
            return await self._rollback_activation(
                report, previous_preset, previous_bundle, policy_version,
                durability or 'The active pointer did not move.')
        # Admission and the published class list must describe the committed bundle
        # before any success is reported or any later job is accepted.
        self._refresh_active_identity()
        # Nothing below may undo the activation. An audit failure is reported beside the
        # active result, never as a failed activation.
        audit = None if durability is None else (
            f'The activation is committed. Its pointer durability failed: {durability}')
        try:
            retired_at = item_jobs.utc_now()
            retiring = _bundle_model_manifest(active_root / 'bundles' / previous_sha256)
            if retiring is not None:
                evidence.setdefault('model.manifest.json', retiring)
            object_catalog.archive_type(self.history_root, victim, retired_at, evidence)
            self._append_activation_history({
                'job_id': job_id, 'activated_at': retired_at, 'bundle_sha256': bundle_sha256,
                'previous_bundle_sha256': previous_sha256,
                'victim_type_id': victim['object_type_id'], 'victim_label': victim_label,
                'activation_policy_version': policy_version, 'reject_classes': captured,
                'session_id': self.state.get('session_id'), 'score_epoch_id': epoch()})
        except (object_catalog.CatalogError, OSError, ValueError) as error:
            audit = f'The activation is committed. Its audit trail failed: ' \
                    f'{_activation_message(error)}'
        report(phase='active', result='active', active_objects=0, rolled_back=False,
               bundle_sha256=bundle_sha256, activation_policy_version=policy_version,
               session_id=self.state.get('session_id'), score_epoch_id=epoch(),
               message=audit)
        return 'active'

    async def _rollback_activation(self, report, preset, bundle, policy_version, message):
        """The pointer never moved, so the prior bundle comes back as one whole unit.

        This is a NEW session and a NEW score epoch. It is never a continuity claim.
        """
        self.preset, self.active_bundle = preset, bundle
        if bundle is not None:
            os.environ[object_catalog.CATALOG_ROOT_ENV] = str(bundle / 'catalog')
        rolled_back, failure = False, None
        try:
            await self._swap_worker(self.state.get('session_id'))
            # A worker that publishes `failed` still releases this wait, so the returned
            # state decides, never the absence of an exception.
            if self.state.get('status') in ('ready', 'running'):
                rolled_back = True
            else:
                failure = self.state.get('error') or 'The prior bundle did not start.'
        except Exception as error:
            failure = _activation_message(error)
        # A rollback is a claim about the pointer too. The worker and the pointer must
        # name ONE bundle, or this is a split, never a rollback.
        pointer = _pointer_bundle(self.item_jobs_root / 'active')
        if rolled_back and bundle is not None and pointer != bundle.name:
            rolled_back = False
            failure = 'The active pointer names another bundle.'
        if rolled_back:
            # The pointer never moved, so this republishes the bundle that is still
            # active. A compatibility claim follows this proof, never precedes it.
            self._refresh_active_identity()
        else:
            # A rollback that did not come back is not a rollback. The existing fatal
            # path owns it, and nothing here may claim a verified catalog or model.
            self.catalog_model_compatible = None
            await self._cleanup_restart_failure(failure)
            message = f'{message} The rollback failed: {failure}'
        epoch = (self.state.get('reject_policy') or {}).get('score_epoch_id')
        report(phase='failed', result='activation_failed', rolled_back=rolled_back,
               activation_policy_version=policy_version, message=message,
               session_id=self.state.get('session_id'), score_epoch_id=epoch)
        return 'activation_failed'

    def _repair_commit(self, job_id, candidate, active_root, bundle_sha256):
        """Finish the commit step of an activation whose pointer already moved.

        It archives the victim only when its entry is absent, and appends one history
        row only when no row names this job and this bundle. A retry therefore writes
        nothing twice, and it never starts a second worker or a second score epoch.
        """
        history = Path(self.history_root) / 'activations.jsonl'
        archived = Path(self.history_root) / 'wall-of-fame' / str(candidate['victim_id'])
        if archived.is_dir() and _activation_recorded(history, job_id, bundle_sha256):
            return None
        retired_sha256, victim = _retired_definition(
            active_root, candidate['victim_id'], bundle_sha256)
        if victim is None:
            return ('The activation is committed. Its audit trail cannot be completed: '
                    'the retired definition is no longer published.')
        try:
            if not archived.is_dir():
                evidence = {name: Path(path)
                            for name, path in (candidate.get('evidence') or {}).items()}
                retiring = _bundle_model_manifest(active_root / 'bundles' / retired_sha256)
                if retiring is not None:
                    evidence.setdefault('model.manifest.json', retiring)
                object_catalog.archive_type(self.history_root, victim, item_jobs.utc_now(),
                                            evidence)
            if not _activation_recorded(history, job_id, bundle_sha256):
                policy = json.loads(
                    (active_root / 'bundles' / bundle_sha256 / 'policy.json').read_text())
                self._append_activation_history({
                    'job_id': job_id, 'activated_at': item_jobs.utc_now(),
                    'bundle_sha256': bundle_sha256,
                    'previous_bundle_sha256': retired_sha256,
                    'victim_type_id': victim['object_type_id'],
                    'victim_label': victim['classifier_label'],
                    # A repair never saw the capture, so it states no policy version.
                    'activation_policy_version': None,
                    'reject_classes': list((policy or {}).get('reject_classes') or []),
                    'session_id': self.state.get('session_id'),
                    'score_epoch_id': (self.state.get('reject_policy') or {}).get(
                        'score_epoch_id')})
        except (object_catalog.CatalogError, OSError, ValueError,
                json.JSONDecodeError) as error:
            return (f'The activation is committed. Its audit trail failed: '
                    f'{_activation_message(error)}')
        return None

    def _append_activation_history(self, row):
        """One durable append. The row must survive the power loss that follows it."""
        path = Path(self.history_root) / 'activations.jsonl'
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, sort_keys=True) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    async def restart(self, request):
        if self.continuous:
            return web.json_response({
                'status': 'unsupported',
                'error_code': 'restart_unsupported',
                'error': 'Continuous visitor restart is disabled. Restart the local process instead.',
                'session_id': self.state.get('session_id'),
            }, status=409)
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
            await self._swap_worker(session_id)
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
        error = self.state.get('error')
        if self.task is not None and self.task.done() and status not in ('completed', 'failed'):
            status = 'failed'
            if self.task.cancelled():
                error = 'The service state pump stopped.'
            else:
                exception = self.task.exception()
                error = f'The service state pump stopped: {exception}'
        elif (self.continuous and status in ('ready', 'running')
              and time.monotonic() - self.last_state_broadcast > PUMP_STALE_SECONDS):
            status = 'failed'
            error = 'The service state pump stopped publishing application heartbeats.'
        packet = {'status': status, 'session_id': self.state.get('session_id'), 'error': error}
        # Additive bundle identity. Null means that the service runs without an active bundle.
        bundle = getattr(self, 'active_bundle', None)
        packet.update(active_bundle_sha256=bundle.name if bundle else None,
                      catalog_model_compatible=getattr(self, 'catalog_model_compatible', None),
                      item_jobs_quality_gate=getattr(self, 'item_jobs_quality_gate', 'strict'))
        # Additive queue liveness. A valid temporary child is never an extra engine.
        runner = getattr(self, 'item_runner', None)
        queue = runner.health() if runner is not None else None
        if queue is not None:
            queue['unhealthy_shutdown'] = (queue['unhealthy_shutdown']
                                           or getattr(self, 'item_jobs_unhealthy', False))
            packet['item_jobs'] = queue
            if not queue['runner_thread_alive'] or queue['unhealthy_shutdown']:
                packet['status'] = status = 'failed'
                packet['error'] = error or 'The item job runner thread stopped.'
            elif queue['last_fault']:
                packet['status'] = status = 'failed'
                packet['error'] = 'The item job runner reported ' + str(queue['last_fault']) + '.'
        return web.json_response(packet, status=503 if status == 'failed' else 200)

    async def state_handler(self, request):
        self._advance_command_epoch()
        return web.json_response(self._state_packet())

    async def _command_error(self, ws, command_id, error, error_code=None, command_epoch=None):
        packet = {'type': 'ack', 'command_id': command_id, 'ok': False, 'error': error}
        if error_code is not None:
            packet['error_code'] = error_code
        if command_epoch is not None:
            packet['command_epoch'] = command_epoch
        await ws.send_json(packet)

    async def _handle_command(self, command, ws):
        command_id = None
        try:
            if not isinstance(command, dict):
                raise ValueError('Send a JSON object.')
            command_id = str(uuid.UUID(command.get('command_id', '')))
            if self.continuous:
                original_payload = dict(command)
                previous = self.requests.get(command_id)
                if previous:
                    command_epoch = command.get('command_epoch')
                    if previous['payload'] != original_payload:
                        await self._command_error(
                            ws, command_id, 'This command ID already belongs to another request.',
                            'command_conflict', command_epoch if isinstance(command_epoch, str) else None)
                        return
                    await ws.send_json(previous.get('ack') or {
                        'type': 'pending', 'command_id': command_id,
                        'command_epoch': command_epoch,
                    })
                    return
            command_type = command.get('type')
            if command_type not in ('inject', 'set_reject_policy'):
                raise ValueError('Select a supported command type.')
            if not self.continuous and command_type != 'inject':
                raise ValueError('Reject policy changes require continuous mode.')
            if self.restarting:
                raise ValueError('The session is restarting. Retry after it starts.')
            if command.get('session_id') != self.state.get('session_id'):
                raise ValueError('The session changed. Reload the page.')
            if command_type == 'inject':
                if not isinstance(command.get('class_name'), str) or len(command['class_name']) > 32:
                    raise ValueError('Select a supported class.')
            else:
                reject_classes = command.get('reject_classes')
                if not isinstance(reject_classes, list) or len(reject_classes) > 32:
                    raise ValueError('Send a bounded reject class list.')
                if not isinstance(command.get('expected_policy_version'), str):
                    raise ValueError('Send the expected policy version.')

            if self.continuous:
                self._advance_command_epoch()
                command_epoch = command.get('command_epoch')
                if not isinstance(command_epoch, str):
                    raise ValueError('Send the server-issued command epoch.')
                if command_epoch != self.command_epoch:
                    await self._command_error(
                        ws, command_id, 'The command epoch expired. Do not replace it during recovery.',
                        'command_epoch_expired', command_epoch)
                    return
                if self.state['status'] not in ('ready', 'running'):
                    raise ValueError('The continuous engine is unavailable.')
                pending = sum(not entry.get('ack') for entry in self.requests.values())
                if pending >= MAX_PENDING_COMMANDS:
                    await self._command_error(
                        ws, command_id, 'The command queue is full. Retry shortly.',
                        'command_queue_full', command_epoch)
                    return
                if self.epoch_admissions[self.command_epoch] >= MAX_COMMANDS_PER_EPOCH:
                    await self._command_error(
                        ws, command_id, 'This command epoch reached its admission limit.',
                        'command_epoch_full', command_epoch)
                    return
                payload = {'type': command_type, 'command_id': command_id,
                           'session_id': command['session_id'],
                           'command_epoch': command_epoch}
                if command_type == 'inject':
                    payload['class_name'] = command['class_name']
                else:
                    payload.update(reject_classes=reject_classes,
                                   expected_policy_version=command['expected_policy_version'])
                self.requests[command_id] = {'payload': original_payload}
                try:
                    self.commands.put_nowait(payload)
                except Full:
                    del self.requests[command_id]
                    await self._command_error(
                        ws, command_id, 'The command queue is full. Retry shortly.',
                        'command_queue_full', command_epoch)
                    return
                self.epoch_admissions[self.command_epoch] += 1
                return

            payload = {'type': 'inject', 'command_id': command_id,
                       'session_id': command['session_id'],
                       'class_name': command['class_name']}
            previous = self.requests.get(command_id)
            if previous:
                if previous['payload'] != payload:
                    raise ValueError('This command ID already belongs to another request.')
                await ws.send_json(previous.get('ack') or {'type': 'pending', 'command_id': command_id})
                return
            if self.state['status'] not in ('ready', 'running'):
                raise ValueError('The session is unavailable. Restart the session.')
            if len(self.requests) >= MAX_COMMANDS:
                raise ValueError('This session reached its command limit. Restart the session.')
            # Store first so a fast worker acknowledgment cannot outrun retention.
            self.requests[command_id] = {'payload': payload}
            try:
                self.commands.put_nowait(payload)
            except Full:
                del self.requests[command_id]
                raise
        except (ValueError, TypeError, AttributeError, Full) as exc:
            error = 'The command queue is full. Retry shortly.' if isinstance(exc, Full) else str(exc)
            await self._command_error(ws, command_id, error)

    async def websocket(self, request):
        if len(self.clients) >= MAX_CLIENTS:
            raise web.HTTPServiceUnavailable(text='Four browsers already share this session.')
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=2048)
        await ws.prepare(request)
        self.clients.add(ws)
        self._advance_command_epoch()
        await ws.send_json(self._state_packet())
        # Retain acknowledgments across reconnects for this bounded session.
        for entry in self.requests.values():
            if entry.get('ack'):
                await ws.send_json(entry['ack'])
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    command = json.loads(message.data)
                except json.JSONDecodeError:
                    await self._command_error(ws, None, 'Send a JSON object.')
                    continue
                await self._handle_command(command, ws)
        finally:
            self.clients.discard(ws)
        return ws


def build_parser():
    """One parser. The documented launch commands are tested against it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host', choices=['127.0.0.1', '0.0.0.0'], default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8890)
    parser.add_argument('--allowed-host', action='append', default=[],
                        help='Exact public Host value. Repeat for more than one host.')
    parser.add_argument('--allowed-origin', action='append', default=[],
                        help='Exact public HTTP or HTTPS Origin. Repeat for more than one origin.')
    parser.add_argument('--preset', type=Path, default=HERE / 'configs/default_demo.json')
    parser.add_argument('--out', type=Path, default=HERE / 'runs' / time.strftime('live-%Y%m%d-%H%M%S'))
    parser.add_argument('--item-jobs-root', type=Path, default=None,
                        help='Stable writable root. It holds the active bundle (active/) and '
                             'the job history (history/). The first start seeds bundle zero '
                             'from --preset. Default: <out>/item-jobs, without any bundle.')
    parser.add_argument('--object-catalog-root', type=Path, default=None,
                        help='Runtime catalog root, used only without --item-jobs-root. '
                             'Default: <out>/item-jobs/object_catalog.')
    parser.add_argument('--item-jobs-provider', choices=list(item_jobs.PROVIDER_MODES),
                        default='cached',
                        help='cached and paid never pass an automatic --live. fake is local only.')
    parser.add_argument('--item-jobs-quality-gate', choices=('strict', 'demo'),
                        default='strict',
                        help='strict keeps every candidate gate. demo is the accepted '
                             'demonstration switch. The published value always states which.')
    parser.add_argument('--item-jobs-provider-cache', type=Path, default=None,
                        help='Provider cache root, layout <root>/cache/<request digest>.json. '
                             'Default: the packaged research results, used read-only.')
    parser.add_argument('--item-jobs-provider-env', type=Path, default=None,
                        help='Read-only credential file. Required for paid, never opened otherwise.')
    parser.add_argument('--item-jobs-generator-root', type=Path, default=item_jobs.GENERATOR_ROOT,
                        help='Directory holding probe.py and render_suite.py.')
    parser.add_argument('--item-jobs-runtime-lock', type=Path, default=DEFAULT_RUNTIME_LOCK,
                        help='Shared render lock path. A deployment passes a writable path.')
    parser.add_argument('--item-jobs-physics-replay', type=Path, default=None,
                        help='Read-only directory of reviewed physics replay metadata, one '
                             '<recipe_sha256>.json per replay. No request or route can select it.')
    return parser


def build_service(parser, args):
    """The startup order. The arguments are parsed. Then the verified active bundle is
    resolved, then its catalog is exported, and only then does LiveService call
    load_preset, which imports profiles. Without --item-jobs-root no bundle exists and the
    service starts from --preset and the packaged catalog.
    """
    item_jobs_root = (args.item_jobs_root or args.out / 'item-jobs').resolve()
    _validate_item_job_arguments(parser, args, item_jobs_root)
    preset_path, active_bundle = args.preset.resolve(), None
    if args.item_jobs_root is not None:
        try:
            active_bundle = bind_active_bundle(preset_path, item_jobs_root)
        except object_catalog.CatalogError as error:
            # No catalog message carries a host path.
            parser.error(f'The active bundle cannot start: {error}')
        preset_path = active_bundle / object_catalog.BUNDLE_PRESET
    return LiveService(
        preset_path, args.out.resolve(), item_jobs_root=item_jobs_root,
        item_jobs_provider=args.item_jobs_provider,
        catalog_root=args.object_catalog_root.resolve() if args.object_catalog_root else None,
        provider_cache=args.item_jobs_provider_cache.resolve() if args.item_jobs_provider_cache else None,
        provider_env=args.item_jobs_provider_env.resolve() if args.item_jobs_provider_env else None,
        generator_root=args.item_jobs_generator_root.resolve(),
        runtime_lock=args.item_jobs_runtime_lock.resolve(),
        physics_replay=args.item_jobs_physics_replay.resolve()
        if args.item_jobs_physics_replay else None,
        active_bundle=active_bundle, quality_gate=args.item_jobs_quality_gate)


def main():
    parser = build_parser()
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    try:
        allowed_hosts, allowed_origins = configured_access_rules(
            args.host, args.port, args.allowed_host, args.allowed_origin)
        _configured_source_revision()
    except ValueError as exc:
        parser.error(str(exc))
    service = build_service(parser, args)

    async def page(request):
        return web.FileResponse(HERE / 'live_web/index.html')

    async def script(request):
        return web.FileResponse(HERE / 'live_web/live.js')

    async def timeline(request):
        return web.FileResponse(HERE / 'live_web/timeline.mjs')

    async def cinematic(request):
        return web.FileResponse(HERE / 'live_web/cinematic.mjs')

    async def generated_assets(request):
        return web.FileResponse(HERE / 'live_web/generated_assets.mjs')

    app = web.Application(
        middlewares=[access_middleware(allowed_hosts, allowed_origins)], client_max_size=2048)
    app.cleanup_ctx.append(service.lifecycle)
    app.on_shutdown.append(service.shutdown)
    app.add_routes([web.get('/', page), web.get('/live.js', script),
                    web.get('/timeline.mjs', timeline), web.get('/cinematic.mjs', cinematic),
                    web.get('/generated_assets.mjs', generated_assets),
                    web.get('/health', service.health),
                    web.get('/state', service.state_handler), web.get('/ws', service.websocket),
                    web.post('/restart', service.restart),
                    web.post('/item-jobs', service.submit_item_job),
                    web.get('/item-jobs', service.list_item_jobs),
                    web.get('/item-jobs/{request_id}', service.get_item_job),
                    web.post('/item-jobs/{request_id}/resolve-provider', service.resolve_item_provider),
                    web.post('/item-jobs/{request_id}/resolve-replacement', service.resolve_item_replacement),
                    web.post('/item-jobs/{request_id}/confirm-cleanup', service.confirm_item_cleanup),
                    web.post('/reset-defaults', service.reset_defaults),
                    web.get('/item-jobs/{request_id}/previews/{name}', service.item_job_preview),
                    web.get('/catalog-assets/{catalog_revision}/{glb_sha256}.glb',
                            service.catalog_asset),
                    web.get('/wall-of-fame/{entry_id}/{name}', service.wall_of_fame_preview),
                    web.get('/wall-of-fame', service.wall_of_fame),
                    # The 3D view reuses the replay viewer's vendored three.js build (no network requests).
                    web.static('/vendor', HERE / 'web/vendor', follow_symlinks=False),
                    # Blender bean/machine GLBs plus the vendored GLTFLoader used by the 3D view.
                    web.static('/assets', HERE / 'visual_assets/browser', follow_symlinks=False)])
    print(f'Evidence directory: {args.out.resolve()}', flush=True)
    web.run_app(app, host=args.host, port=args.port, access_log=None, shutdown_timeout=3)


if __name__ == '__main__':
    main()
