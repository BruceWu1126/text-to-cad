# Migrations

Read this file when source or commands written for an older cadgen fail.
`cadgen doctor <skill-dir>` compares the installed version with the skill's pin.
cadgen cuts interfaces over with no compatibility layer: a retired command, flag,
argument, key or import is gone, and fails the way an unknown one does (an unknown
command, an unrecognized argument, an unknown key, an `ImportError`) without naming
what replaced it. The sections below map old forms to current ones.

## CAD app settings and telemetry in 0.8

Quick Edit has no enable/disable preference. The `features` settings API and
`/__cad/features` route are removed; a stale `features.quickEdit: false` does not
hide Quick Edit and needs no conversion.

The MCP `cad_telemetry` tool is removed. Use `cad_http` with method `GET`, URL
`/__cad/analytics` for status, or method `POST` and base64 body
`eyJzaGFyZSI6ZmFsc2V9` (`{"share":false}`) when the person requests opt-out.
No open CAD page is needed. Agents cannot opt in or reach other viewer routes.
The app page moves to protocol 8 and receives its route capability through
UI-only launch metadata; reopen a restored page after updating.

## Models build into documents in 0.8

cadgen 0.8 builds models with its engine: a model's result is an `.xbf` document,
and STEP, GLB, STL and 3MF are translations of it. A model written for 0.7 with no
decorator options still builds, writing its STEP and no document. Port it so it has
one:

```python
# Before
from cadgen import build123d as bd
from cadgen import step


@step(out="../STEP/bracket.step")
def bracket():
    ...


if __name__ == "__main__":
    bracket()
```

```python
# After
from cadgen.engine import step, xbf
import build123d as bd


@xbf(out="../XBF/bracket.xbf")
@step(out="../STEP/bracket.step")     # keep it only if something reads the STEP
def bracket():
    ...


if __name__ == "__main__":
    bracket()
```

The document is what the viewer follows build by build, what refs and
`cadgen inspect` read, and the model's cache across a daemon restart. Keep the
`__main__` calls: decoration only declares, and each explicit call builds its model.

| 0.7 | 0.8 |
| --- | --- |
| `@step(out=..., kinematics=..., materials=..., animation=...)` | `@step(out=...)`: `out` is every decorator's only argument; the rest is written in the body (below) |
| `@stl(mesh_tolerance=..., mesh_angular_tolerance=...)`, `--mesh-tolerance` | none: every mesh is made at FreeCAD's default tolerance |
| `python model.py --force`, `--json`, `--verbose` | `python model.py` takes no options. `CADGEN_ENGINE_MEMO=0` builds without memo hits; `Model.build()` returns the report as data; `Model.build(verify=True)` checks every replay |
| `python model.py --profile` | `Model.build(profile=True)` or `cadgen.build(code, model="part", profile=True)`; CLI: `cadgen build CODE --model part --profile`. Measures actual worker execution with normal cache hits; skipped work has no timings |
| `python model.py --no-daemon` | `Model.build(daemon=False)` from Python |
| `cadgen step build IN.step OUT.step` | `cadgen export IN.step OUT.step`, or `OUT.xbf` to read it in models |
| `cadgen step compile` | none: the viewer and snapshots read a STEP as it is |
| `cadgen stl build`, `cadgen 3mf build`, `cadgen glb build` | `cadgen export XBF/model.xbf OUT.stl` (`.3mf`, `.glb`), or from a STEP |
| `cadgen glb build --animation CLIP` | every GLB carries every motion of its model |
| `cadgen step snapshot`, `cadgen stl snapshot`, `cadgen 3mf snapshot`, `cadgen glb snapshot` | `cadgen snapshot SRC OUT.png`, one command for every kind |
| a `--job` file's `kinematics`, a STEP job's `selection`, `animation`, `video`, `quality`, `mode`, `output.viewLabels` | a job's pose is `jointValues` (as `--pose` takes it); a document's hidden and isolated parts are its `display.hidden` and `display.isolated` |
| snapshot `--kinematics` | `--pose` |
| snapshot `--animation`, `--time`, `--video` | none: export a GLB and play its clips |
| snapshot `--mode list` | none: list parts with `read_xbf(path).leaves`, and pick them with `select` |
| snapshot `--focus`, `--hide` | `--display '{"isolated": ["lid"]}'` and `--display '{"hidden": ["lid"]}'` (part refs); a Quick Edit packet given to `--annotation` or `--job` carries the person's own |
| `cadgen where`, `cadgen.where()` | `cadgen inspect`, `cadgen.inspect()`; existing files or live models only, never an implicit build |
| `cadgen store why`, `store where`, `store forget`, `store gc`, `store info` | none: no store. `cadgen inspect DOC` prints a document's build closure, `cadgen inspect DOC REF` what made an element |
| `cadgen daemon status` | `cadgen status` (and `cadgen stop`) |
| `cadgen serve DOC --foreground` | `cadgen serve DOC`: start or reuse the installation's server, print its URL, and exit |
| `CADGEN_CACHE_DIR`, `CADGEN_STORE_MAX`, `CADGEN_DAEMON_STATE_DIR` | none; `CADGEN_ENGINE_DIR` moves the engine daemon's socket |
| `read_scene(path).resolve(ref).shape()` | `select(path, ref)[0]` |
| `cadgen.revolute`, `slider`, `cylindrical`, `fastened`, `MateTarget`, `AssemblyHelper` | build123d joints in the body |
| `cadgen.couple(name, {dof: ratio}, limits=...)` | `couple(assembly, name, **ratios)`, two or more joints, ranged by their limits |
| `cadgen.clip(update, duration=...)` | `motion(assembly, name, ...)` |
| `from cadgen import report, track` | `from cadgen.engine import report, track` |
| `<name>.step.json` sidecars, `.params.js` | none: the document carries the mechanism; delete them |

