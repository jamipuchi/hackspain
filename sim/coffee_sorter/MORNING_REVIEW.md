# Morning review, 19 September

Coffee sorter, branch `coffee-sorter-closed-loop`, [PR #1](https://github.com/tarasyarema/hackspain/pull/1) (draft, not merged).
Written by Fable from Astra's night work, refreshed at 05:30 UTC after Astra's last block (sensor realism and economics, commit `6768f4c`). Nothing was rerun; every number links to the run it came from.
"Phone" links are signed downloads that work without login until 26 September. Repo links need GitHub login.
Same content with the images inline: [review page](https://hack.agent-swarm.dev/p/67a769abe0cb4e04b9a9e6c50e655326).

## New since 04:00: lighting breaks the classifier

Astra ran the sorter through 12 degraded-camera cases, one knob at a time, same 2,600 beans, seed 1, 1,000 beans/s, 0.06 N jets ([plot](runs/sensor-realism/summary.png) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/sensor-realism/sensor-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T050553Z&X-Amz-Expires=604800&X-Amz-Signature=a3e9076fa622790f989b763746a9ffa26e12d731c5fa930c97906519f498e515&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27sensor-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject) · [what the camera saw](runs/sensor-realism/camera_contact_sheet.png) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/sensor-realism/camera-contact-sheet.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T050553Z&X-Amz-Expires=604800&X-Amz-Signature=221aa2b852a6f6f2fa5953c034780ae9ebe4525a4d1506c37a4070e05ccb9999&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27camera-contact-sheet.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject)).

Clean camera, same harness: 48.7% of defects captured, 4.1% good beans lost, 88.7% routed correctly ([base case](runs/sensor-realism/base-1000/metrics.json)). Against that:

- [ ] Lights 30% dimmer: good beans lost 4.1% → 22.8%, capture 48.7% → 34.8%. Camera accuracy on single beans 97.8% → 67.6%. This is the one that breaks it.
- [ ] Lights 30% brighter: 17.4% good beans lost. A left-to-right light gradient: 9.0%.
- [ ] Slow shutter (500 µs, 6 px of blur): 15.8% good beans lost. Nominal shutter (100 µs): 6.0%.
- [ ] Sensor noise, stress level: 9.8% lost. Nominal noise: 6.4%.
- [ ] Belt speed wobble ±5%: no harm measured (2.6% lost, 46.8% capture).
- [ ] Beans fed touching each other: the feeder only admits 1,193 of the requested 3,000 beans/s; 39.4% capture, 9.6% lost.
- [ ] Everything at once: 68.6% routed correctly, 31.4% capture, 24.4% good beans lost. Camera accuracy 66.8%.

