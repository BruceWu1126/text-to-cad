# The job file

One JSON object, passed as `--job part.job.json` (or inline: `--job '{...}'`). It names the machine
and its post, the material, the stock, the setups, the tools and the operations. This page is every
key a job shares; each operation's own keys are on its reference page (the table under
[`operations`](#operations)).

```json
{
  "machine": {"kind": "mill", "axes": 3, "post": "grbl", "travel_mm": [800, 600, 120],
              "max_rpm": 24000, "max_feed_mm_min": 5000, "rapid_mm_min": 5000, "units": "mm"},
  "material": "aluminum-6061",
  "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},
  "setups": [{"id": "top", "direction": "+Z", "origin": "stock_top_center", "clearance_mm": 10, "retract_mm": 3,
              "fixture": {"type": "vise", "jaw_height_mm": 20, "grip_mm": 5}}],
  "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 18,
             "stickout_mm": 30, "holder": {"diameter_mm": 32, "length_mm": 40}, "material": "carbide"}],
  "operations": [{"op": "pocket_2d", "setup": "top", "tool": "T1", "features": ["#o1.f12"]}],
  "view": {"show": {"rapids": true, "stock": "final"}}
}
```

Every key and every value is a closed vocabulary: an unknown key, an unknown name or a number out of
range is refused before the part is opened, naming its path and what it takes:

```
operations[2].stepdown_mm: must be > 0, got -1
operations[0]: unknown keys ['stepdown']; pocket_2d takes op, setup, tool, features, region, all, ...
machine.post: required, and never guessed: a wrong post can crash a machine. Name the controller's dialect (or pass --post); built today for a mill: grbl, linuxcnc, fanuc, haas, mach3
```

A file path is read relative to where you run; `stock.from` is read relative to the job file's
folder (to where you run, for inline JSON).

## Units

| Quantity | Unit | Keys |
| --- | --- | --- |
| Length, depth, diameter, margin, height | mm | every `*_mm` key |
| Feed, plunge, rapid | mm/min | `feed_mm_min`, `plunge_mm_min`, `max_feed_mm_min`, `rapid_mm_min` |
| Spindle speed | rpm | `rpm`, `max_rpm` |
| Surface speed | m/min | `material.surface_m_min` |
| Chip load | mm per tooth | `material.chipload_mm` |
| Time | s | `dwell_s`, `spindle_spinup_s` |
| Angle | degrees | `*_deg` |

The whole job is metric. `machine.units: "inch"` writes the G-code in inches (G20, every coordinate,
feed and depth converted); nothing in the job changes.

## `machine`

| Key | Meaning | Default |
| --- | --- | --- |
| `kind` | `mill`, `router`, `lathe`, `laser`, `plasma`, `waterjet`, `wire_edm` or `sinker_edm`; required. Only `mill` and `router` have posts and operations today. | |
| `axes` | a mill or router has 3, 4 or 5; a lathe 2; a wire EDM 2 or 4. Phase 1 plans 3-axis toolpaths on any of them. | the kind's first (3 for a mill) |
| `post` | the controller's dialect: `grbl`, `linuxcnc`, `fanuc`, `haas` (mills only) or `mach3`. Required and never guessed; `--post` overrides it for one run ([machines-and-posts.md](machines-and-posts.md)). | |
| `travel_mm` | `[x, y, z]` (a lathe `[x, z]`) | not stated |
| `max_rpm` | the spindle's top speed; a faster computed speed is clamped to it, with an `rpm_clamped` warning | no limit |
| `max_feed_mm_min` | the fastest cutting feed; faster feeds are clamped, with a `feed_clamped` warning | no limit |
| `rapid_mm_min` | the rapid rate the cycle time is worked out at | 5000, with a warning that it was assumed |
| `units` | `mm` or `inch`: what the G-code is written in | `mm` |
| `spindle_spinup_s` | seconds to wait after every `M3` before cutting (`G4`) | the post's: 3 s on grbl, 0 on the others |

A post that does not exist, is not built yet, or writes for another kind of machine is refused with
the posts built for this one.

## `material`

Required: feeds and speeds are worked out from it. Either a name from the table
([feeds-and-speeds.md](feeds-and-speeds.md#the-material-table)): `aluminum-6061`, `aluminum-7075`,
`mild-steel`, `stainless-304`, `brass`, `acetal`, `hdpe`, `acrylic`, `mdf`, `plywood`, `hardwood`;
or an object:

| Key | Meaning |
| --- | --- |
| `name` | the material's name; required |
| `surface_m_min` | carbide surface speed, m/min; required for a name not in the table |
| `chipload_mm` | chip load per tooth, mm: one number for every tool, or one per tool diameter, `{"3": 0.02, "6": 0.04}`; required for a name not in the table |
| `hardness_hb` | Brinell hardness, recorded |

A table name in an object may override its surface speed or chip load:
`{"name": "aluminum-6061", "surface_m_min": 300}`.

## `stock`

Required: what the machine starts from. Each kind of machine starts from some of them: a mill from
`box`, `cylinder` or `from`; a router from those and `sheet`.

| `type` | Keys | Meaning |
| --- | --- | --- |
| `box` | `margin_mm` `[x, y, z_top, z_bottom]` | the part's bounding box grown by `x` on each side in X, `y` on each side in Y, `z_top` above the part and `z_bottom` below it. Four numbers, each 0 or more; default all 0. |
| `cylinder` | `margin_mm` `[radial, axial]` or one number for both; `diameter_mm`; `axis` (`X`, `Y` or `Z`, default `Z`) | a bar along `axis` through the middle of the part's box: `diameter_mm` across when stated (then no radial margin), else the smallest bar holding the part plus the radial margin; as long as the part plus the axial margin at each end. |
| `sheet` | `margin_mm` `[x, y]` or one number; `thickness_mm` | the part's outline in X and Y grown by the margin, from the part's bottom up `thickness_mm` (default: the part's height). A router only. |

Or `{"from": "blank.step"}` alone: the blank read from a STEP, in the part's frame. It must hold the
part: a stock that does not (a bar too thin, a blank beside the part, a blank with a pocket where the
part stands) is refused.

## `setups`

A non-empty list: one entry per way the part is held. Each setup's operations run in one program,
written to its own `.nc` when the job has more than one.

| Key | Meaning | Default |
| --- | --- | --- |
| `id` | the setup's name; operations name it. In a job of several setups it names the file `<stem>.<id>.nc`, so letters, digits, `-`, `_` and `.` only. | required |
| `direction` | the tool axis in the part's frame, pointing from the part to the spindle: `+X`, `-X`, `+Y`, `-Y`, `+Z`, `-Z`; or a face selector (`#o1.f4`) to look straight down that face's outward normal | `+Z` |
| `origin` | where the work zero (G54) sits: a keyword below, or a point `[x, y, z]` in the part's frame, mm | `stock_top_center` |
| `clearance_mm` | the height above the stock top the tool moves at between features | 10 |
| `retract_mm` | the height above the stock top it lifts to between passes; above 0 and at or under `clearance_mm` | 3 |
| `fixture` | what holds the stock (below); collisions are checked against it | `{"type": "none"}` |

`direction: "+Z"` keeps the part's axes. `-Z` is the part turned over about its X axis (X stays, Y and
Z flip). `+X` maps the part's X, Y, Z to the setup's Z, Y, -X; every other turn is the smallest one
from +Z to the direction.

Origin keywords, read on the box in the setup's frame (looking down the tool):

| `origin` | X0 Y0 | Z0 |
| --- | --- | --- |
| `stock_top_center` | the stock's centre | the stock's top |
| `stock_top_corner` | the stock's min-X, min-Y corner (front left) | the stock's top |
| `part_bbox_top_center` | the part's centre | the part's top |
| `part_bbox_top_corner` | the part's front left corner | the part's top |
| `part_bbox_center` | the part's centre | the part's middle |
| `part_bbox_bottom_center` | the part's centre | the part's bottom |
| `part_bbox_bottom_corner` | the part's front left corner | the part's bottom |

Tell the user where the zero is and how to set it on the machine: the program's header says
`WCS: G54 at <origin>`.

### `fixture`

| `type` | Sizes (mm) | What is modelled |
| --- | --- | --- |
| `vise` | `jaw_height_mm` (40), `jaw_width_mm` (150), `grip_mm`, `parallels_mm` (0) | Two jaws closing on the stock's front and back (along the setup's Y), `jaw_width_mm` wide about its centre, standing on a floor; the stock on parallels `parallels_mm` tall. `grip_mm` is how far the jaws reach up the stock: `jaw_height_mm - parallels_mm`, so any two give the third, and three must agree within 0.01 mm. A grip of 0 or less holds nothing and is refused. |
| `plate` | `thickness_mm` (12) | A plate under the stock, 25 mm wider than it all round (a fixture plate, a spoilboard, a vacuum table). |
| `chuck` | `diameter_mm` (the stock's plus 20 each side), `jaw_length_mm` (30), `grip_mm` (10) | A cylinder about the setup's Z axis through the stock's centre, its jaw face `grip_mm` above the stock's bottom and `jaw_length_mm` long below it. |
| `collet` | `diameter_mm` (the stock's plus 5 each side) | A cylinder holding the bottom 15 mm of the stock, 40 mm long. |
| `sheet` | none | A sheet held flat: nothing to collide with. |
| `none` | none | Nothing modelled. |

A size the job does not state takes the default in brackets, and the sidecar lists it under
`setups[].fixture.assumed`; say which sizes were assumed when you report. A lathe holds work in a
`chuck`, `collet` or `none`; a laser, plasma or waterjet in a `sheet` or `none`.

## `tools`

A non-empty list. [tools.md](tools.md) has every type, the keys each takes, and what a job leaves
out.

| Key | Meaning | Default |
| --- | --- | --- |
| `id` | `T` and the machine's tool table slot, `T1` to `T9999`; the program calls it as `T<n> M6` and its length offset as `H<n>` | required |
| `type` | the tool's type ([tools.md](tools.md#types)) | required |
| `material` | `carbide` or `hss` (HSS runs at 0.3 of carbide's surface speed) | `carbide` |
| `diameter_mm`, `flutes`, `flute_length_mm`, `shank_diameter_mm`, and the type's own sizes | [tools.md](tools.md#types) | |
| `stickout_mm` | how far the tool stands out of its holder | flute length plus one diameter |
| `holder` | `{"diameter_mm", "length_mm"}`, both required; checked for collisions only when stated | none |
| `comment` | a few words, written into the program's header | none |

## `operations`

A non-empty list, run in its order. Every operation takes these keys; each also takes its own.

| Key | Meaning | Default |
| --- | --- | --- |
| `op` | the operation (the table below) | required |
| `setup` | the setup it runs in | the job's one setup, when it has one |
| `tool` | the tool it cuts with; its type must be one the operation cuts with | required |
| `features` | selectors from `cadgen cam features`, `["#o1.f19"]`: the feature's own selector, or any face of it | |
| `region` | a closed outline in the setup's plane, mm: at least three `[x, y]` points enclosing an area, `[[0, 0], [40, 0], [40, 20], [0, 20]]` | |
| `all` | `true`: every feature of the kind the operation cuts that the setup reaches | |
| `rpm`, `feed_mm_min`, `plunge_mm_min` | override the computed spindle speed, feed and plunge (still held to the machine's limits) | worked out ([feeds-and-speeds.md](feeds-and-speeds.md)) |
| `stock_to_leave_mm` | material left on walls and floors for a later finishing operation; a hole cycle refuses any but 0 | 0 |
| `coolant` | `off`, `flood`, `mist` or `air` | `off` |
| `clearance_mm`, `retract_mm` | override the setup's heights for this operation | the setup's |

An operation names one of `features`, `region` or `all`, and only those it takes (its page says
which). `facing` alone needs none: it faces the whole stock. A selector naming a feature of another
kind is refused naming the field; one the setup's direction does not reach is an `unreachable`
error.

| `op` | plain word | status | its keys |
| --- | --- | --- | --- |
| `facing` | Face | runs today | [milling-2d.md](milling-2d.md#facing) |
| `contour_2d` | Profile | runs today | [milling-2d.md](milling-2d.md#contour_2d) |
| `pocket_2d` | Pocket | runs today | [milling-2d.md](milling-2d.md#pocket_2d) |
| `slot` | Slot | runs today | [milling-2d.md](milling-2d.md#slot) |
| `bore` | Helical bore | runs today | [milling-2d.md](milling-2d.md#bore) |
| `circular` | Round pocket or boss | runs today | [milling-2d.md](milling-2d.md#circular) |
| `thread_mill` | Thread mill | runs today | [milling-2d.md](milling-2d.md#thread_mill) |
| `chamfer_2d` | Chamfer | runs today | [milling-2d.md](milling-2d.md#chamfer_2d) |
| `engrave` | V-carve | runs today | [milling-2d.md](milling-2d.md#engrave) |
| `trace` | Trace | runs today | [milling-2d.md](milling-2d.md#trace) |
| `drill` | Drill | runs today | [drilling.md](drilling.md#drill) |
| `spot_drill` | Spot drill | runs today | [drilling.md](drilling.md#spot_drill) |
| `peck` | Peck drill | runs today | [drilling.md](drilling.md#peck-and-chip_break) |
| `chip_break` | Chip-break drill | runs today | [drilling.md](drilling.md#peck-and-chip_break) |
| `tap` | Tap | runs today | [drilling.md](drilling.md#tap-and-rigid_tap) |
| `rigid_tap` | Rigid tap | runs today | [drilling.md](drilling.md#tap-and-rigid_tap) |
| `ream` | Ream | runs today | [drilling.md](drilling.md#ream) |
| `boring_cycle` | Boring bar | runs today | [drilling.md](drilling.md#boring_cycle) |
| `counterbore` | Counterbore | runs today | [drilling.md](drilling.md#counterbore) |
| `back_bore` | Back bore | runs today | [drilling.md](drilling.md#back_bore) |

Every other name is known and refused with its phase: `adaptive_2d` (Adaptive clearing) is phase 2,
designed with `adaptive_3d` ([milling-2d.md](milling-2d.md#adaptive_2d-phase-2)); so are 3D milling,
turning, cutting, EDM and multi-axis ([planned-next.md](planned-next.md)).

## `view`

Optional: what the Viewer draws at first. `{"show": {...}}` with `rapids`, `fixtures` and `tool`
(`true` or `false`) and `stock` (`initial`, `final` or `none`).

## What comes out

`cadgen cam plan part.step [OUT] --job part.job.json` writes beside OUT (default
`part.cam.glb` beside the part; OUT must end in `.cam.glb` or `.glb`), with the stem being OUT's name
without that ending:

| File | What it holds |
| --- | --- |
| `<stem>.nc` | the G-code for the one setup; `<stem>.<setup id>.nc` per setup when the job has several |
| `<stem>.cam.glb` | the part, the stock, each setup's fixture, each tool and each operation's toolpath, in the part's frame |
| `<stem>.cam.json` | the sidecar: `generator`, `ok`, `document`, `occurrence`, `job` (an echo), `machine`, `post`, `material`, `setups`, `tools` (with each use's rpm and feed), `operations`, `summary`, `findings`, `checks`, `warnings`, `files` |

The sidecar's `operations[]` gives each operation's `rpm`, `feed_mm_min`, `plunge_mm_min`,
`start_s`, `time_s`, `cut_length_mm`, `rapid_length_mm` and its `stats` and `lines` (what it did, in a
line or two). `summary` holds `cycle_time_s`, `cut_length_mm`, `rapid_length_mm`, `tool_changes`,
`operations`, `setups`, `programs`, `post` and `rapid_mm_min`. The same job writes the same bytes.

A plan with an error finding (a post's refusal among them: `no_post_support`) writes only the
sidecar (`"ok": false`, with the findings), removes any G-code and GLB an earlier run left at those
paths, and exits 1. A malformed job is an error message and writes nothing; so is a job file at the
sidecar's own path (`<stem>.cam.json`), which the plan would overwrite.
