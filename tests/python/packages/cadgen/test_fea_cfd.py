"""Flow (`"analysis": "cfd"`, lite): the study it reads, Poiseuille, the Reynolds warning and the ladder.

The pipe is a tube 16 mm long, bore radius 2 mm, wall 1 mm, along x: water
enters at x_min with the fully developed profile at a mean speed U and leaves
at x_max at 0 Pa. Laminar pipe flow (Hagen-Poiseuille) loses
Δp = 8 μ L Q / (π R⁴) and has the parabola u = 2 U (1 - r²/R²) across it, and
its wall shear is 4 μ U / R. Past Re 2000 it still solves and warns. The ladder
cases pass a tiny time target: the pipe at Re 600 takes continuation, and a
cube in a free stream takes fluid_coarsen. Every STEP is a build123d solid in a
temporary directory.
"""

from __future__ import annotations

import math
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.fea_fixtures import (
    box_step, class_directory, ends, glb_attribute_names, glb_extras, needs_fea, sidecar, solve, solve_keeping, tube_step,
    value_error,
)

R, WALL, LENGTH = 2.0, 1.0, 16.0
WATER_MU = 1.002e-3
LIMIT_TEXT = "Laminar, steady, incompressible; no turbulence model."
PIPE = {"analysis": "cfd", "mesh": {"size_mm": 1.0},
        "flow": {"kind": "internal", "fluid": "water", "inlets": [{"opening": "x_min", "velocity_m_s": 0.01}],
                 "outlets": [{"opening": "x_max", "pressure_Pa": 0}]}}


def poiseuille_Pa(flow_m3_s: float, mu: float = WATER_MU) -> float:
    return 8.0 * mu * (LENGTH / 1000) * flow_m3_s / (math.pi * (R / 1000) ** 4)


class StudyFile(unittest.TestCase):
    def parse(self, **changes):
        from cadgen._internal.fea.study import parse_study

        return parse_study({**PIPE, **changes})

    def test_it_reads_the_flow_without_a_material(self):
        parsed = self.parse()
        inputs = parsed.inputs
        self.assertIsNone(parsed.material)
        self.assertEqual((inputs.kind, inputs.fluid.name, inputs.fluid.density_kg_m3), ("internal", "water", 998.2))
        self.assertEqual([(i.opening, i.velocity_m_s, i.profile) for i in inputs.inlets], [("x_min", 0.01, "developed")])
        self.assertEqual([(o.opening, o.pressure_Pa) for o in inputs.outlets], [("x_max", 0.0)])
        self.assertFalse(inputs.mapped)
        self.assertEqual(parsed.face_refs, ())

    def test_map_to_structure_brings_its_material_fixtures_and_the_static_checks(self):
        parsed = self.parse(map_to_structure={"material": "aluminum-6061-t6", "fixtures": [{"faces": ["#o1.f1"]}]},
                            view={"checks": [{"kind": "pressure_drop", "limit_Pa": 1000}, {"kind": "stress"}]})
        self.assertTrue(parsed.inputs.mapped)
        self.assertEqual(parsed.inputs.structure_material.name, "Aluminum 6061-T6")
        self.assertEqual((parsed.face_refs, parsed.inputs.anchor_refs), (("#o1.f1",), ("#o1.f1",)))
        self.assertEqual([check["kind"] for check in parsed.checks], ["pressure_drop", "stress"])

    def test_external_flow_takes_a_free_stream(self):
        parsed = self.parse(flow={"kind": "external", "fluid": {"density_kg_m3": 1.2, "viscosity_Pa_s": 1.8e-5},
                                  "velocity_m_s": [5, 0, 0]})
        self.assertEqual((parsed.inputs.kind, parsed.inputs.velocity_m_s, parsed.inputs.fluid.name), ("external", (5.0, 0.0, 0.0), "fluid"))

    def test_the_errors_name_the_field_in_plain_words(self):
        flow = PIPE["flow"]
        for changes, fragment in (
            ({"flow": {**flow, "fluid": None}}, "flow.fluid: name the fluid"),
            ({"flow": {**flow, "fluid": "oil"}}, "is not a fluid cadgen knows"),
            ({"flow": {**flow, "inlets": [{"opening": "left", "velocity_m_s": 1}]}}, "flow.inlets[0].opening"),
            ({"flow": {**flow, "outlets": [{"opening": "x_min"}]}}, "x_min is named twice"),
            ({"flow": {**flow, "inlets": [{"opening": "x_min", "velocity_m_s": 1, "profile": "plug"}]}}, "profile"),
            ({"flow": {"kind": "external", "fluid": "air"}}, "flow.velocity_m_s"),
            ({"view": {"checks": [{"kind": "stress"}]}}, "add map_to_structure"),
            ({"map_to_structure": {"fixtures": [{"faces": ["#o1.f1"]}]}}, "map_to_structure.material"),
            ({"flow": None}, "study.flow"),
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, value_error(self, self.parse, **changes))

    def test_it_is_registered_and_built(self):
        from cadgen._internal.fea.analyses import get_analysis

        cfd = get_analysis("cfd")
        self.assertEqual((cfd.tier, cfd.word, cfd.limits), (3, "Flow", (LIMIT_TEXT,)))
        self.assertEqual(cfd.ladder, ("iterative", "fluid_coarsen", "continuation"))


