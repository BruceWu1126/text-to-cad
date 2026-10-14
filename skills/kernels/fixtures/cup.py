"""A box shelled to 1.5 mm walls, open at the top."""
from cadgen import build123d as bd
from cadgen import step


@step
def cup():
    with bd.BuildPart() as part:
        bd.Box(30, 20, 15)
        bd.offset(amount=-1.5, openings=part.faces().sort_by(bd.Axis.Z)[-1])
    return part.part


if __name__ == "__main__":
    cup()
