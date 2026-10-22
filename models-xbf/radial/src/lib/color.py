"""sRGB hex to the linear RGBA a build123d shape's colour takes."""

from __future__ import annotations


def _linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def srgb(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """The linear RGBA channels for the sRGB hex (``#rgb`` or ``#rrggbb``) you want to
    see on screen; ``alpha`` is 0..1. Assign the result to ``shape.color``."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"expected a #rgb or #rrggbb colour, got {value!r}")
    red, green, blue = (int(text[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    return (_linear(red), _linear(green), _linear(blue), float(alpha))
