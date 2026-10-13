"""A cone on a cylinder."""
from cadgen import build123d as bd
from cadgen import step


@step
def spindle():
    with bd.BuildPart() as part:
        bd.Cone(10, 4, 12)
        with bd.Locations((0, 0, -9)):
            bd.Cylinder(6, 6)
    return part.part


if __name__ == "__main__":
    spindle()
