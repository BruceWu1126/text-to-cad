"""A DXF drawn by OCCT as the viewer's DXF pane draws it (``@text-to-cad/core`` ``lib/drawing2d``):
the whole drawing from above, fitted to the picture with the pane's margin, its lines one pixel
wide in their pens, its fills flat in theirs, its words in their pens, set in the platform's UI
face at their cap height and painted over the frame (sRGB over, as a canvas paints), the default pen
in the look's ink, on the look's paper (``look.DRAWING``: the light or the dark pair,
``display.appearance``). A drawing has no
scene: no lights, no modes, no camera to pose, no grid, clip, Explode or Preview, and those are
refused by name before anything is drawn.

A drawing is held as a document is (``service.Held``): ``open`` reads it (``formats/dxf.py``) and
makes its shapes -- one compound of polygon edges per pen, one triangulation per fill pen, crafted
as a part's are (``shapes.py``) -- and ``draw`` frames and draws it, warm on its canvas.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np

__all__ = ["Held", "refusals"]

REFUSED = {"mode": "display.mode", "grid": "the grid", "clip": "Clip", "exploded": "Explode",
           "camera": "display.camera (a lens)", "hidden": "display.hidden", "isolated": "display.isolated"}


def refusals(still: Any, name: str) -> None:
    """What a drawing does not offer, refused by name before anything renders."""
    from cadgen.engine.snapshot import SnapshotRefused

    asked = [key for key in REFUSED if still.display.get(key) not in (None, False)]
    for flag, given in (("--camera", still.camera is not None), ("--preview", still.preview),
                        ("--pose", still.pose is not None), ("--annotation", bool(still.annotation)),
                        ("--measurements", bool(still.measurements))):
        if given:
            asked.append(flag)
    if asked:
        named = ", ".join(REFUSED.get(key, key) for key in asked)
        raise SnapshotRefused(f"{name} is a drawing, drawn whole from above in its pens: {named} "
                              f"{'do' if len(asked) > 1 else 'does'} not apply to it. Its display takes appearance "
                              "(light or dark)")
    appearance = still.display.get("appearance", "light")
    if appearance not in ("light", "dark"):
        raise SnapshotRefused(f"display.appearance is light or dark, not {appearance!r}")


class Held:
    """One DXF as a renderer holds it: the drawing read, its shapes, a canvas made on first use."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.kind = "dxf"
        self.drawing: Any = None
        self.canvas: Any = None
        self.shown: list[tuple[Any, str | None]] = []  # each presentation and its pen (None: the look's ink)
        self.files: dict[str, Any] = {}
        self.warnings: list[str] = []

    def open(self) -> dict[str, Any]:
        from cadgen.engine.formats.dxf import read_dxf

        started = time.perf_counter()
        self.close()
        self.drawing = read_dxf(self.path)
        self.files, self.warnings = self.drawing.files, self.drawing.warnings
        return {"readMs": round((time.perf_counter() - started) * 1000), "kind": "dxf", "files": self.files,
                "warnings": self.warnings, "entities": self.drawing.entities}

    def close(self) -> None:
        if self.canvas is not None:
            self.canvas.close()
        self.canvas, self.shown = None, []

    def _show(self) -> None:
        """Each pen's lines and fills as one presentation: fills under the lines, every one unlit."""
        from OCP.AIS import AIS_Shape
        from OCP.Graphic3d import Graphic3d_TypeOfShadingModel

        from cadgen.engine.render.shapes import craft, read

        bounds = self.drawing.bounds
        depth = max(bounds[2] - bounds[0], bounds[3] - bounds[1], 1e-9) * 1e-4 if bounds else 1.0
        pens: dict[str | None, list[np.ndarray]] = {}
        for points, pen in self.drawing.lines:
            pens.setdefault(pen, []).append(points)
        fills: dict[str | None, list[np.ndarray]] = {}
        for triangles, pen in self.drawing.fills:
            fills.setdefault(pen, []).append(triangles)
        context = self.canvas.context
        for pen, chunks in fills.items():
            triangles = np.concatenate(chunks).reshape(-1, 2)
            nodes = np.column_stack([triangles, np.full(len(triangles), -depth)])
            shown = AIS_Shape(read(craft([(nodes, np.arange(len(nodes)).reshape(-1, 3), None)], {})))
            shown.Attributes().SetupOwnShadingAspect()
            shown.Attributes().ShadingAspect().Aspect().SetShadingModel(
                Graphic3d_TypeOfShadingModel.Graphic3d_TypeOfShadingModel_Unlit)
            context.Display(shown, 1, -1, False)
            self.shown.append((shown, pen))
        for pen, chunks in pens.items():
            counts = np.array([len(chunk) for chunk in chunks])
            points = np.column_stack([np.concatenate(chunks), np.zeros(int(counts.sum()))])
            shown = AIS_Shape(read(craft([], {"feature": (points, counts)})))
            context.Display(shown, 0, -1, False)
            self.shown.append((shown, pen))

    def draw(self, still: Any) -> tuple[bytes, dict[str, Any]]:
        """``still`` (a ``snapshot.Still``) drawn: the PNG's bytes and what was drawn."""
        from OCP.Graphic3d import Graphic3d_Camera
        from OCP.gp import gp_Dir, gp_Pnt

        from cadgen.engine.render.context import Canvas
        from cadgen.engine.render.look import DRAWING
        from cadgen.engine.render.occt import _color, occt_scale

        from cadgen.engine.render.service import finished, picture

        if self.drawing is None:
            raise RuntimeError("the renderer holds no drawing")
        refusals(still, self.path.name)
        started = time.perf_counter()
        timings: dict[str, int] = {}
        width, height = still.drawn  # supersampled (a job's renderScale), brought down after (``picture``)
        appearance = still.display.get("appearance", "light")
        paper, ink = DRAWING.paper(appearance)
        if self.canvas is None:
            clock = time.perf_counter()
            self.canvas = Canvas(width, height)
            params = self.canvas.view.ChangeRenderingParams()
            # Hairlines aliased, a pixel wide, as a 2D CAD viewer draws them: multisampled 1-pixel
            # lines broke into dots on Mesa's llvmpipe, and drawn larger and brought down
            # (RenderResolutionScale) they come out a quarter as dark, since a core-profile context
            # draws no line wider than a pixel.
            params.NbMsaaSamples = 0
            self._show()
            timings["sceneMs"] = round((time.perf_counter() - clock) * 1000)
        self.canvas.width, self.canvas.height = width, height
        for shown, pen in self.shown:
            shown.SetColor(_color(pen or ink))
            shown.SetWidth(DRAWING.line_width)
        self.canvas.view.SetBackgroundColor(_color(paper))
        bounds = self.drawing.bounds or (0.0, 0.0, 1.0, 1.0)
        span_x, span_y = abs(bounds[2] - bounds[0]), abs(bounds[3] - bounds[1])
        margin = DRAWING.margin * still.scale
        usable_x = max(width - 2 * margin, width * 0.5)
        usable_y = max(height - 2 * margin, height * 0.5)
        scale = min(usable_x / span_x if span_x > 0 else np.inf, usable_y / span_y if span_y > 0 else np.inf)
        scale = scale if np.isfinite(scale) and scale > 0 else 1.0  # pixels a unit, the pane's fit
        centre = ((bounds[0] + bounds[2]) / 2, (bounds[1] + bounds[3]) / 2)
        camera = self.canvas.view.Camera()
        camera.SetProjectionType(Graphic3d_Camera.Projection_e.Projection_Orthographic)
        camera.SetAspect(width / height)
        reach = max(span_x, span_y, 1.0)
        camera.SetEyeAndCenter(gp_Pnt(centre[0], centre[1], reach), gp_Pnt(centre[0], centre[1], 0.0))
        camera.SetUp(gp_Dir(0, 1, 0))
        camera.SetScale(occt_scale(height / scale, width / height))
        self.canvas.view.ZFitAll(1.0)
        clock = time.perf_counter()
        texts = self.drawing.texts

        def place(point: Any) -> tuple[float, float]:
            return ((point[0] - centre[0]) * scale + width / 2, height / 2 - (point[1] - centre[1]) * scale)

        view = self.canvas.view

        def backdrop(colour: Any) -> None:  # the paper, or (a transparent picture's matte) black and white
            view.SetBackgroundColor(_color(paper if colour is None else tuple(colour)))

        data = picture(self.canvas, (lambda frame: paint_words(frame, texts, place, scale, ink)) if texts else None,
                       still, backdrop)
        timings["drawMs"] = round((time.perf_counter() - clock) * 1000)
        timings["renderMs"] = round((time.perf_counter() - started) * 1000)
        return data, {"width": still.size[0], "height": still.size[1], **finished(still), "camera": "top",
                      "projection": "orthographic",
                      "displayMode": "drawing", "appearance": appearance, "preview": False,
                      "entities": self.drawing.entities, "layers": self.drawing.layers,
                      "lines": len(self.drawing.lines), "fills": len(self.drawing.fills),
                      "texts": len(self.drawing.texts), "pose": None,
                      "exploded": None, "unmeshed": {}, "renderer": "occt", "kind": "dxf",
                      "warnings": self.warnings, "timings": timings}


