"""Materials: what a part is made of, as a value, and a catalogue of common ones.

A ``Material`` is a physical material -- its name and density -- with the look it is
drawn in: a PBR appearance (base colour, metallic, roughness, emissive). A model sets
one on a finished part (``part.material = materials.aluminium``), on faces of a part
(``paint(part, faces, material)``), or on an assembly, whose material every part below
it that has none takes when the document is made. The document carries it per part
label (``XCAFDoc_Material`` and ``XCAFDoc_VisMaterial``); STEP carries its name and
density, GLB its PBR appearance.

The catalogue's names and densities are FreeCAD's material cards, as they read (the
card is named beside each entry, under ``src/Mod/Material/Resources/Materials/Standard``
of FreeCAD's sources); the appearance is the engine's own. Any other material is
``Material(...)`` in the model.

Colours are linear RGB(A) in 0-1, as build123d's ``Color`` holds them. Density is in
kg/m^3.

Importing this module imports no kernel.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CATALOGUE", "Material", "abs_plastic", "acetal", "acrylic", "aluminium", "brass", "copper", "glass", "nylon",
    "pet", "pla", "polycarbonate", "stainless_steel", "steel", "titanium",
]


def _unit(field: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Material {field} must be a number in 0-1, not {value!r}") from None
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"Material {field} must be in 0-1, not {value!r}")
    return number


def _components(field: str, value: Any, sizes: tuple[int, ...]) -> tuple[float, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"Material {field} takes {' or '.join(map(str, sizes))} numbers in 0-1, not {value!r}")
    try:
        items = tuple(value)
    except TypeError:
        raise ValueError(f"Material {field} takes {' or '.join(map(str, sizes))} numbers in 0-1, "
                         f"not {value!r}") from None
    if len(items) not in sizes:
        raise ValueError(f"Material {field} takes {' or '.join(map(str, sizes))} numbers in 0-1, not {len(items)}")
    return tuple(_unit(field, item) for item in items)


@dataclass(frozen=True)
class Material:
    """A physical material and its look. ``name`` is required; ``density`` (kg/m^3),
    ``color`` (RGB or RGBA, 0-1), ``emissive`` (RGB, 0-1) and ``description`` are
    optional; ``metallic`` and ``roughness`` (0-1) default to a painted plastic's. A
    field out of range raises ``ValueError`` naming it."""

    name: str
    density: float | None = None
    color: tuple[float, ...] | None = None
    metallic: float = 0.0
    roughness: float = 0.5
    emissive: tuple[float, ...] | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError(f"Material name must be a non-empty string, not {self.name!r}")
        if self.density is not None:
            try:
                density = float(self.density)
            except (TypeError, ValueError):
                raise ValueError(f"Material density must be a number of kg/m^3, not {self.density!r}") from None
            if not math.isfinite(density) or density <= 0:
                raise ValueError(f"Material density must be positive, not {self.density!r}")
            object.__setattr__(self, "density", density)
        if self.color is not None:
            rgba = _components("color", self.color, (3, 4))
            object.__setattr__(self, "color", rgba if len(rgba) == 4 else (*rgba, 1.0))
        object.__setattr__(self, "metallic", _unit("metallic", self.metallic))
        object.__setattr__(self, "roughness", _unit("roughness", self.roughness))
        if self.emissive is not None:
            object.__setattr__(self, "emissive", _components("emissive", self.emissive, (3,)))
        if self.description is not None and not isinstance(self.description, str):
            raise ValueError(f"Material description must be a string, not {self.description!r}")

    # A material is a value: a copy of a shape (build123d's deepcopy) shares it.
    def __copy__(self) -> "Material":
        return self

    def __deepcopy__(self, memo: Any) -> "Material":
        return self


# The catalogue. Name and density: the FreeCAD card named in each comment (Standard/...).
# Appearance: the engine's own preset.

# Metal/Aluminum/Aluminum-Generic.FCMat
aluminium = Material("Aluminum Generic", 2700.0, (0.91, 0.92, 0.92, 1.0), metallic=1.0, roughness=0.35)
# Metal/Steel/Steel-Generic.FCMat
steel = Material("Steel-Generic", 7900.0, (0.56, 0.57, 0.58, 1.0), metallic=1.0, roughness=0.45)
# Metal/Steel/Steel-X5CrNi18-10.FCMat (1.4301, AISI 304)
stainless_steel = Material("X5CrNi18-10", 7800.0, (0.67, 0.66, 0.64, 1.0), metallic=1.0, roughness=0.3)
# Metal/Copper/Copper-Generic.FCMat
copper = Material("Copper Generic", 8960.0, (0.955, 0.638, 0.538, 1.0), metallic=1.0, roughness=0.3)
# Metal/Copper/Copper-464.FCMat (naval brass, CuZn39Sn1)
brass = Material("Copper-464", 8410.0, (0.887, 0.789, 0.434, 1.0), metallic=1.0, roughness=0.3)
# Metal/Titanium/Ti-6Al-4V.FCMat
titanium = Material("Ti-6Al-4V (Grade 5)", 4430.0, (0.542, 0.497, 0.449, 1.0), metallic=1.0, roughness=0.4)
# Thermoplast/ABS-Generic.FCMat
abs_plastic = Material("ABS", 1060.0, (0.18, 0.18, 0.19, 1.0), metallic=0.0, roughness=0.55)
# Thermoplast/PLA-Generic.FCMat
pla = Material("PLA", 1240.0, (0.85, 0.85, 0.83, 1.0), metallic=0.0, roughness=0.45)
# Thermoplast/PET-Generic.FCMat
pet = Material("PET", 1380.0, (0.82, 0.86, 0.88, 0.85), metallic=0.0, roughness=0.2)
# Thermoplast/PA6-Generic.FCMat
nylon = Material("Polyamide 6", 1150.0, (0.86, 0.84, 0.78, 1.0), metallic=0.0, roughness=0.6)
# Thermoplast/PMMA-Generic.FCMat
acrylic = Material("PMMA-Generic", 1170.0, (0.92, 0.95, 0.96, 0.35), metallic=0.0, roughness=0.05)
# Thermoplast/PC-Molded.FCMat
polycarbonate = Material("PC-Molded", 1200.0, (0.9, 0.92, 0.94, 0.5), metallic=0.0, roughness=0.1)
# Thermoplast/POM-Copolymer-Generic.FCMat
acetal = Material("POM-Copolymer-Generic", 1410.0, (0.9, 0.9, 0.88, 1.0), metallic=0.0, roughness=0.4)
# Glass/Glass-Generic.FCMat
glass = Material("Glass", 2520.0, (0.9, 0.95, 0.95, 0.25), metallic=0.0, roughness=0.05)

# Every catalogue entry by its attribute name, in the order above.
CATALOGUE: dict[str, Material] = {name: value for name, value in dict(globals()).items()
                                  if isinstance(value, Material)}