class SolveBudget(unittest.TestCase):
    """Fixed bug: the flow solve's time limit was wall-clock alone (the 600 s default, or three times the estimate),
    so whether a study settled depended on the machine's load: the gallery's sharp orifice (Re 299) settled in 90
    linear solves (454 s), and at the default stopped at a residual of 1.7 after 67 of them on a machine at load 40
    to 200. The limit now never stops a solve before the linear solves its work is expected to need."""

    def test_the_budget_is_in_linear_solves_and_covers_the_orifices_continuation(self):
        from cadgen._internal.fea.analyses.cfd import CONTINUE_FROM, DEADLINE_OVER, flow_solve_budget
        from cadgen._internal.fea.navier_stokes import CONTINUATION

        self.assertGreaterEqual(flow_solve_budget(299.0), 90)       # what the orifice settled in, with room
        self.assertGreater(flow_solve_budget(299.0), 67)            # past where the loaded run was cut off
        # Above CONTINUE_FROM it counts the continuation Newton falls back to; at or below, the direct attempt alone.
        self.assertGreater(flow_solve_budget(CONTINUE_FROM + 1), 2 * flow_solve_budget(CONTINUE_FROM))
        self.assertGreaterEqual(flow_solve_budget(600.0), flow_solve_budget(299.0))
        # A planned continuation counts its stages alone, whatever the Re.
        planned = flow_solve_budget(600.0, (75.0, 150.0, 300.0, 600.0))
        self.assertEqual(planned, flow_solve_budget(5000.0, (1.0,)))
        self.assertEqual(planned, math.ceil(DEADLINE_OVER * (1 + 4 * len(CONTINUATION) + 6)))


@needs_fea
class Openings(unittest.TestCase):
    def test_an_opening_the_part_does_not_have_names_the_sides_it_does(self):
        directory = class_directory(type(self))
        study = {**PIPE, "flow": {**PIPE["flow"], "inlets": [{"opening": "y_min", "velocity_m_s": 0.01}]}}
        message = value_error(self, solve, tube_step(directory, R, WALL, LENGTH), directory / "tube.glb", study)
        self.assertIn("no opening on the low Y side (y_min)", message)
        self.assertIn("its openings are on x_min, x_max", message)


