"""Shared fixtures for the CAM suites (``tests/python/packages/cadgen/test_cam*.py``).

Importing this puts cadgen's source on ``sys.path``. Heavy imports (build123d,
shapely, numpy, OCP) stay inside the helpers, so the job and registry tests run
on the standard library alone.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from contextlib import contextmanager

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


class StubPost:
    """Stands in for a post until task 8 builds them."""


def stub_posts(*names: str):
    """The posts' ``REGISTRY`` with these posts (grbl and haas by default) built as :class:`StubPost`."""
    from dataclasses import replace
    from unittest import mock

    from cadgen._internal.cam.posts import REGISTRY

    entries = {name: replace(REGISTRY[name], module=__name__, cls="StubPost") for name in (names or ("grbl", "haas"))}
    return mock.patch.dict(REGISTRY, entries)


@contextmanager
def stubbed():
    """The four stand-in operations and the grbl and haas posts, built, for a job that parses."""
    with stub_operations(), stub_posts():
        yield


# --- Part fixtures -------------------------------------------------------------------------------------
# build123d parts written as STEP into a directory the test owns (never models/). Heavy imports stay inside.

#: The plate every phase-1 suite machines: 100 x 60 x 10 mm standing on z = 0, centred in x and y. From the top
#: (+Z): a 40 x 20 x 5 mm pocket with R3 inside corners, centred; two 6 mm through holes; one 5 mm blind hole.
PLATE = {
    "size_mm": (100.0, 60.0, 10.0),
    "pocket": {"center": (0.0, 0.0), "length_mm": 40.0, "width_mm": 20.0, "depth_mm": 5.0, "corner_radius_mm": 3.0},
    "through_holes": ((-35.0, 15.0), (35.0, 15.0)),
    "through_diameter_mm": 6.0,
    "blind_hole": (35.0, -15.0),
    "blind_diameter_mm": 5.0,
    "blind_depth_mm": 6.0,
}


def plate_step(directory, name: str = "plate"):
    """:data:`PLATE` written to ``directory/name.step``."""
    from pathlib import Path

    from build123d import Align, Axis, Box, Cylinder, Pos, export_step, fillet

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    length, width, height = PLATE["size_mm"]
    plate = Box(length, width, height, align=base)
    pocket = PLATE["pocket"]
    # Every cutter overshoots the top by 1 mm, so no boolean meets a coplanar face.
    cutter = Pos(*pocket["center"], height - pocket["depth_mm"]) * Box(
        pocket["length_mm"], pocket["width_mm"], pocket["depth_mm"] + 1, align=base)
    plate -= fillet(cutter.edges().filter_by(Axis.Z), pocket["corner_radius_mm"])
    for x, y in PLATE["through_holes"]:
        plate -= Pos(x, y, -1) * Cylinder(PLATE["through_diameter_mm"] / 2, height + 2, align=base)
    depth = PLATE["blind_depth_mm"]
    plate -= Pos(*PLATE["blind_hole"], height - depth) * Cylinder(PLATE["blind_diameter_mm"] / 2, depth + 1, align=base)
    step = Path(directory) / f"{name}.step"
    export_step(plate, str(step))
    return step


def slotted_block_step(directory, name: str = "block"):
    """An 80 x 40 x 20 mm block standing on z = 0, centred in x and y: an 8 mm wide, 5 mm deep slot along x
    across the top; a step 10 mm down over its last 10 mm in +x, which the slot runs into; and a 5 mm through
    hole at (-25, 12) under a 9 mm counterbore 4 mm deep."""
    from pathlib import Path

    from build123d import Align, Box, Cylinder, Pos, export_step

    base = (Align.CENTER, Align.CENTER, Align.MIN)
    block = Box(80, 40, 20, align=base)
    block -= Pos(0, 0, 15) * Box(100, 8, 10, align=base)
    block -= Pos(30, 0, 10) * Box(30, 50, 20, align=(Align.MIN, Align.CENTER, Align.MIN))
    block -= Pos(-25, 12, -1) * Cylinder(2.5, 30, align=base)
    block -= Pos(-25, 12, 16) * Cylinder(4.5, 10, align=base)
    step = Path(directory) / f"{name}.step"
    export_step(block, str(step))
    return step
