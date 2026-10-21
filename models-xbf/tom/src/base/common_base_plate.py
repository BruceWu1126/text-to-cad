"""Manufacture two identical copies for the Tom base and servo carrier."""
from cadgen.engine import xbf
from lib.common_base_plate import common_base_plate as make_plate


@xbf(out='../../XBF/base_link/common_base_plate.xbf')
def common_base_plate():
    return make_plate()


if __name__ == '__main__':
    common_base_plate()
