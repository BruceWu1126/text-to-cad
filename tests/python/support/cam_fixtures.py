"""Shared fixtures for the CAM suites (``tests/python/packages/cadgen/test_cam*.py``).

Importing this puts cadgen's source on ``sys.path``. Heavy imports (build123d,
shapely, numpy, OCP) stay inside the helpers, so the job and registry tests run
on the standard library alone.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from tests.python.support.paths import REPO_ROOT, add_repo_path

add_repo_path("packages/cadgen/src")

#: What CAM must not import before it plans: the kernel, the geometry and the numerics.
HEAVY = ("OCP", "build123d", "shapely", "numpy", "scipy")


def heavy_at_import(*modules: str) -> str:
    """The heavy modules importing ``modules`` pulls into a fresh interpreter: "" when they are stdlib-only at import."""
    code = (f"import sys; sys.path.insert(0, 'packages/cadgen/src'); import {', '.join(modules)};"
            f"print(','.join(m for m in {HEAVY!r} if m in sys.modules))")
    return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def value_error(case: unittest.TestCase, call, *args, **kwargs) -> str:
    """The message of the ValueError ``call(*args, **kwargs)`` must raise."""
    with case.assertRaises(ValueError) as caught:
        call(*args, **kwargs)
    return str(caught.exception)
