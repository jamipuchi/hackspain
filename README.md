# hackspain — THEKER robot sorter

Simulation + GPT-6 control stack for a wooden 3-servo arm with an electromagnet that sorts screws, nuts
and washers. Physics in MuJoCo, photoreal renders in Blender Cycles, GPT-6 (`gpt-6-astra`) as a
tool-calling agent, the real Arduino sketch in `sim/magnet_sorter/firmware/`.

- `LEARNINGS.md` — what we learned (read this first)
- `sim/magnet_sorter/` — the project (`README.md` inside: builds, brains, cameras, how to run)
- `sim/coffee_sorter/` — **track 2: coffee bean optical sorter.** MuJoCo belt sorter (3 m/s belt, 2080-px
  camera strip, 64 air-jet valves) that removes defective green coffee beans and foreign matter at
  ~2000 beans/s. Learned classifier + anomaly detector, honest perception (pixels only), physical ejection.
  `README.md` inside has the layout, run commands, progress log and handoff notes. Status: physics + camera
  done and calibrated; classifier training and the first closed-loop run are next.
- `sim/astra_sort_v0/` — the first cartoon version, kept for reference
- `sim/demos/` — MuJoCo/mink/Pinocchio starter demos
- `runs/` — GPT-6 photos, plans, mosaics and preview videos per run
- `logs/` — run logs
- `sync.sh` — mirrors `~/robotics` into this repo and pushes (runs every 5 min while the agent works)

Shopping sheet v4 and the docx proposal are the hardware source of truth; `theker_v1` in
`sim/magnet_sorter/scene_def.py` is the build that matches them.
