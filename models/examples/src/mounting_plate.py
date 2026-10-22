from __future__ import annotations
from cadgen.engine import glb, materials, stl, threemf, xbf
# Prompt: Mounting plate with central circular cutout, elongated side slot, four corner holes, and rounded edges.

from lib.simple_model_library import make_mounting_plate


@xbf(out="../XBF/mounting_plate.xbf")
@stl(out="../XBF/mounting_plate.stl")
@threemf(out="../XBF/mounting_plate.3mf")
@glb(out="../XBF/mounting_plate.glb")
def mounting_plate():
    part = make_mounting_plate()
    part.material = materials.aluminium_6061.but(color=None)
    return part


if __name__ == "__main__":
    mounting_plate()
