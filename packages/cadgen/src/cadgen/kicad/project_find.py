"""Which KiCad project a folder means, found the way Backplane finds a repository's board.

The project's own ``.kicad_pro`` in the folder wins; else the shallowest one at most three
folders below it. KiCad's ``*-backups`` folders, ``build/`` (where a project's derived files go),
hidden folders and ``node_modules`` are never searched. Two projects equally shallow are
refused by name rather than one picked: the caller names the one meant.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["canonical_project", "find_projects"]

_DEPTH = 4


def _skipped(name: str) -> bool:
    return name.startswith(".") or name in {"node_modules", "build"} or name.endswith("-backups")


def find_projects(root: Path, depth: int = _DEPTH) -> list[Path]:
    """Every ``.kicad_pro`` under ``root``, at most ``depth`` levels down, shallowest first."""
    root = Path(root)
    found: list[tuple[int, str, Path]] = []
    for folder, folders, files in os.walk(root):
        level = len(Path(folder).relative_to(root).parts)
        folders[:] = [] if level + 1 >= depth else sorted(name for name in folders if not _skipped(name))
        for name in files:
            if name.endswith(".kicad_pro"):
                path = Path(folder) / name
                found.append((level, path.relative_to(root).as_posix(), path))
    return [path for _, _, path in sorted(found)]


def canonical_project(path: Path) -> Path:
    """The project ``path`` means: a file is itself; a folder is its one shallowest ``.kicad_pro``."""
    path = Path(path)
    if not path.is_dir():
        return path
    projects = find_projects(path)
    if not projects:
        raise FileNotFoundError(f"no KiCad project (.kicad_pro) in {path} or {_DEPTH - 1} folders below it")
    shallowest = len(projects[0].relative_to(path).parts)
    tied = [project for project in projects if len(project.relative_to(path).parts) == shallowest]
    if len(tied) > 1:
        listed = ", ".join(project.relative_to(path).as_posix() for project in tied)
        raise ValueError(f"{path} holds {len(tied)} KiCad projects at the same depth ({listed}): name the one you mean")
    return tied[0]
