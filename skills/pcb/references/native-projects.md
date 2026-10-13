# Native projects: a board drawn in KiCad

Some boards have no script: a person drew them in KiCad, or they came from a vendor or an
earlier project. Their KiCad files are the source. Check them, edit them and order from them
in place. Don't turn one into a `@pcb` script unless the user asks.

## Find the project

A folder means its one KiCad project. It is the folder's own `.kicad_pro`, else the shallowest
one at most three folders below it. KiCad's `*-backups/` folders, `build/`, hidden folders and
`node_modules` are never searched. Two projects equally shallow are refused, with both named.
Name the one you mean, and ask the user when it is not clear which.

```bash
cadgen pcb validate PCB/controller          # ERC + DRC of the folder's project
cadgen pcb validate PCB/controller/controller.kicad_pro
```

The project's board and schematic are the `.kicad_pcb` and `.kicad_sch` beside its
`.kicad_pro`, with the same name. These are the files to work on. The viewer and every
command above read them.

## Edit it

- Keep using the project's own paths. Save each meaningful step to the canonical
  `.kicad_pcb` / `.kicad_sch`, so the viewer and anyone watching see progress.
- Keep backups and intermediate files (a staged copy, a router's session file, renders) in
  `build/` or another scratch folder, never beside the project files. A tool that writes an
  alternate file: check its result, then copy it over the canonical file, keeping the old one
  in `build/`. Never switch to a different file name without saying so.
- Only one agent or process writes a project at a time. Others read it.
- After each saved step, run `cadgen pcb validate <folder>`. It checks a staged copy and never
  writes into the project. Fix errors before the next step. The review's suggestions
  (decoupling, track current, [sourcing](parts.md)) are advice.
- A KiCad file is text, but edit it with care: change the items you mean, keep every `uuid`,
  and validate after the edit. For placement or routing, prefer KiCad's own tools (the
  user's KiCad, or `kicad-cli`) over rewriting coordinates by hand.
- Custom symbols and footprints go in a project library beside the project (`<project>.kicad_sym`,
  `<project>.pretty/`), listed in its `sym-lib-table` / `fp-lib-table` with
  `${KIPRJMOD}/...` paths, so the project opens anywhere.
- Part numbers go in the schematic's symbol fields (`LCSC`, `MPN`, `Manufacturer`), never
  in a BOM file. The BOM is generated from them.

## Order from it

```bash
cadgen pcb export PCB/controller            # ERC + DRC, then build/: gerbers.zip, bom.csv, pos.csv
```

`export` refuses a project with unrouted connections or any error, and writes nothing then.
The files are the ones a `@pcb(gerber=True, bom=True, pos=True)` build writes, byte for byte
for the same board. [Manufacturing](manufacturing.md) says how to order them.

## Show it

Show the board and the schematic in the CAD Viewer as for any board (see "Show the model"
in SKILL.md). What the user points at comes back as a board reference.
`pcb.read_board(path).resolve(ref)` answers it ([inspection](inspection.md)). A part of a
native project has no `Script` line: edit the KiCad file itself.
