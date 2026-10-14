---
name: cam
description: Plan CNC machining of a STEP part with cadgen and write the G-code for a person to prove out, on a 3-axis mill or router running grbl, LinuxCNC, Fanuc, Haas or Mach3. Covers facing, profiles with tabs, pockets, slots, round features, threads, chamfers, engraving and every drilling cycle, with feeds and speeds worked out from the material. Use when the user wants a part machined, toolpaths or G-code for a CNC mill or router, or asks for CAM. Never contacts a machine.
license: MIT
---

# CAM: plan the machining of a part

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files as the runtime source of truth; the repository link is only for
provenance and release review.

Use this skill to turn a saved STEP part into machine instructions for a CNC mill or router. It
reads the part, the job you write (the machine and its post, the material, the stock, the setups,
the tools and the operations), plans every toolpath, and writes three files: the G-code, a result
GLB the Viewer shows (part, stock, fixtures, tools and toolpaths) and a JSON sidecar with every
number. This page is a router: [Choose the operations](#choose-the-operations) maps what the part
needs to an operation and its reference page, which holds that operation's keys, defaults and
refusals; [job-file.md](references/job-file.md) holds what every job shares.

## It never runs a machine

cadgen never contacts a machine or a controller, and neither do you. It writes files; a person
loads the `.nc` on the controller and proves it out. Every program opens with a comment block
naming the part, the job, the setup, the work zero, the post and each tool with its speed and
feed, and ends that block with the line:

```
(Prove this program on the machine: single block, feed override low, in air first.)
```

Say that line to the user every time you hand over a program, in those words. Phase 1 has no stock
simulation, so a toolpath is checked against the part and the fixture, not against what earlier
operations left (see [findings.md](references/findings.md)). The feeds and speeds are starting
values for a prove-out, not tuned ones ([feeds-and-speeds.md](references/feeds-and-speeds.md)).

## Choose the operations

Match what the part needs to an operation first. The `op` key of each entry in the job's
`operations` names it; a job usually has several, run in the order listed.

| The part needs | `op` | Plain word | Status | Reference |
| --- | --- | --- | --- | --- |
| A flat, clean top: the stock skimmed down to the part's top (or a region of it, or a depth) | `facing` | Face | **Runs today** | [milling-2d.md](references/milling-2d.md#facing) |
| A cut round an outline: the outside (cut free, with tabs), or a pocket's or hole's wall, to size | `contour_2d` | Profile | **Runs today** | [milling-2d.md](references/milling-2d.md#contour_2d) |
| A pocket cleared to its floor, islands left standing | `pocket_2d` | Pocket | **Runs today** | [milling-2d.md](references/milling-2d.md#pocket_2d) |
| A pocket or the outside roughed fast at a light, steady engagement | `adaptive_2d` | Adaptive clearing | phase 1, not built yet | [milling-2d.md](references/milling-2d.md#adaptive_2d-not-built-yet) |
| A slot, open at one end or both, or a straight slot drawn as a region | `slot` | Slot | **Runs today** | [milling-2d.md](references/milling-2d.md#slot) |
| A hole too large for a drill, to size with an end mill going round and down (helical) | `bore` | Helical bore | **Runs today** | [milling-2d.md](references/milling-2d.md#bore) |
| A round pocket much larger than the tool, or a round boss | `circular` | Round pocket or boss | **Runs today** | [milling-2d.md](references/milling-2d.md#circular) |
| A thread milled inside a hole or round a boss | `thread_mill` | Thread mill | **Runs today** | [milling-2d.md](references/milling-2d.md#thread_mill) |
| Top edges broken with a chamfer: an outline, a pocket or a hole | `chamfer_2d` | Chamfer | **Runs today** | [milling-2d.md](references/milling-2d.md#chamfer_2d) |
| A closed area V-carved: a logo or lettering outline, a pocket's shape | `engrave` | V-carve | **Runs today** | [milling-2d.md](references/milling-2d.md#engrave) |
| A cut along a line or curve: a chain of the part's edges, or a drawn path | `trace` | Trace | **Runs today** | [milling-2d.md](references/milling-2d.md#trace) |
| A hole drilled in one go (G81, or G82 with a dwell) | `drill` | Drill | **Runs today** | [drilling.md](references/drilling.md#drill) |
| Holes spotted before drilling, or a small countersink | `spot_drill` | Spot drill | **Runs today** | [drilling.md](references/drilling.md#spot_drill) |
| A deep hole drilled in pecks that clear the chips (G83) | `peck` | Peck drill | **Runs today** | [drilling.md](references/drilling.md#peck-and-chip_break) |
| A deep hole in a stringy material, short pecks that break the chip (G73) | `chip_break` | Chip-break drill | **Runs today** | [drilling.md](references/drilling.md#peck-and-chip_break) |
| A tapped thread with a floating tap holder (G84) | `tap` | Tap | **Runs today** | [drilling.md](references/drilling.md#tap-and-rigid_tap) |
| A tapped thread on a machine that taps rigidly (spindle and feed synchronised) | `rigid_tap` | Rigid tap | **Runs today** | [drilling.md](references/drilling.md#tap-and-rigid_tap) |
| A precise hole reamed to size (G85) | `ream` | Ream | **Runs today** | [drilling.md](references/drilling.md#ream) |
| A precise hole finished with a boring bar (G86, or G76 fine bore) | `boring_cycle` | Boring bar | **Runs today** | [drilling.md](references/drilling.md#boring_cycle) |
| A counterbore cut from the side the tool comes from | `counterbore` | Counterbore | **Runs today** | [drilling.md](references/drilling.md#counterbore) |
| A counterbore on the far side, reached through the hole (G87) | `back_bore` | Back bore | **Runs today** | [drilling.md](references/drilling.md#back_bore) |
| A 3D surface: a mould, a sculpted or curved face, a blend | `adaptive_3d`, `pocket_3d`, `parallel`, `contour_3d`, `scallop`, `pencil`, `spiral`, `radial`, `morph`, `steep_shallow`, `ramp`, `horizontal`, `rest` | 3D milling | phase 2, not built yet | [milling-3d.md](references/milling-3d.md) |
| A turned part on a lathe | `lathe_face`, `lathe_rough`, `lathe_finish`, `lathe_groove`, `lathe_part`, `lathe_thread`, `lathe_bore`, `lathe_drill` | Turning | phase 3, not built yet | [turning.md](references/turning.md) |
| A flat part cut from sheet by laser, plasma or waterjet | `laser_cut`, `laser_engrave`, `plasma_cut`, `waterjet_cut` | Sheet cutting | phase 3, not built yet | [cutting.md](references/cutting.md) |
| A part cut by wire or sinker EDM | `wire_contour`, `wire_open`, `sinker` | EDM | phase 3, not built yet | [edm.md](references/edm.md) |
| A tilted tool, a 4th or 5th axis | `indexed`, `rotary_wrap`, `rotary_parallel`, `swarf`, `multiaxis_contour`, `flowline_5x` | Multi-axis | phase 4, not built yet | [multi-axis.md](references/multi-axis.md) |

A test keeps this table in step with what cadgen builds. An operation that is not built yet is
refused by name with its phase: `operations[1].op: "adaptive_3d" is phase 2, not built yet; built
today: facing, contour_2d, ...`.

### When the part needs an operation that is not here

([planned-next.md](references/planned-next.md) lists what is coming and in which phase.)

- Tell the user plainly, in their words: "That is not in this version." Do not write a job for it.
- Do not stand a 2.5D operation in for a 3D surface: flat passes stepping down leave steps where
  the surface curves, and that is not the part they asked for.
- Offer what honestly covers part of the job, and say which part: the flat features of a part with
  one curved face (facing, profiles, pockets and holes) can be planned, and the curved face left
  for another program or another tool. Say what is left uncut.

## Start with the task

| Task | First action | Reference |
| --- | --- | --- |
| **Pick the operations** | Match each feature of the part to a row of the table above. | [Choose the operations](#choose-the-operations) |
| **Find the features** | `cadgen cam features part.step`; a face the user selected in the Viewer (`part.step#o1.f17`) needs no lookup. | [Workflow](#workflow) |
| **Write or edit a job** | One JSON object: machine and post, material, stock, setups, tools, operations. | [job-file.md](references/job-file.md) |
| **Pick the post** | Ask which controller the machine runs; never guess one. | [machines-and-posts.md](references/machines-and-posts.md) |
| **Pick the tools** | Use the user's own tools and their real sizes; ask for stickout and holder. | [tools.md](references/tools.md) |
| **Set the feeds and speeds** | Leave them out (worked out from the material), or take the user's numbers. | [feeds-and-speeds.md](references/feeds-and-speeds.md) |
| **Fix a refused plan** | Read the findings, change the job, plan again. | [findings.md](references/findings.md) |

Modelling the part itself is `$cad`; this skill only reads a saved `.step`. Slicing a part for a 3D
printer is `$gcode`, not this skill. Show the result GLB with `$cad` (its Show the model step), so the
user sees the toolpaths on the part.

## Setup

Run cadgen through [uv](https://docs.astral.sh/uv/). CAM needs no extra: every command below runs on
a plain cadgen, sharing one installation with the `$cad` skill and the CAD app's server.

- `cadgen` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 cadgen`
- `python` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 python`

Units are fixed: mm for every length, mm/min for feeds, m/min for surface speed, rpm, seconds and
degrees. Restate the user's numbers in those units before you write them down. A machine set to
`"units": "inch"` changes only what the G-code is written in; the job stays in mm.

## Workflow

1. **List the features.** Every operation names what it cuts by the Viewer's selector.

   ```bash
   cadgen cam features part.step
   ```

   ```
   #o1 in plate.step: 11 features
     #o1.f1     face     flat 60 x 10 mm at X -50  from -X
     #o1.f2     face     flat 100 x 10 mm at Y -30  from -Y
     #o1.f3     face     flat 100 x 60 mm at Z 10  from +Z
     #o1.f4     face     flat 100 x 10 mm at Y 30  from +Y
     #o1.f5     face     flat 100 x 60 mm at Z 0  from -Z
     #o1.f6     face     flat 60 x 10 mm at X 50  from +X
     #o1.f7     hole     5 mm blind, 6 mm deep, axis Z, at X 35 Y -15  from +Z
     #o1.f16    hole     6 mm through, 10 mm deep, axis Z, at X -35 Y 15  from +Z -Z
     #o1.f17    hole     6 mm through, 10 mm deep, axis Z, at X 35 Y 15  from +Z -Z
     #o1.f19    pocket   40 x 20 mm, 5 mm deep, R3 inside corners, floor at Z 5  from +Z
     #o1.f2     profile  outline 100 x 60 mm, 10 mm tall along Z  from +Z -Z
   ```

   Each line is one feature: its selector, its kind (`face`, `hole`, `pocket`, `slot`, `profile`),
   its size, and the tool directions that reach it (`from +Z`). A feature with no `from` is not
   reached from any axis by a straight tool: an undercut. `--direction +Z` lists only what one
   direction reaches (`--direction -Z` for the underside);
   `--json` gives every size key. A document with several parts refuses with the occurrences it
   holds; pass one with `--occurrence #o2`. Use the selector of the feature, or any face of it.

2. **Write the job** as `part.cam.json` beside the part (schema in
   [job-file.md](references/job-file.md)). Ask the user for what you cannot know: the controller
   (it decides the post), the machine's top spindle speed and feed, the material, how the stock is
   held, and the tools they have. Never guess a post.

   ```json
   {
     "machine": {"kind": "mill", "post": "grbl", "max_rpm": 24000, "max_feed_mm_min": 3000, "rapid_mm_min": 5000},
     "material": "aluminum-6061",
     "stock": {"type": "box", "margin_mm": [2, 2, 1, 1]},
     "setups": [{"id": "top", "direction": "+Z", "origin": "stock_top_center", "fixture": {"type": "plate"}}],
     "tools": [
       {"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 20, "stickout_mm": 30},
       {"id": "T2", "type": "drill", "diameter_mm": 6, "flute_length_mm": 30, "stickout_mm": 40}
     ],
     "operations": [
       {"op": "facing", "tool": "T1"},
       {"op": "pocket_2d", "tool": "T1", "features": ["#o1.f19"]},
       {"op": "contour_2d", "tool": "T1", "all": true, "tabs": {"count": 4, "width_mm": 5, "height_mm": 2}},
       {"op": "drill", "tool": "T2", "features": ["#o1.f16", "#o1.f17"]}
     ]
   }
   ```

   Order the operations as a machinist would: face first, rough before finish, the big tool before
   the small one, holes spotted before they are drilled, the outline cut free last (with tabs, so
   the part stays put). An unknown key or value is refused before anything is planned, naming its
   path: `operations[2].stepdown_mm: must be > 0, got -1`.

3. **Plan.**

   ```bash
   cadgen cam plan part.step --job part.cam.json
   ```

   ```
   planned #o1 of plate.step for grbl: 4 operations, cycle time 3 min 38 s, 1 tool changes
     1. facing (top, T1): 1 min 16 s, cut 2972.2 mm, rapid 21 mm
     2. pocket_2d (top, T1): 15 s, cut 574.175 mm, rapid 112.685 mm
     3. contour_2d (top, T1): 2 min 04 s, cut 1563.96 mm, rapid 133.155 mm
     4. drill (top, T2): 2 s, cut 31.605 mm, rapid 117.605 mm
   wrote G-code: plate.nc, GLB: plate.cam.glb, sidecar: plate.cam.json
   ```

   This writes `part.nc` (one program per setup: `part.<setup>.nc` when the job has several),
   `part.cam.glb` and `part.cam.json` beside the part. Name the outputs to keep several plans,
   and pass `--post` to write the same job for another controller without editing it:

   ```bash
   cadgen cam plan part.step CAM/part.haas.cam.glb --job part.cam.json --post haas
   ```

   `--json` prints the result as one JSON line; progress goes to stderr, and `--verbose` adds every
   step and the timings there.

4. **Read the findings.** Every plan prints them after the summary, one line each, `error:` or
   `warning:`, errors first (`findings` in `--json` and the sidecar). A plan with an error writes no
   G-code and no GLB, only the sidecar with the findings, and exits 1:

   ```
   not planned: plate.step: no G-code written; sidecar: plate.cam.json
   error: operations[2] (contour_2d): the tool cannot come down at (-50, -33) to z -8.5: the fixture stands in the way
   ```

   Fix the job and plan again. Here the vise's jaws close on the stock's front and back, 2 mm from
   the part, and the 6 mm tool cutting round the outline reaches past the stock into them: hold the
   stock on a plate, or leave more stock in Y than the tool's diameter (`"margin_mm": [2, 10, 1, 1]`)
   so the jaws grip clear of the cut. See [Read the
   findings](#read-the-findings). A post that cannot run an operation safely (a tap on grbl) is
   refused by name before anything is written: change the post or the operation.

5. **Look at it in the Viewer.** Open the `.cam.glb` (with `$cad`'s Show the model step) and look
   with the user: the stock, the fixture, the toolpaths over the part, rapids above the stock,
   nothing cutting where the part is. Check the work zero and the setup direction against how the
   user will hold the part.

6. **Hand over the `.nc`.** Report, in this order: the post and the controller it is for; the work
   zero (`WCS: G54 at stock_top_center` means X0 Y0 at the stock's centre and Z0 on its top) and
   how to set it; each tool by its slot with diameter, rpm and feed, and its stickout; the cycle
   time; every warning, in the user's words; what the plan assumed (fixture sizes it did not know,
   the rapid rate). Then the prove-out line, word for word: "Prove this program on the machine:
   single block, feed override low, in air first."

## What the result means

- **The toolpaths are planned against the part, the stock and the fixture you described**, nothing
  else. A wrong stock size, a fixture that is not where the job says, or a tool longer or shorter
  than stated makes a plan that looks right and is not. The sidecar's `setups[].fixture.assumed`
  lists every fixture size the job did not state, and the default the plan used for it.
- **The cycle time** is the moves and dwells at the planned feeds, with rapids at
  `machine.rapid_mm_min` (5000 mm/min when the job does not say, and a warning says so). Tool
  changes, spindle spin-up and setting up are not in it. A real machine also slows into corners:
  read it as a lower bound.
- **Feeds and speeds** are worked out from the material table for each tool (rpm from surface
  speed, feed from chip load), held to the machine's limits, and each clamp is a warning. They are
  prove-out starting values, not tuned for a machine's rigidity or a tool's coating.
- **The G-code is written for one controller.** Another controller may read the same words
  differently: post again with `--post` rather than editing the file by hand.
- Phase 1 checks each operation against the part and the fixture; it does not simulate the stock,
  so it cannot see material an earlier operation left (a pocket cut with a larger tool, a wall a
  chamfer meets uncut). Order the operations so each one meets what it expects.

## Read the findings

Every plan lists findings, errors first. An **error** means the job was not planned: no G-code, no
GLB. A **warning** is planned, but worth a word to the user: weigh it as a machinist would, act on it
or say why not. Each finding's `summary` names the operation (`operations[2] (contour_2d)`) and what
it found; its `description`, when there is one, says what to do.

<!-- The "Raised" column marks the job checks (from the planning checks, not in this cadgen yet).
     Reconcile it with cadgen._internal.cam.checks.CHECK_KINDS when the job checks land. -->

| Finding | Severity | Raised | What it means | What to do |
| --- | --- | --- | --- | --- |
| `empty_operation` | error | today | The operation has nothing to cut: the feature's floor is not below the material, a region with no depth, `all: true` with no hole that suits the tool. | Check the feature, the depth and the facing before it; drop the operation if there is nothing to do. |
| `too_deep` | error | today | The cut would go into the part: a floor under the feature's, a facing below the part's top, a spot or a pilot past a blind hole's bottom. | Use the feature's own depth (leave `depth_mm` out) or a shallower one. |
| `unreachable` | error | today | The tool cannot get there: the feature is not reached from the setup's direction, the fixture stands in the way, there is no room to ramp in. | Add a setup from the direction that reaches it, grip the stock lower or on a plate, or pick another operation (the summary says which). |
| `tool_too_large` | error | today | The tool does not fit the feature, or is not the hole's size within `diameter_tol_mm`. | Pick a smaller tool, or the tool of the hole's size. |
| `tool_too_small` | error | today | A helical bore would leave a core standing in the middle of the hole. | Use `circular` for a hole over twice the tool's diameter. |
| `gouge` | error | today (a slot or round pocket drawn as a region running into the part); checks too | The path would cut into the part. | Redraw the region inside the material, or cut the feature by its selector. |
| `rpm_clamped` | warning | today | The spindle runs at the machine's top speed, under what the material and tool call for. | Usually fine: the feed follows the slower spindle. Say so; a larger tool may suit the machine better. |
| `feed_clamped` | warning | today | The feed is held to the machine's limit (a tap's spindle slows with it). | Each tooth takes a thinner chip and may rub: consider a smaller or fewer-fluted tool. |
| `rest_material` | warning | today | Material is left where the tool did not reach: a drill's full diameter short of a flat-bottomed hole's floor, a V-carve's flat bottom too narrow for the clearing tool. | Finish it with another tool, or tell the user what is left. |
| `holder_collision` | error | not in this cadgen yet | The holder or shank hits the stock, the part or the fixture. | A longer stickout or a slimmer holder; check the real tool. |
| `fixture_collision` | error | not in this cadgen yet | A path runs into the fixture. | Move the cut or the fixture. |
| `over_travel` | error | not in this cadgen yet | A move is outside the machine's travel. | Re-place the work zero or the part. |
| `no_post_support` | error | not in this cadgen yet (today a post refuses with an error before anything is written) | The post cannot write what the job needs. | Change the post or the operation. |
| `corner_left` | warning | not in this cadgen yet (today a pocket's stats list it) | An inside corner is tighter than the tool's radius: material stays in the corner. | A smaller tool for the corners, or tell the user the radius they get. |
| `full_slot` | warning | not in this cadgen yet | The tool cuts its full width outside adaptive clearing. | Slow the feed or use a smaller stepover. |
| `long_stickout` | warning | not in this cadgen yet | The tool sticks out more than 4 times its diameter: it will chatter and deflect. | Shorten the stickout, or slow down and take lighter cuts. |
| `uncut_feature` | warning | not in this cadgen yet | A feature no operation cuts. | Add the operation, or confirm with the user it is left as it is. |

[findings.md](references/findings.md) has each finding in full, the shape a finding takes, and the
warnings about the run itself (an assumed rapid rate) that are not findings.
