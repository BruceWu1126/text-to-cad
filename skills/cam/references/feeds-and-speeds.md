# Feeds and speeds

**These are starting values for a prove-out, not tuned ones.** cadgen works out each operation's
spindle speed and feed from the material, the tool and the machine with the textbook arithmetic
below, from numbers inside the published ranges (tool makers' charts and Machinery's Handbook). It
knows nothing of the machine's rigidity, the tool's coating, its runout, the workholding or how the
chips clear. Say so when you hand over a program: run it with the feed override low, listen, and let
the machinist raise it.

An operation's `rpm`, `feed_mm_min` and `plunge_mm_min` win over the worked-out values. Use them when
the user gives their own numbers (from their tool maker or their experience); they are still held to
the machine's limits.

## The arithmetic

For a milling cutter of diameter `D` mm with `z` flutes:

- **Spindle speed** `rpm = 1000 x Vc / (pi x D)`, with `Vc` the material's surface speed in m/min
  (x 0.3 for an HSS tool). It is a whole number of rpm, rounded before anything is worked out from it.
- **Feed** `feed = rpm x z x fz`, with `fz` the chip load (mm per tooth) at the tool's diameter. A tool
  that does not state `flutes` has 2, a face mill 4.
- **Plunge** (straight down) is 0.3 x the feed. When the operation states a feed and no plunge, the
  plunge is 0.3 x that feed.
- **Chip load at a diameter** is read from the material's table, in a straight line between the two
  diameters either side of the tool (a 4.5 mm cutter gets a chip load between the 3 mm and 6 mm
  ones). Under the smallest it shrinks in proportion to the diameter; over the largest it holds the
  largest's.
- **A thread mill** mills at half the chip load: its whole thread form is in the cut.
- **Chip thinning** (for adaptive clearing, phase 2): at a radial engagement `e` under half
  the diameter the chip load is raised by `1 / (2 sqrt(e (1 - e)))`, at most 3 times.

Hole makers move along their axis, so their feed is per revolution and their plunge is their feed:

| Tool | Surface speed | Feed per revolution |
| --- | --- | --- |
| `drill`, `spot_drill`, `counterbore` | 0.7 x `Vc` | 3 x `fz` (two lips, each about 1.5 x an end mill's chip) |
| `reamer` | two thirds of the drill's | twice the drill's |
| `boring_bar`, `back_bore` | 0.7 x `Vc` | 1.5 x `fz` |
| `tap` | 0.08 x `Vc` (already a tap's own speed: never derated again for HSS) | exactly the pitch |

A tap feeds exactly `rpm x pitch`: anything else strips the thread or breaks the tap. A tap's
`feed_mm_min` that is not `rpm x pitch` is refused; set `rpm` instead.

## The machine's limits

- A spindle speed over `machine.max_rpm` runs at `max_rpm`, with an `rpm_clamped` warning: `T1 is
  capped at the machine's 18,000 rpm; the tool and material call for 21,221 rpm, so the cut runs
  slower at the same chip load`. The feed follows the slower spindle, unless the operation stated
  its feed, which is then kept (each tooth takes a bigger chip: the warning gives the feed that keeps
  the planned chip).
- A feed or plunge over `machine.max_feed_mm_min` runs at the limit, with a `feed_clamped` warning:
  each tooth takes a thinner chip, which rubs and wears the tool. A lower `rpm` on the operation, or
  a tool with fewer flutes, keeps the planned chip at that feed.
- A tap whose `rpm x pitch` is over the feed limit slows its spindle until the two agree, with a
  `feed_clamped` warning.

Every clamp is a warning in the findings, never silent. Without `max_rpm` and `max_feed_mm_min` the
plan cannot know them: ask the user for both, especially on a hobby machine (a 24,000 rpm router
spindle, a 1,000 mm/min feed limit).

## The material table

Carbide surface speed, and chip load in mm per tooth at each tool diameter:

| Material | `Vc` m/min | 1 mm | 3 mm | 6 mm | 10 mm | 12 mm | 20 mm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `aluminum-6061` | 400 | 0.008 | 0.025 | 0.040 | 0.060 | 0.070 | 0.100 |
| `aluminum-7075` | 400 | 0.008 | 0.025 | 0.040 | 0.060 | 0.070 | 0.100 |
| `mild-steel` | 120 | 0.005 | 0.015 | 0.030 | 0.045 | 0.050 | 0.080 |
| `stainless-304` | 75 | 0.004 | 0.012 | 0.025 | 0.040 | 0.045 | 0.065 |
| `brass` | 250 | 0.008 | 0.020 | 0.035 | 0.050 | 0.060 | 0.090 |
| `acetal` | 250 | 0.015 | 0.035 | 0.060 | 0.090 | 0.100 | 0.150 |
| `hdpe` | 250 | 0.015 | 0.040 | 0.070 | 0.100 | 0.120 | 0.170 |
| `acrylic` | 200 | 0.012 | 0.030 | 0.050 | 0.080 | 0.090 | 0.130 |
| `mdf` | 400 | 0.020 | 0.050 | 0.120 | 0.200 | 0.250 | 0.350 |
| `plywood` | 400 | 0.020 | 0.045 | 0.100 | 0.170 | 0.200 | 0.300 |
| `hardwood` | 400 | 0.015 | 0.040 | 0.090 | 0.150 | 0.180 | 0.260 |

The wrought aluminium alloys share a row (the charts group them). For a material not in the table,
give `{"name": "bronze", "surface_m_min": 90, "chipload_mm": 0.03}` (or a chip load per diameter,
`{"3": 0.015, "6": 0.03}`) from the tool maker's chart; never borrow a row that only sounds close.

## A worked example

A 6 mm 3-flute carbide end mill in 6061 on a machine with no limits stated:
`rpm = 1000 x 400 / (pi x 6) = 21221`; `feed = 21221 x 3 x 0.040 = 2546.5 mm/min`; plunge
`0.3 x 21221 x 3 x 0.040 = 764 mm/min`. A 6 mm carbide drill: `0.7 x 400` m/min gives 14854 rpm, at
`3 x 0.040 = 0.12` mm per revolution, 1782.5 mm/min. These are the numbers the program header and
the sidecar's `tools[].uses` show.
