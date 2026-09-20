"""Durable queue for generated item jobs with early preview renders.

Generation and rendering run in child process groups, never on the HTTP event
loop and never in the engine worker. This module imports no aiohttp and no
mujoco, so the queue is testable without a service or a simulator. Model output
stays data: a recipe is read and hashed, never imported or executed.

Provider safety is structural. `--live` is never automatic in any mode. It is
passed only when the job record holds an unconsumed operator `new_request` and
the service runs in paid mode, and that grant is consumed and persisted before
the child starts. One process owns the writer lock for one job root, and one
re-entrant lock serializes every store mutation between the HTTP handler threads
and the runner thread.
"""
from __future__ import annotations

import contextlib
from collections import deque
import datetime
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping

import object_catalog

HERE = Path(__file__).resolve().parent
# The one generator the service runs. The research copies stay recorded evidence.
GENERATOR_ROOT = HERE / "generator"
FAKE_WORKER = HERE / "tests/fakes/fake_item_worker.py"
FIXTURE_ROOT = HERE / "tests/fixtures"
# Linux start identity. A slim image may ship no ps binary, so /proc comes first.
PROC_ROOT = Path("/proc")

JOB_SCHEMA_VERSION = 1
# Previews are small. The recorded size is the real limit and this is the hard cap.
MAX_PREVIEW_BYTES = 8 * 1024 * 1024
PREVIEW_CONTENT_TYPES = {"perspective.png": "image/png", "top.png": "image/png",
                         "object.glb": "model/gltf-binary"}

PROVIDER_MODES = ("cached", "paid", "fake")
MAX_QUEUED_JOBS = 4
# The candidate joins the feed at this prior. The plan fixes the value.
CANDIDATE_PRIOR = 0.03
# Blocked jobs never become queued work, so a public deployment needs a second bound
# over every retained open job. Without it anonymous requests grow disk without limit.
MAX_RETAINED_OPEN_JOBS = 32
# Terminal jobs stop counting toward every other bound, so without one finite total the
# public queue admits new work forever. History is durable: nothing is ever evicted.
MAX_RETAINED_JOBS = 256
MAX_SUMMARIES = 32
MAX_ATTEMPTS = 2
# The app-wide request body limit stays 2048 bytes. This is the description field alone.
MAX_DESCRIPTION = 600
MAX_REQUESTER = 80
# A busy render lock returns a job without consuming an attempt, so history needs a bound.
MAX_HISTORY = 64
TERM_WAIT_S = 5.0
KILL_WAIT_S = 5.0
LEASE_S = 900.0
POLL_S = 0.02
STEP_S = 0.25
# Bounded backoff for a busy shared render lock. It replaces a 4 Hz relaunch spin.
LOCK_BACKOFF_S = (1.0, 2.0, 4.0, 8.0, 15.0)
MAX_RECENT_FAULTS = 16
# A transient fault must not pin health red forever. It clears after this many clean passes.
FAULT_CLEAR_PASSES = 8

# One definition of the child stage exit contract. Every runner script and every
# fake worker imports these names instead of redeclaring them.
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_NOT_SUBMITTED = 3
EXIT_UNCERTAIN = 4
EXIT_CREDENTIALS = 5
EXIT_CACHE_ENTRY_INVALID = 6
EXIT_RENDER_LOCK = 75

STATES = frozenset({
    "queued", "generating_recipe", "operator_required", "interrupted_uncertain",
    "waiting_for_render", "rendering_previews", "worker_unavailable", "preview_ready",
    "proposing_physics", "validating_physics", "physics_blocked",
    "selecting_training_baseline", "queued_for_training", "training", "validating_candidate",
    "waiting_for_replacement", "draining_for_activation", "activating", "active",
    "replacement_conflict", "activation_conflict", "failed",
})
# The plan error codes plus catalog_revision_conflict, provider_cache_miss, and
# paid_mode_disabled. The deployment addendum documents the last three.
ERRORS = frozenset({
    "invalid_description", "request_conflict", "queue_full", "credentials_missing",
    "provider_interrupted", "generation_failed", "render_failed", "worker_timeout",
    "worker_unavailable", "physics_unsupported", "training_failed",
    "candidate_validation_failed", "replacement_conflict", "activation_failed",
    "catalog_revision_conflict", "provider_cache_miss", "paid_mode_disabled",
    "physics_proposal_failed", "history_full",
})
TERMINAL = frozenset({"active", "failed"})
BLOCKED = frozenset({
    "operator_required", "interrupted_uncertain", "physics_blocked",
    "waiting_for_replacement", "replacement_conflict", "activation_conflict",
    "worker_unavailable",
})
PRIMARY_ACTIONS = {
    "operator_required": "resolve_provider",
    "interrupted_uncertain": "resolve_provider",
    "replacement_conflict": "resolve_replacement",
    "waiting_for_replacement": "resolve_replacement",
    "worker_unavailable": "confirm_cleanup",
}
# Once a request may have reached the provider, nothing may claim it never did.
SUBMITTED_EVER = frozenset({"in_flight", "uncertain", "completed"})
PREVIEW_FILES = ("perspective.png", "top.png", "render.json", "object.glb")
PREVIEW_DOWNLOADS = frozenset({"perspective.png", "top.png", "object.glb"})

# One stage table. Phase 3 adds physics_proposal, physics, and training as extra rows
# plus one settle method each. No scheduling code branches on a stage name.
STAGES: dict[str, dict[str, Any]] = {
    "generation": {
        "ready_states": ("queued", "generating_recipe"),
        "running_state": "generating_recipe",
        "waiting_state": "generating_recipe",
        "retry_state": "generating_recipe",
        "failure_error": "generation_failed",
        "settle": "_settle_generation",
        "provider_backed": True,
        "lock_busy_exit": None,
    },
    "render": {
        "ready_states": ("waiting_for_render", "rendering_previews"),
        "running_state": "rendering_previews",
        "waiting_state": "waiting_for_render",
        # A recovered or cleaned renderer waits for the shared lock again.
        "retry_state": "waiting_for_render",
        "failure_error": "render_failed",
        "settle": "_settle_render",
        "provider_backed": False,
        "lock_busy_exit": EXIT_RENDER_LOCK,
    },
    "physics_proposal": {
        "ready_states": ("preview_ready", "proposing_physics"),
        "running_state": "proposing_physics",
        "waiting_state": "preview_ready",
        "retry_state": "proposing_physics",
        "failure_error": "physics_proposal_failed",
        "settle": "_settle_physics_proposal",
        "provider_backed": True,
        "lock_busy_exit": None,
    },
    "physics": {
        "ready_states": ("validating_physics",),
        "running_state": "validating_physics",
        "waiting_state": "validating_physics",
        "retry_state": "validating_physics",
        "failure_error": "physics_unsupported",
        "settle": "_settle_physics",
        "provider_backed": False,
        "lock_busy_exit": EXIT_RENDER_LOCK,
    },
    "training": {
        # One job at a time takes the training turn, binds its baseline, then trains.
        # `waiting_for_replacement` is re-evaluated on every pass, so a victim that
        # becomes eligible again releases the job without an operator.
        "ready_states": ("selecting_training_baseline", "waiting_for_replacement",
                         "queued_for_training", "training"),
        "running_state": "training",
        "waiting_state": "queued_for_training",
        "retry_state": "queued_for_training",
        "failure_error": "training_failed",
        "settle": "_settle_training",
        "prepare": "_prepare_training",
        "provider_backed": False,
        "lock_busy_exit": EXIT_RENDER_LOCK,
    },
}
TRAINING_LEASE = "training.lease"
# Every evidence block a completed trainer run writes. A record missing any of them is
# incomplete, whatever it claims about `passed`.
VALIDATION_EVIDENCE = ("label_order_ok", "classifier", "anomaly", "keep_outcome", "pulses",
                       "policy", "preset_compatibility")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_FIELDS = {"request_id", "description", "requester_name", "expected_catalog_revision"}
_REQUIRED_FIELDS = {"request_id", "description", "expected_catalog_revision"}
_RESERVED_FIELDS = {"state", "error", "history", "timestamps", "request_id", "request_sha256",
                    "provider_mode"}


class ItemJobError(ValueError):
    """One queue operation failed. The code names the visible reason."""

    def __init__(self, code: str, message: str | None = None):
        super().__init__(message or code)
        self.code = code


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def primary_action(state: str) -> str | None:
    return PRIMARY_ACTIONS.get(state)


def is_queued_work(job: Mapping[str, Any]) -> bool:
    """Queued work counts against the admission limit. Blocked and terminal jobs do not."""
    state = job["state"]
    return state not in TERMINAL and state not in BLOCKED


def is_open(job: Mapping[str, Any]) -> bool:
    """An open job still occupies disk and attention. A blocked job is still open."""
    return job["state"] not in TERMINAL


def live_permitted(job: Mapping[str, Any], provider_mode: str, stage: str) -> bool:
    """The only source of `--live`. There is no automatic path and no fallthrough.

    A grant is bound to the stage the operator saw. An approval of a PHYSICS request can
    never authorize a GENERATION request, so one stage never spends another's grant.
    """
    if provider_mode != "paid" or job.get("provider_permission") != "new_request":
        return False
    return job.get("provider_permission_stage") == stage


