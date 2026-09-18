"""Build the MuJoCo physics model (and a plain preview look) from scene_def."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import mujoco
import numpy as np

import scene_def as sd

ASSET_DIR = sd.ASSETS


def _f(v) -> str:
    return " ".join(f"{x:.6g}" for x in v)


def build_xml() -> str:
    bodies = sd.build()
    root = ET.Element("mujoco", model="magnet_sorter")
    ET.SubElement(root, "compiler", angle="radian", autolimits="true", meshdir=str(ASSET_DIR), texturedir=str(ASSET_DIR))
    ET.SubElement(root, "option", timestep="0.002", integrator="implicitfast", gravity="0 0 -9.81", cone="elliptic", noslip_iterations="3")
    vis = ET.SubElement(root, "visual")
    ET.SubElement(vis, "global", offwidth="1600", offheight="1200")
    ET.SubElement(vis, "headlight", ambient="0.35 0.35 0.35", diffuse="0.5 0.5 0.5", specular="0.2 0.2 0.2")
    ET.SubElement(vis, "quality", shadowsize="4096")

    asset = ET.SubElement(root, "asset")
    ET.SubElement(asset, "texture", type="skybox", builtin="gradient", rgb1="0.75 0.78 0.82", rgb2="0.35 0.37 0.40", width="256", height="256")
    ET.SubElement(asset, "mesh", name="hexprism", file="hexprism.stl")
    for name, m in sd.MATERIALS.items():
        attrs = dict(name=name, rgba=_f((*m.base_color, m.alpha)), specular=f"{0.2 + 0.7 * m.metallic:.2f}", shininess=f"{max(0.05, 1 - m.roughness):.2f}", reflectance=f"{0.35 * m.metallic:.2f}")
        if m.texture and name != "marker":
            ET.SubElement(asset, "texture", name=f"tex_{name}", type="2d", file=m.texture)
            attrs["texture"] = f"tex_{name}"
            attrs["texrepeat"] = "2 2" if name in ("mdf", "wood") else "1 1"
        ET.SubElement(asset, "material", **attrs)
    for mid in sd.MARKERS:
        ET.SubElement(asset, "texture", name=f"tex_aruco_{mid}", type="2d", file=f"aruco_{mid}.png")
        ET.SubElement(asset, "material", name=f"aruco_{mid}", texture=f"tex_aruco_{mid}", specular="0.1", shininess="0.1")

    default = ET.SubElement(root, "default")
    ET.SubElement(default, "geom", condim="4", friction="0.8 0.02 0.001", solref="0.004 1", solimp="0.95 0.99 0.001")
    piece_def = ET.SubElement(default, "default", **{"class": "piece"})
    ET.SubElement(piece_def, "geom", condim="6", friction="0.8 0.02 0.01", solref="0.003 1", priority="1", contype="3", conaffinity="3")

    world = ET.SubElement(root, "worldbody")
    ET.SubElement(world, "light", pos="0.3 -0.2 1.6", dir="-0.15 0.1 -1", directional="false", castshadow="true", diffuse="0.7 0.7 0.7", specular="0.3 0.3 0.3")
    ET.SubElement(world, "light", pos="-0.6 0.7 1.2", dir="0.5 -0.5 -1", directional="false", castshadow="false", diffuse="0.35 0.35 0.38")
    def lookat_axes(pos, look):
        fwd = np.array(look, dtype=float) - np.array(pos, dtype=float)
        fwd /= np.linalg.norm(fwd)
        right = np.cross(fwd, [0, 0, 1])
        if np.linalg.norm(right) < 1e-6:
            right = np.array([0.0, -1.0, 0.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        return _f((*right, *up))

    for cname, cam in sd.CAMERAS.items():
        ET.SubElement(world, "camera", name=f"cam_{cname}", pos=_f(cam["pos"]), xyaxes=lookat_axes(cam["pos"], cam["lookat"]), fovy=str(cam["fovy_deg"]))
    px, py, pz = sd.PHONE_CAM["pos"]
    ET.SubElement(world, "camera", name="phone", pos=_f((px, py, pz)), xyaxes="0 -1 0 1 0 0", fovy=str(sd.PHONE_CAM["fovy_deg"]))
    ET.SubElement(world, "camera", name="cine", pos=_f(sd.CINE_CAM["pos"]), xyaxes=lookat_axes(sd.CINE_CAM["pos"], sd.CINE_CAM["lookat"]), fovy=str(sd.CINE_CAM["fovy_deg"]))

    elems: dict[str, ET.Element] = {"world": world}
    for b in bodies:
        parent = elems[b.parent]
        if b.name == "world_static":
            el = parent
        else:
            el = ET.SubElement(parent, "body", name=b.name, pos=_f(b.pos), quat=_f(b.quat))
            if b.joint:
                j = b.joint
                if j["type"] == "free":
                    ET.SubElement(el, "freejoint", name=f"{b.name}_free")
                else:
                    ja = dict(name=j.get("name", b.name), type=j["type"], axis=_f(j["axis"]), range=_f(j["range"]), damping=str(j.get("damping", 0.01)), armature=str(j.get("armature", 0.0)))
                    if j.get("stiffness"):
                        ja["stiffness"] = str(j["stiffness"])
                    ET.SubElement(el, "joint", **ja)
        elems[b.name] = el
        n_col = sum(1 for g in b.geoms if g.collide)
        # a body with no colliding geom puts its whole mass on its bulkiest geom (e.g. the magnet on the wrist)
        bulk = [(float(np.prod([s for s in g.size[:3]] or [0])), gi) for gi, g in enumerate(b.geoms) if g.kind in ("box", "cylinder", "hexprism")]
        mass_geom = max(bulk)[1] if bulk else 0
        for gi, g in enumerate(b.geoms):
            attrs = dict(pos=_f(g.pos), quat=_f(g.quat), material=g.material)
            if g.name:
                attrs["name"] = g.name
            if b.piece:
                attrs["class"] = "piece"
            if g.kind == "box":
                attrs.update(type="box", size=_f(g.size))
            elif g.kind == "cylinder":
                attrs.update(type="cylinder", size=_f(g.size[:2]))
            elif g.kind == "hexprism":
                attrs.update(type="mesh", mesh="hexprism")
                # scale via a per-geom mesh copy
                mesh_name = f"hex_{b.name}_{gi}"
                ET.SubElement(asset, "mesh", name=mesh_name, file="hexprism.stl", scale=_f((g.size[0], g.size[0], g.size[1])))
                attrs["mesh"] = mesh_name
            elif g.kind == "marker":
                attrs.update(type="box", size=_f(g.size), material=f"aruco_{g.marker_id}")
            elif g.kind == "wire":
                pts = sd.wire_points(g.points, n=max(8, 6 * len(g.points)))
                for k in range(len(pts) - 1):
                    a, c = pts[k], pts[k + 1]
                    ET.SubElement(el, "geom", type="capsule", fromto=_f((*a, *c)), size=f"{g.size[0]:.5f}", material=g.material, contype="0", conaffinity="0", group="2")
                continue
            if not g.collide:
                attrs.update(contype="0", conaffinity="0")
            elif g.contype is not None:
                attrs.update(contype=str(g.contype), conaffinity=str(g.conaffinity if g.conaffinity is not None else g.contype))
            if b.mass is not None and g.collide:
                attrs["mass"] = f"{b.mass / max(1, n_col):.6f}"
            elif b.mass is not None:
                attrs["mass"] = f"{b.mass:.6f}" if (n_col == 0 and gi == mass_geom) else "0"
            ET.SubElement(el, "geom", **attrs)
        if b.piece:
            ET.SubElement(el, "site", name=f"{b.name}_site", pos="0 0 0", size="0.001", rgba="0 0 0 0")
    # magnet face site
    wrist_el = elems["wrist"]
    ET.SubElement(wrist_el, "site", name="magnet_face", pos=_f((0, 0, -sd.HANG)), size="0.002", rgba="1 0 0 0")

    # electromagnet holding: one inactive weld per ferrous part, switched by the firmware model on contact
    eq = ET.SubElement(root, "equality")
    for b in bodies:
        if b.piece and b.piece.get("ferrous"):
            ET.SubElement(eq, "weld", name=f"mag_{b.name}", body1="wrist", body2=b.name, active="false", solref="0.002 1")

    act = ET.SubElement(root, "actuator")
    for jn in ("base", "shoulder", "elbow"):
        lo, hi = sd.JOINTS[jn]["range"]
        servo = sd.joint_servo(jn)
        ET.SubElement(act, "position", name=f"servo_{jn}", joint=jn, kp=str(servo["kp"]), kv=str(servo["kp"] * 0.025), ctrlrange=_f((lo, hi)), forcerange=_f((-servo["torque_nm"], servo["torque_nm"])))
    ET.indent(root)
    return ET.tostring(root, encoding="unicode")


def load() -> tuple[mujoco.MjModel, mujoco.MjData, list[sd.Body]]:
    xml = build_xml()
    (sd.ROOT / f"scene_generated_{sd.BUILD}.xml").write_text(xml)
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    return model, data, sd.build()


if __name__ == "__main__":
    m, d, bodies = load()
    mujoco.mj_forward(m, d)
    print(f"nbody={m.nbody} ngeom={m.ngeom} nu={m.nu} magnet_face={d.site('magnet_face').xpos}")
