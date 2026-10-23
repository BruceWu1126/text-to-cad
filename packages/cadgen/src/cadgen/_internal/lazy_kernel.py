"""``import build123d`` binds the module; the kernel loads on its first attribute.

A model script's module body should cost nothing until the engine has decided whether to build
-- in the process that only hands the build to the warm worker, it never loads the kernel at
all -- yet the idiom every build123d author writes is ``import build123d as bd`` at the top.
This ``sys.meta_path`` finder makes that idiom free: for the top-level names ``build123d`` and
``OCP`` alone it returns the ordinary spec (``PathFinder``'s) with its loader wrapped in
``importlib.util.LazyLoader``, so the import binds a module whose body runs on the first
attribute anything reads from it (``bd.Box``), in whichever process that is.

``from build123d import Box`` reads an attribute to bind ``Box``, so it loads the kernel there and
then, as without the finder; ``import OCP.Standard`` loads ``OCP`` to find its submodule, and the
submodule as ever. Only the top-level packages are lazy: a submodule is imported when asked.

Installed twice, harmlessly (``install`` is idempotent): by ``cadgen-lazy-kernel.pth`` in the
site-packages of an environment cadgen is installed into, before any import, and by cadgen's
package ``__init__`` for an editable install, where a wheel's ``.pth`` is not placed. Importing
this module imports no kernel.
"""

from __future__ import annotations

import importlib.util
import sys
from importlib.machinery import PathFinder
from typing import Any

KERNEL = ("build123d", "OCP")


class LazyKernelFinder:
    """Finds ``build123d`` and ``OCP`` as ``PathFinder`` does, their loaders made lazy."""

    def find_spec(self, fullname: str, path: Any = None, target: Any = None) -> Any:
        if fullname not in KERNEL:
            return None
        spec = PathFinder.find_spec(fullname, path, target)
        if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
            return None  # nothing to defer: the next finder imports it as ever
        spec.loader = importlib.util.LazyLoader(spec.loader)
        return spec


def install() -> None:
    """Put the finder first on ``sys.meta_path``, once."""
    if not any(isinstance(finder, LazyKernelFinder) for finder in sys.meta_path):
        sys.meta_path.insert(0, LazyKernelFinder())


def loaded(name: str) -> bool:
    """Whether ``name`` is imported and its body has run: a lazy module bound by ``import`` and
    never read from is not loaded."""
    module = sys.modules.get(name)
    # By its exact type: an isinstance check that misses reads ``__class__``, which loads it.
    return module is not None and type(module) is not importlib.util._LazyModule  # noqa: SLF001


install()
