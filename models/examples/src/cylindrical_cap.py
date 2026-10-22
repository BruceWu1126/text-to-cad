from __future__ import annotations
from cadgen.engine import materials, xbf
# Prompt: Cylindrical cap with hollow interior, top boss, and rounded external edges.

from lib.simple_model_library import make_cylindrical_cap


@xbf(out="../XBF/cylindrical_cap.xbf")
def cylindrical_cap():
    part = make_cylindrical_cap()
    part.material = materials.aluminium_6061.but(color=None)
    return part


if __name__ == "__main__":
    cylindrical_cap()
