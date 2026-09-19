# Replay validation

Verified 2026-09-19 in headless Chromium through agent-browser.

- Build: pass. The complete page is 1,842,120 bytes, below the 5 MiB publishing limit.
- Five zero-dependency artifact tests pass across all 121 frames: pose/identity integrity, quaternion norms, outcome counters, decision chronology, machine dimensions/valve parameters, embedded-data equality and offline references. An additional optional physical-provenance test passes with the pinned simulator, for **six tests total**.
- Fresh MuJoCo simulation matches the first three exported frames within 0.05 mm position / 0.00005 quaternion-component quantization error. Source-module hashes match the pinned checkout. This check covers the pre-classification segment; it does not assert bit-identical closed-loop reruns, whose measured controller latency can vary.
- Browser: WebGL scene loads; play advances and pause holds; scrubbing to the end displays exactly **3,552 beans through / 306 defects rejected / 129 good beans lost**. Three camera buttons work, orbit/zoom remains available, and recorded jets visibly glow.
- Desktop checked at 1440×900; mobile checked at 390×844. The portrait camera widens its field of view to keep the machine in frame. No document overflow.
- Standalone local playback creates **zero resource requests** after loading HTML. Dataset and code are embedded. The hosted swarm Page also loads successfully; its platform may inject platform-owned scripts.
- No browser runtime errors were reported on local or hosted pages.

## Measured performance

At the peak frame, **551 visible beans**, 11 active jet cues, 10 class InstancedMeshes (some classes may be empty):

| Camera | Measured rate | Sample | Viewport | Internal pixel ratio |
|---|---:|---:|---|---:|
| Overview | 19.47 fps | 98 frames / 5.0332 s | 1440×900 | 0.6 |
| Sorting moment | 16.24 fps | 82 frames / 5.0498 s | 1440×900 | 0.6 |

Renderer: `ANGLE (Google, Vulkan 1.3.0 (SwiftShader Device (Subzero) (0x0000C0DE)), SwiftShader driver)`. This is CPU software rendering, not a physical GPU. The peak simulation frame was held while matrices, colors, jet cues and rendering continued updating each animation frame. Raw measurements are in `evidence/benchmark-*.json`. The average is animation callbacks divided by elapsed time; the `fps` field is the viewer's separate rolling HUD sample.

The full dataset holds 4,000 unique beans across time, not 4,000 simultaneous bodies. The export peaks at 551 visible bodies at the requested feed rate. **60 fps on venue hardware has not been verified.** Software-renderer detection lowers render resolution; hardware browsers use up to 1.5× device pixel ratio. Before the demo, open the standalone page on the presentation laptop and inspect the live FPS readout.
