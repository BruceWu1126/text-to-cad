# Routing: by hand, in small groups

There is no autorouter. Every track is a `Track` the script draws, and that is the point:
an autorouted board of this size is slower to fix than a hand-routed one is to draw, and a
hand-routed board can be read. A board is finished when the build says `0 unconnected` and
`cadgen pcb drc` reports no error.

## The order

1. Place one functional group ([layout](layout.md)) and route it before placing the next.
   A connector and what it feeds; a regulator and its capacitors; an IC with its decoupling
   and pull-ups.
2. Fan out every fine-pitch part first: a short stub out of each pad in the direction its net
   leaves, a via where it changes layer, before any long track exists to block it.
3. Power and ground: the widest tracks, the pours and their vias.
4. Data pairs and clocks: short, side by side, few vias.
5. Everything else, as buses where signals travel together.
6. Build, snapshot, DRC, check scripts; fix; repeat. A real board is hours of this.

## Rules

- **Octolinear**: every segment at 0, 45 or 90 degrees. A route is a point list; each pair of
  points is one `Track`, so write a `track(net, width, *points)` helper (the skill's example
  has one) and choose corner points so that every diagonal segment has |dx| == |dy|: from
  (0, 0) to (5, 3), the corner is (2, 0), so the second segment runs 3 across and 3 down
  at 45 degrees; a corner at (1, 0) would leave a segment at an arbitrary angle.
- **Start and end on pads**: the first and last point of a route are `pad.position` values
  read from the placed footprint, never typed numbers. A track that ends 0.1 mm from a pad
  is unconnected.
- **Fan out first**: out of a QFN, SOT-23-5, USB-C or any pad row at 0.65 mm pitch or
  finer, a stub from each pad at the pad's width toward where its net goes, straight out of
  the row, before routing anything long. Wide power tracks reach such a pad through a stub at
  the pad's width for the first millimetre, then widen.
- **Buses**: related signals (a data bus, the pins of a row of identical connectors) run
  together as parallel, equally spaced tracks that turn together; a row of identical
  connectors gets one straight spine per net through the same pin of each. Generate them in a
  loop over an offset, not one by one.
- **No detours**: a signal track close to the straight-line distance between its pads. If
  it is not, move a part or change layer with a via; do not wind around.
- **Width from current**: every power track sized for the current its net carries. Outer
  layers, 1 oz copper, 10 °C rise (IPC-2221, approximate): 0.5 A → 0.15 mm, 1 A → 0.3 mm,
  2 A → 0.75 mm, 3 A → 1.25 mm, 5 A → 2.5 mm; inner layers need about twice the width.
  Compute it for the real current and temperature, and let a pour carry what a track cannot.
- **Vias**: one per layer change, at or above the fab's minimum (0.6 mm diameter, 0.3 mm
  drill is a safe standard size); several in parallel for power and thermal paths; none under
  a part you cannot inspect unless the fab tents them.
- **Data pairs** (USB, CAN, LVDS): equal lengths, side by side at a constant gap, no vias if
  possible, a solid ground pour under them; a `CRCT_DIFF_PAIR_GAP` or `CRCT_SKEW` custom
  rule makes the DRC hold the pair.
- **Layers**: on a 2-layer board, keep the bottom pour as whole as possible: route on top,
  cross on the bottom only for short jumps, and never run a long track across the pour.

## Pours

Ground is a zone, filled by KiCad after your routes exist, around them.

- **2 layers**: a ground pour on the bottom (`layers=[BoardLayer.BL_B_Cu]`), and every
  surface-mount ground pad reaches it through a via beside the pad: a short track from the
  pad to the via, as the skill's example does for D1. Through-hole ground pins reach the pour
  directly. A pour on top as well is fine for shielding, but it is the vias that make ground
  work.
- **Thermal relief or solid**: KiCad connects pads to a pour with thermal spokes by default
  (`ZoneConnectionStyle.ZCS_THERMAL`), good for hand soldering, weak for current. For a
  power pour, or a connector's ground pads that one spoke cannot serve, set
  `zone.connection.zone_connection = ZCS_FULL` on the zone, or scope it to the footprint
  with a `CRCT_ZONE_CONNECTION` custom rule ([board API](board-api.md#custom-rules)).
- **Islands**: a pour cut into pieces by routes leaves islands KiCad removes (its default)
  or, if kept, copper connected to nothing. After `refill_zones()`, the build's
  `unconnected` count tells you when a pad's only path was through a piece that is gone.
- **Priority**: where pours overlap (a power island inside the ground pour), the higher
  `priority` wins and the other keeps its clearance from it.
- **4 layers**: planes on the inner layers (`BL_In1_Cu` ground, `BL_In2_Cu` power), routes
  on the outer two, a via beside every pad of a plane's net. A plane joins its net only where
  a via or through-hole pad of that net passes through it.

## Check as you go

Write the checks you keep repeating as small scripts and run them every iteration
([inspection](inspection.md)): the narrowest track on each power net against its current;
every ground pad within a millimetre of a ground via; no track segment longer than the
distance between its pads by more than a factor you choose; the pour's filled area per net.
Raw kicad-python reads inside the function (`board.get_tracks()`, `get_items_by_net`,
`get_connected_items`, `zone.filled_polygons`) and `read_board` on the saved file outside it
are both fine; the check is the point, not where it runs.

## Reading the result

- `0 unconnected` and a clean `cadgen pcb drc`: routed.
- `N unconnected`: pads a copper path does not reach. Usual causes, in order: a track ending
  near a pad rather than on it; a via on the wrong net; a pour island removed under a pad
  whose only connection it was; a layer mismatch (track on `BL_F_Cu`, pad on the bottom).
- DRC errors name KiCad's rule and the items: clearance between two nets (move one);
  track width or via size below the rules (widen, or the rule is wrong for the fab);
  copper too close to the edge; courtyards overlapping. Silkscreen findings are warnings, but
  a clipped polarity mark is a real defect.
- Then look at the picture. Long detours, a pour cut in two, a via under a part are not
  errors; fix them anyway.
