from cadgen.engine import materials, step, stl, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/injection.xbf")
@step(out="../XBF/injection.step")
@stl(out="../XBF/injection.stl")
def injection():
    part = tray("injection")
    # moulded: ABS, the common injection-moulding resin
    part.material = materials.abs_plastic
    return part


if __name__ == "__main__":
    injection()
