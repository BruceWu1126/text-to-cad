"""Blender candidate: a GLB and a shot file in, PNG frames out. Cycles (Metal GPU, OIDN) or EEVEE.

Runs inside Blender (the app or the `bpy` wheel, same code):
  Blender -b --factory-startup --python blender_render.py -- SHOT.json ENGINE OUT_DIR [options]
    ENGINE  cycles | eevee
    --samples N        Cycles samples (adaptive) / EEVEE TAA samples
    --frames A:B       render a frame range of the shot (default: all)
    --scale S          resolution scale (0.5 renders a quick preview)
    --motion-blur      180-degree shutter
    --report           print what the glTF importer made of every material and clip, then exit
    --save-blend F     also save the scene (to inspect in the app)

The shot file (shots.py) is renderer-neutral: glTF-space cameras per frame, the clip time per
frame, an equirect environment in the canonical (three.js) mapping with a rotation, a floor.
"""
import json
import math
import sys
import time
from pathlib import Path

import bpy
from mathutils import Vector

argv = sys.argv[sys.argv.index("--") + 1:]
shot_path, engine, out_dir = argv[0], argv[1].lower(), Path(argv[2])
opt = {}
i = 3
while i < len(argv):
    a = argv[i]
    if a in ("--report", "--motion-blur"):
        opt[a[2:]] = True
        i += 1
    else:
        opt[a[2:]] = argv[i + 1]
        i += 2
shot = json.load(open(shot_path))
if "glb" in opt:
    shot["glb"] = opt["glb"]
out_dir.mkdir(parents=True, exist_ok=True)
T0 = time.time()


def g2b(v):
    """glTF (Y up) to Blender (Z up), as the importer converts."""
    return Vector((v[0], -v[2], v[1]))


# --- scene ---------------------------------------------------------------------------------
bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
scene.render.fps = shot.get("fps", 24)
t = time.time()
bpy.ops.import_scene.gltf(filepath=shot["glb"])
t_import = time.time() - t


def principled_report():
    rows = []
    for m in bpy.data.materials:
        if not m.use_nodes:
            continue
        nodes = m.node_tree.nodes
        p = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
        if p is None:
            rows.append((m.name, "no principled"))
            continue

        def val(name):
            s = p.inputs.get(name)
            if s is None:
                return None
            if s.is_linked:
                return "linked:" + s.links[0].from_node.type
            v = s.default_value
            try:
                return round(float(v), 3)
            except TypeError:
                return [round(x, 3) for x in v]
        vol = any(n.type in ("VOLUME_ABSORPTION", "PRINCIPLED_VOLUME") for n in nodes)
        rows.append((m.name, dict(base=val("Base Color"), metallic=val("Metallic"), rough=val("Roughness"),
                                  ior=val("IOR"), transmission=val("Transmission Weight"), coat=val("Coat Weight"),
                                  coat_rough=val("Coat Roughness"), aniso=val("Anisotropic"),
                                  aniso_rot=val("Anisotropic Rotation"), tangent=val("Tangent"),
                                  emission=val("Emission Color"), emission_strength=val("Emission Strength"),
                                  alpha=val("Alpha"), volume_absorption=vol,
                                  render_method=getattr(m, "surface_render_method", None))))
    return rows


def clip_report():
    out = []
    for a in bpy.data.actions:
        users = [o.name for o in bpy.data.objects if o.animation_data and o.animation_data.action == a]
        out.append(dict(action=a.name, frame_range=list(a.frame_range), objects=len(users)))
    return out


if opt.get("report"):
    print("REPORT import_s", round(t_import, 2))
    for name, row in principled_report():
        print("REPORT material", json.dumps([name, row]))
    print("REPORT clips", json.dumps(clip_report()))
    sys.exit(0)

# --- clip: frame offset of clip time 0 ------------------------------------------------------
clip_frame0 = None
if shot.get("clip"):
    ranges = [a.frame_range[0] for a in bpy.data.actions]
    clip_frame0 = min(ranges) if ranges else None

# --- world: the canonical equirect, rotated, and a flat colour for camera rays ---------------
world = bpy.data.worlds.new("studio")
scene.world = world
world.use_nodes = True
nt = world.node_tree
N, L = nt.nodes, nt.links
N.clear()


