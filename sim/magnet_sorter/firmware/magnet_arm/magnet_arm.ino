/*
  magnet_arm.ino — 3-servo arm + electromagnet, driven over USB serial at 115200 baud.

  Wiring (Arduino Uno R3, Madrid build 19 Sep 2026):
    D9   -> base servo signal      (MG90S, orange wire)
    D10  -> shoulder servo signal  (MG946R)
    D11  -> elbow servo signal     (MG90S)
    D7   -> 5 V relay module IN    (switches the 24 V lifting electromagnet; 1N4007 flyback diode
                                    across the coil, band towards +24 V)
    D6   -> DS04-NFC continuous servo signal (conveyor drive, optional; 90 = stop)
    Power: Uno over USB. Servos from the enclosed 5 V 5 A supply, its GND tied to Arduino GND,
    its +5 V never on the Uno 5 V pin. The magnet has its own 24 V supply through the relay
    contacts; never connect 24 V to the servo rail.
  The MG996R/MOSFET hobby_v1 build uses the same sketch: D7 drives the IRF520 SIG instead.

  Protocol (one command per line):
    S <base> <shoulder> <elbow>   servo targets in degrees 0-180        -> ok
    M <0|1>                       electromagnet off / on                -> ok
    C <speed>                     conveyor servo, -100..100: pulse = neutral + speed*5 us; 0 = neutral for 2 frames,
                                  then detach (no signal)                                          -> ok
    H                             home pose                             -> ok
    ?                             -> P <b> <s> <e> M <0|1> B <0|1>   (B=1 while still moving)
    T <speed> <ms>                spin the D6 servo at <speed> (-100..100) for <ms> ms timed on the MCU, then detach
                                  (= C 0). Non-blocking; a second T replaces the first; C 0 or T 0 0 aborts. ms capped
                                  at 2000. For a continuous-rotation servo used as a door: host jitter is out of the loop.  -> ok
    D <deg>                       POSITIONAL door on D6: attach (if needed), ramp to <deg> like S, HOLD (never detaches).
                                  Any C/T afterwards returns D6 to belt (speed) mode.                       -> ok
    N <us>                        D6 speed-mode neutral pulse in microseconds (1300-1700, default 1500). Servo.write(90)
                                  is 1472 us, which is NOT a continuous servo's neutral; calibrate with N.  -> ok
    L <ch> <min> <max>            travel limits in degrees for channel 0-2 (S) or 3 (D door). Defaults 0-180. -> ok
    R <ch> <vmax> <accel>         per-channel ramp override (ch 3 = the D door), ch 0-2 (base/shoulder/elbow), vmax in deg/s,
                                  accel in deg/s^2; 0 restores the defaults (200, 700)     -> ok
                                  (the swing door on D9 uses `R 0 400 8000`: 25 deg in ~0.11 s instead of 0.38 s)

  Servo motion follows a trapezoidal profile (ACCEL_DEG_S2, MAX_DEG_PER_S) so the hanging magnet
  with a part on it is never whipped around. Identical to the simulator's firmware model.
*/

#include <Servo.h>

const uint8_t PIN_BASE = 9, PIN_SHOULDER = 10, PIN_ELBOW = 11, PIN_MAGNET = 7, PIN_BELT = 6;
const float MAX_DEG_PER_S = 200.0f;     // default cruise speed (well below MG90S/MG946R no-load speed)
const float ACCEL_DEG_S2 = 700.0f;      // default acceleration limit of the profile
float vmax[4] = {MAX_DEG_PER_S, MAX_DEG_PER_S, MAX_DEG_PER_S, MAX_DEG_PER_S};  // per-channel, changed by `R` (3 = D door)
float amax[4] = {ACCEL_DEG_S2, ACCEL_DEG_S2, ACCEL_DEG_S2, ACCEL_DEG_S2};
int limLo[4] = {0, 0, 0, 0}, limHi[4] = {180, 180, 180, 180};   // `L`: travel limits per channel, degrees
int beltNeutralUs = 1500;                // `N`: speed-mode neutral pulse (Servo.write(90) would be 1472 us)
const int BELT_US_PER_PCT = 5;           // speed -100..100 -> neutral +/- 500 us
const unsigned long BELT_STOP_HOLD_MS = 40;  // hold neutral this long (2 frames) before detaching
const unsigned long TICK_MS = 20;       // one servo pulse period
const int HOME_POSE[3] = {150, 125, 75};  // taras_v1: parked up and to the side, out of the camera's view

