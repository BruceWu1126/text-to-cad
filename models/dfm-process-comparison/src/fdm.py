from cadgen.engine import materials, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/fdm.xbf")
def fdm():
    part = tray("fdm")
    # printed upright: PLA (README)
    part.material = materials.pla
    return part


if __name__ == "__main__":
    fdm()
