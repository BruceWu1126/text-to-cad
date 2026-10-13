"""Locations, an aligned grid of pins and a polar arc of holes."""
from cadgen import build123d as bd
from cadgen import step


@step
def patterns():
    with bd.BuildPart() as part:
        # build123d ignores the part's Locations for a sketch: the plate stays centered.
        with bd.Locations((10, 0, 0)):
            with bd.BuildSketch():
                bd.Rectangle(30, 20)
            bd.extrude(amount=4)
        with bd.Locations((0, 0, 4)):
            with bd.GridLocations(20, 6, 2, 2, align=bd.Align.MIN):
                bd.Cylinder(1.5, 3, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        with bd.PolarLocations(8, 3, start_angle=15, angular_range=90):
            bd.Hole(1)
    return part.part


if __name__ == "__main__":
    patterns()