class ItemJobStore:
    """Durable job records under one root, guarded by one process-owned writer lock."""

    def __init__(self, root: Path, clock: Callable[[], str] = utc_now,
                 provider_mode: str = "cached"):
        if provider_mode not in PROVIDER_MODES:
            raise ItemJobError("invalid_provider_mode", f"unknown provider mode: {provider_mode}")
        self.root = Path(root)
        self.clock = clock
        self.provider_mode = provider_mode
        self.jobs_root = self.root / "jobs"
        self.jobs_root.mkdir(parents=True, exist_ok=True)
        # A cheap change counter. The service compares it instead of rereading disk.
        self.revision = 0
        # flock excludes another process. This lock excludes the runner thread from the
        # HTTP handler threads, so no read, modify, write sequence can interleave.
        self.lock = threading.RLock()
        # One in-memory index, loaded once. Nothing rescans the tree per scheduling pass.
        # _open is the scheduling view: at most MAX_RETAINED_OPEN_JOBS entries, so a pass
        # never copies the whole retained history.
        self._index: dict[str, dict[str, Any]] = {}
        self._open: set[str] = set()
        self._lock_file = (self.root / "writer.lock").open("a")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            self._lock_file.close()
            raise ItemJobError("writer_locked",
                               "another process owns this item job root") from error
        self._load_index()

    def _load_index(self) -> None:
        """Read the tree exactly once, when this process takes the writer lock."""
        for directory in sorted(self.jobs_root.iterdir()) if self.jobs_root.is_dir() else []:
            if not directory.is_dir():
                continue
            job = self._read(directory)
            if job is not None:
                self._index[job["request_id"]] = job
                if is_open(job):
                    self._open.add(job["request_id"])

    def close(self) -> None:
        with contextlib.suppress(Exception):
            fcntl.flock(self._lock_file, fcntl.LOCK_UN)
        self._lock_file.close()

    def job_dir(self, request_id: str) -> Path:
        # The directory name is the validated canonical UUID, so no separator can appear.
        return self.jobs_root / _canonical_request_id(request_id)

    def get(self, request_id: str) -> dict[str, Any]:
        with self.lock:
            job = self._read(self.job_dir(request_id))
        if job is None:
            raise ItemJobError("unknown_job")
        return job

    def jobs(self) -> list[dict[str, Any]]:
        """Every retained job, oldest first. A failed job stays visible."""
        with self.lock:
            found = self._all()
        found.sort(key=lambda job: (job["timestamps"]["created"], job["request_id"]))
        return found

    def open_jobs(self) -> list[dict[str, Any]]:
        """The scheduling view: only jobs that still need work, oldest first."""
        with self.lock:
            found = [json.loads(json.dumps(self._index[request_id]))
                     for request_id in self._open if request_id in self._index]
        found.sort(key=lambda job: (job["timestamps"]["created"], job["request_id"]))
        return found

    def summaries(self) -> list[dict[str, Any]]:
        """The bounded presentation buffer, newest first."""
        newest = list(reversed(self.jobs()))[:MAX_SUMMARIES]
        return [self.summary(job) for job in newest]

    def summary(self, job: Mapping[str, Any]) -> dict[str, Any]:
        request_id = job["request_id"]
        previewed = "preview_ready" in job["timestamps"]
        return {
            "request_id": request_id,
            "display_name": job.get("display_name"),
            "description": job["description"],
            "requester_name": job.get("requester_name"),
            "state": job["state"],
            "error": job.get("error"),
            "updated_at": job["timestamps"]["updated"],
            "created_at": job["timestamps"]["created"],
            "preview": f"/item-jobs/{request_id}/previews/perspective.png" if previewed else None,
            "attempts": dict(job["attempts"]),
            "progress": job.get("progress"),
            # A short stable code beside the free text, so no caller parses prose.
            "reason": job.get("reason"),
            "blocked_stage": job.get("blocked_stage"),
            "primary_action": primary_action(job["state"]),
            "provider_mode": job.get("provider_mode"),
            "provider_cache_hit": job.get("provider_cache_hit"),
        }

    def submit(self, payload: Any, current_catalog_revision: str) -> tuple[dict[str, Any], bool]:
        """Admit one request. An exact retry returns the existing job without new work."""
        normalized = _normalize_payload(payload)
        request_id = normalized["request_id"]
        request = {key: value for key, value in normalized.items() if key != "request_id"}
        request_sha256 = hashlib.sha256(canonical_json(request)).hexdigest()
        directory = self.job_dir(request_id)
        # One lock over the read, the admission check, and the write. Two HTTP threads
        # cannot both pass the queue limit.
        with self.lock:
            existing = self._read(directory)
            if existing is not None:
                if existing["request_sha256"] != request_sha256:
                    raise ItemJobError("request_conflict")
                return existing, False
            if normalized["expected_catalog_revision"] != current_catalog_revision:
                raise ItemJobError("catalog_revision_conflict")
            retained = list(self._index.values())
            # Checked before any directory is created. An already admitted request took
            # the earlier branch, so an exact retry still returns its job.
            if len(retained) >= MAX_RETAINED_JOBS:
                raise ItemJobError("history_full",
                                   "the item history is full and an operator must archive it")
            if sum(is_queued_work(job) for job in retained) >= MAX_QUEUED_JOBS:
                raise ItemJobError("queue_full", "four jobs are already queued")
            if sum(is_open(job) for job in retained) >= MAX_RETAINED_OPEN_JOBS:
                raise ItemJobError("queue_full",
                                   "retained jobs waiting for an operator fill the queue")

            now = self.clock()
            job = {
                "schema_version": JOB_SCHEMA_VERSION,
                "request_id": request_id,
                "description": normalized["description"],
                "requester_name": normalized["requester_name"],
                "display_name": None,
                "request_sha256": request_sha256,
                "state": "queued",
                "error": None,
                "progress": None,
                "attempts": {stage: 0 for stage in STAGES},
                "provider_mode": self.provider_mode,
                "provider_submission": "not_submitted",
                "provider_submission_history": ["not_submitted"],
                "provider_permission": None,
                # A one-time grant belongs to one stage, and so does the block it answers.
                "provider_permission_stage": None,
                "blocked_stage": None,
                "provider_cache_hit": None,
                "provider_evidence": None,
                # Per stage, so one stage's lock backoff never lengthens another's.
                "lock_busy_count": {},
                "reason": None,
                "timestamps": {"created": now, "queued": now, "updated": now},
                "history": [{"state": "queued", "at": now}],
                "worker": None,
                "admission_catalog_revision": normalized["expected_catalog_revision"],
                "training_baseline": None,
                "victim": None,
                "artifacts": {},
            }
            for name in ("previews", "training", "activation"):
                (directory / name).mkdir(parents=True, exist_ok=True)
            _write_json(directory / "request.json", request)
            self._write(directory, job)
        return job, True

    def transition(self, request_id: str, state: str, *, token: str | None = None,
                   error: str | None = None, **fields: Any) -> dict[str, Any]:
        """The only way to change a job state. A stale token writes nothing."""
        if state not in STATES:
            raise ItemJobError("invalid_state", f"unknown state: {state}")
        if error is not None and error not in ERRORS:
            raise ItemJobError("invalid_error", f"unknown error code: {error}")
        with self.lock:
            job = self._checked(request_id, token, fields)
            # A fake job can never reach activation, whatever calls this.
            if state == "selecting_training_baseline" and job.get("provider_mode") == "fake":
                raise ItemJobError("fake_provider_not_activatable")
            now = self.clock()
            job.update(fields, state=state, error=error)
            _apply_submission(job)
            job["history"] = (job["history"] + [{"state": state, "at": now}])[-MAX_HISTORY:]
            job["timestamps"] = {**job["timestamps"], state: now, "updated": now}
            self._write(self.job_dir(request_id), job)
        return job

    def record(self, request_id: str, *, token: str | None = None,
               **fields: Any) -> dict[str, Any]:
        """Persist attempts, worker ownership, progress, or artifacts without a state change."""
        with self.lock:
            job = self._checked(request_id, token, fields)
            job.update(fields)
            _apply_submission(job)
            job["timestamps"] = {**job["timestamps"], "updated": self.clock()}
            self._write(self.job_dir(request_id), job)
        return job

    def _all(self) -> list[dict[str, Any]]:
        return [json.loads(json.dumps(job)) for job in self._index.values()]

    def _checked(self, request_id: str, token: str | None,
                 fields: Mapping[str, Any]) -> dict[str, Any]:
        reserved = set(fields) & _RESERVED_FIELDS
        if reserved:
            raise ItemJobError("invalid_field", f"reserved fields: {sorted(reserved)}")
        job = self._read(self.job_dir(request_id))
        if job is None:
            raise ItemJobError("unknown_job")
        worker = job.get("worker")
        # Only the current token may publish. Fencing never authorizes an overlapping process.
        if worker and token != worker["token"]:
            raise ItemJobError("stale_token")
        return job

    def _read(self, directory: Path) -> dict[str, Any] | None:
        try:
            job = json.loads((directory / "job.json").read_text())
        except (OSError, json.JSONDecodeError):
            return None
        # A newer or unknown record must be refused, never misread as an older one.
        if not isinstance(job, dict) or job.get("schema_version") != JOB_SCHEMA_VERSION:
            raise ItemJobError("unsupported_job_schema",
                               f"job record schema {job.get('schema_version') if isinstance(job, dict) else None} "
                               f"is not version {JOB_SCHEMA_VERSION}")
        return job

    def _write(self, directory: Path, job: Mapping[str, Any]) -> None:
        _write_json(directory / "job.json", job)
        # The index is authoritative for this process: it holds the writer lock.
        stored = json.loads(json.dumps(job))
        self._index[job["request_id"]] = stored
        if is_open(stored):
            self._open.add(job["request_id"])
        else:
            self._open.discard(job["request_id"])
        self.revision += 1


