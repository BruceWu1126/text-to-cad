"""PROTOTYPE: an engine document drawn by OCCT's own OpenGL renderer, from the engine's meshes.

``python -m cadgen.engine.render.occt DOC.xbf OUT.png [--display MODE|JSON] [--camera C] [--grid]
[--preview] [--pose NAME|JSON] [--explode AMOUNT] [--annotation FILE] [--measurements FILE]
[--path-trace N] [--mesh-cache DIR] [--width W --height H] [--json]``

The flags are ``cadgen xbf snapshot``'s where it has them, and mean what the viewer means:

- The document is read (``document.Loaded``: structure, names, colours, the mechanism) and every
  part's mesh is the engine's XBM buffer -- from the daemon, or a folder of them -- turned into
  triangulation-only faces and polygon edges (``shapes.py``). OCCT meshes nothing.
- One presentation per part and look (``AIS_Shape``), placed per instance by
  ``AIS_ConnectedInteractive``: an instance shares its part's arrays, as the viewer's instances do.
- Modes as the viewer's Display menu (``viewSettings.js``): solid (faces and every CAD edge),
  shaded (faces), parts (solid, each instance a colour of the workbench palette), xray (faces at
  0.22 opacity, every edge over them), hidden-line (faces in the backdrop's colour hiding the edges
  behind them), wireframe (every edge).
- Camera as the snapshot page's (``renderMeshScene.js``): a preset's or ``az:el``'s direction,
  orthographic with the tight frame (every placed vertex projected, padding 0.04), or perspective
  (``display.camera.projection``, 48 degrees, the same tight fit).
- ``--pose``: the engine's forward kinematics (``mechanism.posed_structure``); ``--explode``: the
  viewer's own explode layout (``explode.py``); ``display.clip``: an OCCT clip plane, capped in the
  parts' colours; ``--annotation`` and ``--measurements``: the snapshot job's ``annotation`` and
  ``measurements`` data, drawn as polylines and labels.
- ``--preview``: physically based raster (PBR, an image-based environment, a key light with
  shadows, a floor); ``--path-trace N``: OCCT's path tracer, N samples.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

from cadgen.engine.mesh import Mesh
from cadgen.engine.render.context import Canvas
from cadgen.engine.render.look import LOOK
from cadgen.engine.render.shapes import LINES, part_shapes

MODES = ("solid", "shaded", "parts", "xray", "hidden-line", "wireframe")
PRESETS = {
    "front": ((0, -1, 0), (0, 0, 1)), "back": ((0, 1, 0), (0, 0, 1)), "right": ((1, 0, 0), (0, 0, 1)),
    "left": ((-1, 0, 0), (0, 0, 1)), "top": ((0, 0, 1), (0, 1, 0)), "bottom": ((0, 0, -1), (0, 1, 0)),
    "iso": ((1, -1, 0.8), (0, 0, 1)),
}
# The regular modes' numbers are the look's (``look.py``), which the viewer draws by too.
BACKGROUND = LOOK.backdrop
EDGE = dict(LOOK.edge_colours)
DEFAULT_FILL = LOOK.fill
PARTS = LOOK.palette
REFERENCE_HEIGHT = 900  # an annotation's ink is as heavy against the picture as on a viewer this tall
PADDING = 0.04
# OCCT sizes a label's font in points of the window's backing store; on a Retina screen's hidden
# window that draws a label at about half its pixel height in an off-screen frame of the asked size.
TEXT_SCALE = float(os.environ.get("OCCT_TEXT_SCALE", 2.0))
FOCAL_LENGTH = 50.0  # the view's default camera.focalLength (viewSettings.js), on three's 35 mm film gauge


def vertical_fov(aspect: float, focal_length: float = FOCAL_LENGTH) -> float:
    """three's ``PerspectiveCamera.setFocalLength``: the film's height is the gauge over the aspect."""
    return math.degrees(2 * math.atan(0.5 * (35.0 / max(aspect, 1.0)) / focal_length))
SOURCE = Path(__file__).resolve().parents[6]  # the repository (for the viewer's explode layout)


def _color(value: Any) -> Any:
    """A ``#rrggbb`` (sRGB, as CSS) or an rgb(a) tuple (linear, as the document and three hold it)."""
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB, Quantity_TOC_sRGB

    if isinstance(value, str):
        value = value.lstrip("#")
        value = [int(value[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        return Quantity_Color(float(value[0]), float(value[1]), float(value[2]), Quantity_TOC_sRGB)
    return Quantity_Color(float(value[0]), float(value[1]), float(value[2]), Quantity_TOC_RGB)


def _matrix(values: list[float]) -> np.ndarray:
    return np.array(values, float).reshape(4, 4).T  # column-major, as WebGL reads it


def _trsf(matrix: np.ndarray) -> Any:
    from OCP.gp import gp_Trsf

    trsf = gp_Trsf()
    m = matrix
    trsf.SetValues(m[0, 0], m[0, 1], m[0, 2], m[0, 3], m[1, 0], m[1, 1], m[1, 2], m[1, 3],
                   m[2, 0], m[2, 1], m[2, 2], m[2, 3])
    return trsf


def direction_of(camera: str) -> tuple[np.ndarray, np.ndarray]:
    """A preset's or ``azimuth:elevation``'s direction (target to eye) and up, as ``camera.js``."""
    if camera in PRESETS:
        direction, up = PRESETS[camera]
    else:
        azimuth, elevation = (math.radians(float(part)) for part in camera.split(":"))
        direction = (math.sin(azimuth) * math.cos(elevation), -math.cos(azimuth) * math.cos(elevation),
                     math.sin(elevation))
        up = (0, 0, 1)
    d = np.array(direction, float)
    return d / np.linalg.norm(d), np.array(up, float)


class Scene:
    """A document on a canvas: its parts as shapes, one presentation per part and look, an
    instance a connected object of it."""

    def __init__(self, path: Path, buffers: dict[str, bytes], *, size: tuple[int, int] = (1600, 1200),
                 structure: dict[str, Any] | None = None, pose: Any = None) -> None:
        self.timings: dict[str, float] = {}
        clock = time.perf_counter()
        self.path = Path(path)
        if structure is None:
            from cadgen.engine.document import Loaded

            structure = Loaded(self.path).structure()
        self.rest_structure = structure
        if pose is not None:
            from cadgen.engine.mechanism import posed_structure

            structure = posed_structure(structure, pose)
        self.structure = structure
        self.timings["read"] = time.perf_counter() - clock
        clock = time.perf_counter()
        self.canvas = Canvas(*size)
        self.timings["context"] = time.perf_counter() - clock
        clock = time.perf_counter()
        self.prototypes = structure["prototypes"]
        self.meshes = {p["key"]: Mesh.from_bytes(buffers[p["key"]]) for p in self.prototypes}
        self.parts = []
        for prototype in self.prototypes:
            groups = {int(k): tuple(v) for k, v in (prototype.get("faceColors") or {}).items()}
            self.parts.append(part_shapes(self.meshes[prototype["key"]], groups, None))
        self.timings["shapes"] = time.perf_counter() - clock
        self.matrices = [_matrix(instance["matrix"]) for instance in structure["instances"]]
        self.rest = [_matrix(instance["matrix"]) for instance in self.rest_structure["instances"]]
        self.boxes = {}
        for index, prototype in enumerate(self.prototypes):
            positions = self.meshes[prototype["key"]].positions
            if len(positions):
                self.boxes[index] = (positions.min(0).astype(float), positions.max(0).astype(float))
        self.offsets: dict[int, np.ndarray] = {}
        self.sources: dict[tuple, Any] = {}
        self.shown: list[Any] = []
        self.overlays: list[Any] = []
        self.mode = None
        self.display: dict[str, Any] = {}
        self.preview = False
        self._setup_view()

    # --- the view ------------------------------------------------------------------------

    def _setup_view(self) -> None:
        from OCP.Graphic3d import Graphic3d_TypeOfShadingModel

        from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
        from OCP.V3d import V3d_AmbientLight, V3d_DirectionalLight
        from OCP.gp import gp_Dir

        view = self.canvas.view
        viewer = self.canvas.viewer
        white = Quantity_Color(1, 1, 1, Quantity_TOC_RGB)
        self.lights = [V3d_AmbientLight(white), *(V3d_DirectionalLight(gp_Dir(*light.direction), white) for light in LOOK.lights)]
        for light, intensity in zip(self.lights, (LOOK.ambient, *(light.radiance for light in LOOK.lights))):
            light.SetIntensity(intensity)
            viewer.AddLight(light)
            viewer.SetLightOn(light)
        view.SetBackgroundColor(_color(BACKGROUND))
        view.SetShadingModel(Graphic3d_TypeOfShadingModel.Graphic3d_TypeOfShadingModel_Phong)
        params = view.ChangeRenderingParams()
        params.NbMsaaSamples = 8
        drawer = self.canvas.context.DefaultDrawer()
        drawer.SetAutoTriangulation(False)  # never mesh: every face carries the engine's triangles
        drawer.SetFaceBoundaryDraw(False)

    # --- sources: one presentation per part and look ----------------------------------------

    def _material(self, rgba: Any, look: str) -> Any:
        from OCP.Graphic3d import Graphic3d_MaterialAspect, Graphic3d_NameOfMaterial, Graphic3d_PBRMaterial
        from OCP.Quantity import Quantity_ColorRGBA

        material = Graphic3d_MaterialAspect(Graphic3d_NameOfMaterial.Graphic3d_NameOfMaterial_Plastified)
        color = _color(rgba[:3] if rgba is not None and not isinstance(rgba, str) else (rgba or DEFAULT_FILL))
        material.SetColor(color)
        material.SetAmbientColor(color)
        material.SetDiffuseColor(color)
        material.SetSpecularColor(_color((LOOK.specular,) * 3))
        material.SetShininess(LOOK.shininess)
        pbr = Graphic3d_PBRMaterial()
        pbr.SetColor(Quantity_ColorRGBA(color, 1.0))
        pbr.SetMetallic(0.0)
        pbr.SetRoughness(0.5)
        material.SetPBRMaterial(pbr)
        return material, color

    def _faces(self, index: int, group: Any, rgba: Any, look: str) -> Any:
        key = ("faces", index, group, rgba, look)
        found = self.sources.get(key)
        if found is not None:
            return found
        from OCP.AIS import AIS_Shape
        from OCP.Aspect import Aspect_PolygonOffsetMode
        from OCP.Graphic3d import Graphic3d_TypeOfShadingModel

        source = AIS_Shape(self.parts[index].faces[group])
        material, color = self._material(rgba, look)
        source.SetMaterial(material)
        source.SetColor(color)
        aspect = source.Attributes().ShadingAspect().Aspect()
        aspect.SetPolygonOffsets(Aspect_PolygonOffsetMode.Aspect_POM_Fill, 1.0, 1.0)
        if look == "hidden":
            source.SetColor(_color(BACKGROUND))
            aspect.SetShadingModel(Graphic3d_TypeOfShadingModel.Graphic3d_TypeOfShadingModel_Unlit)
        elif look == "xray":
            source.SetTransparency(1 - LOOK.mode("xray").opacity)
        self.sources[key] = source
        return source

    def _edges(self, index: int, line: str) -> Any:
        key = ("edges", index, line)
        found = self.sources.get(key)
        if found is None:
            from OCP.AIS import AIS_Shape

            found = AIS_Shape(self.parts[index].edges[line])
            found.SetColor(_color(EDGE[line]))
            found.SetWidth(LOOK.edge_width)
            self.sources[key] = found
        return found

    # --- what is shown ----------------------------------------------------------------------

    def placement(self, index: int) -> np.ndarray:
        matrix = self.matrices[index]
        offset = self.offsets.get(index)
        if offset is not None:
            matrix = matrix.copy()
            matrix[:3, 3] += offset
        return matrix

    def show(self, display: dict[str, Any] | None = None, *, preview: bool = False) -> float:
        """Show every instance in ``display``'s mode (or Preview's look); the seconds it took to
        build what is shown (presentations computed on first use, then shared)."""
        from OCP.AIS import AIS_ConnectedInteractive
        from OCP.Graphic3d import Graphic3d_ZLayerId_Topmost

        clock = time.perf_counter()
        display = dict(display or {})
        mode = "solid" if preview else display.get("mode", "solid")
        context = self.canvas.context
        for shown in self.shown:
            context.Remove(shown, False)
        self.shown = []
        self.mode, self.display, self.preview = mode, display, preview
        look = "hidden" if mode == "hidden-line" else "xray" if mode == "xray" else "pbr" if preview else "lit"
        faces = mode != "wireframe"
        edges = not preview and mode != "shaded"
        on_top = mode in ("xray", "wireframe")
        for index, instance in enumerate(self.structure["instances"]):
            p = instance["prototype"]
            part = self.parts[p]
            trsf = _trsf(self.placement(index))
            rgba = instance.get("color") or self.prototypes[p].get("color")
            rgba = tuple(rgba) if rgba else None
            if mode == "parts":
                rgba = PARTS[index % len(PARTS)]
            if faces:
                for group in part.faces:
                    color = rgba if group is None or mode == "parts" else group
                    connected = AIS_ConnectedInteractive()
                    connected.Connect(self._faces(p, group, color, look), trsf)
                    context.Display(connected, 1, -1, False)
                    self.shown.append(connected)
            if edges:
                for line in LINES:
                    if line not in part.edges:
                        continue
                    connected = AIS_ConnectedInteractive()
                    connected.Connect(self._edges(p, line), trsf)
                    context.Display(connected, 0, -1, False)
                    if on_top:
                        context.SetZLayer(connected, Graphic3d_ZLayerId_Topmost)
                    self.shown.append(connected)
        self._clip(display.get("clip"))
        seconds = time.perf_counter() - clock
        self.timings["present"] = seconds
        return seconds

    # --- clip --------------------------------------------------------------------------------

    def rest_bounds(self) -> tuple[np.ndarray, np.ndarray]:
        from cadgen.engine.render.explode import placed_box

        boxes = [placed_box(self.boxes[i["prototype"]], m) for i, m in zip(self.rest_structure["instances"], self.rest)
                 if i["prototype"] in self.boxes]
        return np.min([b[0] for b in boxes], 0), np.max([b[1] for b in boxes], 0)

    def _clip(self, clip: dict[str, Any] | None) -> None:
        """``display.clip``: one plane along an axis, at ``at`` (else the middle of the model at
        rest), keeping the lower side (``invert`` the upper), its cut capped in each part's colour."""
        from OCP.gp import gp_Dir, gp_Pln, gp_Pnt
        from OCP.Graphic3d import Graphic3d_ClipPlane, Graphic3d_SequenceOfHClipPlane

        view = self.canvas.view
        planes = Graphic3d_SequenceOfHClipPlane()
        view.SetClipPlanes(planes)
        if not clip or clip.get("enabled") is False:
            return
        axis = "xyz".index(clip.get("axis", "x"))
        low, high = self.rest_bounds()
        at = clip.get("at", (low[axis] + high[axis]) / 2)
        normal = [0.0, 0.0, 0.0]
        # OCCT keeps the half-space the normal points into.
        normal[axis] = 1.0 if clip.get("invert") else -1.0
        point = [0.0, 0.0, 0.0]
        point[axis] = at
        plane = Graphic3d_ClipPlane(gp_Pln(gp_Pnt(*point), gp_Dir(*normal)))
        plane.SetCapping(True)
        plane.SetUseObjectMaterial(clip.get("partColors", True))
        plane.SetUseObjectShader(False)
        plane.SetUseObjectTexture(False)
        view.AddClipPlane(plane)

    # --- camera ------------------------------------------------------------------------------

    def placed_points(self) -> np.ndarray:
        chunks = []
        for index, instance in enumerate(self.structure["instances"]):
            positions = self.meshes[self.prototypes[instance["prototype"]]["key"]].positions
            if len(positions):
                m = self.placement(index)
                chunks.append(positions.astype(float) @ m[:3, :3].T + m[:3, 3])
        return np.concatenate(chunks)

    def frame(self, camera: str = "iso", projection: str = "orthographic", focal_length: float = FOCAL_LENGTH) -> dict[str, Any]:
        """The snapshot page's framing: the direction a preset or ``az:el`` names, then the tight
        frame on every placed vertex (orthographic), or the perspective fit on the same points."""
        from OCP.gp import gp_Dir, gp_Pnt
        from OCP.Graphic3d import Graphic3d_Camera

        d, up = direction_of(camera)
        forward = -d
        right = np.cross(forward, up)
        right /= np.linalg.norm(right)
        screen_up = np.cross(right, forward)
        if self.mode == "wireframe" and projection != "perspective":
            # The page's tight frame fits the meshes it draws; Wireframe draws none, so the page
            # falls back to the scene's box (``fitOrthographicCamera``): its eight corners.
            from cadgen.engine.render.explode import placed_box

            boxes = [placed_box(self.boxes[i["prototype"]], self.placement(n))
                     for n, i in enumerate(self.structure["instances"]) if i["prototype"] in self.boxes]
            lo, hi = np.min([b[0] for b in boxes], 0), np.max([b[1] for b in boxes], 0)
            points = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        else:
            points = self.placed_points()
        width, height = self.canvas.width, self.canvas.height
        aspect = width / height
        safe = max(1 - 2 * PADDING, 0.1)
        low, high = points.min(0), points.max(0)
        centre = (low + high) / 2
        radius = float(np.linalg.norm(high - low)) / 2 or 1.0
        cam = self.canvas.view.Camera()
        if projection == "perspective":
            cam.SetProjectionType(Graphic3d_Camera.Projection_e.Projection_Perspective)
            fov = vertical_fov(aspect, focal_length)
            cam.SetFOVy(fov)
            vertical = math.tan(math.radians(fov / 2)) * safe
            horizontal = vertical * aspect
            relative = points - centre
            depth = relative @ d
            near = max(radius / 1200, 0.01) * 2
            distance = float(max(np.max(depth + np.abs(relative @ right) / horizontal),
                                 np.max(depth + np.abs(relative @ screen_up) / vertical), np.max(depth + near)))
            target, eye = centre, centre + d * distance
            scale = None
        else:
            cam.SetProjectionType(Graphic3d_Camera.Projection_e.Projection_Orthographic)
            xs, ys = points @ right, points @ screen_up
            half = max((ys.max() - ys.min()) / (2 * safe), (xs.max() - xs.min()) / (2 * aspect * safe), 1e-6)
            mid = right * (xs.max() + xs.min()) / 2 + screen_up * (ys.max() + ys.min()) / 2
            target = mid + d * float(centre @ d)
            eye = target + d * radius * 4
            scale = 2 * half
        cam.SetEyeAndCenter(gp_Pnt(*eye), gp_Pnt(*target))
        cam.SetUp(gp_Dir(*screen_up))
        if scale is not None:
            cam.SetScale(scale)
        self.canvas.view.ZFitAll(1.0)
        self.camera = {"fov": vertical_fov(aspect, focal_length), "direction": d, "up": screen_up, "right": right, "eye": eye, "target": target,
                       "projection": projection, "scale": scale}
        return self.camera

    def project(self, points: np.ndarray) -> np.ndarray:
        """World points to output pixels (x right, y down), through OCCT's own camera."""
        from OCP.gp import gp_Pnt

        cam = self.canvas.view.Camera()
        out = []
        for point in points:
            projected = cam.Project(gp_Pnt(*map(float, point)))  # normalized device coordinates
            out.append(((projected.X() + 1) / 2 * self.canvas.width, (1 - projected.Y()) / 2 * self.canvas.height))
        return np.array(out)

    # --- grid, explode, overlays -----------------------------------------------------------------

    def grid(self, on: bool = True) -> None:
        from OCP.Aspect import Aspect_GridDrawMode, Aspect_GridType
        from OCP.gp import gp_Ax3, gp_Dir, gp_Pnt

        viewer = self.canvas.viewer
        if not on:
            viewer.DeactivateGrid()
            return
        low, high = self.rest_bounds()
        size = float(max(high[:2] - low[:2]))
        step = 10 ** math.floor(math.log10(max(size / LOOK.grid.cells, 1e-6)))
        viewer.SetPrivilegedPlane(gp_Ax3(gp_Pnt(0, 0, float(low[2])), gp_Dir(0, 0, 1), gp_Dir(1, 0, 0)))
        viewer.SetRectangularGridValues(0, 0, step, step, 0)
        viewer.SetRectangularGridGraphicValues(size * LOOK.grid.extent, size * LOOK.grid.extent, 0)
        viewer.SetGridEcho(False)
        viewer.ActivateGrid(Aspect_GridType.Aspect_GT_Rectangular, Aspect_GridDrawMode.Aspect_GDM_Lines)
        grid = viewer.Grid()
        grid.SetColors(_color(LOOK.grid.colour), _color(LOOK.grid.colour))

    def explode(self, amount: float) -> float:
        from cadgen.engine.render.explode import offsets

        clock = time.perf_counter()
        self.offsets = offsets(self.structure, self.boxes, self.matrices, amount, SOURCE) if amount > 0 else {}
        return time.perf_counter() - clock

    def clear_overlays(self) -> None:
        for shown in self.overlays:
            self.canvas.context.Remove(shown, False)
        self.overlays = []

    def _pixel_size(self, point: np.ndarray) -> float:
        """World millimetres per output pixel at ``point`` for the camera framed."""
        camera = self.camera
        if camera["scale"] is not None:
            return camera["scale"] / self.canvas.height
        depth = float(np.dot(np.asarray(point) - camera["eye"], -camera["direction"]))
        return 2 * depth * math.tan(math.radians(camera["fov"] / 2)) / self.canvas.height

    def _ribbon(self, points: np.ndarray, color: str, width: float, closed: bool = False) -> None:
        """A polyline ``width`` pixels wide, as the viewer's screen-space lines are: a strip of
        triangles facing the camera (a core-profile GL context draws GL lines one pixel wide)."""
        from OCP.AIS import AIS_Shape
        from OCP.Graphic3d import Graphic3d_TypeOfShadingModel, Graphic3d_ZLayerId_Top

        from cadgen.engine.render.shapes import craft, read

        points = np.asarray(points, float)
        if closed:
            points = np.vstack([points, points[:1]])
        view = self.camera["direction"]
        tangents = np.gradient(points, axis=0)
        sides = np.cross(tangents, view)
        sides /= np.maximum(np.linalg.norm(sides, axis=1, keepdims=True), 1e-12)
        half = np.array([self._pixel_size(p) * width / 2 for p in points])[:, None]
        nodes = np.empty((2 * len(points), 3))
        nodes[0::2], nodes[1::2] = points + sides * half, points - sides * half
        count = len(points) - 1
        a = np.arange(count) * 2
        triangles = np.concatenate([np.stack([a, a + 1, a + 2], 1), np.stack([a + 1, a + 3, a + 2], 1)])
        compound = read(craft([(nodes, triangles, None)], {}))
        shown = AIS_Shape(compound)
        shown.SetColor(_color(color))
        shown.Attributes().ShadingAspect().Aspect().SetShadingModel(Graphic3d_TypeOfShadingModel.Graphic3d_TypeOfShadingModel_Unlit)
        self.canvas.context.Display(shown, 1, -1, False)
        self.canvas.context.SetZLayer(shown, Graphic3d_ZLayerId_Top)
        self.overlays.append(shown)

    def _polyline(self, points: np.ndarray, color: str, width: float, closed: bool = False) -> None:
        if width > 1.5 and getattr(self, "camera", None) is not None:
            self._ribbon(points, color, width, closed)
            return
        from OCP.AIS import AIS_Shape
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakePolygon
        from OCP.Graphic3d import Graphic3d_ZLayerId_Top
        from OCP.gp import gp_Pnt

        polygon = BRepBuilderAPI_MakePolygon()
        for point in points:
            polygon.Add(gp_Pnt(*map(float, point)))
        if closed:
            polygon.Close()
        shown = AIS_Shape(polygon.Wire())
        shown.SetColor(_color(color))
        shown.SetWidth(width)
        self.canvas.context.Display(shown, 0, -1, False)
        self.canvas.context.SetZLayer(shown, Graphic3d_ZLayerId_Top)
        self.overlays.append(shown)

    def _label(self, at: np.ndarray, text: str, color: str, pixels: float) -> None:
        from OCP.AIS import AIS_TextLabel
        from OCP.Graphic3d import Graphic3d_HTA_CENTER, Graphic3d_VTA_CENTER, Graphic3d_ZLayerId_Topmost
        from OCP.TCollection import TCollection_ExtendedString
        from OCP.gp import gp_Pnt

        label = AIS_TextLabel()
        label.SetText(TCollection_ExtendedString(text, True))
        label.SetPosition(gp_Pnt(*map(float, at)))
        label.SetColor(_color(color))
        label.SetHeight(pixels * TEXT_SCALE)
        label.SetHJustification(Graphic3d_HTA_CENTER)
        label.SetVJustification(Graphic3d_VTA_CENTER)
        self.canvas.context.Display(label, False)
        self.canvas.context.SetZLayer(label, Graphic3d_ZLayerId_Topmost)
        self.overlays.append(label)

    def annotate(self, annotation: dict[str, Any]) -> list[np.ndarray]:
        """The viewer's annotation data drawn: each mark's points in its part's frame (``part``:
        ``i<instance>``, placed where the scene now has the instance; else its recorded placement),
        a stroke a polyline of its width, a text mark its words at its quad's middle. Returns
        each mark's world points, for checking where they land."""
        scale = self.canvas.height / REFERENCE_HEIGHT
        placed_marks = []
        for mark in annotation.get("marks", []):
            points = np.array(mark["points"], float).reshape(-1, 3)
            part = mark.get("part")
            if part and part.startswith("i") and part[1:].isdigit() and int(part[1:]) < len(self.matrices):
                matrix = self.placement(int(part[1:]))
            elif part and part in annotation.get("parts", {}):
                matrix = _matrix(annotation["parts"][part]["placement"])
            else:
                matrix = np.eye(4)
            world = points @ matrix[:3, :3].T + matrix[:3, 3]
            placed_marks.append(world)
            if mark["kind"] == "stroke":
                self._polyline(world, mark["color"], mark.get("width", 2) * scale, bool(mark.get("closed")))
            elif mark["kind"] == "area":
                self._polyline(world[:mark.get("outline", len(world))], mark["color"], 1.5 * scale, True)
            else:
                self._label(world.mean(0), mark.get("text", ""), mark["color"], mark.get("fontPx", 14) * scale)
        return placed_marks

    def measure(self, marks: list[dict[str, Any]]) -> None:
        """The snapshot job's ``measurements``: a dimension line between the two ends, ticks at
        them and the label at its middle, in the mark's colour."""
        scale = self.canvas.height / REFERENCE_HEIGHT
        for mark in marks:
            start, end = np.array(mark["start"], float), np.array(mark["end"], float)
            self._polyline(np.array([start, end]), mark["color"], 2.6 * scale)
            self._label((start + end) / 2, mark["label"], mark["color"], 13 * scale)

    # --- preview ------------------------------------------------------------------------------

    def preview_look(self, path_trace: int = 0) -> float:
        """Preview's photographic look: PBR raster under an image-based environment, a key light
        casting shadows and a floor; or OCCT's path tracer."""
        from OCP.AIS import AIS_Shape
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from OCP.Graphic3d import (Graphic3d_BSDF, Graphic3d_MaterialAspect, Graphic3d_NameOfMaterial,
                                   Graphic3d_PBRMaterial, Graphic3d_RenderingMode, Graphic3d_ToneMappingMethod,
                                   Graphic3d_TypeOfShadingModel)
        from OCP.Quantity import Quantity_Color, Quantity_ColorRGBA, Quantity_TOC_RGB
        from OCP.V3d import V3d_AmbientLight, V3d_DirectionalLight
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        clock = time.perf_counter()
        viewer, view = self.canvas.viewer, self.canvas.view
        for light in self.lights:
            viewer.DelLight(light)
        self.lights = []
        key = V3d_DirectionalLight(gp_Dir(0.5, 0.7, -1.0), Quantity_Color(1, 0.97, 0.92, Quantity_TOC_RGB))
        key.SetIntensity(2.5)
        key.SetCastShadows(True)
        key.SetHeadlight(False)
        ambient = V3d_AmbientLight(Quantity_Color(1, 1, 1, Quantity_TOC_RGB))
        ambient.SetIntensity(float(os.environ.get("OCCT_PBR_AMB", 1.0)))
        viewer.AddLight(key)
        viewer.SetLightOn(key)
        viewer.AddLight(ambient)
        viewer.SetLightOn(ambient)
        low, high = self.rest_bounds()
        radius = float(max(high[0] - low[0], high[1] - low[1])) * 1.6
        floor = AIS_Shape(BRepPrimAPI_MakeCylinder(gp_Ax2(gp_Pnt((low[0] + high[0]) / 2, (low[1] + high[1]) / 2,
                                                                 float(low[2]) - radius * 0.0015),
                                                          gp_Dir(0, 0, 1)), radius, radius * 0.001).Shape())
        material = Graphic3d_MaterialAspect(Graphic3d_NameOfMaterial.Graphic3d_NameOfMaterial_Plastified)
        grey = _color((0.86, 0.87, 0.88))
        pbr = Graphic3d_PBRMaterial()
        pbr.SetColor(Quantity_ColorRGBA(grey, 1.0))
        pbr.SetMetallic(0.0)
        pbr.SetRoughness(0.7)
        material.SetPBRMaterial(pbr)
        material.SetColor(grey)
        material.SetBSDF(Graphic3d_BSDF.CreateMetallicRoughness_s(pbr))
        floor.SetMaterial(material)
        floor.SetColor(grey)
        self.canvas.context.Display(floor, 1, -1, False)
        self.overlays.append(floor)
        view.SetBackgroundColor(_color((0.93, 0.93, 0.94)))
        view.SetShadingModel(Graphic3d_TypeOfShadingModel.Graphic3d_TypeOfShadingModel_Pbr)
        params = view.ChangeRenderingParams()
        params.ToneMappingMethod = Graphic3d_ToneMappingMethod.Graphic3d_ToneMappingMethod_Filmic
        params.WhitePoint = 1.0
        params.IsShadowEnabled = True
        params.ShadowMapResolution = 4096
        params.ShadowMapBias = 0.002
        if path_trace:
            for source in self.sources.values():
                aspect = source.Attributes().ShadingAspect()
                face = aspect.Material()
                face.SetBSDF(Graphic3d_BSDF.CreateMetallicRoughness_s(face.PBRMaterial()))
                aspect.SetMaterial(face)
                source.SynchronizeAspects()
            params.Method = Graphic3d_RenderingMode.Graphic3d_RM_RAYTRACING
            params.IsGlobalIlluminationEnabled = True
            params.RaytracingDepth = 8
            params.IsAntialiasingEnabled = True
            params.CoherentPathTracingMode = False
            params.AdaptiveScreenSampling = False
            params.RadianceClampingValue = 30.0
            params.TwoSidedBsdfModels = True
            params.NbMsaaSamples = 0
        else:
            from OCP.Graphic3d import Graphic3d_CubeMapPacked

            cubemap = Graphic3d_CubeMapPacked(_studio_environment())
            cubemap.SetMipmapsGeneration(True)
            view.SetBackgroundCubeMap(cubemap, True, False)
            view.SetImageBasedLighting(True)
            view.Redraw()  # bakes the environment's lighting from the cube map
            from OCP.Aspect import Aspect_GradientFillMethod

            view.SetBgGradientColors(_color("#f7f7f7"), _color("#d9d9db"),
                                     Aspect_GradientFillMethod.Aspect_GradientFillMethod_Vertical, False)
            params.PbrEnvPow2Size = 9
            params.PbrEnvSpecMapNbLevels = 8
        return time.perf_counter() - clock

    def path_trace(self, out: Path, samples: int) -> float:
        """Accumulate ``samples`` path-traced frames in the view's own frame buffer and write it:
        ``ToPixMap`` would make a new buffer and start the accumulation over."""
        from OCP.Graphic3d import Graphic3d_BufferType
        from OCP.Image import Image_AlienPixMap, Image_Format
        from OCP.TCollection import TCollection_AsciiString

        clock = time.perf_counter()
        width, height = self.canvas.width, self.canvas.height
        graphic = self.canvas.view.View()
        graphic.SetFBO(graphic.FBOCreate(width, height))
        for _ in range(samples):
            self.canvas.view.Redraw()
        image = Image_AlienPixMap()
        image.InitZero(Image_Format.Image_Format_RGB, width, height)
        graphic.BufferDump(image, Graphic3d_BufferType.Graphic3d_BT_RGB)
        image.Save(TCollection_AsciiString(str(out)))
        return time.perf_counter() - clock

    def save(self, out: Path) -> float:
        return self.canvas.save(str(out))


_ENVIRONMENT = None


def _studio_environment(size: int = 64) -> Any:
    """A soft studio as a packed cube map (+X, -X, +Y, -Y, +Z, -Z), made in memory: a bright
    sky over a grey horizon and a dark floor, with three soft boxes. OCCT reads no HDR file here."""
    global _ENVIRONMENT
    if _ENVIRONMENT is not None:
        return _ENVIRONMENT
    from OCP.Image import Image_Format, Image_PixMap
    from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB

    a = (np.arange(size) + 0.5) / size * 2 - 1
    s, t = np.meshgrid(a, a)
    one = np.ones_like(s)
    faces = [(one, -t, -s), (-one, -t, s), (s, one, t), (s, -one, -t), (s, -t, one), (-s, -t, -one)]
    strips = []
    for face in faces:
        v = np.stack(face, -1)
        v /= np.linalg.norm(v, axis=-1, keepdims=True)
        z = v[..., 2]
        sky, horizon, floor = np.array([0.92, 0.94, 0.97]), np.array([0.70, 0.71, 0.73]), np.array([0.25, 0.25, 0.26])
        w = np.clip(z, 0, 1)[..., None] ** 0.6
        c = np.where(z[..., None] > 0, horizon * (1 - w) + sky * w,
                     horizon * np.clip(1 + z * 4, 0, 1)[..., None] + floor * np.clip(-z * 4, 0, 1)[..., None])
        for direction, spread, gain in (((-0.6, -0.8, 0.7), 0.30, 1.0), ((0.9, 0.6, 0.4), 0.18, 0.6), ((0, 0, 1), 0.25, 0.4)):
            dd = np.array(direction) / np.linalg.norm(direction)
            c = c + (np.clip((v @ dd - math.cos(spread)) / 0.02, 0, 1) * gain)[..., None]
        strips.append(np.clip(c, 0, 1))
    array = np.concatenate(strips, axis=1)
    height, width = array.shape[:2]
    pixmap = Image_PixMap()
    pixmap.InitTrash(Image_Format.Image_Format_RGB, width, height)
    pixmap.SetTopDown(True)
    for y in range(height):
        row = array[y]
        for x in range(width):
            r, g, b = row[x]
            pixmap.SetPixelColor(x, y, Quantity_Color(float(r), float(g), float(b), Quantity_TOC_RGB))
    _ENVIRONMENT = pixmap
    return pixmap


# --- the CLI ---------------------------------------------------------------------------------------


def _json_arg(text: str | None) -> Any:
    if text is None:
        return None
    text = text.strip()
    if text.startswith(("{", "[")):
        return json.loads(text)
    if text.endswith(".json") and Path(text).is_file():
        return json.loads(Path(text).read_text())
    return text


def main(argv: list[str] | None = None) -> int:
    started = time.perf_counter()
    parser = argparse.ArgumentParser(prog="python -m cadgen.engine.render.occt")
    parser.add_argument("document")
    parser.add_argument("out")
    parser.add_argument("--display")
    parser.add_argument("--camera", default="iso")
    parser.add_argument("--grid", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--pose")
    parser.add_argument("--explode", type=float, default=0.0)
    parser.add_argument("--annotation")
    parser.add_argument("--measurements")
    parser.add_argument("--path-trace", type=int, default=0)
    parser.add_argument("--mesh-cache", help="a folder of the engine's XBM buffers (<key>.xbm); else the daemon's")
    parser.add_argument("--width", type=int, default=1600)
    parser.add_argument("--height", type=int, default=1200)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    display = _json_arg(args.display)
    display = {"mode": display} if isinstance(display, str) else dict(display or {})
    timings: dict[str, float] = {}
    clock = time.perf_counter()
    from cadgen.engine.document import Loaded

    loaded = Loaded(Path(args.document))
    structure = loaded.structure()
    timings["read_xbf"] = time.perf_counter() - clock
    clock = time.perf_counter()
    if args.mesh_cache:
        from cadgen.engine.render.meshes import load

        buffers = load(Path(args.mesh_cache), [p["key"] for p in structure["prototypes"]])
    else:
        from cadgen.engine.render.meshes import from_daemon

        _structure, buffers, _timings = from_daemon(Path(args.document))
    timings["meshes"] = time.perf_counter() - clock
    scene = Scene(Path(args.document), buffers, size=(args.width, args.height), structure=structure,
                  pose=_json_arg(args.pose))
    timings.update({k: v for k, v in scene.timings.items() if k != "read"})
    if args.explode:
        timings["explode"] = scene.explode(args.explode)
    timings["present"] = scene.show(display, preview=args.preview or bool(args.path_trace))
    camera = display.get("camera") or {}
    projection = camera.get("projection") or ("perspective" if args.preview or args.path_trace else "orthographic")
    scene.frame(args.camera, projection)
    if display.get("grid") or args.grid:
        scene.grid(True)
    if args.annotation:
        scene.annotate(_json_arg(args.annotation))
    if args.measurements:
        scene.measure(_json_arg(args.measurements))
    if args.preview or args.path_trace:
        timings["look"] = scene.preview_look(args.path_trace)
    if args.path_trace:
        timings["frame+encode"] = scene.path_trace(Path(args.out), args.path_trace)
    else:
        timings["frame+encode"] = scene.save(Path(args.out))
    timings["total_in_process"] = time.perf_counter() - started
    if args.json:
        print(json.dumps({k: round(v, 4) for k, v in timings.items()}))
    else:
        print(f"wrote {args.out} in {timings['total_in_process']:.2f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
