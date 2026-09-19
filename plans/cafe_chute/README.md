# Café chute — swing-gate bean inspector (build session, 19 Sep 2026)

Physical MVP for the coffee track: a 40 cm foam-board chute at 15° (adjustable 12–21°), an iPhone over a
9–15 cm inspection zone, and one SG90 that swings a 6 cm section of the right wall (30–36 cm) into the
channel so a suspect bean leaves into a cup while good beans roll straight into the bowl.

Open the HTML files in a browser (they are self-contained; the simulation loads three.js from jsDelivr):

| file | what |
| --- | --- |
| `cutting_plan.html` | cut list, sheet layout (50 × 100, 2.5 mm cartón pluma), side-panel template with coordinates, base layout, servo bracket section, assembly order, tilt blocks |
| `design_sheet.html` | side and plan views, door detail, timing budget, camera placement, software loop, mechanism comparison |
| `simulation_threejs.html` | interactive 3D simulation: beans, door, phone view, six rejection mechanisms ranked by reliability, slope selector, manual door mode, failure injection |
| `servo_wiring_demo.html` | first bench test: one SG90 on D9 / 5V / GND |

Live numbers for the software live in `sim/line/BUILD_ASBUILT.md` (mirrored from `~/robotics/line/`).
