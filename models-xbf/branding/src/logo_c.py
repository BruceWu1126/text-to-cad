from cadgen.engine import step, xbf

from lib.logo_shape import make_logo


@xbf(out="../XBF/logo_c.xbf")
@step(out="../XBF/logo_c.step")
def logo_c():
    return make_logo("C")
