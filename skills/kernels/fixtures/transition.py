"""A loft from a square to a circle."""
from cadgen import build123d as bd
from cadgen import step


@step
def transition():
    with bd.BuildPart() as part:
        with bd.BuildSketch():
            bd.Rectangle(20, 20)
        with bd.BuildSketch(bd.Plane.XY.offset(20)):
            bd.Circle(6)
        bd.loft()
    return part.part


if __name__ == "__main__":
    transition()