def math_node(op, a=None, b=None, c=None):
    n = N.new("ShaderNodeMath")
    n.operation = op
    for k, v in enumerate((a, b, c)):
        if v is None:
            continue
        if isinstance(v, (int, float)):
            n.inputs[k].default_value = v
        else:
            L.new(v, n.inputs[k])
    return n.outputs[0]


tc = N.new("ShaderNodeTexCoord")
sep = N.new("ShaderNodeSeparateXYZ")
L.new(tc.outputs["Generated"], sep.inputs[0])
neg_y = math_node("MULTIPLY", sep.outputs["Y"], -1.0)                # glTF z = -Blender y
az = math_node("ARCTAN2", neg_y, sep.outputs["X"])                     # atan2(gz, gx)
az = math_node("SUBTRACT", az, math.radians(shot.get("env_rot", 0.0)))
u = math_node("MULTIPLY_ADD", az, 1 / (2 * math.pi), 0.5)
lat = math_node("ARCSINE", sep.outputs["Z"])
v = math_node("MULTIPLY_ADD", lat, 1 / math.pi, 0.5)
comb = N.new("ShaderNodeCombineXYZ")
L.new(u, comb.inputs[0])
L.new(v, comb.inputs[1])
img = N.new("ShaderNodeTexImage")
img.image = bpy.data.images.load(shot["env"])
img.extension = "REPEAT"
img.interpolation = "Linear"
L.new(comb.outputs[0], img.inputs[0])
bg_env = N.new("ShaderNodeBackground")
bg_env.inputs["Strength"].default_value = shot.get("env_strength", 1.0)
L.new(img.outputs["Color"], bg_env.inputs["Color"])
bg_flat = N.new("ShaderNodeBackground")
bg_flat.inputs["Color"].default_value = (*shot.get("background", [0, 0, 0]), 1)
lp = N.new("ShaderNodeLightPath")
mix = N.new("ShaderNodeMixShader")
L.new(lp.outputs["Is Camera Ray"], mix.inputs[0])
L.new(bg_env.outputs[0], mix.inputs[1])
L.new(bg_flat.outputs[0], mix.inputs[2])
wout = N.new("ShaderNodeOutputWorld")
L.new(mix.outputs[0], wout.inputs["Surface"])

# --- or an outdoor set: Blender's physical sky (multiple scattering) with its sun -------------
sky = shot.get("sky")
if sky:
    N.clear()
    st = N.new("ShaderNodeTexSky")
    st.sky_type = sky.get("type", "MULTIPLE_SCATTERING")
    st.sun_elevation = math.radians(sky.get("sun_elevation", 10))
    st.sun_rotation = math.radians(sky.get("sun_rotation", 0))
    st.sun_size = math.radians(sky.get("sun_size", 1.5))
    bgs = N.new("ShaderNodeBackground")
    bgs.inputs["Strength"].default_value = sky.get("strength", 0.3)
    L.new(st.outputs["Color"], bgs.inputs["Color"])
    wo = N.new("ShaderNodeOutputWorld")
    L.new(bgs.outputs[0], wo.inputs["Surface"])

# --- floor ---------------------------------------------------------------------------------
objs = [o for o in scene.objects if o.type == "MESH"]
lo = Vector((1e9, 1e9, 1e9))
hi = -lo
for o in objs:
    for c in o.bound_box:
        w = o.matrix_world @ Vector(c)
        lo = Vector((min(lo[k], w[k]) for k in range(3)))
        hi = Vector((max(hi[k], w[k]) for k in range(3)))
