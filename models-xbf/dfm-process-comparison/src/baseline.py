from cadgen.engine import materials, step, stl, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/baseline.xbf")
@step(out="../XBF/baseline.step")
@stl(out="../XBF/baseline.stl")
def baseline():
    part = tray("baseline")
    # the thin-wall FDM starting point: PLA (README)
    part.material = materials.pla
    return part


if __name__ == "__main__":
    baseline()
