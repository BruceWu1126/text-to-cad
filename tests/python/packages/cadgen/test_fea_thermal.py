"""Steady heat (`"analysis": "thermal"`): the study it reads, and two textbook answers.

The slab is a 20 x 10 x 10 mm aluminium block held at 100 °C on one end and
20 °C on the other, its sides insulated: the temperature falls linearly along
it, and Q = k A ΔT / L flows through. The pin fin is a 4 x 4 x 50 mm aluminium
rod held at 100 °C at its base, cooled by air at 20 °C on its four sides, its
tip insulated: its tip sits at T_inf + (T_b - T_inf) / cosh(mL), m = sqrt(hP/(kA)).
Each STEP is a build123d box in a temporary directory.
"""

from __future__ import annotations

import math
import unittest

from tests.python.support.fea_fixtures import (
    box_step, class_directory, ends, glb_attribute_names, glb_extras, glb_rows, needs_fea, quiet, side_faces, sidecar,
    solve, value_error,
)

from cadgen._internal.fea.materials import lookup_material  # noqa: E402

ALUMINIUM = lookup_material("aluminum-6061-t6")
BASE = {"analysis": "thermal", "material": "aluminum-6061-t6", "temperatures": [{"faces": ["#o1.f1"], "C": 25}]}


class StudyFile(unittest.TestCase):
    def parse(self, **changes):
        from cadgen._internal.fea.study import parse_study

        return parse_study({**BASE, **changes})

    def test_it_reads_held_temperatures_heat_and_air(self):
        study = self.parse(
            heat=[{"faces": ["#o1.f7"], "W": 15}, {"faces": ["#o1.f8"], "W_per_m2": 2000}],
            convection=[{"faces": ["#o1.f3", "#o1.f4"], "h_W_m2K": 10, "ambient_C": 20}],
            view={"checks": [{"kind": "temperature", "max_C": 85, "faces": ["#o1.f7"], "label": "Chip side"}]},
        )
        inputs = study.inputs
        self.assertEqual(study.analysis, "thermal")
        self.assertEqual([(h.watts, h.per_m2) for h in inputs.heat], [(15.0, None), (None, 2000.0)])
        self.assertEqual(inputs.convection[0].h, 10.0)
        self.assertEqual(inputs.reference_C, 20.0)  # the coolest temperature the study sets: the air's
        self.assertEqual(inputs.anchor_refs, ("#o1.f1", "#o1.f3", "#o1.f4"))
        self.assertEqual(set(study.face_refs), {"#o1.f1", "#o1.f7", "#o1.f8", "#o1.f3", "#o1.f4"})
        self.assertEqual(study.checks[0]["max_C"], 85.0)
        self.assertEqual(study.fixtures, ())

    def test_a_part_only_heated_has_no_steady_temperature(self):
        message = value_error(self, self.parse, temperatures=[], heat=[{"faces": ["#o1.f7"], "W": 15}])
        self.assertIn("nowhere for the heat to go, has no steady temperature", message)
        self.assertIn("'convection'", message)

    def test_the_errors_name_the_field_in_plain_words(self):
        cases = [
            ({"heat": [{"faces": ["#o1.f7"], "W": 15, "W_per_m2": 3}]}, "heat[0]: give W"),
            ({"heat": [{"faces": ["#o1.f7"], "W": 0}]}, "heat[0].W: the heat is zero"),
            ({"convection": [{"faces": ["#o1.f3"], "ambient_C": 20}]}, "convection[0].h_W_m2K"),
            ({"convection": [{"faces": ["#o1.f3"], "h_W_m2K": -1, "ambient_C": 20}]}, "must be > 0"),
            ({"temperatures": [{"faces": ["#o1.f1"]}]}, "temperatures[0].C"),
            ({"temperatures": [{"faces": ["#o1.f1"], "C": 25, "history": [[0, 1]]}]}, "for thermal_transient studies"),
            ({"view": {"checks": [{"kind": "temperature", "max_C": 25}]}}, "is not above 25 °C"),
            ({"view": {"checks": [{"kind": "stress"}]}}, "is not a check this cadgen makes"),
            ({"fixtures": [{"faces": ["#o1.f2"]}]}, "thermal studies take"),
            ({"material": {"name": "mystery", "E_MPa": 1000, "nu": 0.3, "yield_MPa": 10}}, "conductivity_W_mK"),
        ]
        for changes, fragment in cases:
            with self.subTest(changes=changes):
                self.assertIn(fragment, value_error(self, self.parse, **changes))

    def test_its_view_offers_temperature_and_heat_flow_and_no_deformation(self):
        study = self.parse(view={"controls": [{"drives": "field"}], "presets": [{"label": "Flow", "field": "heat_flux"}]})
        self.assertEqual(study.view["controls"][0]["options"], ["temperature", "heat_flux"])
        with self.assertRaises(ValueError):
            self.parse(view={"controls": [{"drives": "deformation"}]})


