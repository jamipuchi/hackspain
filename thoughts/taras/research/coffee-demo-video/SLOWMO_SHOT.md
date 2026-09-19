# The air-jet moment

Status: Taras approved the concept and requested an ultra-slow-motion effect.
Three [basic stills](BASIC_RENDER_REVIEW.md) now show one recorded bean pair.
Pulse contact and the final animation remain unvalidated.

![Three moments of the slow-motion shot](/private/tmp/coffee-demo-video-previews/mockups/air-jet-slowmo-concept-v1.png)

Make this the central visual explanation of the film.
Track a defective bean and a nearby good bean from the belt edge toward the splitter.
Keep the nozzle and splitter visible so the viewer can understand their paths.

1. Both beans leave the belt. The camera follows them from the side.
2. A recorded air pulse pushes the defective bean downward. Its good neighbour continues along its own recorded path.
3. The defective bean passes below the splitter. The good bean passes above it.

This describes the desired final sequence, not a validated pulse-contact event.
Do not arrange an artificial collision or curve one bean around the other.
Select an actual two-bean event with enough separation for the camera to show both.

## Direction

Use noir-rim materials so viewers can distinguish a black defect from a pale good bean.
Keep the camera near side-on and track both beans laterally.
Let the trajectory change provide the action. Avoid camera rotation during the pulse.
Use approximately five seconds of screen time, subject to the event we capture.
Label the playback speed relative to simulation time after choosing that event.

The cyan arrow in the mockup represents force direction.
It is an optional explanatory overlay, not a rendering of air.
Display it only during the recorded pulse in a final clip.
Do not extend the pulse to make the overlay easier to see.

The generated nozzle and splitter are composition references, not exact copies of the scene geometry.

## Recording check

The existing replay contains 121 samples over approximately four simulated seconds at 30 Hz.
It stores pulse start, pulse end, nozzle, and force.
It does not store a per-bean contact record for each pulse.
Pulse durations can range from 3 to 12 milliseconds under the recorded policy.
The spacing between poses is approximately 33 milliseconds.
That replay cannot establish detailed motion during a short pulse.

Both the historical and checked-out simulator apply jet force along negative Z.
The splitter classifies objects above its height as accepted and objects below it as rejected.
Sources: `sim.py`, the `step` method, and `scene.py`, the splitter geometry.
The source replay identifies simulator revision `340e734d06a91b589248ab6d35f20520ebab22b6`.

A final slow-motion shot needs denser recorded poses and verified pulse contact for the selected bean.
Preserve both object IDs, pose timestamps, pulse timestamps, contact records, and final outcomes.
Record nearby beans too, so the crop does not falsely imply that the pulse affects only one object.
Keep the simulator, model, policy, and preset provenance with the capture.
The previous recording notes propose every-step capture at 500 Hz for a 2 ms timestep.
The existing exporter accepts at most 60 fps. A denser capture requires coordination with its owner.

No simulation, exporter change, or animation ran for this concept.

## Recorded pair for the still studies

The current stills use black bean 1470 and good bean 1480.
Their final recorded outcomes are reject and accept, respectively.
Frame 54 shows their belt-edge approach at 1.800 seconds.
Frame 55 shows both beans below the nozzle region at 1.834 seconds.
Nozzles 12 and 13 have recorded pulses active at that sample.
Those pulses run from 1.833064 to 1.837275 seconds, at 0.06 N each.
The replay does not identify which bean each pulse contacts.
Frame 57 shows their separated paths approaching the splitter at 1.900 seconds.
It does not show both completed crossings.

All nearby beans remain in each render.
The final side study omits both chute walls and their trim for visibility.
Each manifest lists these presentation cutaways and the exact source poses.
The gallery adds optional review markers. The clean PNGs contain no markers or simulated air graphics.

Stretching this 0.100-second interval to five seconds would imply 50-times slower playback.
That is an editorial target, not a rendered or validated result.
The final clip still requires denser capture and per-bean pulse-contact evidence.

## Revised edit proposal

Retain the first three compositions from the six-frame board.
Use one second of blueprint orientation, followed by five seconds of this slow-motion shot.
Reserve nine seconds for the actual UI and three seconds for the warm closing view.
The proposed total becomes 27 seconds.

The slow-motion segment will remain a separate clip for Taras's edit.
