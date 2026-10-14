# Tools

Each entry of the job's `tools` is one tool in the machine's tool table. Use the user's own tools and
their real sizes: the plan, the collision checks and the feeds all read them. Ask for the stickout
(how far the tool stands out of its holder) and the holder; a guessed stickout is a guessed
collision check.

```json
{"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 18,
 "stickout_mm": 30, "holder": {"diameter_mm": 32, "length_mm": 40}, "material": "carbide",
 "comment": "3FL alu"}
```

## Keys every holder-held tool takes

| Key | Meaning | Default |
| --- | --- | --- |
| `id` | `T` and the tool table slot, `T1` to `T9999`: the program calls `T<n> M6` and `H<n>` | required |
| `type` | one of the types below | required |
| `material` | `carbide` or `hss`; an HSS tool runs at 0.3 of carbide's surface speed (a tap at its own speed either way) | `carbide` |
| `stickout_mm` | how far the tool stands out of the holder; must be at least its flutes (and its tip, pilot or ball) | flute length plus one diameter |
| `holder` | `{"diameter_mm", "length_mm"}`, both required; drawn and checked only when stated | none |
| `comment` | a few words for the program's header | none |
| `flutes` | number of cutting edges, 1 to 32 | 2 (a face mill 4) |
| `flute_length_mm` | how far up from the tip it cuts | 3 x diameter, cut short to a stated stickout |
| `shank_diameter_mm` | the shank above the flutes | the diameter (3 mm for a flat, ball, bull or thread mill under 3 mm) |

## Types

The types phase 1 cuts with, the sizes each needs and may take, and the operations that use it:

| `type` | Needs | May take | Cuts in |
| --- | --- | --- | --- |
| `flat_endmill` | `diameter_mm` | `flutes`, `flute_length_mm`, `shank_diameter_mm` | `facing`, `contour_2d`, `pocket_2d`, `slot`, `bore`, `circular`, `trace`; an `engrave`'s `clear_tool` |
| `face_mill` | `diameter_mm` | the same | `facing` |
| `ball_endmill` | `diameter_mm` | the same | `trace` |
| `bull_endmill` | `diameter_mm`, `corner_radius_mm` (at most half the diameter) | the same | `facing`, `contour_2d`, `pocket_2d`, `slot`, `bore`, `circular`, `trace` |
| `chamfer_mill` | `diameter_mm`, `tip_angle_deg` (the included angle, under 180) | the same, `tip_diameter_mm` | `chamfer_2d`, `trace` |
| `v_bit` | `diameter_mm`, `tip_angle_deg` | the same, `tip_diameter_mm` | `chamfer_2d`, `engrave`, `trace` |
| `thread_mill` | `diameter_mm`, `pitch_mm` | `flutes`, `flute_length_mm`, `shank_diameter_mm` | `thread_mill` |
| `drill` | `diameter_mm` | the same, `tip_angle_deg` (118) | `drill`, `peck`, `chip_break` |
| `spot_drill` | `diameter_mm` | the same, `tip_angle_deg` (90) | `spot_drill` |
| `tap` | `diameter_mm` (the major diameter), `pitch_mm` | `flutes`, `flute_length_mm`, `shank_diameter_mm` | `tap`, `rigid_tap` |
| `reamer` | `diameter_mm` | the same | `ream` |
| `boring_bar` | `diameter_mm` (the cutting diameter) | `flute_length_mm` (its reach), `shank_diameter_mm` | `boring_cycle` |
| `back_bore` | `diameter_mm` (the head) | `flute_length_mm` (its reach), `shank_diameter_mm` | `back_bore` |
| `counterbore` | `diameter_mm` | the same, `pilot_diameter_mm` (under the diameter) | `counterbore` |

A tool type an operation does not cut with is refused when the job is read:
`operations[1].tool: T3 is a tap, and pocket_2d cuts with a flat_endmill or bull_endmill`.

The job also knows `slitting_saw`, the lathe inserts (`turning_insert`, `grooving_insert`,
`parting_blade`, `threading_insert`), the kerfs (`laser`, `plasma`, `waterjet`, `wire`: a `kerf_mm` or
`diameter_mm`, no holder, stickout or material) and `electrode`, for the operations of later phases.
No phase 1 operation cuts with them, and a plan whose job lists one is refused (it has no tool shape
yet): leave them out of a phase 1 job.

## What the plan fills in

What a job leaves out is filled in as follows, and drawn that way in the GLB:

- A drill's point is 118 degrees, a spot drill's 90. A chamfer mill or V-bit must state its angle.
- A chamfer mill or V-bit cuts with its cone only, unless the job states longer flutes.
- A boring bar's or back-bore's head is a quarter of its diameter tall, on a neck 0.6 of its
  diameter; its `flute_length_mm` is its reach, how deep the neck goes before the shank.
- A counterbore's pilot is as long as it is wide.
- A thread mill is a full-diameter cutter over its flute length; a tap is a cylinder of its major
  diameter.
- A stated stickout shorter than what the job fixes below the holder (stated flutes, a pilot, a
  drill's point, a ball's end) is refused, naming `stickout_mm`: the job contradicts itself.

## Choosing tools

- **One tool does several operations** when it can: a tool change costs time, and on grbl it is a
  pause for the operator to change the tool and zero it.
- **Pockets and inside corners:** the tool leaves its own radius in every inside corner. A pocket
  with R3 corners wants a tool of 6 mm or less; a larger one leaves material (the pocket's stats list
  the corners it leaves).
- **Holes:** a drill, reamer or boring bar must be the hole's diameter within `diameter_tol_mm`
  (0.05 mm); a tap the hole's major diameter or its tap drill (major less one pitch) within 0.15 mm.
  A hole with no drill of its size is a `bore` (helical, with an end mill) or a `circular`.
- **Stickout:** keep it as short as the deepest cut allows. An end mill or boring bar out more than
  about 4 times its diameter chatters and deflects (`long_stickout`); drills, spot drills, taps,
  reamers and counterbores cut along their axis and are routinely held out further.
- **Carbide or HSS:** ask for each tool. Drills, spot drills, counterbores and taps are often HSS,
  end mills usually carbide. When the user does not know, state `"material": "hss"` (the slower,
  safer speed) and say you assumed it.
- **Tool length:** every Z in the program is the tool's lowest point: a flat end mill's face, a
  ball's bottom, a drill's, spot drill's or chamfer mill's tip, a piloted counterbore's pilot tip
  (the pilot is as long as it is wide, unless the tool says otherwise: then tell the user). Each
  tool's length offset is measured there; tell the user where to touch off each one (the program's
  header has a `Tool length:` line, and one per piloted counterbore).
- **Pitch:** a thread mill or tap is ground for one pitch; the operation's `pitch_mm` must match it.
