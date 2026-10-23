"""cadgen builds from pyproject.toml; this adds one file to the wheel's root.

``cadgen-lazy-kernel.pth`` makes ``import build123d`` lazy in the environment cadgen is installed
into (``cadgen/_internal/lazy_kernel.py``): the interpreter runs a ``.pth`` file's import line at
start only from a site-packages directory, so the file must sit at the wheel's root, which an
installer unpacks into site-packages. setuptools' ``data-files`` cannot put it there -- a wheel's
data scheme installs under the environment's prefix -- so ``build_py`` copies it beside the
packages, as setuptools ships its own ``distutils-precedence.pth``. ``MANIFEST.in`` carries it
into the sdist, which the wheel is built from.
"""

from __future__ import annotations

import os

from setuptools import setup
from setuptools.command.build_py import build_py

PTH = "cadgen-lazy-kernel.pth"


class build_py_with_pth(build_py):  # noqa: N801 - setuptools' command naming
    def run(self) -> None:
        super().run()
        self.copy_file(os.path.join(os.path.dirname(os.path.abspath(__file__)), PTH),
                       os.path.join(self.build_lib, PTH), preserve_mode=False)

    def get_outputs(self, include_bytecode: bool = True) -> list[str]:
        return [*super().get_outputs(include_bytecode), os.path.join(self.build_lib, PTH)]


setup(cmdclass={"build_py": build_py_with_pth})