An older cadgen's cache is no longer read: `CADGEN_CACHE_DIR`, else
`~/.cache/cadgen` (`$XDG_CACHE_HOME/cadgen`, `%LOCALAPPDATA%\cadgen` on Windows).
Delete it to reclaim its space.

## Mates become build123d joints

```python
# Before
KINEMATICS = {
    "mates": [cadgen.revolute("elbow", parent="#upper_arm", child="#forearm",
                              origin=(80, 0, 0), direction=(0, 0, 1), limits=(0, 150))],
    "couplings": [cadgen.couple("curl", {"elbow": 1}, limits=(0, 90))],
    "poses": {"bent": {"elbow": 60}},
}

@step(out="../STEP/arm.step", kinematics=KINEMATICS)
def arm():
    ...
    return bd.Compound(children=[upper_arm, forearm], label="arm")
```

```python
# After
ELBOW = bd.Axis((80, 0, 0), (0, 0, 1))

@xbf(out="../XBF/arm.xbf")
def arm():
    ...
    bd.RevoluteJoint("elbow", upper_arm, axis=ELBOW, angular_range=(0, 150))
    bd.RigidJoint("elbow", forearm, bd.Plane(origin=ELBOW.position, z_dir=ELBOW.direction).location)
    upper_arm.joints["elbow"].connect_to(forearm.joints["elbow"], angle=0)
    assembly = bd.Compound(children=[upper_arm, forearm], label="arm")
    return configuration(assembly, "bent", elbow=60)
```

- `revolute` is a `RevoluteJoint` on the parent's part with its rigid partner on the
  child's, `slider` a `LinearJoint` (`linear_range`), `cylindrical` a
  `CylindricalJoint` (`(angle, distance)` where `.turn` and `.travel` were two DOFs),
  and `fastened` two connected `RigidJoint`s. Targets were labels (`"#forearm"`); a
  joint is attached to the part itself.
- A rest value of 0 was the artifact as written; now the as-built configuration is the
  value each joint is connected at, so connecting at 0, with both frames on the axis,
  keeps the parts where they were built.
- `poses` become `configuration(assembly, name, **values)`, and a pose is drawn or
  written with `--pose` ([kinematics](kinematics.md)).
- A coupling is a constraint among two or more joints, its range their limits
  (`limits=` is gone: give the joints the limits instead). A coupling of one joint is
  that joint, so `curl` above is dropped and `elbow` poses alone.
- A mate's `axis` given as a ref becomes an `Axis` computed in the body, from the
  live shape (`select(part, ref)[0]`).

## Materials are values on the parts

```python
# Before
@step(materials={"definitions": {"cast": {"name": "As-cast steel", "roughness": 0.85, "metalness": 0.2}},
                 "assignments": [{"targets": ["#housing"], "material": "cast"}]})

# After, in the body
housing.material = materials.steel.but(name="As-cast steel", roughness=0.85, metallic=0.2)
```

A definition becomes a `Material` (or a catalogue entry, [materials](build123d-modeling.md#colour-and-materials)):
`metalness` is `metallic`, `baseColor` a linear RGBA `color` (`cadgen.srgb("#RRGGBB")`),
`clearcoat` and `clearcoatRoughness` are `coat` and `coat_roughness`, and `opacity`
is the colour's alpha. An assignment to a group is the material set on that assembly,
the default of every part below it that has none.

## Clips become motions

A clip moved occurrences directly through `m.get("#part").rotate(...)`. A motion
drives the mechanism's joints, so model what moves with joints first, then:

```python
# Before
def demo(t, m):
    m.get("#platter").rotate((0, 0, 1), 90 * t)

ANIMATION = {"demo": cadgen.clip(demo, duration=4)}

# After, in the body, with a RevoluteJoint "platter" on the base
motion(turntable, "demo", sweep={"platter": (0, 360)}, duration=4, ease="linear")
```

Waypoints, a sweep or a function of time over joint values replace `update(t, m)`
([motions](kinematics.md#motions)). A motion is a finite clip, and a player repeats
it. Opacity, visibility and tube deformation have no motion form. A motion plays in a
GLB export, not in the document's views.

## Refs name occurrences by path

A ref was `#o:4b1f9e2a7c03/f:bracket.make_box1.6`: an occurrence by a token and an
element by the operations' call counts. It is now
`/work/XBF/robot.xbf#robot/bracket/f:bracket.body.6`: an occurrence by the labels on
its way from the root, and an element by the function and the variable that made it
([references](inspection-and-validation.md#references)). The old spellings resolve to
nothing. Take refs again from the viewer's copy, or from the `label` of what `select`
returns, after rebuilding the model.
