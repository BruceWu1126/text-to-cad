# Snapshots

A snapshot renders a saved document the way the CAD Viewer draws it:
`cadgen step snapshot` for STEP/STP, and the `stl`, `3mf` or `glb` command's
`snapshot` for a mesh (see [mesh exports](supported-exports.md)). It shows
arrangement and appearance; dimensions come from
[measurement](inspection-and-validation.md). The CAD topology, selection, section
and motion options below apply to STEP. A pose or clip frame takes `--kinematics`
and/or `--animation CLIP --time SECONDS`, and a clip can render to video; see
[kinematics](kinematics.md#reviewing-motion).

A STEP model with no surfaces (empty, or only curves and points) has nothing a
view can draw: the snapshot still writes its image and warns that the model has
no surfaces. A view or list that warns faces `could not be meshed` draws that part
without them (and a list counts it without them); they are usually slivers a
boolean left, and the warning names the part's ref and the faces (`#o1.2`, `f7`).

## Views

No fixed set of views shows every face. Opposed isometric views reveal more
exterior faces, orthographic views show patterns and silhouettes, and a clipped view
shows internal geometry ([cross-sections](#cross-sections)). Several views can share one JSON job:

```json
{
  "input": "models/part.step",
  "mode": "view",
  "outputs": [
    { "path": "/tmp/render/iso.png", "camera": "iso" },
    { "path": "/tmp/render/iso_opposite.png", "camera": { "direction": [-1, 1, -0.8] } },
    { "path": "/tmp/render/top_ortho.png", "camera": "top" },
    { "path": "/tmp/render/front_ortho.png", "camera": "front" }
  ],
  "output": { "viewLabels": true, "padding": 0.12, "sizeProfile": "diagnostic" }
}
```

Set `input` to the saved STEP/STP artifact using a relative or absolute path
(documents only: run a `.py` model first). With no explicit display settings,
snapshots use the `solid` preset, Light appearance and an orthographic isometric
camera. Every mode defaults to 1600x1200 (the `diagnostic` profile). Job `mode`
is `view` or `list`; display presets are a separate choice.

`--size-profile` (`output.sizeProfile` in a job) is one of exactly these names;
anything else is refused with this list:

| Profile | Size |
| --- | --- |
| `simple` | 1200x900 |
| `simple-square` | 1024x1024 |
| `diagnostic` (default), `labeled` | 1600x1200 |
| `assembly` | 1800x1200 |
| `assembly-large` | 1920x1440 |
| `presentation`, `contact-sheet` | 2400x1600 |
| `presentation-large` | 2800x1800 |

`--width`/`--height` (an output's `width`/`height` in a job) override the profile
with a whole number of pixels from 1 to 8192; a larger request is refused rather
than clamped. With `--job` they size every output in the packet.

`--display` accepts a mode name, inline JSON, or a JSON file path. The five
modes are `solid` (the default: shaded, with edge linework), `render`
(perspective projection and photographic lighting), `xray` (translucent surfaces
with occluded edges visible), `hidden-line` (linework with occluded edges
suppressed), and `wireframe` (edges only). Render uses perspective; other modes
use the shared projection, orthographic by default. `appearance` is `light`
(the CLI default) or `dark`. The Viewer and CLI accept the same sparse display
object, with settings stored separately for each mode:

```bash
cadgen step snapshot STEP/part.step tmp/review.png --display render
cadgen step snapshot STEP/part.step tmp/review.png --display '{"mode":"render","modes":{"render":{"floor":{"enabled":false},"background":{"opacity":0.5}}}}'
cadgen step snapshot STEP/part.step tmp/review.png --display display.json --camera front
```

Omitted parameters inherit their mode's defaults. Supplying a group does not
enable it; use `enabled: true` on a toggleable group. Opacity is 0 for fully
transparent and 1 for opaque.

| Location | Parameters |
| --- | --- |
| `camera` | `projection`: `orthographic` or `perspective`; `focalLength`: 20–200 mm |
| `modes.solid` | `parts`, boolean `flat`, `edges` (`enabled`, hex `color`), `grid`, `axes` |
| `modes.render` | `parts`, `lighting`, `background`, `floor` |
| `modes.xray` | `parts`, `opacity`: 0–1 (default 0.22), `edges` (hex `color`), `grid`, `axes` |
| `modes.hidden-line`, `modes.wireframe` | `edges` (hex `color`), `grid`, `axes` |

Within those modes, `parts` accepts `colorMode` (`original`, `single`, `by-part`),
hex `color`, and 1–50 hex `colors`. `lighting` accepts `quality` (`preview` or
`final`), `exposure` (-5–5), `rotation` (-180–180 degrees), `size` (0.25–3), and
`fill` (0–1). `background` accepts hex `color` and `opacity` (0–1). `floor`
accepts boolean `enabled`, `placement` (`lowest` or `origin`), `finish` (`matte`
or `glossy`), hex `color`, and `opacity` (0–1). `grid` and `axes` accept boolean
`enabled`, hex `color`, and `opacity` (0–1); `grid` also accepts `density`
(0.25–4). Grid and axes start off in every mode that offers them.

Render's floor sits at the model's lowest point; `placement: "origin"` moves it
to Z=0. `--camera` (or a job's top-level or per-output `camera`) sets pose and
framing (`preset`, `position`, `target`, `up`, `direction`, `zoom`,
`orthographicHalfHeight`); projection and focal length belong in
`display.camera`. `clip` and `exploded` are shared `display` tools. `edges`,
`clip`, `exploded`, and the `xray`, `hidden-line` and `wireframe` modes are
STEP-only; meshes and robot descriptions take `solid` or `render`. DXF uses
`--appearance` and has no scene display modes. Retired top-level style groups
and the old `grid` mode are refused.

`camera` is a preset (`front`, `back`, `left`, `right`, `top`, `bottom`, `iso`),
an `azimuth:elevation` pair, or a camera object. `output` takes `sizeProfile`,
`padding` (0–0.15), `renderScale` (1–3) and the booleans `viewLabels`,
`tightFrame` and `transparent`. Top-level `timeoutSeconds` is a positive number
and `scale` is `cad` or `urdf`. Unknown keys and out-of-range values are refused.

### Flags and job keys

A JSON job's keys are the flags without their dashes. `--animation CLIP --time
SECONDS` is one `animation` object in a job; `time` is not a top-level key:

```json
{
  "input": "models/arm.step",
  "kinematics": "open",
  "animation": { "clip": "demo", "time": 2.0 },
  "outputs": [{ "path": "/tmp/render/demo_t2.png", "camera": "iso" }]
}
```

`clip` is required; `time` is seconds, default 0. A bare `"animation": "demo"` is
refused (that is the flag's spelling). A `"video"` object beside `animation`
renders the clip's span instead of one frame; see
[rendering the whole clip](kinematics.md#rendering-the-whole-clip).

`--focus` and `--hide` nest under a job-level `selection` object, which applies
to the whole job (give a view its own job in a `jobs` array to select per view):

```json
{
  "input": "STEP/assembly.step",
  "mode": "view",
  "selection": { "hide": ["#o1.3", "#o1.4"] },
  "outputs": [{ "path": "tmp/render/without_covers.png", "camera": "iso" }]
}
```

With `--job FILE`, other flags override every job in it; there is no stdin form.
A TARGET given with `--job` replaces every job's `input`, so one job file renders
the same views over another model; OUT is ignored, and the jobs' outputs name
the files:

```bash
cadgen snapshot STEP/bracket_v2.step --job views.job.json
```

### Annotations and Quick Edit views

`--annotation FILE` (a job's `annotation`: a path or the object) draws a person's ink
over the model as they drew it in the CAD Viewer: each mark sits on its part,
follows it when the view explodes or poses the model, and is cut by a clip.
A DXF and `--mode list` take none.

A Quick Edit prompt names the views the person looked at as a `.job.json` file,
the PNGs it rendered, and their `.annotation.json` file when they drew. Every
path in the prompt and job is absolute, so the saved job can be replayed from
another working folder without copying the annotation into the model directory. The job's `measurements` are
the distances they measured, each `{start, end, label, color}`, drawn over every
view as the viewer drew them. `cadgen snapshot --job <job
file>` renders those views again: after an edit, run it to see the change from
where the person looked, with their annotation still on it. Add a TARGET to aim the
same views at another document.

## Cross-sections

To see inside a STEP model, clip it and look straight at the cut. `display.clip`
keeps the part of the model on one side of a plane across `x`, `y` or `z` and
fills the cut faces. The default display is orthographic, so a camera facing the
cut draws the section, with the walls beyond it:

```bash
cadgen step snapshot STEP/housing.step tmp/cut.png --camera back --display '{"clip": {"axis": "y", "at": 12.5}}'
```

`at` is the plane's coordinate on its axis in millimetres, in the model's own
frame (the same numbers as the `bounds` `--mode list` prints), so this cuts at
Y = 12.5. Without `at` the plane goes through the middle of the model:
`{"clip": {"axis": "y"}}` halves it. The clip keeps the lower side, so the cut faces
the positive axis: look at it with `right` for `x`, `back` for `y` and `top` for
`z`. `"invert": true` keeps the upper side instead; then use `left`, `front` or
`bottom`. The cut follows the display mesh, so a hole cut across its axis is a
many-sided polygon.

## Listing parts

`--mode list` writes no image: it prints one row per placed part — its `ref`
(what `--focus`, `--hide` and `scene.resolve(ref)` accept), its `name`, its exact
`bounds` in model units, and the `triangleCount`/`vertexCount` of the display
mesh a view would draw. `--focus`/`--hide` narrow the rows. A STEP list starts no
browser, so it is the cheap way to learn what an assembly contains.

OUT's extension picks the format: a view writes `.png`, a video `.mp4` or
`.gif`; any other extension is refused.

## Output paths

OUT (and an output's `path` in a job) is written exactly as given, relative to
the working directory. A refused request (an unknown key, value, pose, clip or
joint, or an option this input cannot take) leaves an existing file untouched,
so after a refusal the path can still hold an older image. An accepted request
clears the target first, so a failed build or render leaves no file. A directory
as OUT gets a timestamped name, printed on the `saved snapshot:` line.
