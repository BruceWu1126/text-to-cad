"""Imported parts: STEP files read back as models, through `read_step`.

The port of `electronics_enclosure_base_drawing.py`, whose one kernel call read
the STEP that `electronics_enclosure_base.py` writes beside it and drew it. The
engine has no drawing decorator, so what is kept is the read: the enclosure's
STEP as a document of its own, and the mating fixture's STEP (an assembly) the
same way, which exercises the names and colours an imported tree keeps.
Both are fixed inputs committed in `../STEP/`, exported once from the two
source models; no model writes them.
"""

from cadgen.engine import read_step, xbf

from pathlib import Path

ENCLOSURE_STEP = Path(__file__).parent / "../STEP/electronics_enclosure_base.step"
FIXTURE_STEP = Path(__file__).parent / "../STEP/basic_shape_mating_test_fixture.step"


@xbf(out="../XBF/electronics_enclosure_base_import.xbf")
def electronics_enclosure_base_import():
    return read_step(ENCLOSURE_STEP)


@xbf(out="../XBF/basic_shape_mating_test_fixture_import.xbf")
def basic_shape_mating_test_fixture_import():
    return read_step(FIXTURE_STEP)


if __name__ == "__main__":
    electronics_enclosure_base_import()
