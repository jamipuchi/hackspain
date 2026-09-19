"""Post-run inspection evidence. Ground truth here never feeds the controller."""
import json

import cv2
import numpy as np

from vision import draw_blobs


def valve_status(decision, fires, end_time, dt):
    if decision is None:
        return "UNDECIDED"
    if not decision.reject:
        return "KEEP"
    if decision.late:
        return "LATE: NO FIRE"
    # Older test doubles do not carry the simulator's explicit activation flag.
    if any(getattr(f, "activated", np.ceil(f.t_on / dt) * dt < min(f.t_off, end_time - dt / 2))
           for f in fires):
        return "FIRED"
    return "QUEUED" if fires else "NO VALID VALVE"


def save_samples(samples, ctrl, sim, out, fires_by_track):
    decisions = {d.tid: d for d in ctrl.decisions}
    beans = {b.uid: b for b in sim.beans}
    manifest = []
    for index, (frame, blobs, probabilities, tids, member_uids) in enumerate(samples):
        rows = []
        k = 0
        for i in range(blobs.n):
            d = decisions.get(int(tids[i]))
            fires = fires_by_track.get(int(tids[i]), [])
            uids = [int(uid) for uid in member_uids[i]]
            constituents = [beans[uid] for uid in uids if uid in beans]
            pred, confidence = "partial", None
            if not blobs.partial[i]:
                pred = ctrl.classes[int(probabilities[k].argmax())]
                confidence = float(probabilities[k].max())
                k += 1
            rows.append(dict(blob=i, track=int(tids[i]), predicted=pred, confidence=confidence,
                             decision_class=d.cls if d else None,
                             valve=valve_status(d, fires, sim.data.time, sim.dt),
                             nozzles=[f.nozzle for f in fires],
                             pulse_windows_s=[[f.t_on, f.t_off] for f in fires],
                             bean_uids=uids, truth=[bean.cls for bean in constituents],
                             any_jet_hit=any(bean.jet_hits for bean in constituents),
                             own_pulse_hit_uids=[uid for uid in uids
                                                 if (int(tids[i]), uid) in getattr(sim, "fire_hits", set())],
                             outcomes=[bean.outcome for bean in constituents]))
        colors = [(255, 100, 80) if r['valve'] == 'FIRED' else (100, 230, 130) for r in rows]
        strip = draw_blobs(frame, blobs, [str(i) for i in range(blobs.n)], colors)
        # Numbered crops keep class/action labels legible when blobs touch in the strip.
        width, tile_w, tile_h = frame.shape[1], 520, 150
        panel = np.full((280 + ((len(rows) + 3) // 4) * tile_h, width, 3), 24, np.uint8)
        panel[65:65 + frame.shape[0]] = strip
        def text(line, x, y, color=(235, 235, 235)):
            cv2.putText(panel, line, (x, y), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 1, cv2.LINE_AA)
        text(f"Inspection {index} at t={blobs.t:.3f}s | POST-RUN outcomes | red = valve pulse executed for this track", 12, 22)
        text("Truth lists every rendered bean centre in the connected component. FIRED means a queued valve reached t_on; partial blobs are not classified.", 12, 46)
        for i, r in enumerate(rows):
            x0, y0 = (i % 4) * tile_w, 280 + (i // 4) * tile_h
            x, y, w, h = map(int, blobs.bbox[i])
            crop = frame[max(0, y-3):min(frame.shape[0], y+h+3), max(0, x-3):min(width, x+w+3)]
            panel[y0:y0+90, x0:x0+90] = cv2.resize(crop, (90, 90))
            conf = f" {r['confidence']:.2f}" if r['confidence'] is not None else ""
            text(f"#{i} track {r['track']}: {r['predicted']}{conf}", x0+100, y0+18)
            text(f"{r['valve']} valves {r['nozzles']}", x0+100, y0+40, colors[i])
            text(f"fused class: {r['decision_class'] or '-'}", x0+100, y0+62)
            text(f"truth: {r['truth'] or '?'} | any hit: {r['any_jet_hit']}", x0+100, y0+84)
            text(f"outcome: {r['outcomes'] or '?'} | own hits {r['own_pulse_hit_uids']}", x0+100, y0+106)
        name = f"inspection_{index}.png"
        if not cv2.imwrite(str(out / name), cv2.cvtColor(panel, cv2.COLOR_RGB2BGR)):
            raise RuntimeError(f"Could not write {name}")
        manifest.append(dict(image=name, frame_time_s=blobs.t, blobs=rows))
    (out / "inspection_evidence.json").write_text(json.dumps(manifest, indent=2))
