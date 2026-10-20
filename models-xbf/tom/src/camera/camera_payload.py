"""SO-101 BOM camera: four front M2 x 6 socket caps, with nuts behind the PCB."""
from cadgen.engine import xbf, step
from purchased.innomaker_u20cam import innomaker_u20cam
from hardware.hex_socket_cap import hex_socket_m2x6
from hardware.hex_nut_m2 import hex_nut_m2
from lib.camera_payload_geometry import build_payload

@xbf(out='../../XBF/camera_assembly/camera_payload.xbf')
@step(out='../../XBF/camera_assembly/camera_payload.step')
def camera_payload():
    return build_payload(innomaker_u20cam(),hex_socket_m2x6(),hex_nut_m2())

if __name__=='__main__':camera_payload()
