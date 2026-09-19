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
    C <speed>                     conveyor servo, -100..100 (0 = no signal, stops) -> ok
    H                             home pose                             -> ok
    ?                             -> P <b> <s> <e> M <0|1> B <0|1>   (B=1 while still moving)

  Servo motion follows a trapezoidal profile (ACCEL_DEG_S2, MAX_DEG_PER_S) so the hanging magnet
  with a part on it is never whipped around. Identical to the simulator's firmware model.
*/

#include <Servo.h>

const uint8_t PIN_BASE = 9, PIN_SHOULDER = 10, PIN_ELBOW = 11, PIN_MAGNET = 7, PIN_BELT = 6;
const float MAX_DEG_PER_S = 200.0f;     // cruise speed (well below MG90S/MG946R no-load speed)
const float ACCEL_DEG_S2 = 700.0f;      // acceleration limit of the profile
const unsigned long TICK_MS = 20;       // one servo pulse period
const int HOME_POSE[3] = {150, 125, 75};  // taras_v1: parked up and to the side, out of the camera's view

Servo servos[3];
Servo belt;
float current[3];
float vel[3];
int target[3];
bool magnetOn = false;
unsigned long lastTick = 0;
char line[48];
uint8_t lineLen = 0;

// DS04-NFC conveyor drive. Its dead centre is not exactly 90, so at speed 0 we stop sending
// pulses altogether (detach): a continuous servo with no signal stands still.
void setBelt(int sp) {
  if (sp == 0) {
    if (belt.attached()) belt.detach();
    pinMode(PIN_BELT, OUTPUT);
    digitalWrite(PIN_BELT, LOW);
    return;
  }
  if (!belt.attached()) belt.attach(PIN_BELT);
  belt.write(90 + sp * 90 / 100);   // 0/180 = full speed either way
}

void applyServos() {
  for (uint8_t i = 0; i < 3; i++) servos[i].write((int)(current[i] + 0.5f));
}

bool moving() {
  for (uint8_t i = 0; i < 3; i++) if (fabs(target[i] - current[i]) > 0.01f || fabs(vel[i]) > 1e-6f) return true;
  return false;
}

void handle(char *cmd) {
  if (cmd[0] == 'S') {
    int b, s, e;
    if (sscanf(cmd + 1, "%d %d %d", &b, &s, &e) == 3) {
      target[0] = constrain(b, 0, 180);
      target[1] = constrain(s, 0, 180);
      target[2] = constrain(e, 0, 180);
      Serial.println(F("ok"));
    } else Serial.println(F("err"));
  } else if (cmd[0] == 'M') {
    magnetOn = (cmd[2] == '1');
    digitalWrite(PIN_MAGNET, magnetOn ? HIGH : LOW);
    Serial.println(F("ok"));
  } else if (cmd[0] == 'C') {
    int sp = constrain(atoi(cmd + 1), -100, 100);
    setBelt(sp);
    Serial.println(F("ok"));
  } else if (cmd[0] == 'H') {
    for (uint8_t i = 0; i < 3; i++) target[i] = HOME_POSE[i];
    Serial.println(F("ok"));
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
  if (now - lastTick >= TICK_MS) {
    lastTick = now;
    const float dt = TICK_MS / 1000.0f;
    for (uint8_t i = 0; i < 3; i++) {
      float d = target[i] - current[i];
      float vdes = (fabs(d) > 1e-6f) ? copysignf(fminf(MAX_DEG_PER_S, sqrtf(2 * ACCEL_DEG_S2 * fabs(d))), d) : 0.0f;
      float dv = constrain(vdes - vel[i], -ACCEL_DEG_S2 * dt, ACCEL_DEG_S2 * dt);
      vel[i] += dv;
      float move = vel[i] * dt;
      if (fabs(move) >= fabs(d)) { move = d; vel[i] = 0; }
      current[i] += move;
    }
    applyServos();
  }
}
