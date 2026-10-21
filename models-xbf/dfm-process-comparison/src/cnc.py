from cadgen.engine import materials, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/cnc.xbf")
def cnc():
    part = tray("cnc")
    # 6061-T6 billet (README): the catalogue's generic aluminium
    part.material = materials.aluminium
    return part


if __name__ == "__main__":
    cnc()