Servo servos[3];
Servo belt;
bool beltPulseActive = false;           // `T`: timed spin in progress
unsigned long beltPulseStart = 0, beltPulseMs = 0;
bool beltStopping = false;              // neutral written, detach pending
unsigned long beltStopAt = 0;
bool doorMode = false;                  // `D`: D6 is a positional servo being held at current[3]
float current[4];
float vel[4];
int target[4];
bool magnetOn = false;
unsigned long lastTick = 0;
char line[48];
uint8_t lineLen = 0;

// D6 speed mode (continuous-rotation conveyor/door drive). Pulse = neutral + sp*5 us, so +sp and -sp are symmetric
// about the calibrated neutral (Servo.write(90) = 1472 us is NOT neutral; that made a 1500-us servo creep at `C 1`).
// Speed 0: write neutral for BELT_STOP_HOLD_MS (a deterministic stop for a continuous servo), then detach.
void detachBelt() {
  if (belt.attached()) belt.detach();
  pinMode(PIN_BELT, OUTPUT);
  digitalWrite(PIN_BELT, LOW);
  beltStopping = false;
}

void setBelt(int sp) {
  doorMode = false;                       // any speed command leaves positional door mode
  if (sp == 0) {
    if (belt.attached()) {
      belt.writeMicroseconds(beltNeutralUs);
      beltStopping = true;
      beltStopAt = millis() + BELT_STOP_HOLD_MS;
    } else detachBelt();
    return;
  }
  beltStopping = false;
  if (!belt.attached()) belt.attach(PIN_BELT);
  belt.writeMicroseconds(beltNeutralUs + sp * BELT_US_PER_PCT);
}

// D6 positional mode: hold a target angle, ramped like the arm channels, never detached.
void setDoor(int deg) {
  beltPulseActive = false;
  beltStopping = false;
  if (!doorMode) {                        // entering: start the ramp from the commanded angle (position is unknown after detach)
    if (!belt.attached()) belt.attach(PIN_BELT);
    current[3] = constrain(deg, limLo[3], limHi[3]);
    vel[3] = 0;
    doorMode = true;
  }
  target[3] = constrain(deg, limLo[3], limHi[3]);
}

void applyServos() {
  for (uint8_t i = 0; i < 3; i++) servos[i].write((int)(current[i] + 0.5f));
  if (doorMode) belt.write((int)(current[3] + 0.5f));
}

bool moving() {
  uint8_t n = doorMode ? 4 : 3;
  for (uint8_t i = 0; i < n; i++) if (fabs(target[i] - current[i]) > 0.01f || fabs(vel[i]) > 1e-6f) return true;
  return false;
}