class ItemJobRunner:
    """One background scheduling thread. It owns at most one child per stage."""

    def __init__(self, store: ItemJobStore, commands: Mapping[str, Callable[..., list[str]]], *,
                 runtime_lock_path: Path, lease_s: float = LEASE_S,
                 catalog_provider: Callable[[], Mapping[str, Any]] | None = None,
                 policy_provider: Callable[[], Mapping[str, Any]] | None = None):
        self.store = store
        self.commands = dict(commands)
        # The latest catalog and the latest live policy, read when a training turn begins.
        # live.py injects the engine's current reject classes; tests inject a fake.
        self.catalog_provider = catalog_provider or object_catalog.load_catalog
        # None means no authoritative policy. A runner without an injected provider never
        # trains, rather than training against a guessed Keep all.
        self.policy_provider = policy_provider or (lambda: None)
        # The queue never takes this lock. render_suite.py does, and the queue reads exit 75.
        self.runtime_lock_path = Path(runtime_lock_path)
        self.lease_s = float(lease_s)
        self.term_wait_s = TERM_WAIT_S
        self.kill_wait_s = KILL_WAIT_S
        self.lock_backoff_s = LOCK_BACKOFF_S
        self.slots: dict[str, str | None] = {stage: None for stage in self.commands}
        self.children: dict[str, dict[str, Any]] = {}
        # An unconfirmed child leaves the scheduling map but stays owned, so shutdown
        # still terminates and reaps it instead of leaving it to init.
        self.blocked_children: dict[str, dict[str, Any]] = {}
        self.backoff: dict[str, float] = {}
        self.unavailable: dict[str, str] = {}
        # A repeating fault appends at 4 Hz, so the record must be bounded.
        self.recent_faults: deque[str] = deque(maxlen=MAX_RECENT_FAULTS)
        self.fault_total = 0
        self.last_fault: str | None = None
        self.unhealthy_shutdown = False
        self._clean_passes = 0
        # slots, children, backoff, and unavailable are shared with the HTTP threads.
        # Every read, modify, write of them holds this guard. Child waits stay outside it.
        self._guard = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def provider_mode(self) -> str:
        return self.store.provider_mode

    # Scheduling ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise ItemJobError("runner_started")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="item-jobs", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> bool:
        """Confirm the thread ended before any caller closes the store or drops the lock."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                # An unconfirmed thread may still write. Never report a clean stop.
                self.unhealthy_shutdown = True
                self._fault('runner_thread_join_timeout')
                return False
            self._thread = None
        self.shutdown()
        return True

    def health(self) -> dict[str, Any]:
        """Queue liveness for /health. A valid temporary child is never an extra engine."""
        with self._guard:
            children: dict[str, int] = {stage: 0 for stage in self.commands}
            for entry in list(self.children.values()):
                children[entry['stage']] = children.get(entry['stage'], 0) + 1
            blocked = sorted(self.unavailable)
            recent = list(self.recent_faults)
            total, last = self.fault_total, self.last_fault
        return {
            'runner_thread_alive': bool(self._thread is not None and self._thread.is_alive()),
            'active_children': children,
            'worker_unavailable_stages': blocked,
            'unhealthy_shutdown': self.unhealthy_shutdown,
            'provider_mode': self.provider_mode,
            # Short codes only. A fault must never publish a host path.
            'fault_total': total,
            'last_fault': last,
            'recent_faults': recent,
        }

    def _fault(self, code: str) -> None:
        """The one way a fault is recorded. The total is monotonic, the record bounded."""
        with self._guard:
            self.fault_total += 1
            self.last_fault = code
            self.recent_faults.append(code)
            self._clean_passes = 0

    def _note_clean_pass(self) -> None:
        """A transient fault clears after FAULT_CLEAR_PASSES clean passes.

        fault_total stays monotonic, so an operator still sees that it happened.
        """
        with self._guard:
            if self.last_fault is None:
                return
            self._clean_passes += 1
            if self._clean_passes >= FAULT_CLEAR_PASSES:
                self.last_fault = None
                self.recent_faults.clear()
                self._clean_passes = 0

    def _loop(self) -> None:
        try:
            self.recover()
        except Exception as error:  # A recovery fault must not kill the thread silently.
            self._fault(f"recover_failed_{type(error).__name__}")
        while not self._stop.is_set():
            try:
                self.step()
            except Exception as error:
                self._fault(f"step_failed_{type(error).__name__}")
            self._stop.wait(STEP_S)

    def step(self) -> None:
        """One scheduling pass. A blocked or failed job never stops a later job."""
        self._reap()
        for job in self.store.open_jobs():
            with self._guard:
                stage = self._ready_stage(job)
                free = stage is not None and self.slots.get(stage) is None
            if free:
                self._launch(job, stage)
        self._note_clean_pass()

    def _ready_stage(self, job: Mapping[str, Any]) -> str | None:
        """Called under the runner guard."""
        if job.get("worker") or self.backoff.get(job["request_id"], 0) > time.monotonic():
            return None
        for stage, table in STAGES.items():
            if (stage in self.commands and job["state"] in table["ready_states"]
                    and job["attempts"].get(stage, 0) < MAX_ATTEMPTS):
                return stage
        return None

    def _launch(self, job: Mapping[str, Any], stage: str) -> None:
        table = STAGES[stage]
        request_id = job["request_id"]
        job_dir = self.store.job_dir(request_id)
        prepare = table.get("prepare")
        if prepare is not None:
            # A stage may need in-process work before its child exists. None means the
            # job moved elsewhere, so no attempt is consumed and no child starts.
            job = getattr(self, prepare)(job, job_dir)
            if job is None:
                return
        # No child exists until Popen returns, so a fault in here holds the turn with
        # nothing running. The guard gives it back.
        with self._release_turn_on_fault(request_id, stage):
            attempts = {**job["attempts"], stage: job["attempts"].get(stage, 0) + 1}
            # The builder reads the record as it stands before this launch, so it can see
            # an unconsumed operator grant. Nothing else can produce `--live`.
            pending = {**job, "attempts": attempts}
            fields: dict[str, Any] = {"attempts": attempts, "progress": None, "reason": None}
            if table["provider_backed"]:
                # Only the stage the grant names consumes it. Another stage leaves it alone.
                if live_permitted(pending, self.provider_mode, stage):
                    fields["provider_submission"] = "in_flight"
                    fields["provider_permission"] = None
                    fields["provider_permission_stage"] = None
                elif job.get("provider_permission_stage") in (None, stage):
                    fields["provider_permission"] = None
                    fields["provider_permission_stage"] = None
                # A stale status from a hard-killed attempt must never label this one.
                with contextlib.suppress(OSError):
                    (job_dir / "provider_status.json").unlink()
            try:
                argv = self.commands[stage](pending, job_dir)
            except Exception as error:
                if stage != "training":
                    raise
                # No child exists. Count this training attempt, then use the same bounded
                # retry and terminal-failure transition as a failed trainer process.
                self._fault(f"training_command_failed_{type(error).__name__}")
                failed = self.store.record(request_id, attempts=attempts)
                self._stage_failure(failed, stage, table["failure_error"], None,
                                    reason="training_command_failed",
                                    progress=_short_reason(error))
                return
            token = str(uuid.uuid4())
            # Persist the attempt and the consumed grant before the process can exist.
            self.store.transition(request_id, table["running_state"], error=None, **fields)
            log_path = job_dir / f"{stage}.log"
            # An intent record before the spawn lets recover() find an untracked child.
            intent = {"stage": stage, "pid": None, "pgid": None, "token": token,
                      "lease_deadline": _lease_deadline(self.lease_s),
                      "started": self.store.clock(),
                      "pid_start": None, "log": str(log_path)}
            self.store.record(request_id, worker=intent)
            log = log_path.open("a")
            try:
                child = subprocess.Popen(argv, start_new_session=True, stdout=log,
                                         stderr=subprocess.STDOUT, cwd=str(job_dir),
                                         env=_child_environment(job_dir))
            except OSError:
                log.close()
                _remove_child_temp(request_id)
                self._fault(f"{stage}_launch_failed")
                self._stage_failure(self.store.get(request_id), stage,
                                    table["failure_error"], token)
                return
        # The child exists from here on, so its group may live and the turn must stay.
        pgid = os.getpgid(child.pid)
        # Track the child in memory first. A later write failure can never orphan it.
        with self._guard:
            self.slots[stage] = request_id
            self.children[request_id] = {
                "child": child, "log": log, "stage": stage, "pgid": pgid, "token": token,
                "deadline": time.monotonic() + self.lease_s}
        try:
            self.store.record(request_id, token=token, worker={
                **intent, "pid": child.pid, "pgid": pgid, "pid_start": _pid_start(child.pid)})
        except ItemJobError:
            # Recovery cannot confirm ownership without this record, so health must show it.
            self._fault(f"{stage}_ownership_write_failed")
        if stage == "training":
            # Bind the group to the lease, so a contest confirms liveness, not a clock.
            self._record_training_pgid(request_id, pgid, child.pid)

    def _reap(self) -> None:
        with self._guard:
            pending = list(self.children.items())
        for request_id, entry in pending:
            code = entry["child"].poll()
            if code is None:
                if time.monotonic() >= entry["deadline"]:
                    self._expire(request_id, entry)
                continue
            self._finish(request_id, entry, code)

    def _finish(self, request_id: str, entry: Mapping[str, Any], code: int) -> None:
        """The wrapper exited. The stage is free only once its whole group is gone.

        A stage wrapper starts descendants: item_job_render.py starts render_suite.py,
        which starts Blender. Releasing on the wrapper exit alone would let a second
        renderer overlap a live descendant.
        """
        gone, terminated = self._settle_group(entry["pgid"], child=entry["child"])
        self._release(request_id, entry, free_slot=gone)
        if not gone:
            self._block_stage(request_id, entry,
                              "the owned process group did not confirm its exit")
            return
        _remove_child_temp(request_id)
        if terminated:
            # A descendant outlived the wrapper, so the artifacts may be incomplete.
            stage = entry["stage"]
            self._stage_failure(self.store.get(request_id), stage,
                                STAGES[stage]["failure_error"], entry["token"],
                                progress="a descendant outlived the stage wrapper")
            return
        self._settle(request_id, entry["stage"], entry["token"], code)

    def _settle_group(self, pgid: int, *, child: subprocess.Popen | None = None,
                      pid: int | None = None) -> tuple[bool, bool]:
        """The one way any path confirms an owned process group left.

        Returns (gone, terminated). terminated is True when a member had to be
        signalled, so the attempt can never count as a success.
        """
        if self._group_gone(pgid, None if child is not None else pid):
            return True, False
        return self._terminate(pgid, child, pid), True

    def _block_stage(self, request_id: str, entry: Mapping[str, Any], reason: str) -> None:
        """Keep the slot, expose worker_unavailable, and start no replacement.

        The training lease stays with this job on purpose: its group is unconfirmed, so a
        second trainer must never start.
        """
        with self._guard:
            self.unavailable[entry["stage"]] = request_id
            self.blocked_children[request_id] = entry
        self.store.transition(request_id, "worker_unavailable", token=entry["token"],
                              error="worker_unavailable", reason="worker_unconfirmed",
                              progress=reason)

    def _release(self, request_id: str, entry: Mapping[str, Any], *, free_slot: bool) -> None:
        with contextlib.suppress(Exception):
            entry["child"].poll()
        with contextlib.suppress(Exception):
            entry["log"].close()
        with self._guard:
            self.children.pop(request_id, None)
            if free_slot and self.slots.get(entry["stage"]) == request_id:
                self.slots[entry["stage"]] = None

    # Stage outcomes -----------------------------------------------------

    def _settle(self, request_id: str, stage: str, token: str | None, code: int) -> None:
        job = self.store.get(request_id)
        getattr(self, STAGES[stage]["settle"])(job, self.store.job_dir(request_id), token, code)

    def _settle_generation(self, job: Mapping[str, Any], job_dir: Path,
                           token: str | None, code: int) -> None:
        request_id = job["request_id"]
        status = _read_json(job_dir / "provider_status.json") or {}
        if code == EXIT_OK:
            if not (job_dir / "recipe.json").is_file():
                self._stage_failure(job, "generation", "generation_failed", token,
                                    progress="the generator produced no recipe")
                return
            self.store.transition(
                request_id, "waiting_for_render", token=token, error=None, worker=None,
                provider_submission=status.get("provider_submission",
                                               job["provider_submission"]),
                provider_cache_hit=bool(status.get("cache_hit")),
                provider_evidence=_read_json(job_dir / "provider_evidence.json"))
            return
        if code == EXIT_NOT_SUBMITTED:
            # A cache miss stops before submission. It never consumes an attempt.
            self._unconsumed(job, "generation", token, "operator_required",
                             "provider_cache_miss")
            return
        if code == EXIT_UNCERTAIN:
            self._unconsumed(job, "generation", token, "interrupted_uncertain",
                             "provider_interrupted", provider_submission="uncertain")
            return
        if code == EXIT_CREDENTIALS:
            self.store.transition(request_id, "failed", token=token,
                                  error="credentials_missing", worker=None)
            return
        if code == EXIT_CACHE_ENTRY_INVALID:
            # Never a miss that could lead to a paid request, and never a hit.
            self._stage_failure(job, "generation", "generation_failed", token,
                                progress="cache_entry_invalid")
            return
        if status.get("provider_submission") == "not_submitted":
            self._stage_failure(job, "generation", "generation_failed", token)
            return
        self._unconsumed(job, "generation", token, "interrupted_uncertain",
                         "provider_interrupted", provider_submission="uncertain")

    def _lock_busy(self, job: Mapping[str, Any], stage: str, token: str | None) -> None:
        """Exit 75 from any stage: the visible waiting state, and no attempt consumed.

        The queue never takes the runtime lock. The process that does the heavy work
        takes it, and the queue only reacts to this exit code with a bounded backoff.
        """
        request_id = job["request_id"]
        counts = dict(job.get("lock_busy_count") or {})
        count = min(counts.get(stage, 0) + 1, len(self.lock_backoff_s))
        counts[stage] = count
        wait = self.lock_backoff_s[count - 1]
        with self._guard:
            self.backoff[request_id] = time.monotonic() + wait
        attempts = {**job["attempts"], stage: max(0, job["attempts"].get(stage, 0) - 1)}
        artifacts = {**job["artifacts"], "runtime_lock": str(self.runtime_lock_path)}
        if stage == "training":
            # The turn is given back while this job waits, so another job may train.
            self._release_training_lease(request_id)
        self.store.transition(request_id, STAGES[stage]["waiting_state"], token=token,
                              error=None, worker=None, attempts=attempts, artifacts=artifacts,
                              lock_busy_count=counts, reason="runtime_lock_busy",
                              progress=f"runtime lock busy, retrying in {wait:g} s")

    def _settle_render(self, job: Mapping[str, Any], job_dir: Path,
                       token: str | None, code: int) -> None:
        request_id = job["request_id"]
        if code == STAGES["render"]["lock_busy_exit"]:
            self._lock_busy(job, "render", token)
            return
        if code == EXIT_OK and self._previews_valid(job, job_dir):
            # Record the artifact identity now. The preview route serves only these bytes.
            artifacts = {**job["artifacts"], "previews": _preview_evidence(job_dir)}
            self.store.transition(request_id, "preview_ready", token=token, error=None,
                                  worker=None, artifacts=artifacts,
                                  display_name=_recipe_name(job_dir) or job.get("display_name"))
            return
        self._stage_failure(job, "render", "render_failed", token)

    def _settle_physics_proposal(self, job: Mapping[str, Any], job_dir: Path,
                                 token: str | None, code: int) -> None:
        """The provider rules of generation, applied to the physics description."""
        request_id = job["request_id"]
        status = _read_json(job_dir / "provider_status.json") or {}
        if code == EXIT_OK:
            if not (job_dir / "definition.json").is_file():
                self._stage_failure(job, "physics_proposal", "physics_proposal_failed", token,
                                    reason="definition_missing",
                                    progress="the physics stage produced no definition")
                return
            # The same settlement as generation: a finished call must not stay in_flight.
            self.store.transition(
                request_id, "validating_physics", token=token, error=None, worker=None,
                artifacts={**job["artifacts"], "physics": _read_json(job_dir / "physics.json")},
                provider_submission=status.get("provider_submission",
                                               job["provider_submission"]),
                provider_cache_hit=bool(status.get("cache_hit")))
            return
        if status.get("provider_submission") == "completed":
            # The provider answered and a later step failed. That is known evidence, so
            # it is never uncertain, never unconsumed, and never "no submission". The
            # request identity stays with it.
            self._stage_failure(
                job, "physics_proposal", "physics_proposal_failed", token,
                reason="physics_proposal_failed", provider_submission="completed",
                artifacts={**job["artifacts"], "physics": _read_json(job_dir / "physics.json")},
                **_status_progress(status))
            return
        if code == EXIT_NOT_SUBMITTED:
            self._unconsumed(job, "physics_proposal", token, "operator_required",
                             "provider_cache_miss")
            return
        if code == EXIT_UNCERTAIN:
            self._unconsumed(job, "physics_proposal", token, "interrupted_uncertain",
                             "provider_interrupted", provider_submission="uncertain")
            return
        if code == EXIT_CREDENTIALS:
            self.store.transition(request_id, "failed", token=token,
                                  error="credentials_missing", worker=None)
            return
        if code == EXIT_CACHE_ENTRY_INVALID:
            self._stage_failure(job, "physics_proposal", "physics_proposal_failed", token,
                                reason="cache_entry_invalid")
            return
        if status.get("provider_submission") == "not_submitted":
            # A known failure that sent nothing. The child's short reason shows why.
            self._stage_failure(job, "physics_proposal", "physics_proposal_failed", token,
                                reason="physics_proposal_failed", **_status_progress(status))
            return
        self._unconsumed(job, "physics_proposal", token, "interrupted_uncertain",
                         "provider_interrupted", provider_submission="uncertain")

    def _settle_physics(self, job: Mapping[str, Any], job_dir: Path,
                        token: str | None, code: int) -> None:
        """One verdict decides: accept continues, anything else blocks with its evidence."""
        request_id = job["request_id"]
        if code == STAGES["physics"]["lock_busy_exit"]:
            self._lock_busy(job, "physics", token)
            return
        result = _read_json(job_dir / "physics" / "result.json")
        if code != EXIT_OK or not isinstance(result, Mapping):
            # A crashed validator gets the one safe retry, then a retained failure.
            self._stage_failure(job, "physics", "physics_unsupported", token,
                                reason="validator_error",
                                progress="the route validator did not report a verdict")
            return
        artifacts = {**job["artifacts"], "physics_route": result}
        if result.get("verdict") != "accept":
            self.store.transition(request_id, "physics_blocked", token=token,
                                  error="physics_unsupported", worker=None,
                                  artifacts=artifacts,
                                  reason=str(result.get("reason") or "physics_unsupported"),
                                  progress=str(result.get("detail") or "") or None)
            return
        if job.get("provider_mode") == "fake":
            # A fake job never reaches activation, and the store refuses that state anyway.
            self.store.transition(request_id, "failed", token=token, error="generation_failed",
                                  worker=None, artifacts=artifacts,
                                  reason="fake_provider_not_activatable",
                                  progress="a fake provider job can never activate")
            return
        self.store.transition(request_id, "selecting_training_baseline", token=token,
                              error=None, worker=None, artifacts=artifacts)

    def _prepare_training(self, job: Mapping[str, Any], job_dir: Path) -> dict[str, Any] | None:
        """Take the training turn, then bind the baseline that exists at this moment.

        A queued job never carries an admission-time catalog into training: it reads the
        latest catalog and the latest policy here. Returning None releases the turn so a
        later job can proceed.
        """
        request_id = job["request_id"]
        if not self._take_training_lease(request_id):
            # Another job holds the turn. Say so, or the job looks stalled with no reason.
            if job.get("reason") != "waiting_for_training_turn":
                self.store.record(request_id, reason="waiting_for_training_turn",
                                  progress="waiting for the training turn")
            return None
        if job["state"] not in ("selecting_training_baseline", "waiting_for_replacement"):
            return dict(job)
        try:
            policy = self.policy_provider()
            if policy is None:
                # No authoritative engine policy yet. Waiting is the only honest option: an
                # absent policy must never bind Keep all, and no attempt is consumed.
                self._release_training_lease(request_id)
                if job.get("reason") != "waiting_for_engine_policy":
                    self.store.record(request_id, reason="waiting_for_engine_policy",
                                      progress="waiting for the live policy")
                return None
            # One policy read per turn, so the baseline binds exactly what was checked.
            baseline = self._select_baseline(job, job_dir, policy)
        except Exception as error:
            self._release_training_lease(request_id)
            self._fault(f"training_baseline_failed_{type(error).__name__}")
            # Consume the attempt here, or a permanent binding error would loop.
            attempts = {**job["attempts"], "training": job["attempts"].get("training", 0) + 1}
            self.store.record(request_id, attempts=attempts)
            self._stage_failure(self.store.get(request_id), "training", "training_failed", None,
                                retry_state="selecting_training_baseline",
                                reason="training_baseline_failed", progress=_short_reason(error))
            return None
        if baseline is None:
            # No Keep type is free. Release the turn so the next job continues.
            self._release_training_lease(request_id)
            self.store.transition(request_id, "waiting_for_replacement", error=None,
                                  worker=None, reason="no_keep_victim",
                                  progress="no Keep type is available to replace")
            return None
        return self.store.transition(request_id, "queued_for_training", error=None,
                                     worker=None, reason=None, progress=None,
                                     training_baseline=baseline,
                                     victim=baseline["victim_id"])

    def _select_baseline(self, job: Mapping[str, Any], job_dir: Path,
                         policy: Mapping[str, Any]) -> dict[str, Any] | None:
        """Bind the latest catalog and the given policy, and build the candidate catalog."""
        catalog = self.catalog_provider()
        reject_classes = [str(name) for name in (policy.get("reject_classes") or [])]
        victim_id = object_catalog.select_victim(catalog, reject_classes)
        if victim_id is None:
            return None
        draft = _read_json(job_dir / "definition.json")
        if not isinstance(draft, Mapping):
            raise ItemJobError("invalid_request", "the job has no validated definition")
        recipe = _read_json(job_dir / "recipe.json") or {}
        definition = object_catalog.type_definition_from_draft(
            draft, rgb=object_catalog.recipe_rgb(recipe), prior=CANDIDATE_PRIOR,
            source_sha256=hashlib.sha256((job_dir / "recipe.json").read_bytes()).hexdigest())
        candidate = object_catalog.candidate_catalog(catalog, definition, victim_id)
        paths = _training_paths(job_dir)
        if paths["catalog"].exists():
            shutil.rmtree(paths["catalog"])
        object_catalog.write_catalog(paths["catalog"], candidate)
        # The queue writes the policy the closed-loop run must apply: the survivors' reject
        # classes. The new label is subtracted explicitly, because activation always adds
        # it as Keep, even when the live policy happens to name that label today.
        new_label = definition["classifier_label"]
        survivors = sorted(set(reject_classes)
                           & set(object_catalog.catalog_labels(candidate)) - {new_label})
        _write_json(paths["policy"], {"reject_classes": survivors,
                                      "policy_version": policy.get("policy_version")})
        return {"catalog_revision": catalog["catalog_revision"],
                "active_type_ids": list(catalog["active_type_ids"]),
                "policy_version": policy.get("policy_version"),
                # The exact policy the candidate trained for, kept as evidence beside the
                # policy that validation later observed. Neither one gates activation.
                "reject_classes": sorted(reject_classes),
                "victim_id": victim_id}

    def _settle_training(self, job: Mapping[str, Any], job_dir: Path,
                         token: str | None, code: int) -> None:
        request_id = job["request_id"]
        if code == STAGES["training"]["lock_busy_exit"]:
            self._lock_busy(job, "training", token)
            return
        if code != EXIT_OK:
            self._release_training_lease(request_id)
            self._stage_failure(job, "training", "training_failed", token)
            return
        self.store.transition(request_id, "validating_candidate", token=token, error=None,
                              worker=None)
        # `_settle` runs only after `_finish` confirmed the group left. A validation fault
        # therefore follows the normal bounded training retry path and releases the turn.
        try:
            self._validate_candidate(self.store.get(request_id), job_dir, token)
        except Exception as error:
            self._fault(f"candidate_validation_failed_{type(error).__name__}")
            self._stage_failure(self.store.get(request_id), "training", "training_failed",
                                token, reason="candidate_validation_error",
                                progress=_short_reason(error))
            return
        self._release_training_lease(request_id)

    def _validate_candidate(self, job: Mapping[str, Any], job_dir: Path,
                            token: str | None) -> None:
        """Read the trainer verdict, then re-check the catalog and the victim.

        The live reject policy is recorded, never compared. Only a moved catalog or a
        victim that is no longer eligible blocks activation.
        """
        request_id = job["request_id"]
        validation = _read_json(_training_paths(job_dir)["validation"])
        if not isinstance(validation, Mapping):
            self._stage_failure(job, "training", "training_failed", token,
                                reason="validation_missing",
                                progress="the trainer wrote no validation")
            return
        artifacts = {**job["artifacts"], "candidate_validation": validation}
        missing = incomplete_validation(validation)
        if missing:
            # A partial record never counts as passed. Absence of evidence is not evidence.
            self.store.transition(request_id, "failed", token=token,
                                  error="candidate_validation_failed", worker=None,
                                  artifacts=artifacts, reason="validation_incomplete",
                                  progress=f"missing evidence: {', '.join(missing)}")
            return
        # `passed` is honoured only with an empty failure list beside it.
        if validation.get("passed") is not True or list(validation.get("failures") or []):
            self.store.transition(request_id, "failed", token=token,
                                  error="candidate_validation_failed", worker=None,
                                  artifacts=artifacts, reason="candidate_gate_failed",
                                  progress=", ".join(validation.get("failures") or []) or None)
            return
        baseline = job.get("training_baseline") or {}
        catalog = self.catalog_provider()
        policy = self.policy_provider()
        if policy is None:
            # The engine published no policy, so nothing can confirm the baseline holds.
            self.store.transition(request_id, "activation_conflict", token=token,
                                  error="replacement_conflict", worker=None,
                                  artifacts=artifacts, reason="engine_policy_unavailable",
                                  progress="the live policy is unavailable")
            return
        reject_classes = [str(name) for name in (policy.get("reject_classes") or [])]
        # Evidence only. A survivor toggle stays usable during training and during the
        # drain, so validation never requires this policy to equal the training baseline.
        # Activation captures the latest survivor policy under its own fence.
        artifacts = {**artifacts, "validation_policy": {
            "policy_version": policy.get("policy_version"),
            "reject_classes": sorted(reject_classes)}}
        victim_id = object_catalog.select_victim(catalog, reject_classes)
        # The plan names both: a moved catalog is an activation conflict, a moved victim
        # is a replacement conflict.
        if catalog["catalog_revision"] != baseline.get("catalog_revision"):
            self.store.transition(request_id, "activation_conflict", token=token,
                                  error="catalog_revision_conflict", worker=None,
                                  artifacts=artifacts, reason="stale_catalog_revision",
                                  progress="the active catalog changed since training")
            return
        if victim_id != baseline.get("victim_id"):
            self.store.transition(request_id, "activation_conflict", token=token,
                                  error="replacement_conflict", worker=None,
                                  artifacts=artifacts, reason="victim_no_longer_eligible",
                                  progress="the replacement victim changed since training")
            return
        self.store.record(request_id, token=token, artifacts=artifacts, reason=None)

    # Training turn ------------------------------------------------------

    def _lease_path(self) -> Path:
        return self.store.root / TRAINING_LEASE

    def _take_training_lease(self, request_id: str) -> bool:
        """One trainer across every job and every process, beside the in-process slot.

        A contested lease is never taken on a clock alone. The previous owner's process
        group must be CONFIRMED gone with the same settle used for a worker lease, so a
        still-alive trainer never loses its turn to a second trainer. An unconfirmed group
        keeps the lease and exposes `worker_unavailable` for the training stage.

        The deadline is wall time on purpose: the lease must outlive a service restart,
        and `time.monotonic()` does not survive one. It only bounds an ABANDONED lease
        whose owner left no process group to confirm.
        """
        path = self._lease_path()
        with self._guard:
            current = _read_json(path)
            if isinstance(current, Mapping) and current.get("owner") not in (None, request_id):
                pgid = current.get("pgid")
                if pgid is not None:
                    gone, _ = self._settle_group(int(pgid), pid=current.get("pid"))
                    if not gone:
                        self.unavailable["training"] = current["owner"]
                        self._fault("training_lease_owner_unconfirmed")
                        return False
                elif float(current.get("deadline") or 0) > time.time():
                    # No group to confirm yet. Only the bound may release it.
                    return False
            self.unavailable.pop("training", None)
            # The group is bound by `_record_training_pgid` once the child exists. No store
            # read happens under this guard: the store lock is always taken first elsewhere,
            # and taking them in the other order here could deadlock an HTTP thread.
            _write_json(path, {"owner": request_id, "deadline": time.time() + self.lease_s,
                               "pgid": None, "pid": None})
        return True

    def _record_training_pgid(self, request_id: str, pgid: int, pid: int) -> None:
        """Bind the running group to the lease, so a contest can confirm liveness."""
        with self._guard:
            current = _read_json(self._lease_path())
            if isinstance(current, Mapping) and current.get("owner") == request_id:
                _write_json(self._lease_path(), {**current, "pgid": pgid, "pid": pid})

    def _release_training_lease(self, request_id: str) -> None:
        """The one way the turn goes back. Every path that ends or pauses training calls it."""
        with self._guard:
            current = _read_json(self._lease_path())
            if isinstance(current, Mapping) and current.get("owner") == request_id:
                with contextlib.suppress(OSError):
                    self._lease_path().unlink()

    @contextlib.contextmanager
    def _release_turn_on_fault(self, request_id: str, stage: str):
        """Release a pre-child training turn after an unexpected launch fault."""
        try:
            yield
        except BaseException:
            if stage == "training":
                self._release_training_lease(request_id)
            raise

    def _previews_valid(self, job: Mapping[str, Any], job_dir: Path) -> bool:
        previews = job_dir / "previews"
        if not all((previews / name).is_file() for name in PREVIEW_FILES):
            return False
        try:
            recipe = hashlib.sha256((job_dir / "recipe.json").read_bytes()).hexdigest()
        except OSError:
            return False
        record = _read_json(previews / "render.json")
        if not record or record.get("recipe_sha256") != recipe:
            return False
        # The rendered recipe must be the recorded provider artifact when one exists.
        evidence = job.get("provider_evidence") or {}
        return evidence.get("recipe_sha256") in (None, recipe)

    def _stage_failure(self, job: Mapping[str, Any], stage: str, error: str,
                       token: str | None, retry_state: str | None = None,
                       **fields: Any) -> None:
        """The single automatic retry, then a retained terminal failure."""
        retry = job["attempts"].get(stage, 0) < MAX_ATTEMPTS
        state = (retry_state or STAGES[stage]["retry_state"]) if retry else "failed"
        if stage == "training":
            # The turn goes back whether this ends in a retry or a failure.
            self._release_training_lease(job["request_id"])
        self.store.transition(job["request_id"], state, token=token, error=error,
                              worker=None, **fields)

    def _unconsumed(self, job: Mapping[str, Any], stage: str, token: str | None,
                    state: str, error: str, **fields: Any) -> None:
        """Block the job without consuming its one automatic retry.

        The blocking stage is recorded, so an operator decision resumes THAT stage and a
        one-time grant can only be spent there.
        """
        attempts = {**job["attempts"], stage: max(0, job["attempts"].get(stage, 0) - 1)}
        self.store.transition(job["request_id"], state, token=token, error=error,
                              worker=None, attempts=attempts, blocked_stage=stage, **fields)

    # Process group ownership --------------------------------------------

    def _group_gone(self, pgid: int, pid: int | None = None) -> bool:
        """A zombie member has exited. Reap only what this process owns."""
        if pid is not None:
            _reap_pid(pid)
        members = _group_members(pgid)
        if members is None:
            # No member listing available. Fall back to the signal probe.
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            return False
        for member_pid, state in members:
            if not state.startswith("Z"):
                return False
            _reap_pid(member_pid)
        return True

    def _terminate(self, pgid: int, child: subprocess.Popen | None = None,
                   pid: int | None = None) -> bool:
        """TERM, then KILL, then confirm the complete group left. Never under a lock."""
        # Popen.poll() reaps our own handle. waitpid is only for a recorded pid we own.
        owned = None if child is not None else pid
        for number, wait_s in ((signal.SIGTERM, self.term_wait_s),
                               (signal.SIGKILL, self.kill_wait_s)):
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.killpg(pgid, number)
            deadline = time.monotonic() + wait_s
            while time.monotonic() < deadline:
                if child is not None:
                    child.poll()
                if self._group_gone(pgid, owned):
                    return True
                time.sleep(POLL_S)
        if child is not None:
            child.poll()
        return self._group_gone(pgid, owned)

    def _expire(self, request_id: str, entry: Mapping[str, Any]) -> None:
        gone, _ = self._settle_group(entry["pgid"], child=entry["child"])
        self._release(request_id, entry, free_slot=gone)
        job = self.store.get(request_id)
        if not gone:
            # The slot stays occupied. No replacement process starts for this stage.
            self._block_stage(request_id, entry,
                              "the owned process group did not confirm its exit")
            return
        _remove_child_temp(request_id)
        self._timed_out(job, entry["stage"], entry["token"])

    def _timed_out(self, job: Mapping[str, Any], stage: str, token: str | None) -> None:
        if STAGES[stage]["provider_backed"]:
            # A timeout after launch leaves billing unknown, so it is never safe.
            self._unconsumed(job, stage, token, "interrupted_uncertain",
                             "provider_interrupted", provider_submission="uncertain")
        else:
            self._stage_failure(job, stage, "worker_timeout", token)

    def recover(self) -> None:
        """Reclaim persisted workers before the first scheduling pass."""
        for job in self.store.jobs():
            worker = job.get("worker")
            if not worker:
                continue
            request_id, stage = job["request_id"], worker["stage"]
            token = worker["token"]
            if not self._recover_group(worker):
                with self._guard:
                    self.slots[stage] = request_id
                    self.unavailable[stage] = request_id
                self.store.transition(request_id, "worker_unavailable", token=token,
                                      error="worker_unavailable",
                                      progress="a restart could not confirm the owned group exit")
                continue
            _remove_child_temp(request_id)
            if STAGES[stage]["provider_backed"] and job.get("provider_submission") == "in_flight":
                self._unconsumed(job, stage, token, "interrupted_uncertain",
                                 "provider_interrupted", provider_submission="uncertain")
            elif STAGES[stage]["provider_backed"]:
                self._stage_failure(job, stage, STAGES[stage]["failure_error"], token)
            else:
                self._stage_failure(job, stage, "worker_timeout", token)

    def _recover_group(self, worker: Mapping[str, Any]) -> bool:
        """Always confirm the group. Never signal a group this job cannot prove it owns."""
        pid, pgid = worker.get("pid"), worker.get("pgid")
        if pid is None or pgid is None:
            # A launch intent with no confirmed process. An untracked child may exist.
            return False
        marker = worker.get("pid_start")
        if self._group_gone(pgid, pid):
            return True
        if not (marker and _pid_start(pid) == marker):
            # The group is alive but ownership is unprovable. Never signal it.
            return False
        return self._settle_group(pgid, pid=pid)[0]

    def shutdown(self) -> None:
        """Terminate every owned process group. State recovery happens on the next start."""
        with self._guard:
            pending = list(self.children.items()) + list(self.blocked_children.items())
            self.blocked_children.clear()
        for request_id, entry in pending:
            if self._terminate(entry["pgid"], entry["child"]):
                _remove_child_temp(request_id)
            self._release(request_id, entry, free_slot=True)

    # Operator actions ---------------------------------------------------

    def resolve_provider(self, request_id: str, action: str) -> dict[str, Any]:
        """use_cache re-checks the cache. new_request permits one billable call in paid mode.

        The job returns to the stage that blocked it, never to an earlier one. An operator
        who approved a physics request therefore resumes the physics proposal, and no other
        stage can spend that grant.
        """
        if action not in ("use_cache", "new_request"):
            raise ItemJobError("invalid_action")
        if action == "new_request" and self.provider_mode != "paid":
            raise ItemJobError("paid_mode_disabled")
        with self.store.lock:
            job = self.store.get(request_id)
            if job["state"] not in ("operator_required", "interrupted_uncertain"):
                raise ItemJobError("not_available")
            stage = job.get("blocked_stage") or "generation"
            if stage not in STAGES:
                raise ItemJobError("not_available", f"unknown blocked stage: {stage}")
            grant = action == "new_request"
            return self.store.transition(
                request_id, STAGES[stage]["waiting_state"], error=None, progress=None,
                reason=None, provider_permission="new_request" if grant else None,
                provider_permission_stage=stage if grant else None)

    def resolve_replacement(self, request_id: str, action: str | None = None) -> dict[str, Any]:
        """Reserved for Phase 4. The job keeps its replacement conflict until then."""
        raise ItemJobError("not_available")

    def select_training_baseline(self, request_id: str) -> dict[str, Any]:
        """Phase 3 owns the real path. A fake job can never reach activation."""
        job = self.store.get(request_id)
        if job.get("provider_mode") == "fake":
            return self.store.transition(request_id, "failed", error="generation_failed",
                                         worker=None,
                                         progress="fake_provider_not_activatable")
        raise ItemJobError("not_available")

    def confirm_cleanup(self, request_id: str) -> dict[str, Any]:
        """Release an unavailable worker after the operator confirms its group left."""
        with self.store.lock:
            job = self.store.get(request_id)
            worker = job.get("worker")
            if job["state"] != "worker_unavailable" or not worker:
                raise ItemJobError("not_available")
            pgid = worker.get("pgid")
            if pgid is not None and not self._group_gone(pgid, worker.get("pid")):
                raise ItemJobError("worker_unavailable")
            stage, token = worker["stage"], worker["token"]
            with self._guard:
                if self.slots.get(stage) == request_id:
                    self.slots[stage] = None
                if self.unavailable.get(stage) == request_id:
                    del self.unavailable[stage]
                entry = self.blocked_children.pop(request_id, None)
            if entry is not None:
                self._release(request_id, entry, free_slot=False)
            _remove_child_temp(request_id)
            self._timed_out(job, stage, token)
            return self.store.get(request_id)


# Command builders --------------------------------------------------------


def generation_command(job: Mapping[str, Any], job_dir: Path, *, mode: str, provider_cache: Path,
                       generator_root: Path = GENERATOR_ROOT, env_file: Path | None = None,
                       source_revision: str | None = None) -> list[str]:
    """Build the generation argv. `--live` needs paid mode and an unconsumed grant."""
    argv = [sys.executable, str(HERE / "item_job_generate.py"),
            "--mode", mode,
            "--description", job["description"],
            "--out", str(job_dir / "generation" / "run"),
            "--provider-cache", str(provider_cache),
            "--generator-root", str(generator_root),
            "--recipe", str(job_dir / "recipe.json"),
            "--status", str(job_dir / "provider_status.json"),
            "--evidence", str(job_dir / "provider_evidence.json")]
    if source_revision:
        argv += ["--source-revision", source_revision]
    if mode == "paid":
        argv += ["--env-file", str(env_file)]
        if live_permitted(job, mode, "generation"):
            argv.append("--live")
    return argv


def render_command(job: Mapping[str, Any], job_dir: Path, *, runtime_lock: Path,
                   blender: str = "blender",
                   generator_root: Path = GENERATOR_ROOT) -> list[str]:
    return [sys.executable, str(HERE / "item_job_render.py"),
            "--render-suite", str(Path(generator_root) / "render_suite.py"),
            "--job-dir", str(job_dir), "--runtime-lock", str(runtime_lock),
            "--blender", blender]


def physics_proposal_command(job: Mapping[str, Any], job_dir: Path, *, mode: str,
                             provider_cache: Path, replay_dir: Path | None = None,
                             env_file: Path | None = None) -> list[str]:
    """Build the physics proposal argv. The provider rules equal the generation rules.

    The FULL job description is always passed. Reviewed replay metadata may substitute a
    shorter physics description inside the wrapper, and only when it binds to this job's
    own artifacts. No request field and no public route can reach this argv.
    """
    argv = [sys.executable, str(HERE / "item_job_physics.py"),
            "--job-dir", str(job_dir),
            "--description", job["description"],
            "--mode", mode,
            "--provider-cache", str(provider_cache)]
    if replay_dir is not None:
        argv += ["--replay-dir", str(replay_dir)]
    if mode == "paid":
        argv += ["--env-file", str(env_file)]
        if live_permitted(job, mode, "physics_proposal"):
            argv.append("--live")
    return argv


def physics_command(job: Mapping[str, Any], job_dir: Path, *, preset: Path,
                    runtime_lock: Path) -> list[str]:
    """Validate the draft against the seeded no-air route in a child process.

    The asset root is THIS job's directory, never the repository root: production job
    assets live outside the read-only source tree.
    """
    return [sys.executable, str(HERE / "validate_object_route.py"),
            "--definition", str(job_dir / "definition.json"),
            "--asset-root", str(job_dir),
            "--preset", str(preset),
            "--seed", "8", "--no-air", "--background-rate", "0",
            "--runtime-lock", str(runtime_lock),
            "--json-out", str(job_dir / "physics" / "result.json")]


def training_command(job: Mapping[str, Any], job_dir: Path, *, preset: Path,
                     runtime_lock: Path) -> list[str]:
    """Train one candidate against the catalog and the policy the queue just bound."""
    paths = _training_paths(job_dir)
    return [sys.executable, str(HERE / "train_candidate.py"),
            "--catalog-root", str(paths["catalog"]),
            "--preset", str(preset),
            "--out", str(paths["out"]),
            "--policy", str(paths["policy"]),
            "--runtime-lock", str(runtime_lock)]


def real_commands(*, mode: str, provider_cache: Path, runtime_lock: Path, preset: Path,
                  physics_replay: Path | None = None,
                  generator_root: Path = GENERATOR_ROOT, env_file: Path | None = None,
                  blender: str = "blender",
                  source_revision: str | None = None) -> dict[str, Callable[..., list[str]]]:
    """The provider and Blender adapters. A billable call needs an explicit operator grant."""
    return {
        "generation": lambda job, job_dir: generation_command(
            job, job_dir, mode=mode, provider_cache=provider_cache,
            generator_root=generator_root, env_file=env_file, source_revision=source_revision),
        "render": lambda job, job_dir: render_command(
            job, job_dir, runtime_lock=runtime_lock, blender=blender,
            generator_root=generator_root),
        "physics_proposal": lambda job, job_dir: physics_proposal_command(
            job, job_dir, mode=mode, provider_cache=provider_cache,
            replay_dir=physics_replay, env_file=env_file),
        "physics": lambda job, job_dir: physics_command(
            job, job_dir, preset=preset, runtime_lock=runtime_lock),
        "training": lambda job, job_dir: training_command(
            job, job_dir, preset=preset, runtime_lock=runtime_lock),
    }


def fake_commands(*, worker: Path = FAKE_WORKER, fixtures: Path = FIXTURE_ROOT,
                  stages: tuple[str, ...] | None = None) -> dict[str, Callable[..., list[str]]]:
    """Scenario-driven adapters. They never call a provider and never start Blender.

    `stages` selects a subset. The scheduler only runs a stage it has a command for, so a
    caller can exercise one part of the flow without the later stages starting.
    """
    def build(stage):
        def command(job, job_dir):
            return [sys.executable, str(worker), "--stage", stage,
                    "--job-dir", str(job_dir), "--fixtures", str(fixtures),
                    "--attempt", str(job["attempts"].get(stage, 0))]
        return command

    return {stage: build(stage) for stage in (stages or tuple(STAGES))}


# Helpers -----------------------------------------------------------------


def _apply_submission(job: dict[str, Any]) -> None:
    """Keep provider submission memory sticky. Nothing may erase a possible submission."""
    history = list(job.get("provider_submission_history") or [])
    requested = job.get("provider_submission") or "not_submitted"
    if requested == "not_submitted" and history and history[-1] == "completed":
        # A completed call is a known, billed fact about the JOB. A later attempt that only
        # replayed the cache reports `not_submitted` about itself, and cannot change that.
        requested = "completed"
    elif requested == "not_submitted" and any(item in SUBMITTED_EVER for item in history):
        requested = "uncertain"
    if not history or history[-1] != requested:
        history.append(requested)
    job["provider_submission"] = requested
    job["provider_submission_history"] = history[-MAX_HISTORY:]


def _training_paths(job_dir: Path) -> dict[str, Path]:
    """The one place that names the trainer's files under a job directory."""
    training = Path(job_dir) / "training"
    return {"root": training, "catalog": training / "catalog", "policy": training / "policy.json",
            "out": training / "out", "validation": training / "out" / "validation.json"}