@needs_fea
class Poiseuille(unittest.TestCase):
    """Re 40 water through the tube: Hagen-Poiseuille's pressure drop, parabola and wall shear."""

    U = 0.01

    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        study = {**PIPE, "view": {"checks": [{"kind": "pressure_drop", "limit_Pa": 0.34}, {"kind": "velocity", "limit_m_s": 0.1}]}}
        cls.result, cls.own = solve_keeping("cfd", tube_step(directory, R, WALL, LENGTH), directory / "tube.glb", study)
        cls.extras, cls.attributes = glb_extras(cls.result.glb), glb_attribute_names(cls.result.glb)
        cls.summary = cls.result.summary

    def test_the_pressure_drop_is_hagen_poiseuilles(self):
        flow = self.summary["flow_rate_m3_s"]
        self.assertAlmostEqual(flow, self.U * math.pi * (R / 1000) ** 2, delta=1e-3 * flow)
        self.assertLess(self.summary["flow_balance"], 1e-3)
        expected = poiseuille_Pa(flow)
        self.assertLess(abs(self.summary["pressure_drop_Pa"] - expected) / expected, 0.03, (self.summary["pressure_drop_Pa"], expected))

    def test_the_profile_is_the_parabola(self):
        import numpy as np

        post = self.own.scalars["post"]
        where, velocity = post["velocity_locations"], post["nodal_velocity"] / 1000.0
        middle = np.abs(where[:, 0] - LENGTH / 2) < 1.0
        r = np.hypot(where[middle, 1], where[middle, 2])
        parabola = 2 * self.U * (1 - (r / R) ** 2)
        self.assertLess(float(np.abs(velocity[middle, 0] - parabola).max()), 0.03 * 2 * self.U)
        self.assertLess(float(np.abs(velocity[middle, 1:]).max()), 0.01 * 2 * self.U)

    def test_the_wall_shear_on_the_bore_is_4_mu_u_over_r(self):
        expected = 4 * WATER_MU * self.U / (R / 1000)
        self.assertLess(abs(self.summary["max_wall_shear_Pa"] - expected) / expected, 0.1)

    def test_it_is_laminar_and_says_its_limits_everywhere(self):
        reynolds = self.summary["reynolds"]
        self.assertEqual((reynolds["kind"], reynolds["limit"], reynolds["laminar"]), ("internal", 2000, True))
        self.assertAlmostEqual(reynolds["value"], 998.2 * self.U * 2 * R / 1000 / WATER_MU, delta=0.5)
        self.assertEqual(self.extras["analysis"]["limits"], [LIMIT_TEXT])
        self.assertEqual(sidecar(self.result)["limits"], [LIMIT_TEXT])
        self.assertEqual(self.extras["analysis"]["warnings"], [])
        self.assertTrue(self.summary["solve"]["converged"])
        self.assertLess(self.summary["solve"]["residual"], 1e-8)

    def test_its_tracers_move_at_the_parabolas_speed_from_the_inlet(self):
        """The viewer's Flow routine: particles let go at the inlet (x = 0), each carried along the tube at the solved
        speed at its radius, 2U(1 − r²/R²), every ``step_s`` of real time."""
        import numpy as np

        motion = self.extras["analysis"]["motion"]
        self.assertEqual((motion["kind"], motion["label"]), ("tracers", "Flow"))
        self.assertGreater(len(motion["paths"]), 32)
        for path in motion["paths"]:
            points = np.array(path).reshape(-1, 3)
            self.assertLess(points[0, 0], 0.05 * LENGTH)
            # Its mean speed over the tube's middle (points are written to 0.01 mm, so one step's is coarse).
            inside = np.flatnonzero((points[:, 0] > 0.2 * LENGTH) & (points[:, 0] < 0.8 * LENGTH))
            if len(inside) < 3:
                continue
            first, last = inside[0], inside[-1]
            r = float(np.hypot(points[inside, 1], points[inside, 2]).mean())
            if r > 0.8 * R:
                continue
            moved = (points[last, 0] - points[first, 0]) / ((last - first) * motion["step_s"]) / 1000.0      # m/s
            self.assertAlmostEqual(moved / (2 * self.U * (1 - (r / R) ** 2)), 1.0, delta=0.03)

    def test_the_glb_carries_wall_pressure_and_shear_on_the_part(self):
        fields = {entry["field"]: entry for entry in self.extras["fields"]}
        self.assertEqual(set(fields), {"pressure", "wall_shear", "speed"})
        self.assertTrue(fields["pressure"]["signed"])
        self.assertEqual((fields["pressure"]["units"], fields["wall_shear"]["units"]), ("Pa", "Pa"))
        self.assertIn("_PRESSURE", self.attributes)
        self.assertIn("_WALL_SHEAR", self.attributes)
        self.assertNotIn("_VON_MISES", self.attributes)
        self.assertEqual(self.extras["study"]["flow"]["inlets"], [{"opening": "x_min", "velocity_m_s": self.U, "profile": "developed"}])
        # The inlet end of the bore is at the drop's pressure, the outlet end near 0.
        self.assertAlmostEqual(fields["pressure"]["max"], self.summary["pressure_drop_Pa"], delta=0.1 * self.summary["pressure_drop_Pa"])

    def test_the_speed_the_velocity_check_judges_is_on_the_model(self):
        """The wall is still (no slip), so the speed field is the fastest flow within a passage width of each wetted
        point: on the bore it reads the parabola's centreline, 2 U, and its peak is the check's value."""
        import numpy as np

        fields = {entry["field"]: entry for entry in self.extras["fields"]}
        self.assertIn("_SPEED", self.attributes)
        self.assertEqual((fields["speed"]["units"], fields["speed"]["min"]), ("m/s", 0.0))
        velocity = next(check for check in self.summary["checks"] if check["kind"] == "velocity")
        self.assertAlmostEqual(fields["speed"]["max"], velocity["value"], delta=1e-5 * velocity["value"])
        self.assertEqual(self.summary["max_speed_near_wall_m_s"], fields["speed"]["max"])
        self.assertAlmostEqual(self.summary["speed_reach_mm"], 2 * R, delta=0.05 * R)   # the bore's diameter
        speed, where = self.own.fields["speed"], self.own.dof_locations
        r = np.hypot(where[:, 1], where[:, 2])
        bore = (np.abs(r - R) < 0.05) & (np.abs(where[:, 0] - LENGTH / 2) < 4.0)
        outside = np.abs(r - (R + WALL)) < 0.05
        self.assertTrue(bore.any() and outside.any())
        self.assertLess(float(np.abs(speed[bore] - 2 * self.U).max()), 0.05 * 2 * self.U)
        self.assertEqual(float(np.abs(speed[outside]).max()), 0.0)    # the outside is not wetted

    def test_the_checks_and_the_cli(self):
        checks = {check["kind"]: check for check in self.summary["checks"]}
        self.assertEqual(checks["pressure_drop"]["unit"], "Pa")
        self.assertEqual(checks["pressure_drop"]["status"], "close")
        self.assertEqual(checks["velocity"]["status"], "passes")
        lines = "\n".join(self.result.human_lines())
        self.assertIn("internal flow of water: Re", lines)
        self.assertIn("pressure drop", lines)
        self.assertIn("(cfd)", lines)


