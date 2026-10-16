# Per-mode display settings

The Viewer and snapshots share one strict sparse contract. Core's `common/viewSettings.js`
validates it and resolves the active mode into renderer groups. `common/sceneSettings.js`
translates those groups into the scene recipe; renderer code never reads saved mode settings.
The binding controls are in [settings-ui.md](settings-ui.md#display-settings-and-section-primitives).

| Mode | Settings | Surface / edge recipe |
| --- | --- | --- |
| Solid | parts, flat, edges, grid, axes | Shaded (or flat), visible edges enabled by default |
| Render | parts, lighting, background, floor | Shaded, no edges, photographic studio |
| X-ray | parts, opacity, edges, grid, axes | 22% opacity, all edges |
| Hidden line | edges, grid, axes | Depth-only surfaces, visible edges |
| Wireframe | edges, grid, axes | No fill, all edges |

Grid and axes start off and are independent. Solid alone can disable its edges; other edge
modes always draw them. Render's floor starts enabled at the lowest point. Appearance,
camera, Clip and Explode are shared. Camera defaults to orthographic with a 50mm lens;
Render resolves to perspective without overwriting that shared camera choice.

## Sparse public contract

```json
{
  "mode": "render",
  "appearance": "light",
  "camera": { "projection": "orthographic" },
  "modes": {
    "solid": { "grid": { "enabled": true } },
    "render": { "lighting": { "exposure": 0.5 }, "floor": { "finish": "glossy" } },
    "xray": { "opacity": 0.3 }
  }
}
```

Omitted mode is Solid. Each mode object overrides only supplied values. `parts` contains
`colorMode` (`original`, `single`, `by-part`), `color` and `colors`. `edges` contains `color`
and, for Solid only, `enabled`. `grid` contains `enabled`, `color`, `opacity`, `density`;
`axes` omits density. Render's `lighting` contains `quality`, `exposure`, `rotation`, `size`,
`fill`; `background` contains `color`, `opacity`; `floor` contains `enabled`, `placement`,
`color`, `opacity`, `finish`. Only settings in that mode's list are accepted.

Viewer appearance inherits the host; snapshots default to light. Lighting quality inherits
Preview in a viewer and Final in a snapshot. Disabled guides, edges and floor retain their
chosen appearance for re-enabling. There is no Custom state, Grid mode, legacy alias or
migration. Old-shaped saved display state is discarded at the file-session read boundary.
The same shape powers live `setDisplaySettings` and snapshot `--display`; `--display render`
is shorthand for `{ "mode": "render" }`. `--camera` holds pose/framing only.

## State and updates

`kit/view-settings/viewSettingsStore.js` owns one sparse state per mounted file.
`selectMode(mode)` selects without erasing overrides. `patchMode(mode, patch)` merges that
mode's settings without selecting it. `resetMode(mode)` removes only its overrides.
`patch(patch)` merges the shared fields and any supplied `modes` branches. `restore` replaces
state once on mount; persistence reads the latest `getSnapshot().display`.

Reset preserves the selected mode, every other mode, camera, appearance, Clip and Explode.
No action reframes the camera, changes model pose, selection or visibility. The store preserves
equal JSON branches; editing an inactive mode leaves the scene identity unchanged. Clip
edits do not invalidate materials, lighting, geometry or camera. No-op actions publish nothing.
Do not merge captured props, persist resolved defaults or keep a second display state.

The view injects offered `modes` and `settings`. Mesh views offer Solid and Render and no
edges, Clip or Explode. Unsupported saved modes resolve to Solid without rewriting storage;
unoffered settings do not affect the scene. The shared UI discovers no host APIs.

## Rendering costs and camera behavior

The photographic rig stays lazy. Selecting Render creates its photographic
softbox environment; the other modes use neutral CAD lighting. Authored materials are read-only. Color
overrides are presentation settings, not edits to the model or its material
assignments.

| Lighting quality | Screen error | Shadow map | Environment | Capture scale |
| --- | --- | --- | --- | --- |
| Preview | 1 px | 2048 px | 256 px | 1× |
| Final | 0.25 px | 4096 px | 512 px | 2× |

The interactive viewer retains one WebGL renderer, canvas and camera controls
across every mode and settings edit. It uses shadow-compatible conventional
depth throughout, fitting near/far planes to the current model, closeup records
and floor/grid planes on every frame; a perspective near plane stays at or beyond
1/256 of the orbit pivot's depth, so a closeup never trades depth resolution for
a near plane at the eye. The grid's bounds also fit the far plane;
it is independent of the studio floor used by Render. Grid spacing is five cells across the
default model framing. The grid, the stage and the Render studio's floor are
SIZED from the model's rest placement for every renderer, STEP included: a pose, a
playing routine, or entering Render while the model is posed never rescales or
slides the ground; its height, the lighting and the shadow reach follow the model. Enabling photographic effects updates that live scene;
it never clears or covers the canvas with a loading screen. Only actual context
recovery replaces the renderer. Quality never changes a mesh: every mode draws
cadgen's one display mesh. Picking/topology stays demand-driven in every mode.

Enabling Clip prepares its shader/cap variants before presentation. Moving an
enabled cut stays live. Explicit neutral boundaries
still avoid unnecessary caps. See [responsive View updates](view-updates.md)
for scheduling, resource caching, loading presentation and capture readiness.

Render's floor stands at the model's lowest point, at 60% opacity; Model origin (the
document's Z=0 plane) is a choice away and is remembered in Render, as Glossy is. A snapshot's Render floor stands
where the viewer's does: both resolve it from the same mode default (`RENDER_FLOOR_PLACEMENT`).
Render's floor carries a studio's soft grounding shadow, as deep
as the floor is opaque: darkest where the model touches it, the key light's cast
shadow softening and fading away from the model. The viewer re-renders its
shadows, and re-bakes this one, only for a change that can alter them: never
while the camera alone moves, and never for a hover, a selection or any other
highlight, which leave every part casting as it did (a highlighted part keeps
its shadow). While a routine plays or a pose is dragged, the key's cast shadow on the
floor follows every frame and the contact darkening under the model at most every
100 ms; once the model stops, both are baked for its final pose. A floor at zero
opacity bakes nothing, and a viewer that renders in software, whose key casts no
shadow, draws no floor shadow.
Its finish is Matte, or Glossy: a glossier floor that also reflects the model, crisp
where the model meets it and softening and fading over the model's height, as a
polished studio floor shows a product, in either appearance. The reflection is a
second draw of the scene at half the canvas's resolution, before each frame the camera
or the model moved in (about 4 ms of render CPU a frame on a large assembly); a hover
or selection keeps the last one and catches up within 400 ms. A Matte floor allocates
none of it and draws nothing extra, and switching back to Matte releases it. In
software, a glossy floor reflects nothing.
Its double-sided surface uses its actual elevation during
camera depth fitting, avoiding the origin-placement near-plane gap. Its color
and opacity are independent of Background.
Light Render defaults to a pure white background and retains the slightly gray
floor (`#e7e7e5`); explicit background colors still override the mode default.
External STEP pose and animation passes publish current placed bounds before
drawing, and so does the exploded view wherever it comes to rest (a slider move,
the end of its ease) and at most every 100 ms while it eases in or out. Depth and
lighting follow moving parts without changing camera framing or the floor's
footprint.

All modes share surface-under-cursor zoom, zero-pose framing and the fit
padding: 1.1 across, and 1.1 down on a square or narrower viewport, easing to
1.25 at 16:9 and wider (`interactiveFitPadding`), so a wide view leaves room
above and below the model. Mode changes do not refit, and neither does a
rebuild of the open file. Motion changes geometry, not what 100% means. A
changed camera clears current screen-space drawings; camera state republished
unchanged after a runtime replacement does not.

## Where the controls live

Display settings are a dropdown from the Display button at the tool strip's end
(the current mode's sphere, after the tools' rule and before Preview:
`kit/shell/DisplayPopover.jsx`), which opens down from it — in preview from preview's own
row in the strip's place — present for every 3D file. A file's model tree and Position are
sections of the file sidebar (see
[settings-ui.md](./settings-ui.md#the-file-sidebar-and-the-tool-stack)); nothing in them is
a display setting.

Display and Preview, the view's controls at the strip's end, are offered by a 3D view
alone, as its renderer declares to the shell (`useRendererShell`'s `previewable`):

| Renderer | Files | Display and Preview |
| --- | --- | --- |
| `step` | STEP, STP | Yes |
| `glb` | GLB | Yes |
| `mesh` | STL, 3MF | Yes |
| `robot` | URDF, SRDF, SDF | Yes |
| `dxf` | DXF | No: a 2D drawing, on a surface of its own, without the shell |

A view without them has no Display or Preview control, not a disabled one, and a
request for Preview leaves its normal view as it is.