def incomplete_validation(validation: Mapping[str, Any]) -> list[str]:
    """The evidence blocks a completed trainer run always writes.

    A record that lacks any of them is incomplete and can never be honoured as passed: a
    partial file must not become an activation. A COMPLETE record that reports failures is
    a different outcome, and the gate check reports that instead.
    """
    missing = [name for name in VALIDATION_EVIDENCE if validation.get(name) is None]
    if validation.get("failures") is None:
        missing.append("failures")
    return sorted(set(missing))


def _path_free_line(value: Any) -> str:
    """Child or exception text as one line that names no host path."""
    return re.sub(r"(/[^\s'\"]+)+", "<path>", " ".join(str(value).split()))


def _short_reason(error: BaseException, limit: int = 160) -> str:
    """One bounded line for an operator. It names the failure, never a host path."""
    text = _path_free_line(error)
    return f"{type(error).__name__}: {text}"[:limit] if text else type(error).__name__


def _status_progress(status: Mapping[str, Any], limit: int = 160) -> dict[str, str]:
    """The short reason a provider child recorded, as the progress field. Empty without one."""
    reason = status.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return {}
    return {"progress": _path_free_line(reason)[:limit]}


def _canonical_request_id(value: Any) -> str:
    if not isinstance(value, str):
        raise ItemJobError("invalid_request")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError(value)
    except (ValueError, AttributeError, TypeError):
        raise ItemJobError("invalid_request",
                           "request_id must be canonical lowercase UUID text") from None
    return value