def duct_step(directory: Path, width: float, height: float, length: float = 16.0) -> Path:
    """A rectangular duct along x: a width x height passage through a block with 1 mm walls."""
    from build123d import Align, Box, export_step

    step = directory / f"duct_{width:g}x{height:g}.step"
    along = (Align.MIN, Align.CENTER, Align.CENTER)
    export_step(Box(length, width + 2, height + 2, align=along) - Box(length, width, height, align=along), str(step))
    return step


def rectangular_duct_Pa(flow_m3_s: float, width_mm: float, height_mm: float, length_mm: float = 16.0,
                        mu: float = WATER_MU) -> float:
    """Laminar developed flow through a W x H rectangle (Shah & London's series): Q = W H^3 dp / (12 mu L) times
    1 - (192 H / pi^5 W) sum over odd n of tanh(n pi W / 2H) / n^5, with H the shorter side (f Re = 56.91 square)."""
    w, h = max(width_mm, height_mm) / 1000, min(width_mm, height_mm) / 1000
    series = sum(math.tanh(n * math.pi * w / (2 * h)) / n ** 5 for n in range(1, 40, 2))
    shape = 1 - 192 * h / (math.pi ** 5 * w) * series
    return 12 * mu * (length_mm / 1000) * flow_m3_s / (w * h ** 3 * shape)


