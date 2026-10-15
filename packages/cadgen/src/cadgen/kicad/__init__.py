"""KiCad: the PCB kernel cadgen drives, as build123d drives OpenCascade.

A ``@pcb`` script (:mod:`cadgen.pcb`) fills a board open in KiCad's own PCB
editor through KiCad's API (``kipy``, re-exported as :mod:`cadgen.kipy`): the
editor is the resident process, run headless by :mod:`cadgen.kicad.editor`,
and the board file is KiCad's. cadgen writes no KiCad document itself.

Modules, each importing nothing heavy at module scope:

- ``editor``: find the PCB editor, run it headless with the API on, open or
  create a board document, empty it, count what is unrouted, stop it.
- ``install``: find ``kicad-cli`` and the symbol/footprint libraries.
- ``cli``: run ``kicad-cli`` and read its DRC report (the one check the API
  does not offer).
- ``sexpr``: read the S-expression syntax every KiCad file uses.
- ``geometry`` / ``naming``: the frames, shapes and names the readers share.
- ``board_index`` / ``refs``: any board read back from its file as what a
  person points at, and board references (``#U3.9``, ``#net:VIN``...)
  answered: ``pcb.read_board(path).resolve(ref)``.
- ``plot``: the viewer's picture of a board, geometry per layer, from the file.
"""