What it means:
- The classifier was trained under one lighting condition and does not tolerate ±30%. Regulated, uniform lighting is the first hardware item and the cheapest one. Brightness augmentation at training time is the obvious software fix; untested, see next moves.
- Shutter, noise and wobble values are assumptions written into [the config](configs/sensor_realism_scenarios.json), not measurements of any camera. Nobody knows the real shutter time yet.
- The clean base here is 48.7% / 4.1%, not the 51.5% / 4.3% from the demo run below. Same settings, different bean-stream generator (this harness fixes each bean's identity so the 12 cases are comparable). Both are single 4 s runs; the difference is noise, not a regression.
- Full case list and per-case diagnostics: [report](runs/sensor-realism/REPORT.md), [night log](NIGHT_LOG.md#19-september-0502-utc-final-sensor-realism-and-economics).

## The economics number

Astra built a rerunnable mass and value ledger ([report](runs/sensor-economics/report.md), [plot](runs/sensor-economics/summary.png) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/sensor-realism/economics-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T050553Z&X-Amz-Expires=604800&X-Amz-Signature=1061857b2d66b9eba40ce2bee793480c731f71650e0b2649b2d634f554386534&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27economics-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject), [assumptions](configs/economics.json)).

Clean 1,000 beans/s case, before any operating cost:
- [x] 576 kg/h in, 503 kg/h accepted, 40.5 kg/h of defects removed.
- [ ] 20.4 kg/h of good beans wrongly ejected plus 2.2 kg/h spilled. That is the loss that matters.
- [ ] Net: EUR −187/h versus selling the lot unsorted. Break-even needs a EUR 0.87/kg premium on the accepted stream.
- [ ] With the lights 30% dimmer: EUR −746/h; break-even premium EUR 2.29/kg.

The assumptions, all placeholders: 0.2 g per bean, 80% duty, EUR 6/kg unsorted, EUR 0.50/kg premium if a buyer pays it, zero salvage on rejects, no labour, air or capital costs. The accepted stream still holds 6.5% defects by count, down from 14.5%; whether that earns any premium is unknown.

What it means for the pitch:
- Do not put a EUR/h figure on the slide. At EUR 6/kg, every good bean the jets knock out costs more than the defect it sits next to; the number is negative until good-bean loss drops well under 4%.
- Say instead: "the ledger exists, is rerunnable, and takes real prices". The lever it points at is good-bean loss, not defect capture.
- The older four-rate ledger ([report](runs/economics/report.md)) used the 0.09 N jets and a different cohort; its 1,000 beans/s figure is EUR −222/h. Same conclusion.

## Look at these first

- [ ] [Demo video, 1,000 beans/s, gentler jets](runs/confirmation-seed1/force-0.06/overview_h264.mp4) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/demo-1000-force006.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T035324Z&X-Amz-Expires=604800&X-Amz-Signature=250f73e1da8be02a5560e89e11c14e9241ee6798f73aa38527bec3f5fefd3eec&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27demo-1000-force006.mp4&x-amz-checksum-mode=ENABLED&x-id=GetObject). The clip to show. HUD on, 16 s, the best configuration we have.
- [ ] [Unknown object ejected, 8x slow](runs/generalization/openset/demo/unknown_8x_slow.mp4) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/openset/demo/unknown_8x_slow.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T035324Z&X-Amz-Expires=604800&X-Amz-Signature=b69ce5d08c4a8c7d31e500d18ccac88b537391aef3f5ee5e6cd2cdea641e74db&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27unknown_8x_slow.mp4&x-amz-checksum-mode=ENABLED&x-id=GetObject). A never-trained magenta object gets its own jet pulse and lands in reject. The generalisation clip.
- [ ] [Sensor sweep, four panels](runs/sensor-realism/summary.png) (phone link above). The lighting collapse is visible at a glance; show it if a judge asks "does this survive a real camera".
- [ ] [One-slide summary](slide/one_slide.png). Draft for the judges, made before the sensor sweep; it does not mention lighting. Edit the text in `slide/make_slide.py`, rerun, done.
- [ ] [Gentler jets confirmed on a fresh seed](runs/confirmation-seed1/confirmation_summary.png) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/confirmation-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T035325Z&X-Amz-Expires=604800&X-Amz-Signature=23ba021b1369ed9c93eb8112fcd2d803d262e12d96b86c2151e9c558d75030c5&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27confirmation-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject). Recall up, losses down, on two seeds. This is why the demo uses `--jet-force 0.06`.
- [ ] [Rate sweep, nine panels](runs/rate-sweep/rate_summary.png) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/rate-summary.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T035325Z&X-Amz-Expires=604800&X-Amz-Signature=cf05fcc0090fcb650c3f07fc5db7e8ee679afb52f5665e7467b05ed0f32e5a10&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27rate-summary.png&response-content-type=image%2Fpng&x-amz-checksum-mode=ENABLED&x-id=GetObject). Quality falls steadily from 500 to 3,000 beans/s. Decide which rate to demo from this.

## What works now

Best configuration, [seed 1, 1,000 beans/s, 4 s, 0.06 N jets](runs/confirmation-seed1/force-0.06/metrics.json):
- [x] 51.5% of defects physically reach the reject bin (202/392)
- [x] 4.3% of good beans lost (94/2,208)
- [x] 88.5% of beans routed correctly, 2.1% spills, zero late decisions

Full-rate baseline, [seed 0, 2,000 beans/s, 8 s, default jets](runs/baseline-2000-measured/metrics.json):
- [x] Runs end to end for 13,148 beans with HUD video
- [x] 38.9% defect capture, 7.95% good loss, 4.7% spills
- [x] Latency p50 40 ms, p99 46 ms against the 73 ms camera-to-jet budget; 0 late of 2,220 decisions

