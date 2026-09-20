# CINTA lifecycle and performance badge QA

Date: 2026-09-20

## Scope

This check covers resolved-body recycling and the public simulation-speed badge.

The tested source revision is `2958baa3425f58b42dcb627c1f6c4b3bebf6beea`.
The canonical URL is `http://127.0.0.1:8899/`.
The tested session is `2ff7edcc-c0d9-4897-a298-f436eee449c0`.

During this measurement, the policy remained Good Keep with the other nine classes Reject.
Its policy version remained `5acba1d1a7547e544a6283cb870003120799cc2aa37cb0f273f2af5545c1afff`.
Taras selected Keep all after this measurement finished.

## Lifecycle result

Before the fix, 365 resolved objects moved less than 1 mm during 6.429 simulated seconds.
The reject-bin band contained 417 active bodies in the final sample.
Current interval admission had fallen to 55.06 objects per simulated second.

After the fix, a 28.069-second wall sample advanced 5.072 simulated seconds.
The engine admitted 2,536 objects, or 500.0 objects per simulated second.
This equals 90.35 admitted objects per wall second at the measured engine speed.

Active body count ranged from 249 to 261, with a median of 254.
Feed and belt surfaces contained 210 to 223 bodies, with a median of 217.
The reject-bin band contained zero to four bodies, with a median of one.

Observed outcome-to-retirement time had a 0.058-second median.
Its 95th percentile was 0.072 seconds, and its maximum was 0.508 seconds.

Final pool occupancy was:

- ellipsoid: 242 of 400
- half: 8 of 48
- box: 1 of 20
- capsule: 0 of 20

The grace uses simulation time. It does not retire unresolved objects.
Outcome capture, scoring, manual evidence, and policy truth remain unchanged.

## Performance badge

`engine_rate` is cumulative simulated time divided by wall time since engine start.
A value of `1.00` means physical real time.

The final state reported `engine_rate` near `0.1975`.
The bounded interval measured `0.1807x` because runtime load changed after startup.

The badge waits for a fresh state packet before showing a number.
It distinguishes measuring, connecting, and disconnected states.
A valid zero value displays as `0.00x`.

The final mobile tooltip correction is revision `9eb1412c0e6de2a2c531acefba803207032b2c08`.
It was served without restarting the tested backend session.

## Verification

The focused Python suites passed 62 tests.
The frontend Node suite passed 10 tests.
Python compilation, JavaScript syntax, and Git diff checks passed.

The trusted model SHA-256 remained `89513398373c6e0e81286419962feb3e312742de14a76d02dd0d819ad5264a5a`.
Health remained `running`, and simulation time advanced after restart.
One listener remained on port 8899.

Keep all does not disable anomaly rejection.
The controller fires when class reject probability crosses 0.8 or anomaly score crosses its threshold.
Known Stones exceeded the anomaly threshold and scheduled air under Keep all.

The lifecycle fix does not change passive Stone routing.
Slow Stones can still reach Reject without a jet contact.
See [CINTA Stone diagnosis](./2026-09-20-cinta-stone-diagnosis.md).

Raw pre-fix state sample: `/private/tmp/cinta-lifecycle-sample.json`.
Raw post-fix state sample: `/private/tmp/cinta-lifecycle-postfix-sample.json`.

The measured engine ran below physical real time.
The admission result is stated per simulated second and does not imply 500 objects per wall second.
