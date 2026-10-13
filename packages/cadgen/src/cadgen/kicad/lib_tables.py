"""A generated project's ``sym-lib-table`` and ``fp-lib-table``: its project libraries, for KiCad.

A board's parts are copied into its schematic and board, so the project opens without
them; the tables are what lets a person who opens it in KiCad update a part from its
library or place another from it, as Backplane and KiStack keep a project self-contained.
Each row is the library's path from the project folder, through KiCad's ``${KIPRJMOD}``,
so the tables still resolve wherever the repository is checked out.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

__all__ = ["MARK", "lib_tables", "write_lib_tables"]

#: What every row cadgen writes says, so a build knows a table is its own.
MARK = '(descr "project library")'

# (table file, its root token, the library forms it lists)
_TABLES = (
    ("sym-lib-table", "sym_lib_table", (".kicad_sym", ".kicad_symdir")),
    ("fp-lib-table", "fp_lib_table", (".pretty",)),
)


def _libraries(folders: Sequence[Path], forms: tuple[str, ...]) -> dict[str, Path]:
    """Each library name in ``folders`` and where it is; the first folder to have a name wins,
    as the board's lookup searches them."""
    found: dict[str, Path] = {}
    for folder in folders:
        folder = Path(folder)
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir()):
            for form in forms:
                if entry.name.endswith(form) and entry.name != form:
                    found.setdefault(entry.name[: -len(form)], entry)
    return found


def lib_tables(folders: Sequence[Path], project_dir: Path) -> dict[str, str]:
    """The lib tables of a project in ``project_dir`` whose libraries are in ``folders``:
    ``{file name: text}``, only the tables there is a library for."""
    tables: dict[str, str] = {}
    base = Path(project_dir).resolve()
    for name, token, forms in _TABLES:
        libraries = _libraries([Path(folder).resolve() for folder in folders], forms)
        if not libraries:
            continue
        rows = [
            f'  (lib (name "{library}")(type "KiCad")(uri "${{KIPRJMOD}}/{Path(os.path.relpath(path, base)).as_posix()}")'
            f'(options ""){MARK})'
            for library, path in sorted(libraries.items())
        ]
        tables[name] = f"({token}\n  (version 7)\n" + "\n".join(rows) + "\n)\n"
    return tables


def write_lib_tables(project_dir: Path, stem: str, tables: dict[str, str]) -> tuple[list[Path], str | None]:
    """Write ``tables`` into the project folder, and remove a table a past build wrote that is not
    among them: ``(files written, a note for the build's log or None)``.

    The tables belong to the folder, not to one project: a folder holding another
    ``.kicad_pro`` is left as it is, and the note says why, so one board's build never writes
    or removes another's.
    """
    from cadgen._internal.atomic_replace import write_bytes_atomic

    folder = Path(project_dir)
    others = sorted(path.name for path in folder.glob("*.kicad_pro") if path.stem != stem)
    stale = [folder / name for name, _, _ in _TABLES if name not in tables and (folder / name).is_file()
             and MARK in (folder / name).read_text(encoding="utf-8", errors="replace")]
    if others:
        if not tables and not stale:
            return [], None
        return [], (f"{folder.name}/ also holds {', '.join(others)}, so its lib tables are left as they are: "
                    "give each board its own folder (@pcb(out=\"../PCB/<board>/<board>.kicad_pcb\"))")
    written = []
    for name, _, _ in _TABLES:
        if name in tables:
            write_bytes_atomic(folder / name, tables[name].encode("utf-8"))
            written.append(folder / name)
    for path in stale:
        path.unlink()
    return written, None
