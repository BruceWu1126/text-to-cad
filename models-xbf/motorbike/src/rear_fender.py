"""Rear fender entry: cream arc over the rear tire, bike frame."""

from __future__ import annotations

import build123d as bd
from cadgen.engine import xbf

from lib import bodywork as B


@xbf(out="../XBF/rear_fender.xbf")
def rear_fender():
    built = B.build_rear_fender()
    if isinstance(built, list):
        return bd.Compound(children=built, label="rear_fender")
    return built


if __name__ == "__main__":
    rear_fender()