size = (hi - lo).length
fl = shot["floor"]
bpy.ops.mesh.primitive_plane_add(size=size * 60, location=((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, fl["y"]))
floor = bpy.context.active_object
fm = bpy.data.materials.new("floor")
fm.use_nodes = True
fp = fm.node_tree.nodes["Principled BSDF"]
fp.inputs["Base Color"].default_value = (*fl["color"], 1)
fp.inputs["Roughness"].default_value = fl["roughness"]
fp.inputs["Metallic"].default_value = fl.get("metallic", 0.0)
fp.inputs["Coat Weight"].default_value = fl.get("coat", 0.0)
floor.data.materials.append(fm)

# --- an exploded sequence: every part lifts along an axis by its height in the stack ------
ex = shot.get("explode")
if ex:
    parts = [o for o in scene.objects if o.type == "MESH" and o is not floor]
    bpy.ops.object.select_all(action="DESELECT")
    for o in parts:
        o.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    axis = g2b(ex["axis"]).normalized()
    cen = {o.name: o.matrix_world @ (sum((Vector(c) for c in o.bound_box), Vector()) / 8) for o in parts}
    hts = [cen[o.name].dot(axis) for o in parts]

    def footprint(o):  # the plate is the part with the widest footprint across the axis
        d = o.dimensions
        return sorted((d.x, d.y, d.z))[-1] * sorted((d.x, d.y, d.z))[-2]
    plate = max(parts, key=footprint)
    h0, h1 = cen[plate.name].dot(axis), max(hts)
    n = len(shot["frames"])
    for o in parts:
        h = max((cen[o.name].dot(axis) - h0) / max(h1 - h0, 1e-9), 0.0)  # below the plate: stays
        base = o.location.copy()
        s0 = ex["start"] + (1.0 - h) * ex.get("stagger", 0.0)
        for f in range(n):
            x = min(max(((f / (n - 1)) - s0) / max(ex["end"] - ex["start"], 1e-6), 0.0), 1.0)
            o.location = base + axis * (ex["scale"] * size * h * x * x * (3 - 2 * x))
            o.keyframe_insert("location", frame=f + 1)

# --- camera, keyed per frame (so motion blur sees the move) --------------------------------
cam_data = bpy.data.cameras.new("cam")
cam = bpy.data.objects.new("cam", cam_data)
scene.collection.objects.link(cam)
scene.camera = cam
cam_data.sensor_fit = "HORIZONTAL"
cam_data.sensor_width = 36.0
cam_data.clip_start = max(size * 0.002, 1e-4)
cam_data.clip_end = size * 200
cam_data.dof.use_dof = True
frames = shot["frames"]
a, b = (int(x) for x in opt.get("frames", f"0:{len(frames)}").split(":"))
for f, fr in enumerate(frames):
    pos, tgt = g2b(fr["pos"]), g2b(fr["target"])
    cam.location = pos
    cam.rotation_mode = "QUATERNION"
    cam.rotation_quaternion = (tgt - pos).to_track_quat("-Z", "Y")
    cam_data.lens = fr["focal_mm"]
    cam_data.dof.focus_distance = fr["focus"]
    cam_data.dof.aperture_fstop = fr["fstop"]
    if len(frames) > 1:
        for path, owner in (("location", cam), ("rotation_quaternion", cam), ("lens", cam_data),
                            ("dof.focus_distance", cam_data), ("dof.aperture_fstop", cam_data)):
            owner.keyframe_insert(path, frame=f + 1)

# --- render settings -----------------------------------------------------------------------
scale = float(opt.get("scale", 1.0))
r = scene.render
r.resolution_x, r.resolution_y = int(shot["width"] * scale), int(shot["height"] * scale)
r.resolution_percentage = 100
r.image_settings.file_format = "PNG"
r.image_settings.color_mode = "RGB"
r.image_settings.color_depth = "8"
r.film_transparent = False
r.use_motion_blur = bool(opt.get("motion-blur"))
r.motion_blur_shutter = 0.5
vs = scene.view_settings
vs.view_transform = "AgX"
for look in ("AgX - Medium High Contrast", "Medium High Contrast"):
    try:
        vs.look = look
        break
    except TypeError:
        pass
samples = int(opt.get("samples", 256))
if engine == "cycles":
    prefs = bpy.context.preferences.addons["cycles"].preferences
    prefs.compute_device_type = "METAL"
    prefs.refresh_devices()
    for d in prefs.devices:
        d.use = d.type == "METAL"
    r.engine = "CYCLES"
    c = scene.cycles
    c.device = "GPU"
    c.samples = samples
    c.use_adaptive_sampling = True
    c.adaptive_threshold = 0.01
    c.use_denoising = True
    c.denoiser = "OPENIMAGEDENOISE"
    c.denoising_use_gpu = True
    c.denoising_input_passes = "RGB_ALBEDO_NORMAL"
    c.denoising_prefilter = "ACCURATE"
    c.max_bounces, c.glossy_bounces, c.transmission_bounces, c.transparent_max_bounces = 16, 8, 12, 16
    c.diffuse_bounces = 4
    c.caustics_reflective = c.caustics_refractive = True
    c.blur_glossy = 0.3
    r.use_persistent_data = True
else:
    r.engine = "BLENDER_EEVEE"
    e = scene.eevee
    e.taa_render_samples = samples
    for k, v in (("use_raytracing", True), ("ray_tracing_method", "SCREEN"), ("use_shadows", True),
                 ("shadow_ray_count", 2), ("shadow_step_count", 8), ("use_fast_gi", True),
                 ("fast_gi_method", "GLOBAL_ILLUMINATION")):
        try:
            setattr(e, k, v)
        except (AttributeError, TypeError) as err:
            print("EEVEE setting skipped", k, err)
    try:
        e.ray_tracing_options.resolution_scale = "1"
    except Exception as err:  # noqa: BLE001
        print("EEVEE option skipped", err)
    # EEVEE extracts the brightest part of the world as a sun; our rim strips behind the subject
    # would become one sun casting a hard shadow toward the camera. Off, unless the shot asks.
    world.sun_threshold = float(opt.get("sun-threshold", shot.get("eevee_sun_threshold", 10.0)))
    # a planar probe on the floor (off by default: at watch scale it captures every horizontal
    # face of the movement too, and mirrors the wrong scene into the bridges)
    if opt.get("planar"):
        bpy.ops.object.lightprobe_add(type="PLANE", location=floor.location)
        probe = bpy.context.active_object
        probe.scale = (size * 3, size * 3, 1)

if opt.get("save-blend"):
    bpy.ops.wm.save_as_mainfile(filepath=opt["save-blend"])

# --- frames --------------------------------------------------------------------------------
times = []
fps = shot.get("fps", 24)
for f in range(a, min(b, len(frames))):
    fr = frames[f]
    if clip_frame0 is not None and fr.get("clip_t") is not None:
        x = clip_frame0 + fr["clip_t"] * fps
        scene.frame_set(int(math.floor(x)), subframe=x - math.floor(x))
    elif len(frames) > 1:
        scene.frame_set(f + 1)
    if len(frames) > 1 and clip_frame0 is not None and fr.get("clip_t") is not None:
        # the camera keys sit at frame f+1; with a clip driving the timeline, set the camera directly
        pos, tgt = g2b(fr["pos"]), g2b(fr["target"])
        cam.animation_data_clear()
        cam_data.animation_data_clear()
        cam.location = pos
        cam.rotation_quaternion = (tgt - pos).to_track_quat("-Z", "Y")
        cam_data.lens = fr["focal_mm"]
        cam_data.dof.focus_distance = fr["focus"]
        cam_data.dof.aperture_fstop = fr["fstop"]
    r.filepath = str(out_dir / (f"{shot['name']}_{engine}.png" if len(frames) == 1 else f"{f:04d}.png"))
    t = time.time()
    bpy.ops.render.render(write_still=True)
    times.append(round(time.time() - t, 3))
    print(f"FRAME {f} {times[-1]:.2f}s", flush=True)

summary = dict(shot=shot["name"], engine=engine, samples=samples, width=r.resolution_x, height=r.resolution_y,
               import_s=round(t_import, 2), frames=len(times), first_s=times[0] if times else None,
               mean_s=round(sum(times[1:]) / max(len(times) - 1, 1), 3) if len(times) > 1 else (times[0] if times else None),
               total_s=round(time.time() - T0, 1), blender=bpy.app.version_string,
               motion_blur=bool(opt.get("motion-blur")), times=times)
(out_dir / f"{shot['name']}_{engine}.timing.json").write_text(json.dumps(summary, indent=1))
print("SUMMARY", json.dumps({k: v for k, v in summary.items() if k != "times"}))