@needs_fea
class SharpCorneredDucts(unittest.TestCase):
    """Rectangular passages solve: the inlet's corners, where every velocity around a pressure node is held,
    used to make the flow's matrix exactly singular. A square and a 2:1 duct against Shah & London."""

    def test_a_square_and_a_two_to_one_duct_lose_shah_and_londons_pressure(self):
        directory = class_directory(type(self))
        for width, height in ((3.0, 3.0), (4.0, 2.0)):
            summary = solve(duct_step(directory, width, height), directory / "duct.glb", PIPE).summary
            with self.subTest(width=width, height=height):
                self.assertTrue(summary["solve"]["converged"])
                flow = summary["flow_rate_m3_s"]
                self.assertAlmostEqual(flow, 0.01 * width * height * 1e-6, delta=1e-3 * flow)
                expected = rectangular_duct_Pa(flow, width, height)
                self.assertLess(abs(summary["pressure_drop_Pa"] - expected) / expected, 0.05, (summary["pressure_drop_Pa"], expected))

    def test_an_unreached_pressure_takes_its_neighbours_value(self):
        import numpy as np
        from scipy import sparse

        from cadgen._internal.fea.navier_stokes import fill_unreached_pressures, unreached_pressures

        # Pressure 0 touches only velocity 0 (held); pressures 1 and 2 touch the free velocity 1.
        B = sparse.csr_matrix(np.array([[1.0, 0.0], [0.5, 1.0], [0.0, 2.0]]))
        unreached = unreached_pressures(B, np.array([0]))
        self.assertEqual(unreached.tolist(), [0])
        filled = fill_unreached_pressures(np.array([0.0, 4.0, 6.0]), unreached, np.array([[0], [1], [2]]))
        self.assertEqual(filled.tolist(), [5.0, 4.0, 6.0])


@needs_fea
class PastLaminar(unittest.TestCase):
    """Re 2400 in the same tube: it solves (never refuses) and warns, in words and as data."""

    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        study = {**PIPE, "flow": {**PIPE["flow"], "inlets": [{"opening": "x_min", "velocity_m_s": 0.6}]}}
        cls.result = solve(tube_step(directory, R, WALL, LENGTH), directory / "fast.glb", study)
        cls.extras = glb_extras(cls.result.glb)

    def test_it_solves_and_warns(self):
        self.assertTrue(self.result.ok)
        reynolds = self.result.summary["reynolds"]
        self.assertGreater(reynolds["value"], 2000)
        self.assertEqual((reynolds["limit"], reynolds["laminar"]), (2000, False))
        self.assertTrue(self.result.summary["solve"]["converged"])
        sentence = self.extras["analysis"]["warnings"][0]
        self.assertIn("is past the laminar range", sentence)
        self.assertIn("real flow is likely turbulent, so this pressure drop is a lower bound", sentence)
        self.assertIn("reynolds_past_laminar", [finding["type"] for finding in self.result.findings])
        self.assertIn("past the laminar range", "\n".join(self.result.human_lines()))
        # The structured number rides beside the sentence in extras.analysis, which the viewer reads first.
        carried = self.extras["analysis"]["reynolds"]
        self.assertEqual({k: carried[k] for k in ("value", "limit", "kind")}, {k: reynolds[k] for k in ("value", "limit", "kind")})


