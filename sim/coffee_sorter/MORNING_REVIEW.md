# Morning review, 19 September

Coffee sorter, branch `coffee-sorter-closed-loop`, [PR #1](https://github.com/tarasyarema/hackspain/pull/1) (draft, not merged).
Written by Fable from Astra's night work. Nothing was rerun; every number links to the run it came from.
"Phone" links are signed downloads that work without login until 26 September. Repo links need GitHub login.
Same content with the images inline: [review page](https://hack.agent-swarm.dev/p/67a769abe0cb4e04b9a9e6c50e655326).

## Look at these first

- [ ] [Demo video, 1,000 beans/s, gentler jets](runs/confirmation-seed1/force-0.06/overview_h264.mp4) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/characterization/demo-1000-force006.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T035324Z&X-Amz-Expires=604800&X-Amz-Signature=250f73e1da8be02a5560e89e11c14e9241ee6798f73aa38527bec3f5fefd3eec&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27demo-1000-force006.mp4&x-amz-checksum-mode=ENABLED&x-id=GetObject). The clip to show. HUD on, 16 s, the best configuration we have.
- [ ] [Unknown object ejected, 8x slow](runs/generalization/openset/demo/unknown_8x_slow.mp4) · [phone](https://hack-s3.agent-swarm.dev/agentfs/9d0f4b46-6113-49f7-8e8c-d315a64bd59d/drives/ad84339c-9d70-462a-84cf-b58aba031ac5/hackspain/coffee-sorter/2026-09-19/generalization/openset/demo/unknown_8x_slow.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Content-Sha256=UNSIGNED-PAYLOAD&X-Amz-Credential=minioadmin%2F20260919%2Fus-east-1%2Fs3%2Faws4_request&X-Amz-Date=20260919T035324Z&X-Amz-Expires=604800&X-Amz-Signature=b69ce5d08c4a8c7d31e500d18ccac88b537391aef3f5ee5e6cd2cdea641e74db&X-Amz-SignedHeaders=host&response-content-disposition=attachment%3B%20filename%2A%3DUTF-8%27%27unknown_8x_slow.mp4&x-amz-checksum-mode=ENABLED&x-id=GetObject). A never-trained magenta object gets its own jet pulse and lands in reject. The generalisation clip.
- [ ] [One-slide summary](slide/one_slide.png). Draft for the judges. Edit the text in `slide/make_slide.py`, rerun, done.
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

Generalisation ([night log section](NIGHT_LOG.md#new-product-roasted-unchanged-shared-controller)):
- [x] Roasted product with zero controller changes: 39.5% capture, 3.8% good loss ([confusion matrices](runs/generalization/comparison/confusion_matrices.png), [camera sheet](runs/generalization/comparison/camera_strip_contact_sheet.png))
- [x] Never-seen objects: 81/216 physically captured, 9/444 good beans lost ([tradeoff plot](runs/generalization/physical-thresholds/physical_tradeoff.png))
- [x] Unknown colours flagged 100% by the anomaly detector (100/100 plastic, 218/218 magenta) ([threshold curve](runs/generalization/openset/threshold_sweep.png))
- [x] 36 regression tests pass locally ([output](runs/generalization/final-tests.log)); no CI exists

## What doesn't

The physics, in order of demo risk:
- [ ] Half the defects get through. Best case 51.5% capture; at the 2,000 beans/s we advertise it is 39 to 43%. The classifier is not the problem; jet timing and coverage are.
- [ ] Touching beans break it. Ever-merged beans: 26.2% capture. Single beans: 47.0% ([baseline metrics](runs/baseline-2000-measured/metrics.json)). At high rates more beans touch.
- [ ] Oversize foreign matter is invisible to the anomaly detector. 1/201 flagged, 0/73 physically rejected at the trained threshold. Colour is easy, size is not. Air jets cannot fix this ([night log](NIGHT_LOG.md#open-set-physical-threshold-comparison)).
- [ ] Spills: 2 to 5% of beans leave the belt uncounted. Shells, husks and stones spill most.
- [ ] Good beans get knocked into reject by neighbours' jets. 4 to 10% depending on rate and force.
- [ ] Capture is not always caused by the jet. Some plastic chips fall into reject with zero jet hits. The demo must show the featured object's own hit, which the 8x clip does.

The timing claims:
- [ ] Not real time. 13 to 37 wall seconds per simulated second. A live viewer demo is not an option on this host; play the videos.
- [ ] Camera backlog and dropped frames are not modelled. "Zero late decisions" is a simulated availability result, not a hardware margin.
- [ ] Max latency (76 to 82 ms) exceeds the 73 ms budget in the 8 s runs; three late decisions appeared at 2,000/s in the rate sweep.
- [ ] Detector is 5.44 ms, target was under 5 ms. Open.
- [ ] 3,000 beans/s only runs with an enlarged body pool, and then loses 10.1% of good beans.

The evidence:
- [ ] All tuning and sweep runs are 4 s single-seed screens. The 0.06 N result has two seeds. Nothing has lot-to-lot statistics.
- [ ] Classifier holdout is a random blob split; repeated views of the same bean sit in both halves. Not independent-bean accuracy.
- [ ] Everything is synthetic. No real camera, real coffee or hardware timing has been touched.
- [ ] The 2,000 beans/s baseline video's HUD valve counter counts queued commands, not fired ones. A visible correction card is in the video; the 1,000 beans/s demo video has the fixed HUD.
- [ ] Run `20260919_014250` (duplicate actuation) and `20260919_020354` (failed) exist on disk and are diagnostic only.

## Open decisions for the team

- [ ] Which headline goes on the slide: honest physical (52%) or the classifier number (98%)? Draft slide uses "98% → 52%". Judges will ask about the gap either way.
- [ ] Which rate to demo: 1,000 beans/s (52% capture, 4% loss) or 2,000 beans/s (39%, 8%)? Astra's demo video is at 1,000.
- [ ] Change the default jet force from 0.09 N to 0.06 N? Astra kept the default and made 0.06 an explicit flag. Two seeds agree; longer paired runs were not done.
- [ ] Keep the trained anomaly threshold (18.49)? Astra recommends yes. The lower threshold buys four unknowns for ten good beans.
- [ ] Spend remaining hours on sorter quality (merged beans, jet geometry) or on the UR5e arm for oversize matter? They compete for the same time.
- [ ] Merge PR #1? Your call by design. Reviewing 9 commits and 300 MB of run artifacts is the cost.
- [ ] The published review page is public by URL. Flip it to login-only if you prefer.

## Suggested next moves

Ranked by demo value per hour. README `Next` items folded in.

1. [ ] Rehearse with the two videos and the slide. ~1 h, highest value. Decide the headline and rate above first.
2. [ ] Longer paired seeds at 0.06 N (README). ~2 h machine time, ~20 min human. Makes the headline number defensible; can run unattended during breakfast.
3. [ ] Merged-target jet intersection and capture (README). 3 to 5 h. Biggest single recall lever (26% vs 47%). Medium risk of no improvement.
4. [ ] UR5e picking oversize matter off the infeed (README). 4 to 8 h. Highest wow if it works and the only answer to the 0/73 oversize result. Only if a second person is free; keep it a separate module.
5. [ ] Model camera backlog before claiming timing margin (README). 2 to 3 h. Zero demo value, needed before anyone says "real time" out loud.
6. [ ] Detector under 5 ms (README). 1 to 2 h. Low demo value; 5.44 ms is already 14x faster than the start.

Done from the README list: one-slide summary (draft above), roasted profile, unseen-object experiment, rate and latency sweeps, tuning screen, first closed loop.

## Gaps I could not close

- The night log has no matched 2,000 beans/s run at 0.06 N. The best-configuration numbers are at 1,000 beans/s only.
- No wall-clock figure for the roasted training run beyond the log; not needed for the demo.
- `APP_URL` was unset in my environment, so the page link in the task output was built from the API response; report if it does not open.
