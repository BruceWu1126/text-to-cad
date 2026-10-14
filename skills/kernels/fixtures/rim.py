"""A boss on a plate: its rim chamfered and its foot filleted, edges picked
by position (convex and concave, straight and round)."""
from cadgen import build123d as bd
from cadgen import step

MIN = (bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN)


@step
def rim():
    with bd.BuildPart() as part:
        bd.Box(30, 30, 4, align=MIN)
        with bd.Locations((0, 0, 4)):
            bd.Cylinder(8, 10, align=MIN)
        bd.chamfer(part.edges().group_by(bd.Axis.Z)[-1], length=1)
        bd.fillet(part.edges().group_by(bd.Axis.Z)[2], radius=1.5)
    return part.part


if __name__ == "__main__":
    rim()
