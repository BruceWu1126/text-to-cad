"""Run a model program on a kernel plugin and write what it builds.

The model runs the way ``python model.py`` runs it, on another kernel: either
the plugin runs the program itself (``"input": "model"``, e.g. keel's
build123d layer), or the program is recorded here as a CSG tree and the
plugin builds that (``"input": "program"``). The result is an ordinary
generated file beside the model's own output:

    <model's output folder>/.kernels/<kernel id>/<model's output stem>.<step|stl>

so the CAD Viewer can open it like any other file. A failed run leaves no
file there.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .manifest import KERNELS_DIR, Kernel, expand, runtime_home

SHARED_DIR = KERNELS_DIR / "_shared"
if str(SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(SHARED_DIR))

import cadgen_stub  # noqa: E402

from . import record  # noqa: E402

VARIANTS_DIR = ".kernels"


class KernelError(RuntimeError):
    pass


def describe(target: str) -> cadgen_stub.Model:
    """The model a target names, with its declared outputs, without building it."""
    script, function = cadgen_stub.split_target(target)
    saved = {name: sys.modules.get(name) for name in ("build123d", "cadgen", "cadgen.build123d")}
    cadgen_stub.install(record._recording_module())
    try:
        return cadgen_stub.load(script, function)
    except cadgen_stub.Unsupported as exc:
        raise KernelError(str(exc)) from None
    finally:
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module
            else:
                sys.modules.pop(name, None)


def output_path(model: cadgen_stub.Model, kernel: Kernel) -> Path:
    primary = model.outs.get("step") or next(iter(model.outs.values()))
    return primary.parent / VARIANTS_DIR / kernel.id / f"{primary.stem}.{kernel.output}"


def _arg(path: Path, cwd: Path) -> str:
    """Paths relative to the working directory when they're under it, so a
    runner in Docker (which shares only the working directory) sees them."""
    try:
        return path.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _last_json(stdout: str):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                return None
    return None


def run(kernel: Kernel, target: str, *, cwd: Path | None = None, env: dict | None = None) -> dict:
    """Build ``target`` (``model.py`` or ``model.py::function``) on ``kernel``.

    Returns the runner's report plus ``output`` (the file it wrote). Raises
    KernelError on any failure, after removing a partial output."""
    cwd = Path(cwd or os.getcwd())
    env = dict(os.environ if env is None else env)
    model = describe(target)
    out = output_path(model, kernel)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    program_path = out.with_suffix(".program.json")
    try:
        if kernel.input == "program":
            program = record.trace_model(f"{model.script}::{model.function}")
            if not program.get("ok"):
                raise KernelError(f"{kernel.label} needs the model recorded as a program: {program.get('error')}")
            program_path.write_text(json.dumps(program), encoding="utf-8")
        values = {
            "model": _arg(model.script, cwd),
            "function": model.function,
            "program": _arg(program_path, cwd),
            "step": _arg(out, cwd),
            "stl": _arg(out, cwd),
            "here": _arg(kernel.folder, cwd),
            "runtime": str(runtime_home(env) / kernel.id),
            "python": sys.executable,
            "node": shutil.which("node") or "node",
        }
        command = expand(kernel.command, values, env)
        executable = command[0]
        if "/" in executable or os.sep in executable:
            # Absolute, but not resolved: a venv's python is a symlink, and
            # following it would leave the venv behind.
            executable = os.path.normpath(os.path.join(cwd, executable))
        try:
            proc = subprocess.run([executable, *command[1:]], cwd=cwd, env=env,
                                  capture_output=True, text=True, check=False, timeout=kernel.timeout)
        except subprocess.TimeoutExpired:
            raise KernelError(f"{kernel.label} gave no result within {kernel.timeout:g} s and was stopped "
                              f'("timeout" in its kernel.json)') from None
        except OSError as exc:
            hint = f" {kernel.setup}" if kernel.setup else ""
            raise KernelError(f"{kernel.label}: can't run {command[0]} ({exc.strerror}).{hint}") from None
        report = _last_json(proc.stdout)
        if report is None:
            tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-3:])
            raise KernelError(f"{kernel.label} printed no result{': ' + tail if tail else ''}")
        if not report.get("ok"):
            raise KernelError(f"{kernel.label}: {report.get('error') or 'failed'}")
        if not out.is_file():
            raise KernelError(f"{kernel.label} reported success but wrote no {_arg(out, cwd)}")
    except BaseException:
        out.unlink(missing_ok=True)
        raise
    finally:
        program_path.unlink(missing_ok=True)
    report.pop("trace", None)
    report.pop("ok", None)
    return {"ok": True, "kernel": kernel.id, "model": f"{_arg(model.script, cwd)}::{model.function}",
            "output": _arg(out, cwd), **report}
