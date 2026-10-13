"""What a board's and a harness's manufacturing doors share: one file of a saved document.

``cadgen pcb gerber|bom|pos`` take a ``.kicad_pcb`` DOCUMENT -- one a ``@pcb``
model wrote, or one a person drew in KiCad -- and write only their own file,
through the exporter a model's ``@pcb(gerber=, bom=, pos=)`` uses, so a door and
a model run cannot write different bytes. A door never runs a script. Gerbers
and the placement file are refused for a board with unrouted connections or DRC
errors.

``cadgen pcb export`` takes a whole project -- its file, or a folder holding one --
and writes all three into the project's ``build/`` folder, checked once.

``cadgen harness bom`` takes a wiring harness's ``.harness.yml``: its BOM is
WireViz's list of the document's parts (``cadgen.wireviz.bom``), the exporter
``@harness(bom=)`` writes through.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cadgen.results import FabExportFile, FabExportResult
from cadgen.file_types import format_of


def _absolute(path: Path) -> Path:
    path = Path(path).expanduser()
    return (path if path.is_absolute() else Path.cwd() / path).resolve()


def _destination(document: Path, fmt: str, out: Path | None) -> Path:
    from cadgen.metadata import fab_build_path, fab_sibling

    if out is not None:
        return _absolute(out)
    return fab_build_path(document, fmt) if format_of(document) == "kicad_pcb" else fab_sibling(document, fmt)


def _write(destination: Path, data: bytes, fmt: str) -> FabExportResult:
    from cadgen._internal.atomic_replace import write_bytes_atomic

    destination.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(destination, data)
    return FabExportResult(ok=True, files=(FabExportFile(path=destination, fmt=fmt),))


def board_export(fmt: str, target: Path, out: Path | None, *, verbose: bool = False, **options) -> FabExportResult:
    """``cadgen pcb <fmt>``: the ``fmt`` manufacturing file of the saved board ``target``."""
    from cadgen.kicad.fab import MANUFACTURING, export, require_finished

    board = _absolute(target)
    if format_of(board) != "kicad_pcb":
        hint = " (a harness's BOM is cadgen harness bom)" if format_of(board) == "harness" else ""
        raise ValueError(f"{board.name} is not a KiCad board: cadgen pcb {fmt} takes a .kicad_pcb{hint}")
    if not board.is_file():
        raise FileNotFoundError(f"no board at {board}")
    destination = _destination(board, fmt, out)
    if verbose:
        print(f"[pcb {fmt}] {board} -> {destination}", file=sys.stderr)
    if fmt in MANUFACTURING:
        require_finished(board, fmt=fmt)
    return _write(destination, export(fmt, board, **options), fmt)


def project_export(target: Path, out: Path | None, *, verbose: bool = False) -> FabExportResult:
    """``cadgen pcb export``: every manufacturing file of the finished project ``target``."""
    from cadgen.kicad.fab import FAB_FORMATS, FAB_SUFFIX, export, require_finished
    from cadgen.kicad.project_find import canonical_project

    project = canonical_project(_absolute(target))
    board = project.with_suffix(".kicad_pcb")
    if project.suffix not in {".kicad_pcb", ".kicad_pro"}:
        raise ValueError(f"{project.name} is not a KiCad project: cadgen pcb export takes a .kicad_pro, a .kicad_pcb or a folder")
    if not board.is_file():
        raise FileNotFoundError(f"no board at {board}")
    folder = _absolute(out) if out is not None else board.parent / "build"
    if verbose:
        print(f"[pcb export] {board} -> {folder}", file=sys.stderr)
    require_finished(board, fmt="manufacturing")
    stem = board.name[: -len(".kicad_pcb")]
    made = [(folder / (stem + FAB_SUFFIX[fmt]), export(fmt, board), fmt) for fmt in FAB_FORMATS]
    return FabExportResult(ok=True, files=tuple(file for path, data, fmt in made for file in _write(path, data, fmt).files))


def harness_bom(target: Path, out: Path | None, *, verbose: bool = False) -> FabExportResult:
    """``cadgen harness bom``: a harness document's BOM, through the exporter ``@harness(bom=)`` uses."""
    from cadgen.wireviz.bom import harness_bom as wireviz_bom

    document = _absolute(target)
    if format_of(document) != "harness":
        raise ValueError(f"{document.name} is not a wiring harness: cadgen harness bom takes a .harness.yml")
    if not document.is_file():
        raise FileNotFoundError(f"no harness at {document}")
    destination = _destination(document, "bom", out)
    if verbose:
        print(f"[harness bom] {document} -> {destination}", file=sys.stderr)
    return _write(destination, wireviz_bom(document.read_bytes(), label=document.name), "bom")
