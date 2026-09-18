# hackspain — THEKER robot sorter

Simulation + GPT-6 control stack for a wooden 3-servo arm with an electromagnet that sorts screws, nuts
and washers. Physics in MuJoCo, photoreal renders in Blender Cycles, GPT-6 (`gpt-6-astra`) as a
tool-calling agent, the real Arduino sketch in `sim/magnet_sorter/firmware/`.

- `LEARNINGS.md` — what we learned (read this first)
- `sim/magnet_sorter/` — the project (`README.md` inside: builds, brains, cameras, how to run)
- `sim/astra_sort_v0/` — the first cartoon version, kept for reference
- `sim/demos/` — MuJoCo/mink/Pinocchio starter demos
- `runs/` — GPT-6 photos, plans, mosaics and preview videos per run
- `logs/` — run logs
- `sync.sh` — mirrors `~/robotics` into this repo and pushes (runs every 5 min while the agent works)

Shopping sheet v4 and the docx proposal are the hardware source of truth; `theker_v1` in
`sim/magnet_sorter/scene_def.py` is the build that matches them.
