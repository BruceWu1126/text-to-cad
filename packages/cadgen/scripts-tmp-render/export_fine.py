"""Scratch experiment: export a GLB with a render-grade tessellation.

The engine meshes at FreeCAD's default (0.5 % deviation, 28.5 deg angular) and has no knob.
This patches the two module constants in THIS process only (inline meshing, no daemon) to see
what a finer "render tessellation" costs and buys. Nothing in the engine source changes.

usage: RX_DEVIATION=0.05 RX_ANGULAR_DEG=5 python export_fine.py SRC.xbf OUT.glb
"""
import math
import os
import sys
import time

os.environ["CADGEN_ENGINE_MESH"] = "inline"

from cadgen.engine import mesh  # noqa: E402

mesh.DEVIATION = float(os.environ.get("RX_DEVIATION", "0.05"))
mesh.ANGULAR = math.radians(float(os.environ.get("RX_ANGULAR_DEG", "5")))

from cadgen.engine.cli import main  # noqa: E402

t = time.time()
code = main(["export", sys.argv[1], sys.argv[2], "--no-daemon"])
print(f"fine export: deviation {mesh.DEVIATION} angular {math.degrees(mesh.ANGULAR):.1f} deg "
      f"in {time.time() - t:.1f}s", file=sys.stderr)
sys.exit(code)
