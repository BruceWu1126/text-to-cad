"""Kernel plugins: one folder per kernel under the skill's ``kernels/``.

Each folder holds a ``kernel.json`` and the runner its ``command`` names.
This module reads and checks them; ``references/plugins.md`` documents the
fields. Folders starting with ``_`` (shared code, templates) are not kernels.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

KERNELS_DIR = Path(__file__).resolve().parents[2] / "kernels"
INPUTS = ("model", "program")
OUTPUTS = ("step", "stl")
FIELDS = {"id", "label", "detail", "exact", "input", "output", "command", "setup", "tolerance", "timeout"}
PLACEHOLDERS = {"model", "function", "program", "step", "stl", "here", "runtime", "python", "node"}
DEFAULT_TIMEOUT = 300  # seconds: a run longer than this is a hang, not a slow kernel
DEFAULT_TOLERANCE = 0.01
_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TOKEN = re.compile(r"\{([^{}]+)\}")


def runtime_home(env=None) -> Path:
    """Where kernels keep what their setup installs (a venv, node_modules), one folder each:
    outside the skill, which may be installed read-only and ships no runtime."""
    env = os.environ if env is None else env
    return Path(env.get("CAD_KERNELS_HOME") or Path.home() / ".cache" / "text-to-cad" / "kernels")


class ManifestError(ValueError):
    pass


@dataclass(frozen=True)
class Kernel:
    id: str
    label: str
    detail: str
    exact: bool
    input: str
    output: str
    command: tuple
    setup: str
    tolerance: float
    timeout: float
    folder: Path


def _fail(folder: str, message: str):
    raise ManifestError(f"kernels/{folder}/kernel.json: {message}")


def parse(raw, folder: str, path: Path | None = None) -> Kernel:
    """Check one manifest. Unknown fields are errors, never ignored."""
    if not isinstance(raw, dict):
        _fail(folder, "not a JSON object")
    unknown = sorted(set(raw) - FIELDS)
    if unknown:
        _fail(folder, f"unknown field {', '.join(unknown)} (fields: {', '.join(sorted(FIELDS))})")
    kernel_id = raw.get("id")
    if not isinstance(kernel_id, str) or not _ID.match(kernel_id):
        _fail(folder, "id must be lowercase letters, digits and dashes")
    if kernel_id != folder:
        _fail(folder, f'id "{kernel_id}" must match its folder name')
    label = raw.get("label")
    if not isinstance(label, str) or not label.strip():
        _fail(folder, "label is required")
    if raw.get("input") not in INPUTS:
        _fail(folder, f"input must be one of {', '.join(INPUTS)}")
    if raw.get("output") not in OUTPUTS:
        _fail(folder, f"output must be one of {', '.join(OUTPUTS)}")
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) for part in command):
        _fail(folder, "command must be a non-empty array of strings")
    for part in command:
        for token in _TOKEN.findall(part):
            if not token.startswith("env:") and token not in PLACEHOLDERS:
                _fail(folder, f"unknown placeholder {{{token}}} in command")
    if "{" + raw["output"] + "}" not in "".join(command):
        _fail(folder, f'command must pass {{{raw["output"]}}}, where the runner writes its {raw["output"]}')
    if raw["input"] == "program" and "{program}" not in "".join(command):
        _fail(folder, "a program kernel's command must pass {program}")
    if raw["input"] == "model" and "{model}" not in "".join(command):
        _fail(folder, "a model kernel's command must pass {model}")
    tolerance = raw.get("tolerance", DEFAULT_TOLERANCE)
    if not isinstance(tolerance, (int, float)) or tolerance <= 0:
        _fail(folder, "tolerance must be a positive number")
    timeout = raw.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or timeout <= 0:
        _fail(folder, "timeout must be a positive number of seconds")
    return Kernel(
        id=kernel_id,
        label=label.strip(),
        detail=str(raw.get("detail") or "").strip(),
        exact=bool(raw.get("exact", False)),
        input=raw["input"],
        output=raw["output"],
        command=tuple(command),
        setup=str(raw.get("setup") or "").strip(),
        tolerance=float(tolerance),
        timeout=float(timeout),
        folder=path or KERNELS_DIR / folder,
    )


def load_all(kernels_dir: Path = KERNELS_DIR) -> list[Kernel]:
    kernels = []
    for folder in sorted(p for p in kernels_dir.iterdir() if p.is_dir()):
        manifest = folder / "kernel.json"
        if folder.name.startswith("_") or not manifest.is_file():
            continue
        try:
            raw = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ManifestError(f"kernels/{folder.name}/kernel.json: {exc}") from None
        kernels.append(parse(raw, folder.name, folder))
    return kernels


def by_id(kernel_id: str, kernels_dir: Path = KERNELS_DIR) -> Kernel:
    kernels = load_all(kernels_dir)
    for kernel in kernels:
        if kernel.id == kernel_id:
            return kernel
    raise ManifestError(f"no kernel {kernel_id!r}; kernels: {', '.join(k.id for k in kernels)}")


def expand(command, values: dict, env: dict) -> list[str]:
    """Fill a command template. ``{env:NAME|fallback}`` takes an environment
    variable, else the fallback, in which ``<here>`` is the plugin folder and
    ``<runtime>`` its runtime folder."""
    def fill(match):
        key = match.group(1)
        if key.startswith("env:"):
            name, _, fallback = key[4:].partition("|")
            return env.get(name) or fallback.replace("<here>", values["here"]).replace("<runtime>", values["runtime"])
        return values[key]

    return [_TOKEN.sub(fill, part) for part in command]
