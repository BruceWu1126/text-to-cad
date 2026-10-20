"""`srgb()`: the linear RGBA channels of the sRGB hex you want to SEE (was `cadgen.color.srgb`)."""
from __future__ import annotations

__all__ = ["srgb"]


def _to_linear(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def srgb(value: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
    """``value`` is ``#rgb`` or ``#rrggbb``; a plain tuple, so a palette module imports no kernel."""
    text = value.strip().lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    if len(text) != 6:
        raise ValueError(f"expected a #rgb or #rrggbb colour, got {value!r}")
    red, green, blue = (int(text[i:i + 2], 16) for i in (0, 2, 4))
    return (_to_linear(red / 255.0), _to_linear(green / 255.0), _to_linear(blue / 255.0), float(alpha))
