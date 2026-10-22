from __future__ import annotations
from cadgen.engine import materials, xbf
# Prompt: Rectangular clamp block with a split slot and two transverse screw holes.

from lib.simple_model_library import make_rectangular_clamp_block


@xbf(out="../XBF/rectangular_clamp_block.xbf")
def rectangular_clamp_block():
    part = make_rectangular_clamp_block()
    part.material = materials.aluminium_6061.but(color=None)
    return part


if __name__ == "__main__":
    rectangular_clamp_block()
