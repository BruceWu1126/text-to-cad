"""Mitsuba 3 candidate (metal_ad_rgb on the M1 GPU): the same shot file, PNG frames out.

There is no glTF importer: glbscene.py reads the GLB (meshes, world matrices posed by the clip,
every KHR_materials_* field) and this maps each look onto Mitsuba's `principled` BSDF. The
environment is the canonical equirect (to_world calibrated against the marker HDRI), the camera
a thin lens, the floor a rectangle. Linear output -> OIDN (pyoidn, albedo + normal guides) ->
Blender's own OCIO AgX grade, so the tone mapping is identical to the Blender candidate's.

usage: python mitsuba_render.py SHOT.json OUT_DIR [--spp N] [--frames A:B] [--scale S]
                                 [--variant metal_ad_rgb] [--no-denoise]
"""
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glbscene import GLB  # noqa: E402
from grade import grade  # noqa: E402

args = sys.argv[1:]
shot_path, out_dir = Path(args[0]), Path(args[1])
opt = {}
i = 2
while i < len(args):
    if args[i] in ("--no-denoise",):
        opt[args[i][2:]] = True
        i += 1
    else:
        opt[args[i][2:]] = args[i + 1]
        i += 2
T0 = time.time()

import drjit as dr  # noqa: E402
import mitsuba as mi  # noqa: E402

mi.set_variant(opt.get("variant", "metal_ad_rgb"))
mi.set_log_level(mi.LogLevel.Error)
T = mi.ScalarTransform4f
shot = json.load(open(shot_path))
out_dir.mkdir(parents=True, exist_ok=True)
frames = shot["frames"]
spp = int(opt.get("spp", 256))
scale = float(opt.get("scale", 1.0))
W, H = int(shot["width"] * scale), int(shot["height"] * scale)

t = time.time()
g = GLB(shot["glb"])
looks = {}


def bsdf(mat_index):
    if mat_index in looks:
        return looks[mat_index]
    L = g.material(mat_index)
    p = {"type": "principled",
         "base_color": {"type": "rgb", "value": [float(c) for c in L["base_color"][:3]]},
         "metallic": float(L["metallic"]),
         "roughness": float(max(L["roughness"], 0.01)),
         "eta": float(max(L["ior"], 1.01)),
         "clearcoat": float(L["clearcoat"]),
         "clearcoat_gloss": float(1.0 - L["clearcoat_roughness"]),
         "spec_trans": float(L["transmission"]),
         # brushed finishes: Mitsuba takes the anisotropy frame from the UV parameterisation,
         # and these meshes carry tangents but no UVs -- left isotropic (see the report)
         "anisotropic": 0.0}
    d = p if L["transmission"] > 0 else {"type": "twosided", "material": p}
    looks[mat_index] = (mi.load_dict(d), L)
    return looks[mat_index]


def world_arrays(inst, prim):
    Wm = inst.world
    P = (Wm[:3, :3] @ prim.positions.T.astype(np.float64)).T + Wm[:3, 3]
    if prim.normals is not None:
        Nm = np.linalg.inv(Wm[:3, :3]).T
        Nn = (Nm @ prim.normals.T.astype(np.float64)).T
        Nn /= np.maximum(np.linalg.norm(Nn, axis=1, keepdims=True), 1e-12)
    else:
        Nn = None
    return P.astype(np.float32), None if Nn is None else Nn.astype(np.float32)


clip = shot.get("clip")
t0 = frames[0].get("clip_t") if clip else None
instances = g.instances(clip, t0 or 0.0) if clip else g.instances()
shapes = {}
keys = []  # (key, instance index, prim)
for ii, inst in enumerate(instances):
    for prim in inst.prims:
        b, L = bsdf(prim.material)
        P, Nn = world_arrays(inst, prim)
        props = mi.Properties()
        props["bsdf"] = b
        if any(L["emissive"]) and L["emissive_strength"] > 0:
            rad = [float(c * L["emissive_strength"]) for c in L["emissive"]]
            props["emitter"] = mi.load_dict({"type": "area", "radiance": {"type": "rgb", "value": rad}})
        key = f"m{len(keys)}"
        mesh = mi.Mesh(key, len(P), len(prim.indices), props=props, has_vertex_normals=Nn is not None)
        mp = mi.traverse(mesh)
        mp["vertex_positions"] = mi.Float(P.ravel())
        mp["faces"] = mi.UInt32(prim.indices.astype(np.uint32).ravel())
        if Nn is not None:
            mp["vertex_normals"] = mi.Float(Nn.ravel())
        mp.update()
        shapes[key] = mesh
        keys.append((key, ii, prim))

lo, hi = g.bounds()
size = float(np.linalg.norm(hi - lo))
center = (lo + hi) / 2
fl = shot["floor"]
floor = {"type": "rectangle",
         "to_world": T().translate([float(center[0]), float(fl["y"]), float(center[2])]) @ T().rotate([1, 0, 0], -90) @ T().scale(size * 30),
         "bsdf": {"type": "twosided", "material": {"type": "principled", "base_color": {"type": "rgb", "value": fl["color"]},
                                                     "roughness": float(fl["roughness"]), "metallic": float(fl.get("metallic", 0.0))}}}
