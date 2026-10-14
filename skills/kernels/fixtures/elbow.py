"""A round bar swept along a quarter circle."""
from cadgen import build123d as bd
from cadgen import step


@step
def elbow():
    with bd.BuildPart() as part:
        with bd.BuildLine() as path:
            bd.CenterArc((0, 0), 20, 0, 90)
        with bd.BuildSketch(bd.Plane(origin=(20, 0, 0), x_dir=(1, 0, 0), z_dir=(0, 1, 0))):
            bd.Circle(3)
        bd.sweep(path=path.wires()[0])
    return part.part


if __name__ == "__main__":
    elbow()