def _normalize_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) - _PAYLOAD_FIELDS \
            or not _REQUIRED_FIELDS <= set(payload):
        raise ItemJobError("invalid_request")
    description = payload["description"]
    if not isinstance(description, str) or not 1 <= len(description.strip()) <= MAX_DESCRIPTION:
        raise ItemJobError("invalid_description")
    requester = payload.get("requester_name")
    if requester is not None:
        if not isinstance(requester, str) or len(requester.strip()) > MAX_REQUESTER:
            raise ItemJobError("invalid_request")
        requester = requester.strip() or None
    revision = payload["expected_catalog_revision"]
    if not isinstance(revision, str) or not _SHA256_RE.fullmatch(revision):
        raise ItemJobError("invalid_request")
    return {"request_id": _canonical_request_id(payload["request_id"]),
            "description": description.strip(), "requester_name": requester,
            "expected_catalog_revision": revision}


def _child_temp_root(request_id: str) -> Path:
    """The one ephemeral root of a job's children, below the configured temporary directory.

    The name derives from the request id alone, so no job record ever stores this path
    and a restart can still find and clean it.
    """
    return Path(tempfile.gettempdir()) / f"cinta-item-job-{request_id}"


def _child_environment(job_dir: Path) -> dict[str, str]:
    """Point every child home, cache, and temporary path below its ephemeral root.

    The job directory is persistent and holds only artifacts. Blender home, XDG, and
    temporary state is disposable, so it lives under the configured temporary directory
    with owner-only permissions. The service never mutates its own os.environ. The engine
    worker inherits that environment, so a provider value or a redirected cache must
    never land in it.
    """
    root = _child_temp_root(Path(job_dir).name)
    names = {"HOME": "home", "XDG_CACHE_HOME": "cache", "XDG_CONFIG_HOME": "config",
             "XDG_STATE_HOME": "state", "TMPDIR": "tmp"}
    # The root and each of the five fixed children take the same rule, so a symlink
    # planted at any of those names is refused and never followed.
    for directory in (root, *(root / name for name in names.values())):
        try:
            os.mkdir(directory, 0o700)
        except FileExistsError:
            # A leftover of this job is reused. Anything else at that name is refused.
            found = os.lstat(directory)
            if not stat.S_ISDIR(found.st_mode) or found.st_uid != os.getuid():
                raise OSError("a child temporary directory is not an owned directory") from None
            os.chmod(directory, 0o700)
    return {**os.environ, **{variable: str(root / name) for variable, name in names.items()}}


