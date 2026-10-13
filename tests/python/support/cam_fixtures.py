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


# --- Stand-in operations -------------------------------------------------------------------------------
# No operation is built until its phase-1 task lands. These stand in for four of them, registered the way a
# built one is (its family module defines its class), so the job parser's delegation can be tested now.

from cadgen._internal.cam.operations.base import OperationBase  # noqa: E402
from cadgen._internal.cam.values import number  # noqa: E402


class StubFacing(OperationBase):
    name, phase, word = "facing", 1, "Face"
    keys = frozenset({"stepdown_mm"})
    tool_types = ("flat_endmill", "face_mill")
    targets = ("all", "region")
    target_required = False

    def parse(self, raw, ctx):
        return {"stepdown_mm": number(raw.get("stepdown_mm", 1.0), where=f"{ctx.where}.stepdown_mm", positive=True)}


class StubPocket2d(StubFacing):
    name, word = "pocket_2d", "Pocket"
    tool_types = ("flat_endmill", "bull_endmill", "ball_endmill")
    targets = ("features", "region")
    target_required = True


class StubDrill(OperationBase):
    name, phase, word = "drill", 1, "Drill"
    keys = frozenset({"depth_mm"})
    tool_types = ("drill", "spot_drill")

    def parse(self, raw, ctx):
        return {"depth_mm": None if "depth_mm" not in raw else number(raw["depth_mm"], where=f"{ctx.where}.depth_mm", positive=True)}


class StubTap(StubDrill):
    name, word = "tap", "Tap"
    tool_types = ("tap",)


STUBS = {"facing": "StubFacing", "pocket_2d": "StubPocket2d", "drill": "StubDrill", "tap": "StubTap"}


def stub_operations(*names: str):
    """``REGISTRY`` with these operations (all four stand-ins by default) built as this module's classes."""
    from unittest import mock

    from cadgen._internal.cam.operations import REGISTRY, Entry

    entries = {name: Entry(name, REGISTRY[name].phase, __name__, STUBS[name], REGISTRY[name].word)
               for name in (names or STUBS)}
    return mock.patch.dict(REGISTRY, entries)
