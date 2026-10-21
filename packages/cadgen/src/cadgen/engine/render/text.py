"""PROTOTYPE: the viewer's words as pictures, drawn the way the viewer's page draws them, for the
OCCT renderer to lay on a quad or over the frame.

The viewer writes a text mark's words onto a 2D canvas and maps the canvas onto the mark's quad
(``annotation.js`` ``textCanvas``): ``500 <2 x fontPx>px ui-sans-serif, system-ui, sans-serif``,
``textBaseline = "top"``, lines 1.25 apart, the canvas ``ceil(widest line) + 2`` by
``ceil(lines x fontPx x 1.25 x 2) + 2`` with the words at (1, 1). Measure's label is the same
face at 600 (``measureDimension.js``). Chromium has no ``ui-sans-serif``; its ``system-ui`` on
macOS is the system face, San Francisco, the variable ``SFNS.ttf``. So the words here are that
file, set as Chromium sets it (measured against its canvas, ``measureText`` to within 0.04 px a
line):

- the weight axis at the CSS weight (500, 600), the optical size at the font size (17 to 96), the
  font's own advances at that location (HVAR);
- the font's kerning (GPOS ``kern``, pair adjustments with their variation deltas) and its size
  tracking (the ``trak`` table, after each glyph);
- no hinting, glyphs at fractional x positions on a baseline snapped to a whole pixel (Skia's
  axis-aligned text), each outline grown on every side by macOS's font smoothing, which Chromium's
  canvas text keeps (``0.00605 x size`` px, at most 0.3, none from 72 px up), rasterised by
  FreeType at ten times the size and averaged down.

Fitted against Chromium's own canvas at the viewer's sizes (32, 40 and 56 px: Annotate's 16, 20
and 28 at twice), the coverage differs by 1.4 to 2.1 levels in 255 on average, the ink by under 3%.

Not done: substitutions (GSUB ligatures and contextual alternates) and scripts other than the
font's simple ones. Without the system face (not macOS), DejaVu Sans, matplotlib's, stands in.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np

SYSTEM_FACE = Path("/System/Library/Fonts/SFNS.ttf")
TEXT_SCALE = 2  # the viewer's text canvas is twice the words' size (annotation.js textCanvas)
LINE_HEIGHT = 1.25
SUPERSAMPLE = 10


def _face_path() -> Path:
    if SYSTEM_FACE.is_file():
        return SYSTEM_FACE
    from matplotlib import font_manager

    return Path(font_manager.findfont("DejaVu Sans"))


@lru_cache(maxsize=4)
def _font(path: str) -> Any:
    from fontTools.ttLib import TTFont

    return TTFont(path, lazy=True)


class Face:
    """The face at a CSS weight and size: glyph advances, kerning and tracking in pixels, and its
    em box's top above the baseline (where ``textBaseline = "top"`` puts the words)."""

    def __init__(self, weight: float, size: float) -> None:
        self.path = _face_path()
        self.font = _font(str(self.path))
        self.size = float(size)
        self.weight = float(weight)
        font = self.font
        self.upm = font["head"].unitsPerEm
        self.cmap = font.getBestCmap()
        self.location: dict[str, float] = {}
        if "fvar" in font:
            axes = {axis.axisTag: axis for axis in font["fvar"].axes}
            if "wght" in axes:
                self.location["wght"] = min(max(self.weight, axes["wght"].minValue), axes["wght"].maxValue)
            if "opsz" in axes:
                self.location["opsz"] = min(max(self.size, axes["opsz"].minValue), axes["opsz"].maxValue)
        self.glyphs = font.getGlyphSet(location=self.location) if self.location else font.getGlyphSet()
        hhea = font["hhea"]
        ascent, descent = hhea.ascent, -hhea.descent
        self.em_top = self.size * ascent / (ascent + descent)  # the em box's top above the baseline
        self.track = self._track() * self.size / self.upm
        self._pairs = _pair_values(str(self.path), tuple(sorted(self.location.items())))

    def _track(self) -> float:
        """The ``trak`` table's normal track at this size, in font units (0 without one)."""
        if "trak" not in self.font:
            return 0.0
        data = self.font["trak"].horizData
        entry = data.get(0.0)
        if not entry:
            return 0.0
        sizes = sorted(entry)
        if self.size <= sizes[0]:
            return float(entry[sizes[0]])
        if self.size >= sizes[-1]:
            return float(entry[sizes[-1]])
        for low, high in zip(sizes, sizes[1:]):
            if low <= self.size <= high:
                return entry[low] + (entry[high] - entry[low]) * (self.size - low) / (high - low)
        return 0.0

    def glyph(self, char: str) -> str:
        return self.cmap.get(ord(char)) or ".notdef"

    def layout(self, line: str) -> tuple[list[float], float]:
        """Each glyph's pen position from the line's start (its ink drawn from there), and the
        line's width, as Chromium's ``measureText`` gives it, in pixels."""
        scale = self.size / self.upm
        names = [self.glyph(char) for char in line]
        positions, pen = [], 0.0
        for index, name in enumerate(names):
            positions.append(pen)
            advance = self.glyphs[name].width * scale + self.track
            if index + 1 < len(names):
                advance += self._pairs(name, names[index + 1]) * scale
            pen += advance
        return positions, pen

    def width(self, line: str) -> float:
        return self.layout(line)[1]


