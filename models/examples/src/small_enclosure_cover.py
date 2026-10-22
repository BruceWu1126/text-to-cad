from __future__ import annotations
from cadgen.engine import materials, xbf
# Prompt: Small enclosure cover with raised rim, corner screw holes, and shallow recessed center.

from lib.simple_model_library import make_small_enclosure_cover


@xbf(out="../XBF/small_enclosure_cover.xbf")
def small_enclosure_cover():
    part = make_small_enclosure_cover()
    part.material = materials.abs.but(color=None)
    return part


if __name__ == "__main__":
    small_enclosure_cover()
