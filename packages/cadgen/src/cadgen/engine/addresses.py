"""Document addresses: file paths and script.py::name.

Names select documents; #owner/element selects geometry inside the document.
Scripts use their absolute path. Nothing here reads, builds, or persists a model.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class ModelAddress:
    script: Path
    name: str

    def __str__(self) -> str:
        return f"{self.script}::{self.name}"


def parse(value: str | os.PathLike) -> ModelAddress | None:
    text = os.fspath(value)
    if "::" not in text:
        return None
    source, name = text.rsplit("::", 1)
    if not _NAME.fullmatch(name):
        raise ValueError("a model address ends in ::NAME, with NAME a Python identifier")
    script = Path(source).expanduser()
    if script.suffix.lower() != ".py":
        raise ValueError("a model address is script.py::NAME")
    return ModelAddress(script.resolve(), name)


def key(value: str | os.PathLike) -> Path | str:
    """File keys stay Paths; model keys are canonical addresses, never fake files."""
    found = parse(value)
    return str(found) if found is not None else Path(value).expanduser().resolve()


def split(value: str | os.PathLike) -> tuple[str, str | None]:
    source, separator, ref = os.fspath(value).partition("#")
    return str(key(source)), ref if separator else None


def declarations(script: Path) -> list[str]:
    """List a script's model declarations without importing or executing it."""
    import ast

    tree = ast.parse(script.read_bytes(), filename=str(script))
    names, modules = set(), set()
    engines = {"cadgen", "cadgen.engine", "cadgen.engine.model"}
    kinds = {"model", "xbf", "step", "glb", "stl", "threemf", "dxf"}
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in engines and not node.level:
            for item in node.names:
                if item.name in kinds:
                    names.add(item.asname or item.name)
                if node.module == "cadgen" and item.name == "engine":
                    modules.add(item.asname or item.name)
        elif isinstance(node, ast.Import):
            for item in node.names:
                if item.name in engines:
                    modules.add(item.asname or item.name)
    found = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            text = ast.unparse(decorator.func if isinstance(decorator, ast.Call) else decorator)
            module, dot, name = text.rpartition(".")
            if not dot and name in names or module in modules and name in kinds:
                found.append(node.name)
                break
    return found
