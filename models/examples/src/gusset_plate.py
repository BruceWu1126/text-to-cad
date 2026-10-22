from __future__ import annotations
from cadgen.engine import materials, xbf
# Prompt: Gusset plate with a triangular web, base holes, and softened perimeter edges.

from lib.simple_model_library import make_gusset_plate


@xbf(out="../XBF/gusset_plate.xbf")
def gusset_plate():
    part = make_gusset_plate()
    part.material = materials.steel_a36.but(color=None)
    return part


if __name__ == "__main__":
    gusset_plate()
