from cadgen.engine import materials, step, stl, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/fdm.xbf")
@step(out="../XBF/fdm.step")
@stl(out="../XBF/fdm.stl")
def fdm():
    part = tray("fdm")
    # printed upright: PLA (README)
    part.material = materials.pla
    return part


if __name__ == "__main__":
    fdm()