@lru_cache(maxsize=32)
def face(weight: float, size: float) -> Face:
    """The face at ``weight`` and ``size``, made once per process."""
    return Face(weight, size)


@lru_cache(maxsize=16)
def _pair_values(path: str, location: tuple[tuple[str, float], ...]) -> Any:
    """A function (left glyph, right glyph) -> the GPOS ``kern`` feature's x advance adjustment
    of the left glyph, in font units, at ``location`` (variation deltas applied)."""
    font = _font(path)
    if "GPOS" not in font:
        return lambda left, right: 0.0
    gpos = font["GPOS"].table
    instancer = None
    if location and "GDEF" in font and getattr(font["GDEF"].table, "VarStore", None) is not None:
        from fontTools.varLib.models import normalizeLocation, piecewiseLinearMap
        from fontTools.varLib.varStore import VarStoreInstancer

        axes = {axis.axisTag: (axis.minValue, axis.defaultValue, axis.maxValue) for axis in font["fvar"].axes}
        normal = normalizeLocation(dict(location), axes)
        if "avar" in font:
            segments = font["avar"].segments
            normal = {tag: piecewiseLinearMap(value, segments[tag]) if tag in segments else value
                      for tag, value in normal.items()}
        instancer = VarStoreInstancer(font["GDEF"].table.VarStore, font["fvar"].axes, normal)
    features = gpos.FeatureList.FeatureRecord
    lookups: list[int] = []
    for record in gpos.ScriptList.ScriptRecord:
        if record.ScriptTag in ("latn", "DFLT") and record.Script.DefaultLangSys is not None:
            for index in record.Script.DefaultLangSys.FeatureIndex:
                if features[index].FeatureTag == "kern":
                    lookups += [i for i in features[index].Feature.LookupListIndex if i not in lookups]
            break
    tables = []
    for index in sorted(lookups):
        lookup = gpos.LookupList.Lookup[index]
        if lookup.LookupType == 9:
            tables.append([sub.ExtSubTable for sub in lookup.SubTable if sub.ExtensionLookupType == 2])
        elif lookup.LookupType == 2:
            tables.append(list(lookup.SubTable))

    def value(record: Any) -> float:
        if record is None:
            return 0.0
        found = float(getattr(record, "XAdvance", 0) or 0)
        device = getattr(record, "XAdvDevice", None)
        if instancer is not None and device is not None and getattr(device, "DeltaFormat", 0) == 0x8000:
            found += instancer[(device.StartSize << 16) + device.EndSize]
        return found

    covered: dict[int, dict[str, int]] = {}
    pair_sets: dict[tuple[int, int], dict[str, Any]] = {}

    @lru_cache(maxsize=4096)
    def pair(left: str, right: str) -> float:
        total = 0.0
        for subtables in tables:
            for sub in subtables:
                coverage = covered.get(id(sub))
                if coverage is None:
                    coverage = covered[id(sub)] = {name: i for i, name in enumerate(sub.Coverage.glyphs)}
                at = coverage.get(left)
                if at is None:
                    continue
                if sub.Format == 1:
                    key = (id(sub), at)
                    seconds = pair_sets.get(key)
                    if seconds is None:
                        seconds = pair_sets[key] = {r.SecondGlyph: r.Value1 for r in sub.PairSet[at].PairValueRecord}
                    if right not in seconds:
                        continue
                    total += value(seconds[right])
                else:
                    first = sub.ClassDef1.classDefs.get(left, 0)
                    second = sub.ClassDef2.classDefs.get(right, 0)
                    total += value(sub.Class1Record[first].Class2Record[second].Value1)
                break  # a lookup's first subtable to take the pair is its only one
        return total

    return pair


