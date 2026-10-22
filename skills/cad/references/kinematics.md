# Kinematics: joints, configurations, couplings and motions

Read this file when the user asks to articulate, pose or animate a model, or when
designing or reviewing joints, couplings, named poses, posed exports or motions.

An assembly's mechanism is written in its body, and the document keeps it:

- **Joints** are build123d's: `RevoluteJoint`, `LinearJoint`, `CylindricalJoint`,
  `BallJoint` and `RigidJoint`, connected with `connect_to`. The values they are
  connected at are the **as-built configuration**, the pose every file of the model
  shows.
- **Configurations** name other poses: `configuration(assembly, name, **values)`.
- **Couplings** make several joints move together by ratio: `couple(assembly, name, **joints)`.
- **Motions** are definitions over time: `motion(assembly, name, ...)`.

None of them moves the build. The `.xbf` keeps the mechanism, its configurations,
couplings and motion definitions; a STEP, STL or 3MF is placements only. A GLB
export bakes each motion into an animation clip of its name: the one place a motion
is played back.

## Joints place parts and make the mechanism

Build each part where it stands, give the parent the moving joint on an `Axis`, and
give the child a `RigidJoint` on the plane of that same axis. Connected at 0, the
child stays where it was built; connected at another value, it moves there, and that
value is as built:

```python
from cadgen.engine import configuration, glb, motion, xbf
import build123d as bd

ELBOW = bd.Axis((40, 0, 20), (0, -1, 0))   # +angle lifts the arm


@xbf(out="../XBF/hinge.xbf")
@glb(out="../GLB/hinge.glb")
def hinge():
    base = bd.Box(100, 60, 20, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    arm = bd.Pos(40, 0, 20) * bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    base.label, arm.label = "base", "arm"
    bd.RevoluteJoint("elbow", base, axis=ELBOW, angular_range=(-10, 120))
    bd.RigidJoint("elbow", arm, bd.Plane(origin=ELBOW.position, z_dir=ELBOW.direction).location)
    base.joints["elbow"].connect_to(arm.joints["elbow"], angle=30)   # as built: elbow 30
    assembly = bd.Compound(children=[base, arm], label="hinge")
    configuration(assembly, "closed", elbow=0)
    configuration(assembly, "open", elbow=90)
    return motion(assembly, "cycle", [(0, "closed"), (2, "open"), (5, "closed")])
```

- **`connect_to` on the joint that stays**: `parent.joints[name].connect_to(child.joints[name], angle=..., position=..., angles=...)`
  moves the child's part. Connect parts before putting them in the compound that
  holds them.
- **Values are degrees and millimetres.** A joint's `angular_range` or
  `linear_range` is its limits; a pose or a configuration outside them is refused.
- **A pair is named by its moving joint's label** (`elbow`). A label two pairs share
  takes the path of the part that holds it (`hand/left:knuckle`).
- **Links are parts that move as one**: parts whose rigid joints are connected are
  one link, and a part below a jointed sub-assembly moves with it. The ground is every
  link no `connect_to` moved, and every part no joint places.
- **A nested assembly is paired by label.** build123d's `connect_to` cannot place a
  part inside a nested assembly: put each part where it stands, author the two joints
  there with one label, and the engine pairs the moving joint with the `RigidJoint`
  of the same label nearest it in the tree. Two rigid joints of one label fasten their
  parts.
- **The document keeps what STEP AP242 can say, and nothing else.** A pair the
  built parts contradict (its part moved after `connect_to`, connected after its
  compound was made, a rigid joint off the joint's axis, or two parts rigid joints
  make one body) fails the build by name: fix where the part stands. Pairs closing
  a loop are kept and not posed. A configuration, motion or coupling naming a joint
  the assembly does not hold fails the build.
- **No mates vocabulary**: placement and motion are build123d's joints, and nothing
  else. A gear ratio between two joints is a coupling (below); a cam, a non-linear
  linkage or any other relation a ratio cannot say is a motion's function of time.

## Configurations

`configuration(assembly, name, **values)` names a pose by joint label, the joints it
leaves out as built, and returns the assembly. A cylindrical joint takes
`(angle, distance)` and a ball joint its three angles; a coupling takes its value
(`curl=0.5`). Configurations are what the viewer's Position lists, and what `--pose`
names.

## Couplings

`couple(assembly, name, **joints)` is a constraint, never a control: two or more
joints move together, each by its ratio, and only through it. At value `c` each joint
stands at its as-built value plus its ratio times `c`, so `c = 0` is as built. Its
range is its joints' limits: the tightest interval of `c` in which every joint stays
within its own (a joint with no limits bounds nothing), so give the joints the limits
the mechanism has. A joint takes at most one coupling, and a ball joint none. The
document keeps it as AP242's couplers: a gear pair, a rack and pinion, a screw (one
cylindrical joint's turn and travel) or a two-joint coupler.