def _remove_child_temp(request_id: str) -> None:
    """Drop the ephemeral root. Call this only after the owned group is confirmed gone."""
    root = _child_temp_root(request_id)
    with contextlib.suppress(OSError):
        found = os.lstat(root)
        # Only an owned real directory. A symlink or a foreign entry is never followed.
        if stat.S_ISDIR(found.st_mode) and found.st_uid == os.getuid():
            shutil.rmtree(root, ignore_errors=True)


def _preview_evidence(job_dir: Path) -> dict[str, dict[str, Any]]:
    """Record the identity of every preview artifact at preview_ready."""
    evidence = {}
    for name in PREVIEW_FILES:
        data = (job_dir / "previews" / name).read_bytes()
        evidence[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return evidence


def read_preview(job: Mapping[str, Any], job_dir: Path, name: str) -> tuple[bytes, str]:
    """Open and verify one preview without a check-to-open substitution window.

    Every path component must be a real directory, the final component is opened
    with O_NOFOLLOW, and the bytes answered are the bytes read from that one
    descriptor. No message repeats a host path.
    """
    if name not in PREVIEW_CONTENT_TYPES:
        raise ItemJobError("unknown_preview")
    if "preview_ready" not in job["timestamps"]:
        raise ItemJobError("preview_unavailable")
    recorded = (job.get("artifacts") or {}).get("previews")
    entry = recorded.get(name) if isinstance(recorded, Mapping) else None
    if not isinstance(entry, Mapping) or entry.get("bytes") is None:
        raise ItemJobError("preview_unavailable")
    size = entry["bytes"]
    if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_PREVIEW_BYTES:
        raise ItemJobError("preview_unavailable")

    # Every decision comes from a descriptor chain, so no component can be substituted
    # between a check and the open. No path-based test remains on this route.
    directories: list[int] = []
    try:
        directories.append(os.open(job_dir.parent,
                                   os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW))
        for component in (job_dir.name, "previews"):
            directories.append(os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                       dir_fd=directories[-1]))
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directories[-1])
    except OSError:
        raise ItemJobError("preview_unavailable") from None
    finally:
        for opened in directories:
            os.close(opened)
    try:
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_size != size:
            raise ItemJobError("preview_unavailable")
        data = b""
        while len(data) <= size:
            chunk = os.read(descriptor, size + 1 - len(data))
            if not chunk:
                break
            data += chunk
    finally:
        os.close(descriptor)
    if len(data) != size or hashlib.sha256(data).hexdigest() != entry.get("sha256"):
        raise ItemJobError("preview_unavailable")
    return data, PREVIEW_CONTENT_TYPES[name]


