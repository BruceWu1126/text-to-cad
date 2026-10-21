from cadgen.engine import materials, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/baseline.xbf")
def baseline():
    part = tray("baseline")
    # the thin-wall FDM starting point: PLA (README)
    part.material = materials.pla
    return part


if __name__ == "__main__":
    baseline()