@needs_fea
class Slab(unittest.TestCase):
    """Held at 100 °C and 20 °C at its ends: linear in x, Q = k A ΔT / L."""

    LENGTH, SIDE, HOT, COLD = 20.0, 10.0, 100.0, 20.0

    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        step = box_step(directory, "slab", (cls.LENGTH, cls.SIDE, cls.SIDE), "x")
        cls.hot, cls.cold = ends(step)
        study = {"analysis": "thermal", "material": "aluminum-6061-t6", "mesh": {"size_mm": 4.0},
                 "temperatures": [{"faces": [cls.hot], "C": cls.HOT}, {"faces": [cls.cold], "C": cls.COLD}],
                 "view": {"checks": [{"kind": "temperature", "max_C": 105, "label": "Hot end"},
                                     {"kind": "temperature", "max_C": 150, "faces": [cls.cold]}]}}
        cls.result = solve(step, directory / "slab.glb", study)
        cls.extras = glb_extras(cls.result.glb)
        cls.attributes = glb_rows(cls.result.glb, "POSITION", "_TEMPERATURE", "_HEAT_FLUX")
        cls.sidecar = sidecar(cls.result)

    def test_the_temperature_falls_linearly_along_it(self):
        worst = 0.0
        for (x, _, _), (t,) in zip(self.attributes["POSITION"], self.attributes["_TEMPERATURE"]):
            expected = self.HOT + (self.COLD - self.HOT) * (x * 1000.0) / self.LENGTH
            worst = max(worst, abs(t - expected))
        self.assertLess(worst / (self.HOT - self.COLD), 0.005)
        self.assertAlmostEqual(self.result.summary["max_temperature_C"], self.HOT, places=6)
        self.assertAlmostEqual(self.result.summary["min_temperature_C"], self.COLD, places=6)

    def test_the_heat_through_it_is_k_a_dt_over_l(self):
        q = ALUMINIUM.conductivity * (self.SIDE / 1000) ** 2 * (self.HOT - self.COLD) / (self.LENGTH / 1000)
        summary = self.result.summary
        self.assertAlmostEqual(summary["heat_in_W"] / q, 1.0, delta=0.005)
        self.assertAlmostEqual(summary["heat_out_W"] / q, 1.0, delta=0.005)
        flux = q / (self.SIDE / 1000) ** 2
        self.assertAlmostEqual(summary["max_heat_flux_W_m2"] / flux, 1.0, delta=0.02)
        self.assertEqual(self.result.warnings, ())

    def test_the_checks_measure_from_the_coolest_temperature_it_sets(self):
        hot, cold = self.result.summary["checks"]
        self.assertEqual((hot["label"], hot["status"], hot["unit"], hot["reference"]), ("Hot end", "close", "°C", 20.0))
        self.assertAlmostEqual(hot["ratio"], (100 - 20) / (105 - 20), places=5)
        self.assertEqual(cold["status"], "passes")
        self.assertAlmostEqual(cold["value"], self.COLD, places=6)
        self.assertEqual(cold["faces"], [self.cold])
        # The words the analysis's findings give a close or failing check (run.py hands findings() the judged checks).
        from cadgen._internal.fea.analyses.thermal import temperature_findings

        close = temperature_findings(self.result.summary["checks"], assembly=False)
        self.assertEqual([f["type"] for f in close], ["temperature_close_to_limit"])
        self.assertIn("'Hot end' gets to 100 °C, close to the 105 °C allowed", close[0]["summary"])

    def test_the_glb_says_what_it_is_for_the_viewer(self):
        extras = self.extras
        self.assertEqual(extras["analysis"]["type"], "thermal")
        self.assertEqual(extras["analysis"]["word"], "Heat")
        self.assertEqual(extras["analysis"]["reference_C"], 20.0)
        self.assertIsNone(extras["deformation_scale"])
        fields = {entry["field"]: entry for entry in extras["fields"]}
        self.assertEqual(fields["temperature"]["attribute"], "_TEMPERATURE")
        self.assertTrue(fields["temperature"]["signed"])
        self.assertEqual((fields["temperature"]["min"], fields["temperature"]["max"]), (20.0, 100.0))
        self.assertEqual(fields["heat_flux"]["units"], "W/m²")
        self.assertEqual(extras["study"]["temperatures"], [{"faces": [self.hot], "C": 100.0}, {"faces": [self.cold], "C": 20.0}])
        self.assertEqual(extras["study"]["heat"], [])
        self.assertNotIn("series", extras)
        self.assertEqual(self.sidecar["analysis"], "thermal")
        self.assertNotIn("fixtures", self.sidecar)

    def test_the_cli_says_the_hottest_and_the_heat(self):
        lines = self.result.human_lines()
        self.assertIn("(thermal)", lines[0])
        self.assertTrue(lines[1].startswith("hottest 100 °C at"))
        self.assertTrue(lines[2].startswith("heat in 66."))


