"""The continuous-waist middle proximal phalanx as STEP gives it back.

The r5 combs and outlets clip against the host's STEP reconstruction ("native STEP
reconstruction is required for reliable clipping"). The original read
`phalanx_continuous_r5`'s STEP output with build123d's import_step; here the same
phalanx is written to STEP and read back in memory, so no model reads another's file.
"""
import io

import build123d as bd
from OCP.STEPControl import STEPControl_Reader

from lib.phalanx_continuous_r5 import make_phalanx as make_continuous


def warm_host():
    buffer = io.BytesIO()
    bd.export_step(make_continuous(45, 18, label="middle_proximal_frame"), buffer)
    buffer.seek(0)
    reader = STEPControl_Reader()
    reader.ReadStream("phalanx_continuous_r5.step", buffer)
    reader.TransferRoots()
    return bd.Compound(reader.OneShape())


def make_phalanx(length, width, *args, **kwargs):
    assert (length, width) == (45, 18)
    return warm_host()
