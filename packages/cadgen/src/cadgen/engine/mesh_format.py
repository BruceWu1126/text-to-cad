"""The kernel-free mesh wire header shared by producers and the HTTP server."""
import struct

MAGIC = b"XBM2"
VERSION = 2
HEADER = struct.Struct("<4sI6I7I2f3I")
assert HEADER.size == 80
