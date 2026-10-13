"""Sketch with a hole, extruded, vertical edges filleted."""
from cadgen import build123d as bd
from cadgen import step


@step
def bracket():
    with bd.BuildPart() as part:
        with bd.BuildSketch():
            bd.Rectangle(24, 16)
            bd.Circle(3, mode=bd.Mode.SUBTRACT)
        bd.extrude(amount=6)
        bd.fillet(part.edges().filter_by(bd.Axis.Z), radius=1.5)
    return part.part


if __name__ == "__main__":
    bracket()
