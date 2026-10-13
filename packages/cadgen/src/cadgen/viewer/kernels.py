"""A model's kernel variants: the same model built on other CAD kernels, saved beside it.

A model's own build writes ``STEP/bracket.step``. Building the same model on another kernel writes
an ordinary generated file next to it, one folder per kernel::

    STEP/bracket.step
    STEP/.kernels/usd-brep/bracket.step
    STEP/.kernels/sdm/bracket.stl

``kernel_variants`` answers, for any one of those files, the whole family: the model's own file and
every variant that exists, so a view can switch between them. It is one directory read per kernel
folder. It reads file names only: never a source script, never a kernel, never the store (a
variant is a saved file the person opens like any other, and a STEP variant compiles like any
foreign STEP). Paths are absolute in and out, with ``/`` separators on every platform.
"""

from __future__ import annotations

import os

from .backend import absolute_path

VARIANTS_FOLDER = ".kernels"
# The model's own file; listed first. Every other kernel is the name of its folder.
OWN_KERNEL = "brep"
FORMATS = (".step", ".stp", ".stl", ".3mf", ".glb")


def _slashed(path: str) -> str:
    return path.replace(os.sep, "/") if os.sep != "/" else path


def _family(file: str) -> tuple[str, str]:
    """(the folder holding the model's own file, the model's file stem) for any member."""
    folder, name = os.path.split(file)
    stem, _ = os.path.splitext(name)
    kernel_folder = os.path.dirname(folder)
    if os.path.basename(kernel_folder) == VARIANTS_FOLDER:
        return os.path.dirname(kernel_folder), stem
    return folder, stem


def _first_file(folder: str, stem: str) -> str | None:
    for ext in FORMATS:
        candidate = os.path.join(folder, stem + ext)
        if os.path.isfile(candidate):
            return candidate
    return None


def kernel_variants(file: object) -> dict:
    """``{"variants": [{"kernel", "file", "current"}, ...]}`` for ``file``'s model: its own file
    first (kernel ``brep``), then each kernel's, by kernel name. Empty when the model has no variant:
    a view shows no kernel menu for a model built on one kernel. ``ValueError`` (400) for a path that
    is not absolute."""
    file = absolute_path(file)
    home, stem = _family(file)
    variants = []
    own = _first_file(home, stem)
    if own:
        variants.append({"kernel": OWN_KERNEL, "file": own})
    try:
        kernels = sorted(entry.name for entry in os.scandir(os.path.join(home, VARIANTS_FOLDER)) if entry.is_dir())
    except (FileNotFoundError, NotADirectoryError, PermissionError):
        kernels = []
    for kernel in kernels:
        built = _first_file(os.path.join(home, VARIANTS_FOLDER, kernel), stem)
        if built:
            variants.append({"kernel": kernel, "file": built})
    if not any(v["kernel"] != OWN_KERNEL for v in variants):
        return {"variants": []}
    return {
        "variants": [
            {"kernel": v["kernel"], "file": _slashed(v["file"]), "current": os.path.normcase(v["file"]) == os.path.normcase(file)}
            for v in variants
        ]
    }
