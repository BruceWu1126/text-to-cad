"""Case cluster entry: every case part positioned in the watch frame.

z = 0 at the case-middle / caseback joint plane, +Z through the crystal,
crown at +X (see `lib/spec.py`).
"""

from cadgen.engine import xbf

from lib import watch


@xbf(out="../XBF/case.xbf")
def case():
    return watch.case()


if __name__ == "__main__":
    case()