@needs_fea
class Ladder(unittest.TestCase):
    """A tiny time target: the pipe at Re 600 reaches its Re by continuation, and Poiseuille holds within 5 %;
    a cube in a free stream coarsens its far field, its part kept at the size asked for."""

    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        study = {**PIPE, "flow": {**PIPE["flow"], "inlets": [{"opening": "x_min", "velocity_m_s": 0.15}]},
                 "fit": {"seconds": 0.5, "allow": ["fluid_coarsen", "continuation"]}}
        cls.pipe = solve(tube_step(directory, R, WALL, LENGTH), directory / "pipe.glb", study)
        cls.pipe_extras = glb_extras(cls.pipe.glb)
        study = {"analysis": "cfd", "mesh": {"size_mm": 2.0}, "flow": {"kind": "external", "fluid": "air", "velocity_m_s": [0.1, 0, 0]},
                 "fit": {"seconds": 0.5, "allow": ["fluid_coarsen"]}}
        cls.cube = solve(box_step(directory, "cube", (4, 4, 4)), directory / "cube.glb", study)
        cls.cube_extras = glb_extras(cls.cube.glb)

    def test_continuation_reaches_re_600_and_poiseuille_holds(self):
        rungs = [step["rung"] for step in self.pipe.fit]
        self.assertIn("continuation", rungs)
        step = next(step for step in self.pipe.fit if step["rung"] == "continuation")
        self.assertIn("Reached Re 598 in steps", step["words"])
        self.assertEqual(self.pipe_extras["fit"][rungs.index("continuation")]["words"], step["words"])
        self.assertIn("fit_continuation", [finding["type"] for finding in self.pipe.findings])
        self.assertIn(f"adapted: {step['words']}", self.pipe.human_lines())
        solve_info = self.pipe.summary["solve"]
        self.assertTrue(solve_info["continuation"])
        self.assertEqual(len(solve_info["stages_Re"]), 4)
        expected = poiseuille_Pa(self.pipe.summary["flow_rate_m3_s"])
        self.assertLess(abs(self.pipe.summary["pressure_drop_Pa"] - expected) / expected, 0.05)

    def test_fluid_coarsen_keeps_the_part_fine_and_says_what_it_gave_up(self):
        step = next(step for step in self.cube.fit if step["rung"] == "fluid_coarsen")
        self.assertIn("the part is still meshed at 2 mm, the free stream at 16 mm", step["words"])
        self.assertTrue(step["accuracy"])
        self.assertEqual(self.cube.summary["fluid_mesh"]["far_size_mm"], 16.0)
        self.assertIn("fit_fluid_coarsen", [finding["type"] for finding in self.cube.findings])

    def test_the_free_stream_pushes_the_cube_downstream(self):
        force = self.cube.summary["force_N"]
        self.assertGreater(force[0], 0)
        self.assertLess(max(abs(force[1]), abs(force[2])), 0.2 * force[0])
        self.assertEqual(self.cube.summary["reynolds"]["kind"], "external")
        self.assertEqual(self.cube_extras["study"]["flow"], {"kind": "external", "velocity_m_s": [0.1, 0.0, 0.0],
                                                             "fluid": {"name": "air", "density_kg_m3": 1.204, "viscosity_Pa_s": 1.81e-05}})


def orifice_step(directory: Path, length: float = 10.0) -> Path:
    """A 6 mm bore through a 10 x 10 mm block along x, closed down to 3 mm by a sharp 2 mm thick plate at its middle."""
    from build123d import Box, Cylinder, Rotation, export_step

    step = directory / "orifice.step"
    along = Rotation(0, 90, 0)
    block = Box(length, 10, 10) - along * Cylinder(3, length)
    export_step(block + along * (Cylinder(3, 2) - Cylinder(1.5, 2)), str(step))
    return step


