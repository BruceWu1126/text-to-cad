from cadgen.engine import materials, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/sheet.xbf")
def sheet():
    part = tray("sheet")
    # 5052-H32 sheet (README): the catalogue's generic aluminium
    part.material = materials.aluminium
    return part


if __name__ == "__main__":
    sheet()