Perception ([README progress log](README.md#progress-log)):
- [x] Classifier blob holdout 97.9% green, 98.6% roasted
- [x] Detector 79 ms → 5.44 ms median per frame

Characterisation ([night log](NIGHT_LOG.md)):
- [x] Rate sweep 500 → 3,000 beans/s: recall 61.6% → 39.9%, good loss 2.9% → 8.8% ([plot](runs/rate-sweep/rate_summary.png))
- [x] Latency boundary found: +30 ms injected delay gives 6.9% late decisions, +40 ms gives 79% ([plot](runs/latency-sweep/latency_summary.png))
- [x] Tuning screen of force, pulse, splitter, valves per target, pool size ([plot](runs/tuning-sweep/tuning_1000_summary.png))
- [x] Sensor sweep, 12 cases, every completed case checksum-bound to its images and decisions ([summary](runs/sensor-realism/summary.json))
- [x] Mass and value ledger, rerunnable from any set of metrics files ([ledger](runs/sensor-economics/summary.json))

Generalisation ([night log section](NIGHT_LOG.md#new-product-roasted-unchanged-shared-controller)):
- [x] Roasted product with zero controller changes: 39.5% capture, 3.8% good loss ([confusion matrices](runs/generalization/comparison/confusion_matrices.png), [camera sheet](runs/generalization/comparison/camera_strip_contact_sheet.png))
- [x] Never-seen objects: 81/216 physically captured, 9/444 good beans lost ([tradeoff plot](runs/generalization/physical-thresholds/physical_tradeoff.png))
- [x] Unknown colours flagged 100% by the anomaly detector (100/100 plastic, 218/218 magenta) ([threshold curve](runs/generalization/openset/threshold_sweep.png))
- [x] 65 regression tests pass locally ([output](runs/sensor-realism/final-tests.log)); no CI exists

## What doesn't

The physics, in order of demo risk:
- [ ] Lighting. ±30% brightness takes camera accuracy from 98% to 68 to 75% and good-bean loss to 17 to 23%. See the top of this page.
- [ ] Half the defects get through. Best case 51.5% capture; at the 2,000 beans/s we advertise it is 39 to 43%. The classifier is not the problem on a clean camera; jet timing and coverage are.
- [ ] Touching beans break it. Ever-merged beans: 26.2% capture. Single beans: 47.0% ([baseline metrics](runs/baseline-2000-measured/metrics.json)). At high rates more beans touch; the crowded-feed case above confirms it with a non-overlapping feeder.
- [ ] Oversize foreign matter is invisible to the anomaly detector. 1/201 flagged, 0/73 physically rejected at the trained threshold. Colour is easy, size is not. Air jets cannot fix this ([night log](NIGHT_LOG.md#open-set-physical-threshold-comparison)).
- [ ] Spills: 2 to 5% of beans leave the belt uncounted. Shells, husks and stones spill most.
- [ ] Good beans get knocked into reject by neighbours' jets. 4 to 10% depending on rate and force. This is what makes the ledger negative.
- [ ] Capture is not always caused by the jet. Some plastic chips fall into reject with zero jet hits. The demo must show the featured object's own hit, which the 8x clip does.

The timing claims:
- [ ] Not real time. 13 to 37 wall seconds per simulated second. In the sensor sweep, detector plus controller overran the 4 ms frame interval on 996 of 1,000 frames even on the clean case. A live viewer demo is not an option on this host; play the videos.
- [ ] Camera backlog and dropped frames are not modelled. "Zero late decisions" is a simulated availability result, not a hardware margin.
- [ ] Max latency (76 to 82 ms) exceeds the 73 ms budget in the 8 s runs; three late decisions appeared at 2,000/s in the rate sweep.
- [ ] Detector is 5.44 ms, target was under 5 ms. Open.
- [ ] 3,000 beans/s only runs with an enlarged body pool, and then loses 10.1% of good beans.

The evidence:
- [ ] All tuning and sweep runs, including the 12 sensor cases, are 4 s single-seed screens. The 0.06 N result has two seeds. Nothing has lot-to-lot statistics.
- [ ] Shutter time, sensor noise and belt wobble in the sensor sweep are assumed values, not measurements. Lighting ±30% is a plain gain change on rendered pixels.
- [ ] The economics prices are placeholders. Nobody has asked a buyer what the accepted stream is worth.
- [ ] Classifier holdout is a random blob split; repeated views of the same bean sit in both halves. Not independent-bean accuracy.
- [ ] Everything is synthetic. No real camera, real coffee or hardware timing has been touched.
- [ ] The 2,000 beans/s baseline video's HUD valve counter counts queued commands, not fired ones. A visible correction card is in the video; the 1,000 beans/s demo video has the fixed HUD.
- [ ] Run `20260919_014250` (duplicate actuation) and `20260919_020354` (failed) exist on disk and are diagnostic only. Astra's first sensor pass was archived as diagnostic and is not in the published suite.

## Open decisions for the team

- [ ] Which headline goes on the slide: honest physical (52%) or the classifier number (98%)? Draft slide uses "98% → 52%". Judges will ask about the gap either way.
- [ ] Does the lighting result go on the slide? It is the strongest "we measured where it breaks" story we have, and it is also the weakest number. My recommendation: one line, framed as the first hardware requirement.
- [ ] Show the economics at all? My recommendation: no number on the slide; mention the ledger exists if asked. See above.
- [ ] Which rate to demo: 1,000 beans/s (52% capture, 4% loss) or 2,000 beans/s (39%, 8%)? Astra's demo video is at 1,000.
- [ ] Change the default jet force from 0.09 N to 0.06 N? Astra kept the default and made 0.06 an explicit flag. Two seeds agree; longer paired runs were not done.
- [ ] Keep the trained anomaly threshold (18.49)? Astra recommends yes. The lower threshold buys four unknowns for ten good beans.
- [ ] Spend remaining hours on sorter quality (merged beans, jet geometry, lighting robustness) or on the UR5e arm for oversize matter? They compete for the same time.
- [ ] Merge PR #1? Your call by design. Reviewing 14 commits and 275 MB of run artifacts is the cost.
- [ ] The published review page is public by URL. Flip it to login-only if you prefer.

## Suggested next moves

Ranked by demo value per hour. README `Next` items folded in.

1. [ ] Rehearse with the two videos and the slide. ~1 h, highest value. Decide the headline, the rate and the lighting line above first.
2. [ ] Brightness augmentation in classifier training, then rerun the three lighting cases. ~1 to 2 h machine, ~30 min human. Assumption: this recovers most of the lighting loss; it is the standard fix and it is cheap, but nobody has tried it here. If it works, the lighting story becomes "found it, fixed it" by lunchtime.
3. [ ] Longer paired seeds at 0.06 N (README). ~2 h machine time, ~20 min human. Makes the headline number defensible; can run unattended during breakfast.
4. [ ] Merged-target jet intersection and capture (README). 3 to 5 h. Biggest single recall lever (26% vs 47%). Medium risk of no improvement.
5. [ ] UR5e picking oversize matter off the infeed (README). 4 to 8 h. Highest wow if it works and the only answer to the 0/73 oversize result. Only if a second person is free; keep it a separate module.
6. [ ] Model camera backlog before claiming timing margin (README). 2 to 3 h. Zero demo value, needed before anyone says "real time" out loud.
7. [ ] Detector under 5 ms (README). 1 to 2 h. Low demo value; 5.44 ms is already 14x faster than the start.

Done from the README list: one-slide summary (draft above), roasted profile, unseen-object experiment, rate and latency sweeps, tuning screen, sensor sweep, economics ledger, first closed loop.

## Gaps I could not close

- The night log has no matched 2,000 beans/s run at 0.06 N. The best-configuration numbers are at 1,000 beans/s only.
- The sensor sweep's clean base (48.7%) and the demo run (51.5%) come from different bean-stream generators. Both are single runs; I cannot say which is closer to the long-run mean.
- No wall-clock figure for the roasted training run beyond the log; not needed for the demo.
- The slide was not regenerated for the sensor result. One text edit in `slide/make_slide.py` does it; the decision on what it should say is yours.
