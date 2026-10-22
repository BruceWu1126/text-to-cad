from cadgen.engine import step, xbf

from lib.logo_shape import make_logo


@xbf(out="../XBF/logo_cad.xbf")
@step(out="../XBF/logo_cad.step")
def logo_cad():
    return make_logo("CAD")


if __name__ == "__main__":
    logo_cad()
