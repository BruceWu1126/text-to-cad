# Imported boards: a board drawn in KiCad

Some boards have no script: a person drew them in KiCad, or they came from a vendor or an
earlier project. Their KiCad files are the source, as an imported STEP is for the CAD skill.
Keep them under `PCB/imported/`, with their upstream names and their `.kicad_pro`,
`.kicad_sch` and `.kicad_pcb` side by side. They are inputs: no script makes them, and
nothing rebuilds them. Don't turn one into a `@pcb` script unless the user asks.

## Check it

```bash
cadgen pcb validate PCB/imported/controller.kicad_pcb     # ERC + DRC with parity, on a staged copy
cadgen pcb snapshot PCB/imported/controller.kicad_pcb tmp/controller.png
```

`validate` never writes into the project. It reports KiCad's errors and the review's
suggestions (decoupling, track current, [sourcing](parts.md)).

## Edit it

- Edit the project's own files, and keep their names. If a tool writes an alternate file,
  check its result, then copy it over the original. Never switch to a new file name without
  saying so.
- Keep backups and intermediate files (a staged copy, a router's session file, renders) in
  `tmp/`, never beside the project.
- Only one agent or process writes a project at a time. Others read it.
- Run `cadgen pcb validate` after each saved step, and fix errors before the next one.
- A KiCad file is text, but edit it with care: change only the items you mean, keep every
  `uuid`, and validate after the edit. For placement or routing, prefer KiCad's own tools
  (the user's KiCad, or `kicad-cli`) over rewriting coordinates by hand.
- Part numbers go in the schematic's symbol fields (`LCSC`, `MPN`, `Manufacturer`), never
  in a BOM file. The BOM is generated from them.

## Order from it

The doors take the saved board, as they do for a generated one, and write beside it:

```bash
cadgen pcb gerber PCB/imported/controller.kicad_pcb
cadgen pcb bom PCB/imported/controller.kicad_pcb
cadgen pcb pos PCB/imported/controller.kicad_pcb
```

Gerbers and the placement file are refused for a board with unrouted connections or a DRC
error. [Manufacturing](manufacturing.md) says how to order.

## Show it

Show the board and its schematic in the CAD Viewer as for any board (see "Show the model" in
SKILL.md). What the user points at comes back as a board reference, and
`pcb.read_board(path).resolve(ref)` answers it ([inspection](inspection.md)). A part on an
imported board has no `Script` line: edit the KiCad file itself.