```python
from cadgen.engine import couple, motion, xbf
import build123d as bd

CURL = {"index_mcp": 90, "index_pip": 100, "index_dip": 70}           # degrees at a full curl
BONES = {"base": 30, "proximal": 40, "middle": 25, "distal": 20}     # lengths along +X


@xbf
def index_finger():
    bones, x = [], -BONES["base"]
    for name, length in BONES.items():
        bone = bd.Pos(x, 0, 0) * bd.Box(length, 16, 12, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
        bone.label, x = name, x + length
        bones.append(bone)
    for joint, parent, child in zip(CURL, bones, bones[1:]):
        knuckle = bd.Axis(child.location.position, (0, 1, 0))
        bd.RevoluteJoint(joint, parent, axis=knuckle, angular_range=(0, CURL[joint]))
        bd.RigidJoint(joint, child, bd.Plane(origin=knuckle.position, z_dir=knuckle.direction).location)
        parent.joints[joint].connect_to(child.joints[joint], angle=0)   # as built: straight
    finger = bd.Compound(children=bones, label="index_finger")
    couple(finger, "index_curl", **CURL)                                # 0 straight, 1 a fist
    return motion(finger, "grip", [(0, {"index_curl": 0}), (1.5, {"index_curl": 1}), (3, {"index_curl": 0})])
```

`--pose '{"index_curl": 0.6}'` poses the knuckles at 54, 60 and 42 degrees; its
range, `[0, 1]`, is where every knuckle stays within its own. The viewer shows the
coupling's slider and none of its joints', and setting one of its joints alone is
refused. A planetary stage turns its carrier and planets from the sun, ranged by the
sun's limits:

```python
couple(assembly, "drive", sun=1.0, carrier=24 / 84, planet1=-0.952, planet2=-0.952, planet3=-0.952)
```

## Motions

A motion is a definition in the body, beside the configurations:

```python
motion(arm, "cycle", [(0, "folded"), (2, "reach"), (5, "folded")])          # waypoints, eased
motion(arm, "wave", [(0, {"wrist": -30}), (1, {"wrist": 30}), (2, {"wrist": -30})], ease="linear")
motion(arm, "nod", lambda t: {"elbow": 45 + 20 * math.sin(t)}, duration=6)  # a function of time
```

`motion(assembly, name, waypoints_or_fn=None, *, sweep=None, duration=None,
ease="cubic")` takes one of:

- waypoints `[(t, pose), ...]`: seconds from 0, increasing, each pose a
  configuration's name or `{joint: value}` (the rest as built); the last time is the
  duration;
- `sweep={joint: (from, to)}` with `duration`;
- a function `t -> {joint: value}` with `duration`, kept as samples close enough to
  it (1e-3 radian, 1e-3 of the model's size), the same code always the same samples.

`ease` is `cubic` (each step eased in and out, every joint arriving together at
every waypoint) or `linear` (constant speed). A motion is a finite clip, and a player
repeats it: one that comes back writes its way back as waypoints. A coupling's name
poses in waypoints and sweeps like a joint's. A motion naming a configuration or joint
the assembly lacks fails the build.

Every `@glb` and `cadgen export ... model.glb` carries each motion as a glTF
animation of its name, keyed so it stays within 1e-3 of the model's size of the true
motion, its duration in the animation's `extras.cadgen`. Blender, three.js and the
CAD Viewer's GLB view play it, repeating. Neither the decorator nor the export takes an argument
for it.

## Reviewing motion

A pose is drawn or written on demand, never declared:

```bash
cadgen snapshot XBF/hinge.xbf tmp/open.png --pose open
cadgen snapshot XBF/hinge.xbf tmp/elbow_45.png --pose '{"elbow": 45}'
cadgen export XBF/hinge.xbf STEP/hinge_open.step --pose open
```

`--pose` takes a configuration's name, or joint and coupling values as JSON (the
rest as built). An unknown configuration, joint or coupling, a value out of range,
and a coupling's joint set alone are refused by name before anything renders or is
written.

The viewer's Position poses a document live: a slider per coupling and per joint no
coupling moves, and the configurations as presets. To watch a motion, open the GLB the model
declares (or one `cadgen export` wrote) in the viewer: its clips play there.

A pose or a clip shows motion; it does not measure it. Checks on the `.xbf` measure
the parts as built, so a part that clears there can still pass through another
mid-motion. Measure a pose by exporting it and checking that STEP:

```python
from cadgen.engine import read_step, select
from cadgen.geometry import overlap_volume

posed = read_step("STEP/hinge_open.step")
arm, base = select(posed, "arm")[0], select(posed, "base")[0]
print(overlap_volume(arm.solids()[0], base.solids()[0]))   # 0: the open arm clears the base
```
