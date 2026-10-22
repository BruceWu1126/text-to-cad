from cadgen.engine import step, xbf

from lib.logo_shape import make_logo


@xbf(out="../XBF/logo_texttocad.xbf")
@step(out="../XBF/logo_texttocad.step")
def logo_texttocad():
    # "TO" (letters 5 and 6) is the lighter blue.
    return make_logo("TEXTTOCAD", lighter=(4, 5))


if __name__ == "__main__":
    logo_texttocad()