@needs_fea
class SharpOrifice(unittest.TestCase):
    """Re 300 through a sharp orifice (a jet at Re 600 through it): it settles to 1e-8. Newton's Jacobian used to
    leave out the streamline diffusion's derivative, so Newton converged only linearly and diverged past Re ~100
    here; the gallery's orifice stopped at a residual of 4.4 after 369 Picard and 69 Newton steps, its checks
    passing. A solve short of its tolerance stops at its deadline, says so, and its checks are not passes."""

    @classmethod
    def setUpClass(cls):
        from cadgen._internal.fea import navier_stokes

        directory = class_directory(cls)
        study = {**PIPE, "mesh": {"size_mm": 1.25},
                 "flow": {**PIPE["flow"], "inlets": [{"opening": "x_min", "velocity_m_s": 0.05}]},
                 "view": {"checks": [{"kind": "pressure_drop", "limit_Pa": 1000}]}}
        original, cls.problems, cls.options = navier_stokes.solve_flow, [], []

        def keep(problem, **options):
            cls.problems.append(problem)
            cls.options.append(options)
            return original(problem, **options)

        with mock.patch.object(navier_stokes, "solve_flow", keep):
            cls.result = solve(orifice_step(directory), directory / "orifice.glb", study)

    def test_re_300_settles_to_the_tolerance(self):
        solve_info = self.result.summary["solve"]
        self.assertGreater(self.result.summary["reynolds"]["value"], 290)
        self.assertTrue(solve_info["converged"])
        self.assertLess(solve_info["residual"], 1e-8)
        self.assertFalse(any("approximate" in warning for warning in self.result.warnings))
        self.assertEqual(self.result.summary["checks"][0]["status"], "passes")
        self.assertNotIn("settled", self.result.summary["checks"][0])
        # The orifice loses far more than the straight bore would (Poiseuille through 6 mm over the 10 mm).
        straight = 8 * WATER_MU * 0.010 * self.result.summary["flow_rate_m3_s"] / (math.pi * 0.003 ** 4)
        self.assertGreater(self.result.summary["pressure_drop_Pa"], 5 * straight)

    def test_newton_converges_quadratically_at_re_300_and_says_where_it_is(self):
        """With the whole Jacobian, Newton's last steps at the target square the residual (linear convergence cut
        it by about half a step). Whether it gets there straight from the Stokes flow or by continuation is not
        asserted: the damped steps from Stokes at Re 300 follow the mesh to the last bit (netgen's mesh of this
        orifice differs between Apple silicon, x86 macOS and Linux), so one build goes straight in 16 Newton steps
        and another runs out of its 25 and steps the Reynolds number up."""
        import re

        from cadgen._internal.fea import navier_stokes

        lines: list[str] = []
        with mock.patch.object(navier_stokes, "PROGRESS_S", 0.0):
            flow = navier_stokes.solve_flow(self.problems[0], log=lines.append)
        self.assertTrue(flow.converged)
        self.assertEqual(flow.stages[-1], 1.0)
        # Every step said where it was: the progress line a long solve prints.
        self.assertIn(f"flow: Newton step {flow.newton} at 1 of the target Re", "\n".join(lines))
        # The target's last Newton steps: once under 1e-2, each cuts the residual by fifty times or more.
        last = [float(found.group(1)) for line in lines
                if (found := re.match(r"flow: Newton step \d+ at 1 of the target Re, residual (\S+) ", line))]
        tail = last[next(i for i, r in enumerate(last) if r < 1e-2 and all(later < 1e-2 for later in last[i:])):]
        self.assertGreaterEqual(len(tail), 2, last)
        self.assertTrue(all(after < 0.02 * before for before, after in zip(tail, tail[1:])), last)

    def test_at_its_deadline_it_stops_at_the_target_and_says_so(self):
        from cadgen._internal.fea import navier_stokes
        from cadgen._internal.fea.analyses.cfd import unsettled_check

        flow = navier_stokes.solve_flow(self.problems[0], deadline_s=0.0)
        self.assertEqual((flow.converged, flow.timed_out, flow.picard + flow.newton), (False, True, 0))
        self.assertEqual(flow.stages[-1], 1.0)
        self.assertGreater(flow.residual, 1e-3)    # measured at the target Re, from the Stokes flow
        self.assertIn("stopped at its time limit (0 s)", flow.warnings[-1])
        self.assertIn("its numbers are approximate", navier_stokes.unsettled_sentence(flow))
        # A check on that flow is not a pass: labelled, held at close; a failure stays one.
        judged = unsettled_check({"label": "Flow resistance", "status": "passes", "ratio": 0.4}, flow.converged)
        self.assertEqual((judged["label"], judged["status"], judged["settled"]),
                         ("Flow resistance (flow not settled)", "close", False))
        self.assertEqual(unsettled_check({"label": "x", "status": "fails", "ratio": 2}, False)["status"], "fails")

    def test_its_deadline_waits_for_the_work_it_was_given(self):
        """A deadline long past (a loaded machine) does not stop the solve before ``min_solves`` linear solves; the
        study's own solve is given its budget."""
        from cadgen._internal.fea import navier_stokes
        from cadgen._internal.fea.analyses.cfd import flow_solve_budget

        flow = navier_stokes.solve_flow(self.problems[0], deadline_s=0.0, min_solves=4)
        self.assertGreaterEqual(flow.linear_solves, 4)
        self.assertGreater(flow.picard + flow.newton, 0)
        if not flow.converged:
            self.assertTrue(flow.timed_out)
            self.assertIn(f"stopped at its time limit (0 s, {flow.linear_solves} linear solves)", flow.warnings[-1])
        reynolds = self.result.summary["reynolds"]["value"]
        self.assertEqual(self.options[0]["min_solves"], flow_solve_budget(reynolds))