@needs_fea
class TooHot(unittest.TestCase):
    """A check the slab fails: its finding reaches the result, the sidecar and the CLI, and the GLB carries no stress."""

    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        step = box_step(directory, "slab", (20.0, 10.0, 10.0), "x")
        hot, cold = ends(step)
        study = {"analysis": "thermal", "material": "aluminum-6061-t6", "mesh": {"size_mm": 5.0},
                 "temperatures": [{"faces": [hot], "C": 100}, {"faces": [cold], "C": 20}],
                 "view": {"checks": [{"kind": "temperature", "max_C": 80, "label": "Hot end"}]}}
        cls.result = solve(step, directory / "slab.glb", study)
        cls.sidecar = sidecar(cls.result)
        cls.extras = glb_extras(cls.result.glb)
        cls.attributes = glb_attribute_names(cls.result.glb)

    def test_the_failing_check_is_an_error_finding_everywhere(self):
        for findings in (self.result.findings, self.sidecar["findings"], self.extras["findings"]):
            over = [finding for finding in findings if finding["type"] == "temperature_over_limit"]
            self.assertEqual(len(over), 1)
            self.assertEqual(over[0]["severity"], "error")
            self.assertEqual(findings[0]["type"], "temperature_over_limit")  # errors first
        self.assertIn("error: 'Hot end' reaches 100 °C, hotter than the 80 °C allowed", self.result.human_lines())
        self.assertIn("check 'Hot end': 100 °C against a 80 °C limit, 1.33× it, fails", self.result.human_lines())

    def test_the_glb_carries_its_own_fields_and_no_stress(self):
        self.assertNotIn("_VON_MISES", self.attributes)
        self.assertIn("_TEMPERATURE", self.attributes)
        self.assertIn("_HEAT_FLUX", self.attributes)
        listed = {entry["attribute"] for entry in self.extras["fields"]}
        self.assertEqual(listed, {"_TEMPERATURE", "_HEAT_FLUX"})


