# CAM in cadgen: design

Status: draft for review (2026-10-13). Branch `amy/cam`, stacked on `amy/fea-assemblies` (draft PR #595, itself on #588).
This is a working design document; it is not package documentation and does not ship in the wheel.

## Goal

Add CAM (computer-aided manufacturing: turning a part into machine instructions) to cadgen the way FEA was added:
one public namespace, a generated CLI, an in-house engine, a result GLB the viewer renders through a lazily loaded
chunk, a JSON sidecar with the numbers, and a `cam` skill that teaches the agent to drive it.

```bash
cadgen cam plan part.step --job job.cam.json
# -> part.nc        G-code for the job's machine and post
# -> part.cam.glb   part, stock, fixtures, toolpaths and tools, for the viewer
# -> part.cam.json  per-operation stats, cycle time, findings
```

The agent never contacts a machine. Like the `gcode` skill, CAM writes files; a person loads them on the controller.

## Decisions already made

- **In-house engine.** OCCT (through OCP, already a dependency), shapely and numpy. No FreeCAD, no opencamlib, no
  external CAM process. numpy is already a base dependency (FEA moved it there), so CAM needs **no extra**: every verb
  works on a plain `cadgen` install.
- **One command does the job.** `cam plan` reads a part and a job file and writes all three outputs.
- **Mirror FEA's shape**, file for file, where it applies (table below).
- **Breadth: 40+ operations** across 2.5D and 3D milling, drilling, turning, 2D cutting, EDM and multi-axis, built in
  four phases.

## How it maps onto FEA

| Concern | FEA (as built on #595) | CAM |
| --- | --- | --- |
| Public namespace | `cadgen/fea.py`, `__all__ = ["faces", "parts", "solve"]` | `cadgen/cam.py`, `__all__ = ["features", "plan"]` |
| CLI | `_COMMANDS["fea solve"] -> cadgen.cli.fea_solve`, 28-line stubs over `cli_from_function` | `"cam features"`, `"cam plan"`, same stubs. Not in `_WARM_COMMANDS` |
| Input document | study (path, inline JSON or dict), `_internal/fea/study.py`, stdlib-only | job (same three forms), `_internal/cam/job.py`, stdlib-only |
| Registry | `analyses/__init__.py` `REGISTRY` of `Entry(name, tier, module, cls, word)` | `operations/__init__.py` `REGISTRY`, same `Entry` shape; unbuilt names refuse in a sentence |
| Engine | `_internal/fea/*`, numerics imported lazily | `_internal/cam/*`, same import discipline |
| Results | `results.py`: `FeaResult`, `FeaFacesResult` | `results.py`: `CamResult`, `CamFeature`, `CamFeaturesResult` |
| Findings | plain dicts in KiCad shape (`check`, `severity`, `type`, `summary`, `description`, `items`) | the same shape, from `_internal/cam/checks.py` |
| Progress | `CliLogger("fea")`, ~1 line / 10 s unless `--verbose` | `CliLogger("cam")`, same throttle |
| GLB | hand-rolled writer `_internal/fea/outputs.py`, extras on `meshes[0].extras`, `generator: "cadgen fea"` | `_internal/cam/outputs.py`, extras on `meshes[0].extras` (the part mesh), `generator: "cadgen cam"` |
| Sidecar | `<stem>.fea.json` | `<stem>.cam.json` |
| Extra | `cadgen[fea]` (netgen, scikit-fem, pyamg) | none |
| Viewer | `feaView.jsx` chunk, `carriesFeaResult`, Study panel under Select | `camView.jsx` chunk, `carriesCamResult`, Job panel under Select |
| Skill | `skills/fea`: SKILL.md, `references/` (study-file.md, one page per analysis), `agents/openai.yaml` | `skills/cam`: SKILL.md, `references/` (job-file.md, one page per operation family), `agents/openai.yaml` |
| Tests | `tests/python/packages/cadgen/test_fea*.py`, `support/fea_fixtures.py`, own CI job | `test_cam*.py`, `support/cam_fixtures.py`, runs in the cadgen job (no extra to install) |

## Public surface

`cadgen/cam.py` imports only `cadgen.results` at module scope. `--help` must not load OCP, shapely or numpy
(package README: import discipline, ~0.2 s pre-gate).

```python
def features(target: Path, *, occurrence: str | None = None, direction: str | None = None,
             verbose: bool = False) -> CamFeaturesResult:
    """List what a part offers a machinist: faces, holes, pockets, slots, bosses, turned diameters,
    each with the viewer's selector (#o1.f17), its size and the direction it can be reached from."""

def plan(target: Path, out: Path | None = None, *, job: str | dict | None = None,
         occurrence: str | None = None, post: str | None = None, verbose: bool = False) -> CamResult:
    """Plan a job: stock, setups, tools and operations from the job file; write G-code, the result GLB and
    its JSON sidecar."""
```

`features` is to CAM what `fea faces` is to FEA: the agent reads it, then names features in the job by selector.
`--post` overrides `machine.post` for a one-off export without editing the job.

`CamResult` (frozen, stdlib) carries `ok`, `document`, `occurrence`, `nc` (list of paths, one per setup),
`glb`, `sidecar`, `summary` (cycle time, cut and rapid length, tool changes, removed volume), `operations`
(per-op stats), `timings`, `warnings`, `findings`, plus `human_lines()`. A job that cannot be planned returns
`ok=False` with findings (exit 1), never a plausible G-code file (README law 10).

## The job file

Closed vocabulary (README law 9): an unknown key or value is a `ValueError` naming its path
(`"operations[2].stepover: ..."`) before anything heavy is imported. Units: mm, mm/min, rpm, seconds; an
`"units": "inch"` on the machine changes only what the G-code is written in.

```jsonc
{
  "machine": {
    "kind": "mill",                 // mill | router | lathe | laser | plasma | waterjet | wire_edm | sinker_edm
    "axes": 3,                      // 3 | 4 | 5 (mills); a lathe is 2 (X, Z), live tooling later
    "post": "grbl",                 // required: never guessed (see Posts)
    "travel_mm": [800, 600, 120],
    "max_rpm": 24000, "max_feed_mm_min": 5000,
    "units": "mm"
  },
  "material": "aluminum-6061",      // or {"name", "surface_m_min", "chipload_mm": {...}, "hardness_hb"}
  "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},   // box | cylinder | {"from": "stock.step"} | sheet (2D)
  "setups": [{
    "id": "top",
    "direction": "+Z",              // tool axis in part frame; "#o1.f4" to face a part face
    "origin": "stock_top_center",   // WCS: stock_top_center | stock_top_corner | part_bbox_* | [x, y, z]
    "clearance_mm": 10, "retract_mm": 3,   // heights above the stock top (these defaults); an operation may override
    "fixture": {"type": "vise", "jaw_height_mm": 20, "grip_mm": 5}   // vise | plate | chuck | collet | sheet | none; used for collisions
  }],
  "tools": [
    {"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 18,
     "stickout_mm": 30, "holder": {"diameter_mm": 32, "length_mm": 40}, "material": "carbide"}
  ],
  "operations": [
    {"op": "facing", "setup": "top", "tool": "T1", "stepdown_mm": 0.5},
    {"op": "adaptive_2d", "setup": "top", "tool": "T1", "features": ["#o1.f12"], "stock_to_leave_mm": 0.2},
    {"op": "contour_2d", "setup": "top", "tool": "T1", "features": ["#o1.f3"], "side": "outside", "tabs": {"count": 4}},
    {"op": "drill", "setup": "top", "tool": "T2", "features": ["#o1.f20", "#o1.f21"], "cycle": "peck", "peck_mm": 2}
  ],
  "view": {"show": {"rapids": true, "stock": "final"}}
}
```

Box stock's `margin_mm` is `[x, y, z_top, z_bottom]`: added on each side in x and in y, above the part and below
it (that one form only). A cylinder's `diameter_mm` and a radial margin exclude each other. A vise or chuck's
`grip_mm` is how much of the stock its jaws hold, and a vise may take `parallels_mm`; both feed the fixture and
holder collision checks. A `*_corner` origin is the min-X, min-Y corner (front left). A setup's `clearance_mm`
(default 10) is the height above the stock top the tool moves between features at, `retract_mm` (default 3) the
height it lifts to between passes; retract is above zero and at or under clearance, and an operation may override
either. A custom material states `surface_m_min` (the job is all metric; the material table is in m/min too).
`acrylic` is in the table. A post not built yet is refused like an operation not built yet.

Feeds and speeds are computed (below) unless an operation states `rpm`, `feed_mm_min`, `plunge_mm_min`.
Every operation takes `features` (selectors from `cam features`) or, where it makes sense, `region`
(a sketch/selection boundary) or `all: true`.

## Operations

Each operation is a registry entry with `parse`, `plan` (toolpath), `checks` and `human_lines`, like an FEA
analysis. Names are what the job writes in `"op"`.

**Phase 1: 2.5D milling and drilling (21).**
`facing`, `contour_2d` (inside/outside/on, tabs, lead-in/out, multiple depths, finishing passes), `pocket_2d`
(offset or zigzag), `adaptive_2d` (constant engagement, trochoidal in slots), `slot`, `bore` (helical),
`circular` (round pocket/boss), `thread_mill`, `chamfer_2d` (chamfer mill or V-bit), `engrave` (V-carve), `trace`
(follow a curve), `drill` (G81), `spot_drill` (G82), `peck` (G83), `chip_break` (G73), `tap` (G84),
`rigid_tap` (G84 with sync), `ream` (G85), `boring_cycle` (G86/G76), `counterbore`, `back_bore` (G87).
Canned cycles are posted as cycles when the post supports them and expanded to moves when it does not.

**Phase 2: 3D milling and stock simulation (13).**
`adaptive_3d` (roughing), `pocket_3d` (z-level roughing), `parallel` (raster), `contour_3d` (waterline),
`scallop`, `pencil`, `spiral`, `radial`, `morph` (between two curves), `steep_shallow`, `ramp`, `horizontal`
(flats only), `rest` (cleans what the stock simulation says a larger tool left).
Surface operations use a drop-cutter over the tessellated part (numpy, vectorised over a grid), with the
tessellation tolerance tied to the operation's tolerance.

**Phase 3: turning, cutting, EDM (15).**
Turning: `lathe_face`, `lathe_rough`, `lathe_finish`, `lathe_groove`, `lathe_part` (parting), `lathe_thread`
(G76), `lathe_bore`, `lathe_drill`. The turned profile comes from a section of the revolved part (shapely).
Cutting: `laser_cut`, `laser_engrave`, `plasma_cut` (lead-ins, kerf, pierce delay, THC on/off), `waterjet_cut`
(quality zones slow down at corners). Router profiling reuses `contour_2d`.
EDM: `wire_contour` (taper, skim passes, stop-tabs for the slug), `wire_open` (open profile),
`sinker` (electrode orbit around a cavity).

**Phase 4: multi-axis (6).**
`indexed` (3+2: any phase-1/2 operation run with a tilted tool axis), `rotary_wrap` (4th axis: a 2D path
wrapped round a cylinder), `rotary_parallel` (4th axis surface), `swarf` (5-axis, side of the tool on a ruled
wall), `multiaxis_contour` (5-axis, tool tilted to the surface normal with a lead/lag), `flowline_5x`.
Posts write TCP (G43.4/G43.5 style) where the post supports it and inverse kinematics for the machine's
table/head layout where it does not.

Total: 55. Anything named in the vocabulary but not built yet refuses with "not built yet" and the phase it is
in, like an unbuilt FEA analysis.

## Engine

`_internal/cam/`:

- `job.py`: parse and validate the job (stdlib only).
- `features.py`: feature recognition on the B-rep (planar faces and their normals, cylindrical holes and their
  depth/through-ness, pockets as closed loops of walls over a floor, turned diameters).
- `stock.py`, `setups.py`: stock solid, WCS transforms, reachability per setup direction.
- `tools.py`: tool shapes (flat, ball, bull, chamfer, V-bit, drill, tap, reamer, boring bar, thread mill,
  slitting saw, lathe inserts, wire, laser/plasma/waterjet kerf). One profile function per type gives the
  drop-cutter and collision geometry.
- `feeds.py`: material table x tool material x operation: surface speed to rpm, chip load per tooth to feed,
  radial chip thinning for adaptive, clamped to the machine (a clamp is a finding, not silent).
- `toolpath.py`: the one internal format every operation emits and every post reads:
  `Move(kind, xyz, axis, feed, op, t)` where `kind` is rapid / feed / plunge / arc_cw / arc_ccw / lead / cycle,
  `axis` is the tool direction (always +Z in setup frame until phase 4) and `t` is the cumulative time.
  Arcs are fitted where a path allows (G2/G3), within tolerance.
- `operations/`: registry plus one module per family.
- `sim/`: stock simulation. Phase 2 uses a heightfield (numpy grid in setup frame) for 3-axis; phase 3 a 2D
  profile (lathe) and sheet (cutting) in shapely; phase 4 a tri-dexel model for multi-axis.
- `posts/`: post processors (below).
- `checks.py`: findings (below).
- `outputs.py`: GLB, sidecar, G-code files. Byte-deterministic (README law 5).
- `run.py`: orchestration, `CliLogger("cam")`.

## Posts

A post is a small class: header, tool change, spindle and coolant, linear, arc, cycle, comment, footer, plus
capabilities (`arcs`, `helical`, `cycles`, `tcp`, `units`). The job must name one: a wrong post can crash a
machine, so cadgen never guesses (the same rule as the `gcode` skill's presets).

Phase 1: `grbl`, `linuxcnc`, `fanuc`, `haas`, `mach3`. Phase 3: `fanuc_lathe`, `linuxcnc_lathe`, `grbl_laser`
(M4 dynamic power), `linuxcnc_plasma`, `waterjet`, `fanuc_wire`, `sinker_iso`. Phase 4: `linuxcnc_5x`,
`fanuc_5x`, `haas_umc` (TCP), and `fanuc_g68_2`, a generic 3+2 post using G68.2.

Every `.nc` file opens with a comment block: part, job, setup, WCS, tools, and the line "Prove this program on
the machine: single block, feed override low, in air first."

## Findings

Same shape as FEA's (`check`, `severity`, `type`, `summary`, `description`, `items`), errors first, written in
plain words an engineer agrees with and a maker cannot misread.

- **error**: `gouge` (path cuts into the part past its tolerance), `holder_collision` (holder or shank hits stock,
  part or fixture), `fixture_collision`, `unreachable` (feature not reachable from any setup: undercut),
  `too_deep` (depth beyond flute length or stickout), `over_travel`, `no_post_support` (e.g. 5-axis on a 3-axis
  post), `empty_operation`.
- **warning**: `corner_left` (inside corner radius below the tool radius: material remains), `rest_material`
  (simulated stock left above stock-to-leave), `rpm_clamped`, `feed_clamped`, `full_slot` (100% engagement
  outside adaptive), `thin_wall`, `long_stickout` (L/D over 4), `uncut_feature` (a feature no operation touches).
- **info**: cycle time, tool changes, removed volume, the post used.

## Outputs

- **`<stem>.nc`**, or `<stem>.<setup>.nc` per setup when a job has more than one. OUT names the GLB (must end in
  `.cam.glb` or `.glb`), and the other two sit beside it.
- **`<stem>.cam.json`**: `generator`, `ok`, `document` (relative to the sidecar), `occurrence`, `job` (echo),
  `machine`, `post`, `material`, `setups`, `tools` (with computed rpm/feed), `operations` (per op: tool, time,
  cut/rapid length, its stats), `summary`, `findings`, `checks`, `warnings`, `files{nc, glb}` (names beside it).
  `allow_nan=False`, indent 2. No `timings`: they are wall-clock numbers, so a sidecar holding them would differ on
  every run and break README law 5 (the same job writes the same bytes); they stay on `CamResult` and `--json`.
  Written also when the job is not planned (`ok: false`, with the findings), when no `.nc` and no GLB are.
- **`<stem>.cam.glb`** (hand-rolled glTF 2.0 like FEA's):
  - `meshes[0]`: the part, triangles, with the extras (marker on a mesh, since the viewer's check skips
    non-meshes): `generator: "cadgen cam"`, `document`, `occurrence`, `machine`, `setups`, `tools`, `operations`,
    `summary`, `findings`, `checks`, `playback{duration_s}`, `view`.
  - stock meshes: initial, and (from phase 2) the simulated stock after each operation, for the playbar.
  - fixture meshes, and one mesh per tool (built from its profile, holder included).
  - toolpaths as glTF `LINES` primitives (mode 1), one per operation, with vertex attributes `_T` (time),
    `_KIND` (rapid/feed/plunge/...), `_OP` (operation index). Real line primitives, so any glTF viewer shows
    the paths; cadgen's viewer replaces them (below).
  - As built (task 13; `_internal/cam/outputs.py` holds the contract): the whole file is in the PART's frame
    (`extras.frame`), each setup's toolpaths moved back from its setup frame. Nodes in order: `part`, `stock`,
    `fixture:<setup>` (setups with a fixture), `tool:<id>` (each tool that cuts; mesh in the tool's frame, tip at
    the origin along glTF +Y, the node's translation/rotation at its first toolpath's start in that setup), then
    `path:<op index>:<op>`. The part mesh adds `_FACE` (face ordinal, the `N` of `#o1.fN`). `_KIND` codes: rapid
    0, feed 1, plunge 2, arc 3, lead 4, cycle 5; `_T` is the job clock in seconds, non-decreasing along a path;
    arcs drawn as chords within 0.05 mm, cycles as the moves they stand for. Each `extras.setups[i]` carries
    `quaternion` (setup to part rotation in glTF axes) for placing the tool.

## Viewer

Follows the FEA pattern exactly; nothing in apps/web or apps/mcp, nothing in `renderers/kit` (format-blind).

- **Detection**: `carriesCamResult(root)` in `GlbRenderer.jsx`, beside `carriesFeaResult`: a mesh whose
  `userData.generator === "cadgen cam"` with an `operations` array.
- **Lazy chunk**: `loadCamView = () => import("./camView.jsx")`, cached, fetched only for a CAM result.
  `GlbRenderer.lazy.test.tsx` gains the CAM rows: plain GLB 0 requests, FEA result loads only FEA, CAM result
  loads only CAM.
- **Toolpaths**: the chunk hides the glTF `LINES` objects (stock three draws them 1px with no theme) and redraws
  each from the same buffers as fat lines with `createScreenSpaceLineSegments` from core's `renderEdges.js`,
  coloured by `_KIND` (feed, rapid dashed, plunge, lead) from theme tokens. Clipping by `_T` shows the path up to
  the playhead.
- **Job panel** (`CamJobPanel.jsx`), under Select as FEA's Study is (`panel: {id: "tree", label: "Job"}`):
  setups, tools (with computed rpm/feed), operations (each with time, a visibility toggle, select to isolate it),
  summary and findings in the shared findings card (byte-for-byte FEA's, so the second PR to merge has only
  fixture conflicts).
- **Playbar**: one own clip through `useGlbAnimation`'s `own` argument. `apply(t)` places the tool mesh on the
  path at time `t`, clips the drawn path, and from phase 2 swaps the stock mesh to the snapshot of the current
  operation. A "Time" row with a play button and Preset entries per operation start, through the same motion
  contract FEA uses (`fea/motion.js` shape). Preview only, honours reduced motion, restores rest on leaving
  preview (settings-ui.md).
- **Display gates** via `displaySections` after Surfaces: Rapids, Stock, Fixtures, Tool.
- **Readout**: a cycle-time / current-operation readout overlay, which steps above the playbar in preview like
  FEA's colour bar.
- `settings-ui.md` gains a CAM section and a tools-table row; panels at 164 px, shared ToolPanel/FileSheet only.

## Skill

`skills/cam/`:

- `SKILL.md`: frontmatter `name: cam`, description under 600 characters, MIT. Sections in FEA's order: provenance;
  never contacts a machine; choose the operations (table linking references, a "Runs today" column); start
  with the task; setup (the pinned `cadgen` launch command, no extra); workflow (`cam features`, write the job,
  `cam plan`, read findings, look at the result in the viewer, hand over the `.nc` with the prove-out line);
  what the result means; read the findings (table of codes).
- `references/`: `job-file.md` (schema, units), `machines-and-posts.md`, `tools.md`, `feeds-and-speeds.md`,
  `milling-2d.md`, `drilling.md`, `milling-3d.md`, `turning.md`, `cutting.md`, `edm.md`, `multi-axis.md`,
  `findings.md`, `planned-next.md`.
- `agents/openai.yaml` with `$cam`.
- The `gcode` skill's description gains one line pointing CNC (subtractive) work to `$cam`.

## Registries to touch

`cli/__init__.py` `_COMMANDS`; `results.py` + `__all__`; `test_public_surface.py` (`PUBLIC_SURFACE`, `MIRRORS`);
`skills.sh.json`; root `README.md` skill table; `apps/docs/src/lib/content.ts` `skillGroups`;
`packages/cadgen/README.md` package-shape tree and "Verbs by format"; `select_checks.py` (a rule over
`_internal/cam/**`, `cam.py`, `cli/cam_*`, `skills/cam/**`, `test_cam*.py` selecting the cadgen Python job, and
`renderers/glb/cam*` selecting `web_ui`). Not touched: plugin manifests (they read `./skills/`), MCP server,
viewer scanner (`.cam.glb` already matches `.glb`).

## Tests

Short, one per contract, cheapest level (AGENTS.md):

- `support/cam_fixtures.py`: build123d fixtures in temp dirs (a plate with a pocket and holes, a 3D bump, a
  turned shaft, a sheet profile), `plan()`, `read_glb`, `glb_extras`, and a tiny G-code reader that back-plots
  a `.nc` into moves.
- `test_cam_protocol.py`: registry order, every operation module stdlib-only at import, job errors name their
  field, unbuilt operations refuse, `SkillDocs` (every built operation has a "Runs today" row).
- One file per family (`test_cam_milling_2d.py`, `test_cam_drilling.py`, ...). The core assertion for every
  operation: **the posted G-code, back-plotted, is the toolpath** (same moves within tolerance), and the
  simulated stock matches the part within stock-to-leave with no gouge.
- `test_cam_posts.py`: golden snippets per post (header, tool change, arc, cycle), byte-deterministic.
- `test_cam_checks.py`: each finding fires on a fixture made to trigger it, and does not fire on a clean one.
- Viewer: `camResult.test.ts` (pure readers), `GlbRenderer.cam.test.tsx` (Job panel, toggles, Time row),
  lazy-chunk rows. No new WebGL test unless the fat-line clipping needs one.
- CI: the cadgen Python job picks them up (no extra). If the CAM suite grows past ~2 minutes it gets its own
  job, as FEA did, with its cost stated in the PR.

## Phases

1. **Core and 2.5D**: job, features, stock, setups, tools, feeds, toolpath format, posts (5), the 21 phase-1
   operations, heightfield-free checks (gouge by distance to part, reach, depth, travel), outputs, viewer
   (detection, chunk, fat-line paths, Job panel, playbar with tool), skill, registries. Shippable alone.
2. **3D milling and stock simulation**: drop-cutter, 13 operations, heightfield sim, per-operation stock
   snapshots in the GLB and the viewer, rest machining, `rest_material` and `corner_left` from the sim.
3. **Turning, cutting, EDM**: 15 operations, 2D sims, 7 posts, lathe/sheet setups in the viewer.
4. **Multi-axis**: tool axis in `Move`, tri-dexel sim, 6 operations, TCP posts, machine kinematics for collision.

Each phase is its own plan, its own reviewed set of commits, and is looked at on screen before it is called done.

## Out of scope

Sending to a machine or controller; importing vendor tool libraries (Fusion, HSMWorks); feed optimisation from
measured load; machine-specific post tuning beyond the listed posts; nesting for sheet cutting; adaptive
toolpaths' patented specifics (ours is a constant-engagement offset approach, clean-room).

## Open questions

1. **First post to prove on a real machine.** Phase 1 ships five; which controller do you have access to for a
   prove-out (`grbl` on a hobby router, or `haas`/`fanuc` on a VMC)? That one gets an air-cut check before the
   phase is called done.
2. **Is `features` worth a verb in phase 1**, or should the agent use `cadgen fea faces` selectors? A separate verb
   keeps the skills independent and adds machining facts (hole depth, reach direction); recommended.
3. **PR shape.** One draft PR per phase, each stacked on the previous (and the first on #595), for Jake to review.
