from cadgen.engine import materials, step, stl, xbf
from lib.tray_geometry import tray


@xbf(out="../XBF/sheet.xbf")
@step(out="../XBF/sheet.step")
@stl(out="../XBF/sheet.stl")
def sheet():
    part = tray("sheet")
    # 5052-H32 sheet (README): the catalogue's generic aluminium
    part.material = materials.aluminium
    return part


if __name__ == "__main__":
    sheet()