@needs_fea
class MappedToStructure(unittest.TestCase):
    """A thick fluid (10 Pa s) pushes on the bore at a few kPa; held at its outlet end, the tube is stressed by it."""

    @classmethod
    def setUpClass(cls):
        directory = class_directory(cls)
        step = tube_step(directory, R, WALL, LENGTH)
        outlet_end = ends(step)[1]
        study = {**PIPE, "flow": {**PIPE["flow"], "fluid": {"name": "syrup", "density_kg_m3": 1000, "viscosity_Pa_s": 10}},
                 "map_to_structure": {"material": "aluminum-6061-t6", "fixtures": [{"faces": [outlet_end]}]},
                 "view": {"checks": [{"kind": "pressure_drop", "limit_Pa": 1e5}, {"kind": "stress"}]}}
        cls.result = solve(step, directory / "mapped.glb", study)
        cls.extras, cls.attributes = glb_extras(cls.result.glb), glb_attribute_names(cls.result.glb)

    def test_the_wall_pressure_stresses_the_part(self):
        summary = self.result.summary
        drop = summary["pressure_drop_Pa"]
        self.assertLess(abs(drop - poiseuille_Pa(summary["flow_rate_m3_s"], mu=10.0)) / drop, 0.03)
        # A thick-walled tube under its inlet pressure: the bore's hoop stress is p (Ro² + R²) / (Ro² - R²).
        hoop = drop / 1e6 * ((R + WALL) ** 2 + R ** 2) / ((R + WALL) ** 2 - R ** 2)
        self.assertGreater(summary["max_von_mises_MPa"], 0.5 * hoop)
        self.assertLess(summary["max_von_mises_MPa"], 3.0 * hoop)
        self.assertEqual(summary["yield_MPa"], 276.0)
        kinds = [check["kind"] for check in summary["checks"]]
        self.assertEqual(kinds, ["pressure_drop", "stress"])
        self.assertEqual(summary["checks"][1]["status"], "passes")
        self.assertEqual(self.extras["study"]["material"]["name"], "Aluminum 6061-T6")
        self.assertEqual(len(self.extras["study"]["fixtures"]), 1)
        self.assertIsNotNone(self.extras["deformation_scale"])
        self.assertFalse(any("do not balance" in warning for warning in self.result.warnings))

    def test_the_mapped_stress_is_in_the_file_it_lists(self):
        # extras.fields lists von Mises, so the GLB carries it, though the colours are the wall pressure.
        listed = {entry["attribute"] for entry in self.extras["fields"]}
        self.assertIn("_VON_MISES", listed)
        self.assertTrue(listed <= set(self.attributes))


if __name__ == "__main__":
    unittest.main()
