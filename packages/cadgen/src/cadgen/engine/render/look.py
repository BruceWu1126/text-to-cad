"""The regular display modes' look, as the OCCT prototype draws them: one frozen record.

``occt.py`` sets OCCT up from it, the engine serves it as JSON (``GET /api/look``,
``serve.py``), and the CAD Viewer draws an engine document's regular modes by it (core's
``lib/xbf/xbfLook.js``). Preview's photographic look is not here.

**What OCCT 7.9 draws.** Read from the GLSL programs its raster compiles for these modes
(``occt_phong``: faces; ``occt_unlit``: edges and a hidden-line face; dumped with
``OpenGl_Caps.glslDumpLevel``) and checked on its pixels. Per pixel of a lit face, in linear RGB,
``c`` the face's colour, ``N`` its normal, for each light ``k``: ``L_k`` the unit vector towards
it, ``H_k`` the half vector of ``L_k`` and the view:

    albedo  = c ** colour_power                             (colour_power = 2)
    rgb     = albedo * (ambient + sum_k radiance_k * max(0, N.L_k))
            + specular * sum_k radiance_k * [N.L_k > 0] * max(0, N.H_k) ** (128 * shininess)
    pixel   = sRGB(clamp(rgb, 0, 1))

- ``albedo = c * c``: a face is an ``AIS_Shape`` given the colour twice, as its material's
  diffuse and ambient colour and as the presentation's colour, and OCCT's ASPECT material
  (``Graphic3d_NameOfMaterial_Plastified``) multiplies the two. It is what saturates the
  prototype's colours.
- ``radiance = 1``: the Phong program reads a directional light's colour (white) and not its
  intensity, which only the PBR program reads: the 0.45 and 0.12 the prototype once set on its
  key and fill drew nothing (pictures with 0.01 or 3.0 are the same bytes). The ambient light's
  intensity is read: 0.5.
- No image-based light (``environment = 0``), no exposure, no tone curve: the frame buffer is
  sRGB, so the linear sum is clamped and encoded, and nothing else (``tone = "clamp"``).

**OCCT's Filmic curve** (``Graphic3d_ToneMappingMethod_Filmic``) is only in the path tracer's
display pass (``Display.fs``, under ``PATH_TRACING``); no raster program applies it, Preview's
PBR raster included. With ``x`` the linear colour times ``2 ** Exposure`` and ``W`` the
``WhitePoint``:

    f(x)  = (x * (1.425 x + 0.05) + 0.004) / (x * (1.425 x + 0.6) + 0.0491) - 0.0821
    out   = f(x) / f(W)

Edges are unlit lines in their class's colour, 1 px; X-ray's faces are lit at 0.22 opacity
under every edge; Hidden line's faces are unlit in the backdrop's colour, hiding what is behind
them; Wireframe draws edges alone. The grid is the viewer's measuring grid (core's
``lib/viewer/stageGrid.js``, drawn by ``grid.py``): graph paper on z = 0 through the origin, the
step of 1, 2 or 5 x 10^n nearest ``framing / cells`` (``framing`` the model's diameter at rest
times 1.04), a major every 5th or 10th line, a red X and a green Y axis, faded out ``extent``
framings from the origin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Light:
    """A directional light: ``direction`` the way it travels (``V3d_DirectionalLight``'s), in the
    document's frame (Z up); a face turned against it is lit."""

    name: str
    direction: tuple[float, float, float]
    radiance: float = 1.0


@dataclass(frozen=True)
class Mode:
    """A display mode's recipe. ``faces``: ``lit``, ``backdrop`` (unlit in the backdrop's colour,
    hiding what is behind) or ``none``; ``edges``: ``none``, ``visible`` (depth tested) or ``all``
    (over every face); ``palette``: each instance in the parts palette, by its index."""

    name: str
    faces: str
    edges: str
    opacity: float = 1.0
    palette: bool = False


@dataclass(frozen=True)
class Grid:
    colour: str
    cells: float  # minor cells across the model's framing: the cell is the round step nearest framing / cells
    extent: float  # framings from the origin by which the paper has faded out
    axes: bool  # a red X and a green Y axis line


@dataclass(frozen=True)
class Look:
    backdrop: str
    ambient: float
    lights: tuple[Light, ...]
    colour_power: float
    specular: float
    shininess: float
    environment: float
    tone: str
    exposure: float
    fill: str
    palette: tuple[str, ...]
    edge_colours: tuple[tuple[str, str], ...]
    edge_width: float
    modes: tuple[Mode, ...]
    grid: Grid
    version: int = field(default=1)

    def edge(self, line: str) -> str:
        return dict(self.edge_colours)[line]

    def mode(self, name: str) -> Mode:
        return next(mode for mode in self.modes if mode.name == name)

    def json(self) -> dict[str, Any]:
        """The record as ``GET /api/look`` serves it (camelCase, as the structure is)."""
        return {
            "version": self.version, "backdrop": self.backdrop, "ambient": self.ambient,
            "lights": [{"name": light.name, "direction": list(light.direction), "radiance": light.radiance}
                       for light in self.lights],
            "colourPower": self.colour_power, "specular": self.specular, "shininess": self.shininess,
            "environment": self.environment, "tone": self.tone, "exposure": self.exposure, "fill": self.fill,
            "palette": list(self.palette), "edges": {"colours": dict(self.edge_colours), "width": self.edge_width},
            "modes": {mode.name: {"faces": mode.faces, "edges": mode.edges, "opacity": mode.opacity,
                                  "palette": mode.palette} for mode in self.modes},
            "grid": {"colour": self.grid.colour, "cells": self.grid.cells, "extent": self.grid.extent,
                     "axes": self.grid.axes},
        }


LOOK = Look(
    backdrop="#f0f4f9",
    ambient=0.5,
    lights=(Light("key", (-0.35, 0.55, -1.0)), Light("fill", (0.6, 0.2, -0.3))),
    colour_power=2.0,
    specular=0.08,
    shininess=0.35,
    environment=0.0,
    tone="clamp",
    exposure=0.0,
    fill="#b6c4ce",
    palette=("#b6c4ce", "#f4a7a7", "#f8c77e", "#f7e38d", "#b9e88f", "#8fe3c0", "#92d7f5", "#a9b8ff", "#c7a8ff",
             "#f2a7d9"),
    edge_colours=(("feature", "#253443"), ("tangent", "#667788"), ("seam", "#495b6c")),
    edge_width=1.0,
    modes=(Mode("solid", "lit", "visible"), Mode("shaded", "lit", "none"),
           Mode("parts", "lit", "visible", palette=True), Mode("xray", "lit", "all", opacity=0.22),
           Mode("hidden-line", "backdrop", "visible"), Mode("wireframe", "none", "all")),
    grid=Grid(colour="#4ea8cc", cells=20.0, extent=6.0, axes=True),
)
