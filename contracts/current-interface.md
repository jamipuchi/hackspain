# Current robot interface

Reviewed on 19 September 2026 at commit `37ba133eb71e0477a1bfba79ce4ad4fecc118842`.
This document describes the current code. It does not define an approved replacement contract.

## Existing layers

The agent calls robot actions. The controller converts coordinates to servo targets. An Arduino adapter executes the serial commands.

| Layer | Inputs | Result |
|---|---|---|
| Agent tools | Photos, inventory, robot actions | Tool result, text, optional images |
| Robot API | Table coordinates in centimetres, destination names | `ToolResult(ok, text, photo, extra)` |
| Controller | World coordinates in metres, destination names | Serial operations and `OpResult` |
| Arduino | ASCII command lines at 115200 baud | `ok`, `err`, or status text |

Sources: [robot_api.py](../sim/magnet_sorter/robot_api.py), [controller.py](../sim/magnet_sorter/controller.py), and [hardware.py](../sim/magnet_sorter/hardware.py).

## Agent actions

The tools include `take_photo`, `note_parts`, `move_to`, `nudge`, `magnet`, `pick_at`, `place_in`, `home`, and `done`.
Conveyor builds also expose `belt_advance`.
`note_parts` stores the agent's inventory. It does not supply independently verified labels.
The first `done` call requests final photos. A subsequent `done` call accepts the agent's completion claim.
This sequence does not independently validate the final arrangement.

Source: [agent_brain.py](../sim/magnet_sorter/agent_brain.py), `tool_defs`, `_execute`, and `run_loop`.

## Existing Arduino emulation

`SimArduino` reimplements firmware behavior in Python and connects it to MuJoCo.
It does not execute the compiled Arduino sketch.
`SerialArduino` sends commands to a physical serial port.
The sketch contains the corresponding command parser and servo motion profile.

| Command | Meaning | Reply |
|---|---|---|
| `S b s e` | Set three servo targets in degrees | `ok` |
| `M 0` or `M 1` | Set the magnet output | `ok` |
| `C speed` | Set conveyor speed from -100 to 100 | `ok` |
| `H` | Set the home targets | `ok` |
| `?` | Read firmware position and output state | `P b s e M m B busy` |

An `ok` reply acknowledges command handling. It does not prove motion completion or a successful pick.
The position values represent firmware output angles. They are not measurements from servo encoders.
The busy flag describes the firmware motion profile. It does not prove physical settling.

Sources: [hardware.py](../sim/magnet_sorter/hardware.py) and [magnet_arm.ino](../sim/magnet_sorter/firmware/magnet_arm/magnet_arm.ino).

## Evidence limits relevant to the contract

- `ArmController.servo` ignores the command reply. `_wait` does not report an error when its timeout expires.
- `SerialArduino.busy` treats empty or malformed status replies as idle because it only checks for a `B 1` suffix.
- The camera check asks whether the source image patch changed. It does not establish object identity or arrival in the destination.
- Missing camera evidence and inconclusive comparisons can return `ok=True`.
- `RobotAPI` updates its magnet flag after pick or place, including some failed operations. That flag can differ from the controller state.
- Agent mode receives magnet-face coordinates from MuJoCo through `get_face_pos`. Those coordinates are simulator telemetry.
- The current runner uses simulated cameras in both serial modes. A real serial port alone does not establish physical visual feedback.
- The sketch and Python emulator have different parsing behavior for some malformed commands. Shared command names do not establish full parity.
- The emulator's status query checks position error. Its direct `busy` method and the sketch also check trajectory velocity.
- The sketch fixes the home pose and cruise speed. The emulator obtains these values from the selected build.

Sources: [controller.py](../sim/magnet_sorter/controller.py), [robot_api.py](../sim/magnet_sorter/robot_api.py), [run_demo.py](../sim/magnet_sorter/run_demo.py), and the Arduino implementations above.

## Review scope

This review inspected source code and existing records.
It did not execute the robot, compile the sketch, run a paid agent, or measure physical performance.
The comparison identifies requirements for future conformance checks. It does not certify the implementations.
