"""Complete movement: base (plate/train/escapement/balance) + keyless and
motion works + chronograph works, all authored in the shared MOVEMENT local
frame (see `lib/spec.py`), so composition is identity — no re-posing here.
"""

import chrono_works
import keyless_works
import movement_base
import build123d as bd
from cadgen.engine import xbf

# Composition is by FUNCTION: importing a sibling model links it (import never
# builds), and calling it inside this body runs it in the same build as a
# sub-assembly. An edit that does not reach a child's code replays that child's
# body from the worker's memory.


@xbf(out="../XBF/movement.xbf")
def movement():
    children = []

    base = movement_base.movement_base()
    base.label = "movement_base"
    children.append(base)

    keyless = keyless_works.keyless_works()
    keyless.label = "keyless_works"
    children.append(keyless)

    chrono = chrono_works.chrono_works()
    chrono.label = "chronograph_works"
    children.append(chrono)

    return bd.Compound(children=children, label="movement")


if __name__ == "__main__":
    movement()
