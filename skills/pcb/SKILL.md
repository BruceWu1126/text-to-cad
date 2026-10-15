---
name: pcb
description: Design printed circuit boards as Python scripts on KiCad's own API and get a KiCad board file. Use for PCBs and circuit boards, .kicad_pcb files, footprints and footprint libraries (fp-lib-table), placement, hand routing, vias and copper pours, design rules and DRC, Gerbers, drill, pick-and-place and KiCad's other exports, and for opening a board in CAD Viewer. Needs the KiCad 10.99 nightly (the KiCad 11 API).
license: MIT
---

# PCB design

Provenance: maintained in [earthtojake/text-to-cad](https://github.com/earthtojake/text-to-cad).
Use the installed local skill files as the runtime source of truth; the
repository link is only for provenance and release review.

## Setup

Run cadgen through [uv](https://docs.astral.sh/uv/), so this skill's commands share
one installation, and its warm build daemon, with the CAD app's server:

- `cadgen` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 cadgen`
- `python` below means `uvx --no-config --managed-python --python 3.13 --from cadgen==0.7.21 python`

The first run downloads that installation and the first snapshot its headless
browser; later runs reuse both. `cadgen doctor <skill-dir>` reports the installation in
use and checks that it is the one this skill pins; use it for installation errors.

A board is built by KiCad itself, through its API server (`kicad-cli api-server`), which
only the **KiCad 10.99 nightly** has (KiCad 11 when it is released). KiCad 10.0 is refused
at start: it has no `api-server`. Install the nightly; it is never bundled:

- Ubuntu: `sudo add-apt-repository ppa:kicad/kicad-dev-nightly && sudo apt install kicad-nightly kicad-nightly-footprints`
  (symbols and 3D models are not needed).
- macOS: the nightly installer from https://www.kicad.org/download/macos/ (KiCad.app carries `kicad-cli`).
- Windows: the nightly installer from https://www.kicad.org/download/windows/
  (`C:\Program Files\KiCad\<version>\bin\kicad-cli.exe`).

cadgen finds `kicad-cli-nightly` or `kicad-cli` on `PATH`, then in those install folders;
`CADGEN_KICAD_CLI` names one explicitly. Each build starts its own server with an empty
settings directory and stops it when done: no KiCad window, no daemon, nothing resident.

## The contract

**A board is a Python script. The decorated function receives an empty KiCad board
(`cadgen.kipy.board.Board`, kicad-python's board) and fills it with raw kicad-python calls;
cadgen opens the document, loads the footprint libraries, refills the zones, counts what is
unconnected, saves, and prints one line.** The script is the source; `blinky.kicad_pcb` is
an output, as a STEP is for a part: never edit it by hand, edit the script and run it.

```python
"""An LED and its resistor on a 2-pin header: the smallest complete board."""
from cadgen import pcb
from cadgen.kipy.board_types import BoardLayer, BoardPolygon, Net, Track, Via, Zone
from cadgen.kipy.geometry import Angle, PolygonWithHoles, PolyLineNode, Vector2
from cadgen.kipy.util.units import from_mm

WIDTH, HEIGHT = 40.0, 30.0           # the outline, centred on KiCad's origin
PARTS = {                             # ref: footprint, (x, y) mm, rotation, pad -> net
    "J1": ("Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical", (-15, 0), 0, {"1": "VBUS", "2": "GND"}),
    "R1": ("Resistor_SMD:R_0603_1608Metric", (0, 5), -90, {"1": "VBUS", "2": "LED"}),
    "D1": ("LED_SMD:LED_0603_1608Metric", (8, -5), 0, {"1": "GND", "2": "LED"}),
}


def rectangle(w, h, inset=0.0):
    poly = PolygonWithHoles()
    for x, y in ((-w / 2 + inset, -h / 2 + inset), (w / 2 - inset, -h / 2 + inset),
                 (w / 2 - inset, h / 2 - inset), (-w / 2 + inset, h / 2 - inset)):
        poly.outline.append(PolyLineNode.from_xy(from_mm(x), from_mm(y)))
    poly.outline.closed = True
    return poly


def track(net, width, *points, layer=BoardLayer.BL_F_Cu):
    """Straight segments through `points` (Vector2 or (x, y) mm), one Track each."""
    nodes = [p if isinstance(p, Vector2) else Vector2.from_xy_mm(*p) for p in points]
    for a, b in zip(nodes, nodes[1:]):
        t = Track()
        t.start, t.end, t.width, t.layer, t.net = a, b, from_mm(width), layer, Net(name=net)
        yield t


@pcb
def blinky(board):
    # Place: every footprint comes from a library the project table (fp-lib-table) declares.
    pads, placed = {}, []
    for ref, (footprint, at, rotation, nets) in PARTS.items():
        fp = board.place_footprint_from_library(footprint, Vector2.from_xy_mm(*at), Angle.from_degrees(rotation))
        fp.reference_field.text.value = ref
        for pad in fp.definition.pads:
            pad.net = Net(name=nets[pad.number])
            pads[f"{ref}.{pad.number}"] = pad.position         # absolute, in nm: route from these
        placed.append(fp)
    board.update_items(placed)

    # Outline and copper, created in one call.
    edge = BoardPolygon()
    edge.polygons.append(rectangle(WIDTH, HEIGHT))
    edge.layer = BoardLayer.BL_Edge_Cuts
    edge.attributes.stroke.width = from_mm(0.1)

    via = Via()
    via.position, via.net = Vector2.from_xy_mm(7.2125, -8), Net(name="GND")
    via.diameter, via.drill_diameter = from_mm(0.6), from_mm(0.3)

    pour = Zone()                                                # a copper zone by default
    pour.outline, pour.layers, pour.net, pour.name = rectangle(WIDTH, HEIGHT, inset=0.5), [BoardLayer.BL_B_Cu], Net(name="GND"), "GND"

    board.create_items([
        edge,
        *track("VBUS", 0.3, pads["J1.1"], (0, 0), pads["R1.1"]),
        *track("LED", 0.25, pads["R1.2"], (0, 8), (12, 8), (12, -5), pads["D1.2"]),
        *track("GND", 0.25, pads["D1.1"], via.position),
        via,
        pour,
    ])

    rules = board.get_design_rules().rules                       # the board's DRC limits
    rules.constraints.min_clearance = from_mm(0.15)
    rules.constraints.min_track_width = from_mm(0.15)
    board.set_design_rules(rules)


if __name__ == "__main__":
    blinky()
```

```bash
python blinky.py      # blinky.kicad_pcb: 3 parts, 7 tracks, 1 via, 1 zone, 0 unconnected, 3.6 s
```

The run writes `blinky.kicad_pcb`, `blinky.kicad_pro` and `blinky.kicad_prl` beside the
script (`@pcb(path="boards/blinky.kicad_pcb")` moves them, relative to the script), and
`blinky.kicad_dru` when the script sets custom rules. Every run rebuilds the board from
nothing: KiCad assigns the ids, so nothing is remembered between runs and nothing needs
deleting. The call, in order:

1. finds `kicad-cli` and starts its API server with an empty settings directory, so only the
   project's library table is read;
2. creates the empty document at the output path and loads the footprint libraries
   `fp-lib-table` declares (a row that fails to load is reported by nickname and status);
3. calls your function with the board;
4. `refill_zones()`, then counts pads a copper path does not join to the rest of their net;
5. `save()`, closes the document, stops the server;
6. prints `blinky.kicad_pcb: 3 parts, 7 tracks, 1 via, 1 zone, 0 unconnected, 3.6 s`.

An exception in your function stops the server and propagates: nothing is saved.
`footprint 'Lib:Name' not found` is KiCad's message for a footprint no loaded library has;
the fix is a row in `fp-lib-table` ([parts](references/parts.md)). `0 unconnected` is
connectivity, not DRC: run `cadgen pcb drc` for KiCad's rules ([below](#checking-a-board)).

**Everything on the board is raw kicad-python** (`cadgen.kipy`, vendored). Lengths are
integers in nanometres (`from_mm(0.25)`, `to_mm(n)`), positions `Vector2.from_xy_mm(x, y)`,
angles `Angle.from_degrees(r)`, layers `BoardLayer.BL_F_Cu`, `BL_B_Cu`, `BL_In1_Cu`,
`BL_Edge_Cuts`, `BL_F_SilkS`... Coordinates are KiCad's: millimetres, **y down**, from the
page origin; a footprint placed at (0, 5) with rotation -90 is what KiCad shows there. Nets
exist by being named: `Net(name="VBUS")` on a pad, a track or a zone is the net. A placed
footprint returns with its pads at absolute positions, so route from `pad.position`, never
from numbers copied by hand. The full reference, with design rules, custom rules
(`.kicad_dru`), net classes, text, keepouts and exports, is [the board API](references/board-api.md).

## Libraries

Beside the script, `fp-lib-table` (KiCad's project library table) names every footprint
library the board uses, stock or its own:

```text
(fp_lib_table
  (version 7)
  (lib (name "Resistor_SMD") (type "KiCad") (uri "${KICAD10_FOOTPRINT_DIR}/Resistor_SMD.pretty") (options "") (descr ""))
  (lib (name "LED_SMD") (type "KiCad") (uri "${KICAD10_FOOTPRINT_DIR}/LED_SMD.pretty") (options "") (descr ""))
  (lib (name "Connector_PinHeader_2.54mm") (type "KiCad") (uri "${KICAD10_FOOTPRINT_DIR}/Connector_PinHeader_2.54mm.pretty") (options "") (descr ""))
  (lib (name "my") (type "KiCad") (uri "${KIPRJMOD}/my.pretty") (options "") (descr "this board's own footprints"))
)
```

`${KICAD10_FOOTPRINT_DIR}` is KiCad's stock footprint folder (the variable's name is the
nightly's today; KiCad renames it per major release); `${KIPRJMOD}` is the script's folder,
so `my.pretty/` holds the board's own `.kicad_mod` files. Only the rows listed are loaded
(each costs ~0.3 s and ~60 MB), so list what the board uses. Finding names, browsing a
library and reading a footprint's pads: [parts](references/parts.md).

## Workflow

A real board takes hours of iteration, not one run. Work in small steps and look after each.

1. Turn the request into a brief: what the board does, its connectors and power, the
   outline and mounting holes (from the enclosure when there is one), layer count, the fab
   and its limits (they become the design rules).
2. Choose footprints and write `fp-lib-table`. Read each footprint's pads before placing it
   ([parts](references/parts.md)): pad numbers are what the nets go on.
3. Place one functional group at a time and route it before placing the next: a connector
   and what serves it, a regulator and its capacitors, an IC with its decoupling. Connectors
   at the edges, decoupling at the pins, fanouts planned for every fine-pitch part before any
   long track ([layout](references/layout.md)).
4. Route by hand, never by autorouter: octolinear (0/45/90 degrees), buses as parallel
   tracks that turn together, widths and pour sizes from the current each net carries,
   ground as a pour ([routing](references/routing.md)).
5. Iterate: build, snapshot or show the board, `cadgen pcb drc`, fix, again. Write small
   check scripts for what you keep verifying (pad-to-pad distances, a net's narrowest track,
   every ground pad reaching the pour) and run them each round ([inspection](references/inspection.md)).
6. Set the design rules to the fab's limits, export what the fab needs
   ([manufacturing](references/manufacturing.md)), show the board, hand off.

## Checking a board

```bash
cadgen pcb drc blinky.kicad_pcb              # KiCad's DRC on the saved board
cadgen pcb snapshot blinky.kicad_pcb tmp/blinky.png
```

`cadgen pcb drc` runs KiCad's design rule check (`kicad-cli pcb drc`, the same install) on
the saved file and prints every violation and unconnected item as text; it exits 1 when any
error-severity violation or unconnected item remains. It is a separate step, never part of
a build, so a build with mistakes still writes a board you can look at. The snapshot is the
CAD Viewer's picture, drawn from the file; it needs no KiCad.

## Show the model

Show the user each file you create or change, and any they ask to see. Snapshots and
validation don't replace this.

- If your tools include `cad_show` (your host may prefix it), use it with the file's
  absolute path, and follow its description for when to call it again. `cad_view` reads
  what the user selected; `cad_screenshot` shows you what they see. Neither is a review
  of your own work.
- Otherwise run the CAD Viewer, from any folder:

  ```bash
  cadgen viewer --host 127.0.0.1 --json --detach
  ```

  `--detach` returns once the viewer answers requests and leaves it running in the
  background: always pass it, since a foreground viewer never exits (and piping its
  output through `tail` can hide the URL for good). It starts this machine's one viewer,
  or reuses it. Read `url` from its one JSON line (never guess the port), and for each
  file return `url?file=<its URL-encoded absolute path>`. If it fails to launch, say so.

The viewer draws a `.kicad_pcb` read-only from the file, every layer, with its board tools
(select, the tree, measure, display, copy a reference): drag to pan, wheel or pinch to zoom,
double-click to fit. What the user points at reaches you as a board reference
(`/work/boards/blinky.kicad_pcb#R1`, `#R1.2`, `#net:VBUS`, `#net:VBUS@x40.1y21.6`,
`#@x40.1y21.6`); `read_board(path).resolve(ref)` answers it
([inspection](references/inspection.md)).

## Handoff

Report the files written, the build's line (parts, tracks, vias, zones, unconnected), the
`cadgen pcb drc` result, the checks you scripted, and your assumptions (footprints chosen,
pad-to-pin mapping, currents, the fab's limits in the rules). Show the board
([Show the model](#show-the-model)). Before ordering, $pcb-dfm reviews the board for
fabrication, assembly and sourcing.

## References

- [The board API](references/board-api.md): kicad-python for boards: units, layers, geometry, items, nets, rules, net classes, exports, library browsing.
- [Parts](references/parts.md): `fp-lib-table`, stock libraries, the board's own `.pretty`, reading a footprint's pads.
- [Layout](references/layout.md): placement that makes boards that work.
- [Routing](references/routing.md): hand routing, widths for current, vias, pours.
- [Manufacturing](references/manufacturing.md): design rules from the fab's limits, `cadgen pcb drc`, the export jobs, ordering.
- [Inspection](references/inspection.md): the viewer, snapshots, board references, `read_board`, check scripts.
