"""A headless OCCT view: an OpenGL context on an off-screen Cocoa window (macOS), drawn into
off-screen frame buffers by ``V3d_View.ToPixMap`` at the exact pixel size asked."""

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

    def save(self, path: str) -> float:
        """Draw the view and write it as a PNG; the seconds the frame and the encoding took."""
        from OCP.TCollection import TCollection_AsciiString

        started = time.perf_counter()
        image = self.pixmap()
        image.Save(TCollection_AsciiString(str(path)))
        return time.perf_counter() - started
