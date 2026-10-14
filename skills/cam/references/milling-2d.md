# 2.5D milling

Flat passes stepping down: the tool cuts at one height, steps down, cuts again. Every operation here
climb mills (the material on the tool's right, the spindle turning clockwise), goes down into
material only by a ramp or a helix no steeper than `ramp_angle_deg` (3 degrees) unless the tool is in
air, and moves between cuts at the setup's retract and clearance heights. Each takes the keys every
operation takes ([job-file.md](job-file.md#operations)) and its own, below. Depths are in the setup's
frame: `depth_mm` is below the top of the material, `z_mm` a height in the setup's frame (Z0 is the
work zero); give one or the other, not both.

An operation that cannot be planned on this part is an error finding, and the job is not planned:
`empty_operation` (nothing to cut), `too_deep` (into the part), `tool_too_large` (the tool does not
fit), `tool_too_small`, `unreachable` (no way in) or `gouge` ([findings.md](findings.md)).

## `facing`

Flattens the stock down to the part's top, row by row across the setup's X. With no target it faces
the whole stock (the rows run off it at both ends); with a `region`, the tool's centre stays inside
the region and hangs over its edge by a tenth of its diameter, no further.

Tools: `face_mill`, `flat_endmill`, `bull_endmill`. Targets: none (the whole stock), `all`, or `region`.

| Key | Meaning | Default |
| --- | --- | --- |
| `stepover` | the distance between rows, as a fraction of the tool's diameter (over 0, up to 1) | 0.7 for a face mill, 0.4 for an end mill |
| `stepdown_mm` | the depth of each level; the last is exactly the floor | 1 |
| `pattern` | `zigzag` (back and forth, staying down where the turn is in air) or `one_way` (lift and come back, every row cut the same way) | `zigzag` |
| `depth_mm` / `z_mm` | the floor: below the stock top, or a height | the part's top |
| `finish_mm` | leave this much for a last pass of its own | 0 |

Refused (`too_deep`) when what the tool sweeps at the floor would cut the part. Face first: the other
operations start from the faced top.

## `contour_2d`

Cuts round an outline, in levels down to the feature's bottom. Targets: a `profile` (the part's
outside; `all: true` cuts every outline the setup reaches), a `pocket` or a `hole` (from inside), or a
`region`. Tools: `flat_endmill`, `bull_endmill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `side` | `outside`, `inside` or `on` (the tool's centre on the outline). Cutting a profile from inside, or a pocket from outside, is refused: it goes into the part. | a profile's and a region's `outside`; a pocket's or hole's `inside` |
| `stepdown_mm` | the depth of each level | half the tool's diameter |
| `depth_mm` / `z_mm` | the floor; a region needs one | the feature's bottom |
| `breakthrough_mm` | how far past the bottom a through outline goes | 0.3 on sheet stock (into the spoilboard), else 0 |
| `finish_passes` | passes at the final depth, 0 to 10 | 0 |
| `finish_mm` | what the finishing passes take off, in equal shares; needs `finish_passes` (with none, the passes are spring passes) | 0 |
| `tabs` | `{"count", "width_mm", "height_mm"}`: bridges left standing at the bottom so a part cut free stays put; not with `side: on` | none |
| `lead_radius_mm` | a quarter-circle lead on and off each pass from the air side, where it stays clear of the part and the fixture; 0 for none | the tool's radius |
| `ramp_angle_deg` | the steepest the tool goes down into material | 3 |

`tabs`: `count` (1 to 64) is required; `width_mm` defaults to the tool's diameter but at least 3 mm,
`height_mm` to a quarter of the cut's depth, at least 1 mm and at most half the depth. Tabs are spaced
evenly round each ring; the tool rises straight onto each and ramps down off it. Tell the user to cut
the tabs by hand after the program.

Where a lead does not fit, a pass enters along itself: a helix inside a pocket or hole where one
fits, else a ramp along its first straight run, else laps of the ring sloping down.

## `pocket_2d`

Clears a `pocket` (its islands left standing) or a `region` down to its floor. Tools: `flat_endmill`,
`bull_endmill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `pattern` | `offset` (rings offset from the walls inward, cut from the innermost out) or `zigzag` (rows, then a ring round the walls) | `offset` |
| `stepover` | between rings or rows, a fraction of the tool's diameter | 0.4 |
| `stepdown_mm` | the depth of each level | half the tool's diameter |
| `depth_mm` / `z_mm` | the floor; a region needs one; never under the pocket's own floor | the pocket's floor |
| `finish_mm` | leave this on the walls for one ring at the final depth | 0 |
| `finish_floor_mm` | leave this on the floor for a last level of its own (its outer ring finishes the walls too) | 0 |
| `ramp_angle_deg` | the steepest entry: a helix inside the pocket, a ramp where none fits | 3 |

Inside corners tighter than the tool's radius keep the tool's radius: the stats' `corner_left` lists
each, and the operation's `lines` in the sidecar say `#o1.f19: R3 inside corners left at R4 by this
tool`. Tell the user, or add a smaller tool for the corners.

## `adaptive_2d` (not built yet)

Constant-engagement roughing (a light, steady cut, trochoidal in narrow places) is planned for phase 1
and refused today: `"adaptive_2d" is phase 1, not built yet`. Rough with `pocket_2d` (a smaller
`stepover`, and `finish_mm` for a finishing ring) meanwhile.

## `slot`

Cuts a `slot` feature (open at one end or both) or a straight `region` (a rectangle or an obround,
with `depth_mm` or `z_mm`) along its centre line, full width, level by level. Each level starts at an
open end where there is one (off the stock, so the tool goes down in air), else ramps in along the
slot. A tool narrower than the slot then widens it with rings out to the walls. A tool wider than the
slot is refused. Tools: `flat_endmill`, `bull_endmill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `stepdown_mm` | the depth of each level | a quarter of the tool's diameter |
| `stepover` | between the widening rings, a fraction of the tool's diameter | 0.4 |
| `finish_mm` | leave this on both walls for one ring at the final depth | 0 |
| `depth_mm` / `z_mm` | the floor; a region needs one | the slot's floor |
| `ramp_angle_deg` | the steepest ramp in | 3 |

A closed end is cut round (the slot's half width) or square (to the tool's radius, its corners left
at it). A region slot that runs into the part is a `gouge` error.

## `bore`

Helical interpolation of a `hole` to size with an end mill: the tool's centre goes round a circle of
the hole's radius less its own, down at `pitch_mm` per turn, a whole circle at the bottom, then over
to the hole's axis and straight up. Targets: `features` (holes). Tools: `flat_endmill`,
`bull_endmill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `pitch_mm` | the depth per turn; a steeper pitch than `ramp_angle_deg` allows is refused (`unreachable`) | 0.2 x the tool's diameter, and no steeper than the ramp angle |
| `target` | `hole`, or `counterbore` (bore the hole's counterbore to its diameter and depth; then no `depth_mm` or `z_mm`) | `hole` |
| `depth_mm` / `z_mm` | the floor | the hole's bottom |
| `breakthrough_mm` | past a through hole's bottom | 0.5 |
| `ramp_angle_deg` | the steepest the helix goes | 3 |

Refused when the tool does not fit (`tool_too_large`), when it is within a tenth of its radius of the
hole's size (drill or ream it instead), and when it would leave a core standing in the middle, a hole
over twice the tool's diameter (`tool_too_small`: use `circular`).

## `circular`

A round pocket (a `hole` larger than the tool, or the largest circle inside a `region`) or a round
boss (a cylindrical `profile`, or a `region`'s bounding circle with `boss: true`), level by level. A
pocket enters each level with a helix at its centre, spirals out and ends with a whole finishing
circle. A boss clears the stock round it, spiralling in to a whole finishing circle. Tools:
`flat_endmill`, `bull_endmill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `boss` | `true` for a boss from a region; a pocket takes no `clear_mm` | from the feature's kind |
| `clear_mm` | how far round a boss to clear from its wall | the whole stock round it (the tool starts off the stock) |
| `stepdown_mm` | the depth of each level | half the tool's diameter |
| `stepover` | the spiral's step per turn, a fraction of the tool's diameter | 0.4 |
| `depth_mm` / `z_mm` | the floor | the feature's bottom |
| `breakthrough_mm` | past a through hole's bottom | 0.5 |
| `ramp_angle_deg` | the steepest entry | 3 |

Where the tool cuts with its edge on the inside of a circle (a bore's helix, a round pocket's
finishing circle, an internal thread), the feed is the feed at that edge: the centre's feed is
scaled down by the centre's radius over the edge's (never under a quarter of it), as a machinist
compensates an internal arc.

## `thread_mill`

Mills an internal thread in a `hole` (drilled to the tap drill size by an earlier operation) or, with
`external: true`, an external thread round a cylindrical `profile`, with a single-point thread mill:
one helix, one turn per pitch, entered and left by quarter-circle leads. Climb, so a right-hand
internal thread is cut from the bottom up and a right-hand external one from the top down. Tools:
`thread_mill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `pitch_mm` | the thread's pitch (1.5 for M10); must be the thread mill's own `pitch_mm` | required |
| `thread_depth_mm` | the thread's depth: the internal thread's major diameter is the hole's plus twice it, the external's minor the boss's less twice it | 0.6134 x pitch (ISO metric) |
| `hand` | `right` or `left` | `right` |
| `external` | `true` for a thread round a boss | `false` |
| `breakthrough_mm` | past a through hole's bottom | 0.5 |

## `chamfer_2d`

Breaks the top edge of each `profile` (from outside), `pocket` and `hole` (from inside) it is pointed
at (`all: true`: every one the setup reaches) with a chamfer, one pass round each edge, with the cone
of a `chamfer_mill` or `v_bit`. A 90 degree tool cuts a 45 degree chamfer as deep as it is wide. The
wall must already be cut: phase 1 has no stock simulation to check it.

| Key | Meaning | Default |
| --- | --- | --- |
| `width_mm` | the chamfer's width across the top face | 0.5, or the width the part models |
| `tip_offset_mm` | how far the tip runs below the chamfer's bottom, so the cone and not the point cuts it | 0.2 |
| `ramp_angle_deg` | the steepest the pass ramps down from the edge's height | 3 |

A chamfer the part already models (inclined faces or a cone at the top of the walls, a hole's
countersink) is cut as modelled: its own width and height, and the tool's angle must match its angle
within half a degree. Refused when the cone is too short for the chamfer, when the tip or the tool's
body would touch the part anywhere but the chamfer, when the tool does not fit inside a pocket or hole,
and when stock still stands over the part's top (face it first).

## `engrave`

V-carves a closed area with a `v_bit`: a `region` drawn in the setup's plane (carved down from the top
of the material) or a `pocket` of the part (from its top, at most to its floor). The tool's centre
runs along the area's middle line at the depth where its cone just touches the nearest side, so the
cut meets the outline at the top and never crosses it.

| Key | Meaning | Default |
| --- | --- | --- |
| `max_depth_mm` | the deepest the V-bit goes; where the carve would go deeper, the bottom is flat | none: as deep as the carve needs |
| `clear_tool` | another tool of the job, a `flat_endmill`, that clears the flat bottom first | none |
| `stepdown_mm` | split the carve into levels; also the clearing's stepdown | one pass (the clearing: half the flat tool's diameter) |

A carve that needs a flat bottom and has no `clear_tool` is refused, as is one deeper than the
V-bit's cone. Where the flat tool does not fit the flat bottom's corners, a `rest_material` warning
says how much is left. Single-line text (lettering as a single stroke) is not built: draw the letters
as closed outlines.

## `trace`

Cuts along a curve `depth_mm` deep: a chain of the part's edges (`features`: edge selectors, `#o1.e12`,
followed end to end, open or closed, level in the setup's plane; the depth is below the edges) or a
`region` (always closed; the depth is below the top of the material; its points' order is its
direction). Tools: `flat_endmill`, `ball_endmill`, `bull_endmill`, `v_bit`, `chamfer_mill`.

| Key | Meaning | Default |
| --- | --- | --- |
| `depth_mm` | how deep the cut runs under the curve | required |
| `offset` | `none` (the tool's centre on the curve); a closed loop takes `inside` or `outside`, an open chain `left` or `right` of its direction | `none` |
| `start` | where an open chain starts: a point `[x, y]` (setup frame) at one of its ends, or its first edge's selector; required to offset an open chain | |
| `stepdown_mm` | the depth of each level | half the tool's diameter |
| `ramp_angle_deg` | the steepest ramp down along the curve | 3 |
