"""Front wheel entry: tire + five-spoke cast rim + brake disc, bike frame."""

from __future__ import annotations

import build123d as bd
from cadgen.engine import step, xbf

from lib import wheels as W


@xbf(out="../XBF/front_wheel.xbf")
@step(out="../XBF/front_wheel.step")
def front_wheel():
    return bd.Compound(children=W.build_front_wheel(), label="front_wheel")


if __name__ == "__main__":
    front_wheel()