# How far FreeType's ink sits from CoreText's for the same baseline, fitted: up a fifth of a pixel.
BASELINE_SHIFT = -0.2


def _dilation(size: float) -> float:
    """macOS's font smoothing, as Chromium's canvas text shows it (fitted): each side of an
    outline grown by ``0.00605 x size`` pixels, at most 0.3, none from 72 px up."""
    return 0.0 if size >= 72 else min(0.00605 * size, 0.3)


@lru_cache(maxsize=16)
def _raster_font(path: str, size: float, location: tuple[tuple[str, float], ...]) -> Any:
    from PIL import ImageFont

    font = ImageFont.truetype(path, size)
    if location:
        axes = font.get_variation_axes()
        wanted = dict(location)
        names = {b"Weight": "wght", b"Optical Size": "opsz", b"Width": "wdth"}
        values = []
        for axis in axes:
            tag = names.get(axis["name"]) or (axis["name"].decode() if isinstance(axis["name"], bytes) else axis["name"])
            values.append(wanted.get(tag, axis["default"]))
        font.set_variation_by_axes(values)
    return font


def coverage(lines: list[str], face: Face, size: tuple[int, int], origin: tuple[float, float],
             line_step: float, scale: float = 1.0, align: str = "left") -> np.ndarray:
    """The words' coverage (0 to 1) on a ``size`` (width, height) picture: line ``i``'s em box
    top at ``origin + (0, i x line_step)``, everything in the face's pixels times ``scale`` (a
    canvas transform); ``align`` "center" centres each line on ``origin``'s x."""
    from PIL import Image, ImageDraw

    ss = SUPERSAMPLE
    raster = _raster_font(str(face.path), face.size * scale * ss, tuple(sorted(face.location.items())))
    picture = Image.new("L", (size[0] * ss, size[1] * ss), 0)
    draw = ImageDraw.Draw(picture)
    grow = int(round(_dilation(face.size * scale) * ss))
    for index, line in enumerate(lines):
        positions, width = face.layout(line)
        left = origin[0] - (width * scale / 2 if align == "center" else 0.0)
        baseline = round(origin[1] + index * line_step + face.em_top * scale) + BASELINE_SHIFT
        for char, at in zip(line, positions):
            if char.isspace():
                continue
            draw.text(((left + at * scale) * ss, baseline * ss), char, font=raster, fill=255, anchor="ls",
                      stroke_width=grow, stroke_fill=255)
    return np.asarray(picture.reduce(ss), np.float64) / 255.0


def text_canvas(text: str, font_px: float, color: str) -> np.ndarray:
    """A text mark's picture as the viewer makes it (``textCanvas``): RGBA bytes, (height, width,
    4), the colour where there is ink (as a canvas hands its pixels to WebGL: not premultiplied,
    nothing where there is none)."""
    lines = str(text).split("\n")
    face_ = face(500, font_px * TEXT_SCALE)
    width = max(1.0, *(face_.width(line) for line in lines))
    size = (int(math.ceil(width)) + 2, int(math.ceil(len(lines) * font_px * LINE_HEIGHT * TEXT_SCALE)) + 2)
    alpha = coverage(lines, face_, size, (1.0, 1.0), font_px * LINE_HEIGHT * TEXT_SCALE)
    rgb = np.array([int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)], np.uint8)
    out = np.zeros((size[1], size[0], 4), np.uint8)
    a = np.rint(alpha * 255).astype(np.uint8)
    out[..., 3] = a
    out[a > 0, :3] = rgb
    return out


def measure(text: str, font_px: float, weight: float = 500) -> float:
    """``measureText(text).width`` for the viewer's face at ``font_px``: a text mark's quad width."""
    return max(face(weight, font_px).width(line) for line in str(text).split("\n"))
