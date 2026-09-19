---
date: 2026-09-19T00:34:02+02:00
author: Taras and Codex
topic: "Theker: learn item classification from human demonstrations"
tags: [brainstorm, hackspain, theker, learning, arduino]
status: complete
exploration_type: experiment
last_updated: 2026-09-19
last_updated_by: Codex
---

# Theker: learn item classification from human demonstrations

## Context

Taras wants to explore an algorithm and an abstraction above Arduino code for the HackSpain Theker challenge.
One or more cameras observe a human who classifies items.
The system learns from those demonstrations and creates an algorithm that communicates with Arduino.
Taras describes a system that evolves and optimizes itself.

Taras requested a review of the challenge files in the swarm organization in agent-fs before clarification questions.
Access uses the local `.env` credentials. This document contains no credentials.
At the first review, the local repository contained only a short README and no implementation.

The current framing is an idea to develop, inferred from Taras's request.
The brainstorm remains in exploration. No implementation decisions are final.

## Source Review

Reviewed the `theker/` folder in the `swarm` organization on 19 September 2026.
The organization ID is `9d0f4b46-6113-49f7-8e8c-d315a64bd59d`.
The drive ID is `ad84339c-9d70-462a-84cf-b58aba031ac5`.

| Source | Relevant finding |
|---|---|
| [Challenge review](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/theker/01-review-and-research.md) | The stored brief emphasizes autonomy, variability, generalization, and measured improvement. Its source is an unofficial participant copy. |
| [Original proposal](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/theker/robot-sorter-shopping-proposal.md) | A phone and laptop identify screws, nuts, and washers. An Arduino moves a three-axis arm and switches an electromagnet. |
| [Arm model](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/theker/02-arm-model.md) | The model validates sampled Cartesian paths through inverse kinematics. It does not validate physical performance. |
| [Shopping sheet](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/theker/03-shopping-sheet.md) | The proposed hardware remains an Uno, positional servos, an electromagnet, and external power. |
| [Perception model](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/theker/04-perception-model.md) | The classifier uses manually written shape rules. No demonstration learner exists in the reviewed material. |
| [Challenge capture](https://live.agent-fs.dev/file/~/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/ad84339c-9d70-462a-84cf-b58aba031ac5/theker/sources/hackspain-browser-capture-2026-09-18.md) | The previous anonymous browser session could not access the signed-in challenge brief. |

Also inspected `models/arm_model.py`, `perception/perception_probe.py`, and both corresponding `results/summary.json` files.
The scripts contain geometry calculations and a fixed classifier. They do not implement demonstration capture, training, code evolution, or Arduino firmware.
The review did not execute those scripts or validate the physical robot.

### Evidence and limits

- The blue-background perception self-test reports 89.3% accuracy for flat parts and 74.1% for harder synthetic scenes.
- Those scores do not establish accuracy on the team's parts or camera.
- The real-photo checks cover one isolated screw and merged groups of touching parts. They do not establish sorting performance.
- The arm model checks a small fixture with three destination bins and a 14 by 14 mm pickup-center region.
- The arm model specifies Cartesian interpolation before inverse kinematics. Direct interpolation between servo angles falls outside that tested behavior.
- The perception document assumes separated parts. The arm document recommends an initial 30 mm center spacing pending physical tests.
- The documents propose an unknown class, but do not establish an autonomous physical rejection mechanism.
- The challenge review proposes simulation and hardware backends with one interface. This remains a proposal, not an implemented abstraction.
- The challenge review sketches serial angle and magnet commands. The material does not establish a tested Arduino protocol.

Agent-fs access succeeded with `.env` credentials.
The initial Swarm task request returned HTTP 401 with the supplied Swarm credential.
After Taras updated that credential, the task request succeeded.
The initial access limitation no longer applies.

### Implications for this brainstorm

Taras's idea adds a learning process above the existing perception and motion work.
Learning destinations, learning motion, and generating executable code are separate scope decisions.
The phrase "self-evolving" does not yet define what changes or which measurement determines improvement.
We must distinguish better predictions from better physical sorting results.
We must also decide how the system observes a correct result after the human stops demonstrating.

## Resolved boundary

Taras selected high-level robot actions.
The policy assigns a demonstrated destination to an observed item.
The adapter calls `pick_at` and `place_in`.
Jaume's controller retains motion, serial, and electromagnet control.

For this experiment, clusters mean groups defined by demonstrated destinations.
Separating touching objects remains outside its scope.

The root `contracts/` folder keeps learning work outside `sim/magnet_sorter/`.
The sync script replaces the simulator directory with `rsync --delete`.
The contract folder prevents learning changes from conflicting with simulator synchronization.

## First experiment result

The first experiment is complete.
It rendered 69 simulated images from Jaume's `theker_v1` camera.
It used three initial M3 demonstrations and 18 additional M3 demonstrations.
The candidate policy used all 21 demonstrations.

Validation used 18 new poses of the training M3 models.
It did not use new part models.
The held-out test used 30 poses of three M4 fixtures.

The deployed policy made 19 correct assignments and two wrong assignments.
It deferred nine test poses.
Its total accuracy was 19/30.
Its accepted-assignment accuracy was 19/21.

Additional demonstrations improved ungated nearest assignment from 24/30 to 28/30.
That result is diagnostic only.
It is not accepted-action accuracy.

The smoke run used `RobotAPI`, `ArmController`, `SimArduino`, and MuJoCo.
Two simulated actions reached expected bin footprints.
One screw prediction deferred before an action.
The fixed pickup station was `(8, 0)` cm.
It was not visual localization.

`reported_ok` records the adapter result for each action.
`task_verified` remains `null` because no independent verifier exists.
The simulated footprint result does not prove a correct physical sort.

Read the [evaluation results](../../../contracts/learning/results/v1/evaluation/results.json) and [smoke results](../../../contracts/learning/results/v1/smoke/results.json).

## Reproduce v1

Run these commands from the repository root.

```bash
python3.14 -m venv contracts/.venv
contracts/.venv/bin/python -m pip install mujoco==3.13.0 numpy==2.4.4 opencv-python==5.0.0.93 pillow==12.1.1

contracts/.venv/bin/python contracts/learning/sim_dataset.py contracts/learning/results/v1/dataset
contracts/.venv/bin/python contracts/learning/run_experiment.py \
  --dataset contracts/learning/results/v1/dataset/records.json \
  --out contracts/learning/results/v1/evaluation
contracts/.venv/bin/python contracts/learning/sim_smoke.py \
  --policy contracts/learning/results/v1/evaluation/policy.json \
  --out contracts/learning/results/v1/smoke

PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python -m unittest discover \
  -s contracts/learning -p 'test_*.py' -v
RUN_RENDER_TESTS=1 PYTHONDONTWRITEBYTECODE=1 contracts/.venv/bin/python -m unittest discover \
  -s contracts/learning -p 'test_*.py' -v
```

The renderer commands require host graphics access.
Default tests reported 14 passed and one skipped rendering test.
The 69-frame capture and smoke run succeeded separately with host graphics.

## OpenRouter and Jev experiment

Taras requested TypeSafe Jev for decisions and OpenRouter for LLM APIs.
The second experiment now uses OpenRouter vision to describe objects and Jev to select a demonstrated destination.
It retains the same robot contract and Jaume's controller.

The policy with three demonstrations assigned 28 test images correctly and deferred two.
The policy with 21 demonstrations assigned all 30 correctly.
All 30 predictions followed the changed destination labels in a separate control.
The models retain fixed weights. The policy learns by accumulating demonstration examples in context.

Three subsequent MuJoCo trials captured new images and called both providers.
The screw, nut, and washer reached their expected bin footprints.
Physical task verification remains unknown.

Both the visual representation and decision model changed from v1.
These results cannot isolate Jev's contribution or establish broader generalization.
The next useful experiment is to capture human demonstrations with the actual camera and parts.

Read the [architecture](../../../contracts/learning/JEV.md) and [measured results](../../../contracts/learning/results/v2/report.md).

## Remaining limits

All demonstrations use simulated labels.
Human video extraction remains unimplemented.
Arbitrary cluster discovery remains unimplemented.
Physical hardware evaluation remains unimplemented.