@needs_fea
class PinFin(unittest.TestCase):
    """A fin with an insulated tip: T_tip - T_inf = (T_b - T_inf) / cosh(mL)."""

    LENGTH, SIDE, BASE_C, AIR_C, H = 50.0, 4.0, 100.0, 20.0, 50.0

    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        step = box_step(directory, "fin", (cls.LENGTH, cls.SIDE, cls.SIDE), "x")
        base, _ = ends(step)
        study = {"analysis": "thermal", "material": "aluminum-6061-t6", "mesh": {"size_mm": 2.0},
                 "temperatures": [{"faces": [base], "C": cls.BASE_C}],
                 "convection": [{"faces": side_faces(step), "h_W_m2K": cls.H, "ambient_C": cls.AIR_C}]}
        cls.result = solve(step, directory / "fin.glb", study)

    def test_the_tip_sits_where_fin_theory_says(self):
        # mm units: h in mW/(mm² K), k in mW/(mm K).
        m = math.sqrt((self.H / 1000) * 4 * self.SIDE / (ALUMINIUM.conductivity * self.SIDE ** 2))
        tip = self.AIR_C + (self.BASE_C - self.AIR_C) / math.cosh(m * self.LENGTH)
        excess = self.result.summary["min_temperature_C"] - self.AIR_C
        self.assertAlmostEqual(excess / (tip - self.AIR_C), 1.0, delta=0.02)

    def test_what_the_base_puts_in_the_air_takes_out(self):
        summary = self.result.summary
        m = math.sqrt((self.H / 1000) * 4 * self.SIDE / (ALUMINIUM.conductivity * self.SIDE ** 2))
        # Q = sqrt(hPkA) (T_b - T_inf) tanh(mL), in mW.
        q = math.sqrt((self.H / 1000) * 4 * self.SIDE * ALUMINIUM.conductivity * self.SIDE ** 2) * 80 * math.tanh(m * self.LENGTH) / 1000
        self.assertAlmostEqual(summary["heat_in_W"] / q, 1.0, delta=0.02)
        self.assertLess(summary["heat_balance"], 1e-6)


@needs_fea
class CompositeWall(unittest.TestCase):
    """Aluminium bonded to steel, end to end: the heat crosses both resistances, Q = ΔT / (L/(k_al A) + L/(k_st A))."""

    SIDE, HOT, COLD = 10.0, 100.0, 20.0

    @classmethod
    def setUpClass(cls):
        from build123d import Align, Box, Compound, Pos, export_step

        from cadgen import fea

        directory = class_directory(cls)
        hot_part = Box(cls.SIDE, cls.SIDE, cls.SIDE, align=Align.MIN)
        hot_part.label = "spreader"
        cold_part = Pos(cls.SIDE, 0, 0) * Box(cls.SIDE, cls.SIDE, cls.SIDE, align=Align.MIN)
        cold_part.label = "mount"
        step = directory / "wall.step"
        export_step(Compound(children=[hot_part, cold_part]), str(step))
        with quiet():
            refs = {part.name: part.ref for part in fea.parts(step).parts}

        def end(name, x):
            return next(face.ref for face in fea.faces(step, occurrence=refs[name]).faces
                        if face.normal and abs(abs(face.normal[0]) - 1) < 1e-6 and abs(face.center_mm[0] - x) < 1e-6)

        study = {"analysis": "thermal", "material": "aluminum-6061-t6", "parts": {"mount": {"material": "steel"}},
                 "mesh": {"size_mm": 3.0},
                 "temperatures": [{"faces": [end("spreader", 0.0)], "C": cls.HOT}, {"faces": [end("mount", 2 * cls.SIDE)], "C": cls.COLD}]}
        cls.result = solve(step, directory / "wall.glb", study)
        cls.extras = glb_extras(cls.result.glb)

    def test_the_heat_crosses_both_parts_in_series(self):
        area, length = (self.SIDE / 1000) ** 2, self.SIDE / 1000
        r_al, r_st = length / (ALUMINIUM.conductivity * area), length / (lookup_material("steel").conductivity * area)
        q = (self.HOT - self.COLD) / (r_al + r_st)
        self.assertAlmostEqual(self.result.summary["heat_in_W"] / q, 1.0, delta=0.005)
        joint = self.HOT - q * r_al
        parts = {part["name"]: part for part in self.result.summary["parts"]}
        self.assertAlmostEqual(parts["mount"]["max_temperature_C"], joint, delta=0.005 * (self.HOT - self.COLD))
        self.assertEqual(parts["mount"]["material"], "Steel (structural, generic)")
        self.assertEqual([part["name"] for part in self.extras["parts"]], ["spreader", "mount"])
        self.assertEqual(set(self.extras["parts"][0]), {"ref", "name", "material", "max_temperature_C"})


if __name__ == "__main__":
    unittest.main()