def _write_json(path: Path, value: Any) -> None:
    """Replace one JSON file atomically from a temporary file in the same directory."""
    handle = tempfile.NamedTemporaryFile("w", dir=path.parent, prefix=".tmp-", delete=False)
    try:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.flush()
        os.fsync(handle.fileno())
        handle.close()
        os.replace(handle.name, path)
    except BaseException:
        handle.close()
        with contextlib.suppress(OSError):
            os.unlink(handle.name)
        raise


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _lease_deadline(lease_s: float) -> str:
    deadline = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=lease_s)
    return deadline.strftime("%Y-%m-%dT%H:%M:%SZ")


def _reap_pid(pid: int) -> None:
    """Collect one owned child. Never waitpid(-1): that would steal the engine worker."""
    with contextlib.suppress(ChildProcessError, OSError):
        os.waitpid(pid, os.WNOHANG)


def _group_members(pgid: int) -> list[tuple[int, str]] | None:
    """Every member of one process group with its state letter, or None when unknown."""
    if PROC_ROOT.is_dir():
        members = []
        for entry in PROC_ROOT.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                tail = (entry / "stat").read_text().rpartition(")")[2].split()
            except OSError:
                continue
            # After "pid (comm)" the fields start at 3: state, ppid, then pgrp.
            if len(tail) > 2 and tail[2] == str(pgid):
                members.append((int(entry.name), tail[0]))
        return members
    try:
        result = subprocess.run(["ps", "-o", "pid=,stat=", "-g", str(pgid)],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    members = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) >= 2 and fields[0].isdigit():
            members.append((int(fields[0]), fields[1]))
    return members


def _proc_start(text: str) -> str | None:
    """Field 22 of /proc/<pid>/stat. The command name can hold spaces and brackets."""
    tail = text.rpartition(")")[2].split()
    # Removing "pid (comm)" leaves field 3 at index 0, so field 22 is index 19.
    return tail[19] if len(tail) > 19 else None


def _pid_start(pid: int) -> str | None:
    """The process start marker. It separates a live owner from a recycled pid."""
    if PROC_ROOT.is_dir():
        # A slim Linux image may carry no ps. /proc always answers there.
        try:
            return _proc_start((PROC_ROOT / str(pid) / "stat").read_text())
        except (OSError, ValueError):
            return None
    try:
        result = subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _recipe_name(job_dir: Path) -> str | None:
    """Read the generated name as data. A recipe is never imported or executed."""
    recipe = _read_json(job_dir / "recipe.json")
    name = (recipe or {}).get("name")
    return name[:120] if isinstance(name, str) and name.strip() else None
