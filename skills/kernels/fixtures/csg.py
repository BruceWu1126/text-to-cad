"""Algebra mode: box intersect sphere, minus two crossing cylinders."""
from cadgen import build123d as bd
from cadgen import step


@step
def csg():
    body = bd.Box(20, 20, 20) & bd.Sphere(13)
    return body - bd.Cylinder(5, 30) - bd.Rot(90, 0, 0) * bd.Cylinder(5, 30)


if __name__ == "__main__":
    csg()
