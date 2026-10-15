# Inspection: the viewer, snapshots, board references and check scripts

A board is looked at three ways, all from the saved `.kicad_pcb` and none needing KiCad:
the CAD Viewer shows it to the person and lets them point at things; a snapshot gives you
the same picture as a file; and scripts read it back, inside the build through kicad-python
or afterwards through `read_board`. The viewer never edits; you do, in the script.

## The viewer and snapshots

Show every board you build or change ([Show the model](../SKILL.md#show-the-model)). The
viewer draws the file read-only: every copper layer with its tracks, vias, pads and the
pours as KiCad filled them; silkscreen, fabrication and courtyard graphics; the outline and
holes; text; a ratsnest line for each connection copper does not make. Its board tools are
select, the item tree, a reference you can copy, measure, draw, display (layers on and off),
and Quick Edit. Drag to pan, wheel or pinch to zoom, double-click to fit.

```bash
cadgen pcb snapshot boards/blinky.kicad_pcb tmp/blinky.png
cadgen pcb snapshot boards/blinky.kicad_pcb tmp/blinky-dark.png --appearance dark --width 2400 --height 1600
```

A snapshot is the viewer's picture of the whole board, fitted to the image: the file for
your own review after each build. It takes `TARGET`, `OUT`, `--appearance` (`light` or
`dark`), `--width`/`--height`; a scene request (a camera, a display mode) is refused by name,
because a board is a drawing. It needs no KiCad.

## Board references

What the person points at reaches you as a **board reference**, a token
`<file>#<selector>` exactly like a STEP reference: the file is the board's absolute path
(JSON-quoted when it holds a space, `#`, a quote or a backslash), and several selectors share
one `#`, comma-joined: `/work/boards/servo.kicad_pcb#U3,C14.2`.

| Selector | Names |
| --- | --- |
| `#U3`, `#J2-J4`, `#_U1` | a part, by its reference designator |
| `#U3.9`, `#J4.A4`, `#U2.EP` | a pad of a part, numbered as the footprint numbers it |
| `#net:VIN`, `#net:TX/RX`, `#net:"a b"` | a net, by name; quoted when it holds whitespace, a quote, a comma, `@` or `#` |
| `#net:VIN@x57.6y21.6` | that net's copper (a track, via, pour or pad) at a point |
| `#@x3.5y3.5` | a point on the board |

Points are millimetres in the board's own frame, the one the script's `Vector2.from_xy_mm`
takes, written with at most three decimals (`x-3y0.5`). Paste a reference the viewer gave
you as it is: `resolve()` reads it in the frame it was written in.

## Resolving one

```python
from cadgen.kicad.board_index import read_board

board = read_board("boards/blinky.kicad_pcb")       # the saved file; runs nothing
part = board.resolve("boards/blinky.kicad_pcb#R1")  # a Part: .at, .rotation, .side, .value, .footprint, .fields, .pads
pad = board.resolve("#R1.2")                        # a Pad: .net, .at, .number, .type, .side, .polygon
net = board.resolve("#net:VBUS")                    # a Net: .netclass, .pads, .tracks, .vias, .zones
copper = board.resolve("#net:GND@x7.2y8")           # Copper: .items, that net's copper there, nearest first
point = board.resolve("#@x0y0")                     # a Point: .on_board, .parts, .pads, .copper, .holes
board.resolve_all("boards/blinky.kicad_pcb#R1,D1.2")   # a token of several, each resolved
board.at(12, 8)                                     # what is at a point
board.parts, board.nets, board.tracks, board.vias, board.zones, board.holes, board.outline
```

A file before the `#` must name the board you read; a part, pad or net the board does not
have is refused with what it does have, and copper nowhere near the point names the nearest.
A number several pads share (a connector's shield pads, a regulator's tab and pin both `2`)
resolves to the first, whose repr says how many share it; `part.pads_numbered("SH")` lists
them all. A reference names the board as it was when the person picked: if you have rebuilt
since, resolve against the new board and check it still names what they meant.

## Check scripts

A real board is checked hundreds of times, so write the checks once and run them every
iteration. Two places to read from, both fine:

- **Inside the function**, raw kicad-python on the live board, before it is saved:
  `board.get_tracks()`, `board.get_items_by_net(Net(name="VBUS"))`,
  `board.get_connected_items([pad])`, `board.get_pad_shapes_as_polygons(pads)`,
  `zone.filled_polygons` after `board.refill_zones()`. Assert, or print a table; an
  exception stops the build and saves nothing, which is what a failed check should do.
- **After the build**, `read_board` on the saved file: no server, fast, and the same
  objects the viewer's references resolve to. Keep these as `check_*.py` scripts beside the
  board and run them with `python` after each build.

```python
from cadgen.kicad.board_index import read_board

board = read_board("boards/blinky.kicad_pcb")
for net in board.nets:
    if net.name in ("VBUS", "GND") and net.tracks:
        narrowest = min(track.width for track in net.tracks)
        assert narrowest >= 0.3, f"{net.name}: narrowest track {narrowest} mm"
gnd = board.resolve("#net:GND")
for pad in gnd.pads:                                 # every SMD ground pad has a via within 1.5 mm
    if pad.side == "top":
        near = min(((pad.at[0] - via.at[0]) ** 2 + (pad.at[1] - via.at[1]) ** 2) ** 0.5 for via in gnd.vias)
        assert near <= 1.5, f"{pad.part}.{pad.number}: nearest GND via {near:.2f} mm away"
```

Useful checks: the narrowest track per power net against its current; every surface-mount
ground pad near a ground via; a decoupling capacitor's pad within 3 mm of the IC pin it
serves; no part's outline over another's; every pad in a net reachable (`get_connected_items`
from the first pad); the pour's filled area per layer. The viewer's references are the same
data: a check that fails can print `#U1.3`-style selectors the person can click.

## The loop

1. The person picks in the viewer and writes what they want: "move `#C14` next to
   `#U3.9`", "this track `#net:VIN@x57.6y21.6` is too thin".
2. Resolve each reference with `read_board`; find the line of the script that placed the
   part or drew the copper (the pad dict and the route list name them).
3. Edit the script; run it; run the checks and `cadgen pcb drc`.
4. The viewer shows the new board. A review before ordering is $pcb-dfm's.
