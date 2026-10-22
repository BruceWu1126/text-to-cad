from __future__ import annotations
from cadgen.engine import glb, materials, stl, threemf, xbf
# Prompt: Spur gear blank with central bore, raised hub, and simplified perimeter teeth.

from lib.simple_model_library import make_spur_gear_blank


@xbf(out="../XBF/spur_gear_blank.xbf")
@stl(out="../XBF/spur_gear_blank.stl")
@threemf(out="../XBF/spur_gear_blank.3mf")
@glb(out="../XBF/spur_gear_blank.glb")
def spur_gear_blank():
    part = make_spur_gear_blank()
    part.material = materials.brass_c360.but(color=None)
    return part


if __name__ == "__main__":
    spur_gear_blank()
