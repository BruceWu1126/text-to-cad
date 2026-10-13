"""A box filleted on every edge."""
from cadgen import build123d as bd
from cadgen import step


@step
def pill():
    with bd.BuildPart() as part:
        bd.Box(20, 10, 8)
        bd.fillet(part.edges(), radius=2)
    return part.part


if __name__ == "__main__":
    pill()