void handle(char *cmd) {
  if (cmd[0] == 'S') {
    int b, s, e;
    if (sscanf(cmd + 1, "%d %d %d", &b, &s, &e) == 3) {
      target[0] = constrain(b, limLo[0], limHi[0]);
      target[1] = constrain(s, limLo[1], limHi[1]);
      target[2] = constrain(e, limLo[2], limHi[2]);
      Serial.println(F("ok"));
    } else Serial.println(F("err"));
  } else if (cmd[0] == 'M') {
    magnetOn = (cmd[2] == '1');
    digitalWrite(PIN_MAGNET, magnetOn ? HIGH : LOW);
    Serial.println(F("ok"));
  } else if (cmd[0] == 'C') {
    int sp = constrain(atoi(cmd + 1), -100, 100);
    beltPulseActive = false;            // any C cancels a running T pulse
    setBelt(sp);
    Serial.println(F("ok"));
  } else if (cmd[0] == 'D') {
    int d;
    if (sscanf(cmd + 1, "%d", &d) == 1) { setDoor(d); Serial.println(F("ok")); }
    else Serial.println(F("err"));
  } else if (cmd[0] == 'N') {
    int us;
    if (sscanf(cmd + 1, "%d", &us) == 1 && us >= 1300 && us <= 1700) {
      beltNeutralUs = us;
      if (belt.attached() && !doorMode && !beltPulseActive && beltStopping) belt.writeMicroseconds(beltNeutralUs);
      Serial.println(F("ok"));
    } else Serial.println(F("err"));
  } else if (cmd[0] == 'L') {
    int ch, lo, hi;
    if (sscanf(cmd + 1, "%d %d %d", &ch, &lo, &hi) == 3 && ch >= 0 && ch <= 3 && lo >= 0 && hi <= 180 && lo < hi) {
      limLo[ch] = lo; limHi[ch] = hi;
      target[ch] = constrain(target[ch], lo, hi);
      Serial.println(F("ok"));
    } else Serial.println(F("err"));
  } else if (cmd[0] == 'T') {
    int sp; long ms;
    if (sscanf(cmd + 1, "%d %ld", &sp, &ms) == 2) {
      sp = constrain(sp, -100, 100);
      ms = constrain(ms, 0L, 2000L);
      if (sp == 0 || ms == 0) {
        beltPulseActive = false;
        setBelt(0);
      } else {
        setBelt(sp);
        beltPulseStart = millis();
        beltPulseMs = (unsigned long)ms;
        beltPulseActive = true;
      }
      Serial.println(F("ok"));
    } else Serial.println(F("err"));
  } else if (cmd[0] == 'H') {
    for (uint8_t i = 0; i < 3; i++) target[i] = HOME_POSE[i];
    Serial.println(F("ok"));
  } else if (cmd[0] == 'R') {
    int ch; long v, a;
    if (sscanf(cmd + 1, "%d %ld %ld", &ch, &v, &a) == 3 && ch >= 0 && ch <= 3) {
      vmax[ch] = (v <= 0) ? MAX_DEG_PER_S : (float)constrain(v, 1L, 2000L);
      amax[ch] = (a <= 0) ? ACCEL_DEG_S2 : (float)constrain(a, 1L, 50000L);
      Serial.println(F("ok"));
    } else Serial.println(F("err"));
  } else if (cmd[0] == '?') {
    Serial.print(F("P "));
    for (uint8_t i = 0; i < 3; i++) { Serial.print((int)(current[i] + 0.5f)); Serial.print(' '); }
    Serial.print(F("M ")); Serial.print(magnetOn ? 1 : 0);
    Serial.print(F(" B ")); Serial.println(moving() ? 1 : 0);
  } else {
    Serial.println(F("err"));
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(PIN_MAGNET, OUTPUT);
  digitalWrite(PIN_MAGNET, LOW);
  servos[0].attach(PIN_BASE);
  servos[1].attach(PIN_SHOULDER);
  servos[2].attach(PIN_ELBOW);
  setBelt(0);   // conveyor off: no pulses on D6 until a C command asks for speed
  for (uint8_t i = 0; i < 3; i++) { current[i] = HOME_POSE[i]; target[i] = HOME_POSE[i]; vel[i] = 0; }
  current[3] = 90; target[3] = 90; vel[3] = 0;
  applyServos();
  Serial.println(F("magnet_arm ready"));
}

void loop() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (lineLen) { line[lineLen] = 0; handle(line); lineLen = 0; }
    } else if (lineLen < sizeof(line) - 1) {
      line[lineLen++] = c;
    }
  }
  unsigned long now = millis();
  if (beltPulseActive && now - beltPulseStart >= beltPulseMs) {   // end of a `T` pulse, timed here, not on the host
    beltPulseActive = false;
    setBelt(0);
  }
  if (beltStopping && (long)(now - beltStopAt) >= 0) detachBelt();   // neutral held for 2 frames -> no signal
  if (now - lastTick >= TICK_MS) {
    lastTick = now;
    const float dt = TICK_MS / 1000.0f;
    uint8_t n = doorMode ? 4 : 3;
    for (uint8_t i = 0; i < n; i++) {
      float d = target[i] - current[i];
      float vdes = (fabs(d) > 1e-6f) ? copysignf(fminf(vmax[i], sqrtf(2 * amax[i] * fabs(d))), d) : 0.0f;
      float dv = constrain(vdes - vel[i], -amax[i] * dt, amax[i] * dt);
      vel[i] += dv;
      float move = vel[i] * dt;
      if (fabs(move) >= fabs(d)) { move = d; vel[i] = 0; }
      current[i] += move;
    }
    applyServos();
  }
}
