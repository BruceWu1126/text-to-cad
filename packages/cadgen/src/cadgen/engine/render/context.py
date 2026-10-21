"""A headless OCCT view: an OpenGL context on an off-screen Cocoa window (macOS), drawn into
off-screen frame buffers by ``V3d_View.ToPixMap`` at the exact pixel size asked, and handed to
Python as bytes (PIL writes the PNG)."""

from __future__ import annotations

import sys
import time
from typing import Any

_APP = None


def _cocoa_application() -> None:
    """``NSApplication.sharedApplication`` once: a Cocoa window needs an application object."""
    global _APP
    if _APP is not None or sys.platform != "darwin":
        return
    import ctypes
    import ctypes.util

    objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
    ctypes.cdll.LoadLibrary("/System/Library/Frameworks/AppKit.framework/AppKit")
    objc.objc_getClass.restype = ctypes.c_void_p
    objc.sel_registerName.restype = ctypes.c_void_p
    objc.objc_msgSend.restype = ctypes.c_void_p
    objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    _APP = objc.objc_msgSend(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"))


class Canvas:
    """A viewer, a view and an interactive context on a hidden window of ``width`` x ``height``."""

    def __init__(self, width: int, height: int) -> None:
        started = time.perf_counter()
        _cocoa_application()
        from OCP.AIS import AIS_InteractiveContext
        from OCP.Aspect import Aspect_DisplayConnection
        from OCP.OpenGl import OpenGl_GraphicDriver
        from OCP.V3d import V3d_Viewer

        self.width, self.height = width, height
        self.driver = OpenGl_GraphicDriver(Aspect_DisplayConnection(), False)
        options = self.driver.ChangeOptions()
        options.buffersNoSwap = True
        options.contextCompatible = False  # a core profile: PBR and the path tracer need it
        self.driver.InitContext()
        self.viewer = V3d_Viewer(self.driver)
        if sys.platform == "darwin":
            from OCP.Cocoa import Cocoa_Window

            self.window = Cocoa_Window("occt", 0, 0, width, height)
        else:  # pragma: no cover - the prototype runs on macOS
            raise RuntimeError("the OCCT prototype's headless window is macOS-only")
        self.view = self.viewer.CreateView()
        self.view.SetWindow(self.window)
        self.context = AIS_InteractiveContext(self.viewer)
        self.seconds = time.perf_counter() - started

    def pixmap(self) -> Any:
        from OCP.Image import Image_AlienPixMap

        image = Image_AlienPixMap()
        if not self.view.ToPixMap(image, self.width, self.height):
            raise RuntimeError("OCCT drew nothing")
        return image

    @staticmethod
    def _pixels(image: Any) -> Any:
        """An OCCT picture as sRGB bytes (height, width, 3), handed over as an uncompressed TGA in
        memory (a millisecond), not through OCCT's PNG encoder (40 ms of a 1600 x 1200 frame)."""
        import io

        import numpy as np
        from OCP.TCollection import TCollection_AsciiString
        from PIL import Image

        stream = io.BytesIO()
        if not image.Save(stream, TCollection_AsciiString("tga")):
            raise RuntimeError("OCCT could not hand its frame over")
        return np.asarray(Image.open(io.BytesIO(stream.getvalue())).convert("RGB")).copy()

    def frame(self) -> Any:
        """The view drawn, as sRGB bytes (height, width, 3)."""
        return self._pixels(self.pixmap())

    def tile(self, left: int, top: int, width: int, height: int) -> Any:
        """The view's pixels in one rectangle of the frame (``left``, ``top`` from its top-left),
        drawn alone: a camera tile keeps the whole frame's projection, so the pixels are the
        frame's there (to a level at an edge's multisample) at the cost of the rectangle."""
        from OCP.Graphic3d import Graphic3d_BufferType, Graphic3d_CameraTile, Graphic3d_Vec2i
        from OCP.Image import Image_AlienPixMap

        camera = self.view.Camera()
        tile = Graphic3d_CameraTile()
        tile.TotalSize = Graphic3d_Vec2i(self.width, self.height)
        tile.TileSize = Graphic3d_Vec2i(width, height)
        tile.Offset = Graphic3d_Vec2i(left, self.height - top - height)  # counted from the lower left
        camera.SetTile(tile)
        try:
            image = Image_AlienPixMap()
            if not self.view.ToPixMap(image, width, height, Graphic3d_BufferType.Graphic3d_BT_RGB, False):
                raise RuntimeError("OCCT drew nothing")
        finally:
            camera.SetTile(Graphic3d_CameraTile())
        return self._pixels(image)

    def save(self, path: str, paint: Any = None) -> float:
        """Draw the view, let ``paint`` paint over the frame (sRGB bytes, in place), and write it
        as a PNG; the seconds the frame, the painting and the encoding took."""
        from PIL import Image

        started = time.perf_counter()
        frame = self.frame()
        if paint is not None:
            paint(frame)
        Image.fromarray(frame).save(str(path))
        return time.perf_counter() - started