f0 = frames[0]


def camera_values(fr):
    hfov = math.degrees(2 * math.atan(18.0 / fr["focal_mm"]))
    ap = (fr["focal_mm"] / 1000.0) / (2 * fr["fstop"])
    return T().look_at(origin=fr["pos"], target=fr["target"], up=[0, 1, 0]), hfov, ap, fr["focus"]


tw, hfov, ap, fd = camera_values(f0)
scene_dict = {
    "type": "scene",
    "integrator": {"type": "aov", "aovs": "albedo:albedo,nn:sh_normal",
                   "img": {"type": "path", "max_depth": 16, "rr_depth": 8, "hide_emitters": True}},
    "env": {"type": "envmap", "filename": shot["env"], "scale": float(shot.get("env_strength", 1.0)),
            "to_world": T().rotate([0, 1, 0], 90.0 - shot.get("env_rot", 0.0))},
    "camera": {"type": "thinlens", "fov": hfov, "fov_axis": "x", "to_world": tw, "aperture_radius": ap,
               "focus_distance": fd, "near_clip": size * 0.002, "far_clip": size * 400,
               "film": {"type": "hdrfilm", "width": W, "height": H, "rfilter": {"type": "gaussian"}, "pixel_format": "rgb"},
               "sampler": {"type": "independent", "sample_count": spp}},
    "floor": floor,
    **shapes,
}
scene = mi.load_dict(scene_dict)
params = mi.traverse(scene)
t_load = time.time() - t
print(f"scene: {len(keys)} meshes, {sum(len(p.indices) for _, _, p in keys):,} tris, load {t_load:.1f}s", flush=True)

a, b = (int(x) for x in opt.get("frames", f"0:{len(frames)}").split(":"))
times, parts = [], []
for f in range(a, min(b, len(frames))):
    fr = frames[f]
    tf = time.time()
    if clip and fr.get("clip_t") is not None and f != 0:
        posed = g.instances(clip, fr["clip_t"])
        for key, ii, prim in keys:
            if not np.allclose(posed[ii].world, instances[ii].world):
                P, Nn = world_arrays(posed[ii], prim)
                params[f"{key}.vertex_positions"] = mi.Float(P.ravel())
                if Nn is not None:
                    params[f"{key}.vertex_normals"] = mi.Float(Nn.ravel())
        instances = posed
    tw, hfov, ap, fd = camera_values(fr)
    params["camera.to_world"] = mi.Transform4f(tw)
    params["camera.x_fov"] = hfov
    params["camera.aperture_radius"] = ap
    params["camera.focus_distance"] = fd
    # opaque, or Dr.Jit bakes the camera into the megakernel and recompiles it every frame (+0.5-2 s)
    for k in ("camera.to_world", "camera.x_fov", "camera.aperture_radius", "camera.focus_distance"):
        dr.make_opaque(params[k])
    params.update()
    t_up = time.time() - tf
    t = time.time()
    # passes of <= 2^26 paths each (one megakernel launch per pass), averaged
    per = max(1, min(spp, (1 << 26) // (W * H)))
    passes = (spp + per - 1) // per
    arr = None
    for k in range(passes):
        n = min(per, spp - k * per)
        img = np.array(mi.render(scene, params, spp=n, seed=f * 1009 + k))
        arr = img * n if arr is None else arr + img * n
    arr /= spp
    t_render = time.time() - t
    color, albedo, normal = arr[..., 0:3], arr[..., 3:6], arr[..., 6:9]
    t = time.time()
    if not opt.get("no-denoise"):
        import pyoidn
        color = pyoidn.denoise(np.ascontiguousarray(color, dtype=np.float32), np.ascontiguousarray(np.clip(albedo, 0, 1), dtype=np.float32),
                               np.ascontiguousarray(normal, dtype=np.float32), hdr=True, quality="high")
    t_dn = time.time() - t
    t = time.time()
    from PIL import Image
    name = f"{shot['name']}_mitsuba.png" if len(frames) == 1 else f"{f:04d}.png"
    Image.fromarray(grade(color)).save(out_dir / name)
    t_io = time.time() - t
    times.append(round(time.time() - tf, 3))
    parts.append(dict(update=round(t_up, 3), render=round(t_render, 3), denoise=round(t_dn, 3), grade_save=round(t_io, 3)))
    print(f"FRAME {f} {times[-1]:.2f}s {parts[-1]}", flush=True)

summary = dict(shot=shot["name"], engine="mitsuba-" + mi.variant(), spp=spp, width=W, height=H, load_s=round(t_load, 2),
               frames=len(times), first_s=times[0] if times else None,
               mean_s=round(sum(times[1:]) / max(len(times) - 1, 1), 3) if len(times) > 1 else (times[0] if times else None),
               total_s=round(time.time() - T0, 1), mitsuba=mi.__version__, times=times, parts=parts)
(out_dir / f"{shot['name']}_mitsuba.timing.json").write_text(json.dumps(summary, indent=1))
print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k not in ("times", "parts")}))
