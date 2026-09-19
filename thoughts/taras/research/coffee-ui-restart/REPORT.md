# Restart from the live page

Taras can restart the shared session from the page. The service stops the old worker before it starts a new worker.
Every connected browser receives the new session. Each restart preserves earlier evidence in its original directory.
No engine, model, policy, preset, or rendering asset changed in this increment.
Taras retains functional QA and acceptance.

## Reported failure

The updated page initially reached an older running service without the restart endpoint.
That service returned `404: Not Found`. The page attempted to parse that plain text as JSON.
The resulting error was `Unexpected non-whitespace character after JSON at position 3 (line 1 column 4)`.

The page now requires the server's explicit `restart_supported` capability before it enables the button.
It also handles non-JSON responses with a readable error. The running service must use this increment.

## Verification

Syntax and whitespace checks passed:

```bash
cd /private/tmp/hackspain-coffee-core
.venv-coffee/bin/python -m py_compile sim/coffee_sorter/live.py
node --check sim/coffee_sorter/live_web/live.js
git diff --check
```

The [bounded lifecycle result](lifecycle.json) used 0.7 simulated seconds on development seed 8.
The worker acknowledged an injection and completed the bounded session.
Two connected clients then received the same fresh session at time zero.
Concurrent restart requests returned 200 and 409. Stale requests returned 409. Invalid input returned 400.
Missing and foreign origins returned 403. The previous report retained the same SHA-256 hash.

The [browser result](browser.json) records another restart through the actual control.
The session changed from `5b149900` to `5b7e2833`. The old worker process, PID 61502, exited.
The page reached Ready with both controls enabled and no visible error.
The [screenshot](ready-after-restart.png) shows that state.

An earlier probe exposed an unbounded close operation for a slow WebSocket client.
The service now limits that close operation to 0.5 seconds. The final probe drained both clients and completed.
The [failure-recovery record](failure-recovery.txt) covers queue draining, pending acknowledgments, initialization failure, cancellation, failed pumps, and cleanup errors.
Those additional probes used fake workers and did not start MuJoCo.

Separate Spec and Standards reviews found no remaining actionable findings after the fixes.
These checks establish restart behavior. They do not establish sorting quality, runtime acceptance, or Taras's functional acceptance.
The quality task confirmed its timed runs ended before the final bounded probe began.

## Manual checkpoint

Use this command when port 8890 has no existing service:

```bash
cd /private/tmp/hackspain-coffee-core
.venv-coffee/bin/python sim/coffee_sorter/live.py --host 127.0.0.1 --port 8890 --preset sim/coffee_sorter/configs/default_demo.json
```

Open <http://127.0.0.1:8890> and select **Restart session**.
Check that every open browser shows the same new session ID. Select **Inject stone** and check its acknowledgment.
The next increment adds retained injection cards and distinct markers for commands and actual air contact.
Live scores and measured decision reasons follow the engine handoff. The required 3D view remains a later increment.