def paint_words(frame: np.ndarray, texts: list[Any], place: Any, scale: float, ink: str) -> None:
    """Each text's lines over ``frame`` (sRGB bytes, rows top first, in place): set in the face at
    their cap height in pixels, stretched by their width factor, turned to their direction about
    each line's baseline start (``place``: drawing to pixels), laid over in their pen's colour."""
    import math

    from PIL import Image, ImageDraw

    from cadgen.engine.formats.dxf import cap_per_em, font

    height, width = frame.shape[:2]
    for text in texts:
        size = text.height / cap_per_em() * scale
        if size < 1:
            continue
        face = font(size)
        ascent, descent = face.getmetrics()
        rgb = np.array([int((text.pen or ink)[at:at + 2], 16) for at in (1, 3, 5)], float)
        angle = math.degrees(math.atan2(text.direction[1], text.direction[0]))
        for line, start in zip(text.lines, text.starts):
            if not line.strip():
                continue
            pad = 2
            mask = Image.new("L", (int(math.ceil(face.getlength(line))) + 2 * pad, ascent + descent + 2 * pad), 0)
            ImageDraw.Draw(mask).text((pad, pad + ascent), line, fill=255, font=face, anchor="ls")
            anchor = np.array([pad, pad + ascent], float)
            if abs(text.width - 1) > 1e-6:
                mask = mask.resize((max(1, round(mask.width * text.width)), mask.height), Image.BICUBIC)
                anchor[0] *= text.width
            centre = np.array([mask.width / 2, mask.height / 2])
            turned = mask.rotate(angle, resample=Image.BICUBIC, expand=True)
            theta = math.radians(angle)
            offset = anchor - centre
            moved = np.array([offset[0] * math.cos(theta) + offset[1] * math.sin(theta),
                              -offset[0] * math.sin(theta) + offset[1] * math.cos(theta)])
            at = np.array(place(start)) - (np.array([turned.width / 2, turned.height / 2]) + moved)
            left, top = int(round(at[0])), int(round(at[1]))
            cover = np.asarray(turned, float) / 255.0
            x0, y0 = max(left, 0), max(top, 0)
            x1, y1 = min(left + turned.width, width), min(top + turned.height, height)
            if x1 <= x0 or y1 <= y0:
                continue
            alpha = cover[y0 - top:y1 - top, x0 - left:x1 - left, None]
            region = frame[y0:y1, x0:x1].astype(float)
            frame[y0:y1, x0:x1] = np.rint(region * (1 - alpha) + rgb * alpha).astype(np.uint8)
