"""Static body loads: a cantilever under its own weight against beam theory, and an accelerated part.

The beam is a build123d box written to a temporary STEP: 100 mm long, 6 x 6 mm
steel, clamped at x = 0. Its own weight is a uniform load q = ρ g A, so the
tip deflects w = q L^4 / (8 E I) (shear adds under half a percent at this
slenderness). Accelerating the part up at 1 g loads it exactly as gravity
down does, and the reactions carry the weight.
"""

from __future__ import annotations

import unittest

from tests.python.support.fea_fixtures import box_step, class_directory, ends, glb_extras, needs_fea, solve

from cadgen._internal.fea.materials import lookup_material  # noqa: E402

LENGTH, SIDE = 100.0, 6.0
STEEL = lookup_material("steel")
G0 = 9806.65


def _tip_deflection() -> float:
    q = STEEL.density * G0 * SIDE * SIDE
    inertia = SIDE ** 4 / 12.0
    return q * LENGTH ** 4 / (8.0 * STEEL.E * inertia)


@needs_fea
class SelfWeight(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        step = box_step(directory, "beam", (LENGTH, SIDE, SIDE), "x")
        base = {"material": "steel", "fixtures": [{"faces": [ends(step)[0]]}], "mesh": {"size_mm": 2.0}}
        cls.gravity = solve(step, directory / "gravity.glb", {**base, "loads": [{"type": "gravity", "vector_g": [0, 0, -1]}]})
        cls.lifted = solve(step, directory / "lifted.glb", {**base, "loads": [{"type": "acceleration", "vector_g": [0, 0, 1]}]})

    def test_the_tip_sags_as_beam_theory_says(self):
        summary = self.gravity.summary
        self.assertAlmostEqual(summary["max_displacement_mm"] / _tip_deflection(), 1.0, delta=0.03)
        self.assertAlmostEqual(summary["max_displacement_at_mm"][0], LENGTH, delta=1e-6)

    def test_the_weight_is_applied_and_the_clamp_carries_it(self):
        weight = STEEL.density * LENGTH * SIDE * SIDE * G0
        applied, reaction = self.gravity.summary["applied_force_N"], self.gravity.summary["reaction_force_N"]
        self.assertAlmostEqual(applied[2] / -weight, 1.0, delta=1e-3)
        self.assertAlmostEqual(reaction[2] / weight, 1.0, delta=1e-3)
        self.assertEqual(self.gravity.warnings, ())

    def test_accelerating_up_loads_it_as_gravity_down(self):
        self.assertEqual(self.lifted.summary["max_displacement_mm"], self.gravity.summary["max_displacement_mm"])
        self.assertEqual(self.lifted.summary["applied_force_N"], self.gravity.summary["applied_force_N"])

    def test_the_glb_echoes_the_body_load_without_faces(self):
        extras = glb_extras(self.gravity.glb)
        self.assertEqual(extras["study"]["loads"], [{"type": "gravity", "vector_g": [0.0, 0.0, -1.0]}])
        self.assertNotIn("analysis", extras)


if __name__ == "__main__":
    unittest.main()
