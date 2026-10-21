"""Renderer-neutral shot files: every renderer adapter reads the same JSON and follows it.

All positions are glTF space (metres, +Y up). Azimuth: atan2(z, x) in degrees (0 = +X, 90 = +Z).
Each frame carries its own camera (pos, target, focal_mm, fstop, focus distance) and clip time,
so a camera move, a rack focus and a mechanism clip are just data.

usage: python shots.py ROOT   (writes ROOT/shots/*.json)
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

ROOT = Path(sys.argv[1])
GLB = ROOT / "glb"
ENV = ROOT / "env"
OUT = ROOT / "shots"


def smooth(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def lerp(a, b, t):
    return [float(a[i] + (b[i] - a[i]) * t) for i in range(len(a))] if isinstance(a, (list, tuple)) else float(a + (b - a) * t)


def orbit(target, az, el, dist):
    a, e = math.radians(az), math.radians(el)
    return [target[0] + dist * math.cos(e) * math.cos(a), target[1] + dist * math.sin(e), target[2] + dist * math.cos(e) * math.sin(a)]


def frame(pos, target, focal, fstop, focus_point=None, t=None):
    fp = focus_point or target
    d = float(np.linalg.norm(np.subtract(fp, pos)))
    # focus distance along the view axis (a thin lens focuses a plane)
    axis = np.subtract(target, pos) / np.linalg.norm(np.subtract(target, pos))
    d = float(np.dot(np.subtract(fp, pos), axis))
    return dict(pos=[float(x) for x in pos], target=[float(x) for x in target], focal_mm=focal, fstop=fstop,
                focus=d, clip_t=t)


def write(name, **shot):
    OUT.mkdir(parents=True, exist_ok=True)
    shot["name"] = name
    (OUT / f"{name}.json").write_text(json.dumps(shot, indent=1))
    print(name, len(shot["frames"]), "frames")


MOVE_CENTER = [-0.0008, -0.0005, -0.0007]
BALANCE = [-0.0004, 0.0022, 0.0076]
COLUMN = [0.0083, 0.0034, -0.0036]

# --- the movement ------------------------------------------------------------------------
movement = dict(glb=str(GLB / "movement-look.glb"), looks="movement", env=str(ENV / "studio_dark.hdr"),
                env_rot=0.0, env_strength=1.35, background=[0.004, 0.004, 0.005],
                floor=dict(y=-0.0034 - 0.00002, color=[0.006, 0.006, 0.007], roughness=0.12, metallic=0.0, coat=0.0),
                clip=None)
target = [MOVE_CENTER[0], MOVE_CENTER[1] + 0.0010, MOVE_CENTER[2] + 0.0015]
write("movement_hero", **movement, width=1920, height=1080, fps=24,
      frames=[frame(orbit(target, 90, 36, 0.122), target, 100, 32, BALANCE)])

frames = []
N = 120
for i in range(N):
    u = i / (N - 1)
    az, el, dist = lerp(72, 104, u), lerp(42, 33, u), lerp(0.150, 0.112, smooth(u))
    tgt = lerp(target, [target[0] + 0.002, target[1], target[2]], u)
    rack = smooth((i - 34) / 52)
    fp = lerp(BALANCE, COLUMN, rack)
    frames.append(frame(orbit(tgt, az, el, dist), tgt, 100, 16, fp))
write("movement_rack", **movement, width=1280, height=720, fps=24, frames=frames)

# --- the motorbike ------------------------------------------------------------------------
bike = dict(glb=str(GLB / "motorbike-look.glb"), looks="motorbike", env=str(ENV / "studio_stage.hdr"),
            env_strength=1.0, background=[0.011, 0.011, 0.013],
            floor=dict(y=0.0, color=[0.008, 0.008, 0.009], roughness=0.2, metallic=0.0, coat=0.0),
            eevee_sun_threshold=1000.0)  # EEVEE: no world sun (the rims would cast toward camera)
bt = [0.02, 0.46, 0.0]
write("motorbike_hero", **bike, env_rot=35 - 90, clip="steer", width=1920, height=1080, fps=24,
      frames=[frame(orbit(bt, 35, 6, 4.6), bt, 65, 4.0, [0.33, 0.62, 0.05], t=1.6)])

frames = []
for i in range(N):
    u = i / (N - 1)
    az, el = lerp(12, 72, smooth(u * 0.9 + 0.05)), lerp(5, 13, u)
    frames.append(frame(orbit(bt, az, el, 3.5), bt, 40, 4.0, [0.25, 0.6, 0.0], t=i / 24.0))
write("motorbike_orbit", **bike, env_rot=-45.0, clip="steer", width=1280, height=720, fps=24, frames=frames)

# --- an exploded movement: the parts lift off the plate in layers while the camera circles ----
frames = []
for i in range(N):
    u = i / (N - 1)
    az, el, dist = lerp(58, 118, smooth(u)), lerp(30, 38, u), lerp(0.165, 0.27, smooth(u))
    tgt = [target[0], target[1] + lerp(0.0, 0.015, smooth(u)), target[2]]
    frames.append(frame(orbit(tgt, az, el, dist), tgt, 100, 22, tgt))
write("movement_explode", **movement, width=1280, height=720, fps=24, frames=frames,
      explode=dict(axis=[0, 1, 0], scale=0.62, start=0.12, end=0.62, stagger=0.18))

# --- an outdoor set for the bike (Blender only: its physical sky), golden hour, concrete apron --
write("motorbike_daylight", **{**bike, "floor": dict(y=0.0, color=[0.2, 0.19, 0.18], roughness=0.65, metallic=0.0, coat=0.0)},
      env_rot=0.0, clip="steer", width=1920, height=1080, fps=24,
      sky=dict(type="MULTIPLE_SCATTERING", sun_elevation=8, sun_rotation=275, sun_size=1.2, strength=0.09),
      frames=[frame(orbit(bt, 35, 6, 4.6), bt, 65, 4.0, [0.33, 0.62, 0.05], t=1.6)])
