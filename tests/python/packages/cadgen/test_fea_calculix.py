"""CalculiX for a linear static solve: used when ``ccx`` is installed, the same answer as the built-in solver.

Without ccx:
- the C3D10 connectivity turns every element to a positive volume and puts each mid-edge node at its corner pair's
  midpoint (a straight mesh) or on its own bowed edge (a curved one), whichever way skfem listed it;
- the .inp holds the nodes, one element set and material per part, every held DOF, the loads on the free DOF only,
  and no number longer than the 20 characters CalculiX reads, no line longer than its 132;
- the .dat reader reads each block, Fortran's bare three-digit exponent included;
- the gate: an orthotropic material or a thermal strain stays on the built-in solver without a word, and so does
  everything when ``CADGEN_CCX=0``; a CalculiX asked for and not run, or one that fails, says so in one warning;
- the module is stdlib-only at import;
- the command line's first line names the solver that ran.
With ccx at hand (``CADGEN_CCX``, cadgen's cached download or PATH; skipped otherwise), on the same mesh and loads:
- a cantilever under a force, a pressure and gravity, on a clamp and a roller, agrees with the built-in direct solve
  to 0.005 % in displacement and peak von Mises (the two's agreement measured on a real part), and its reactions
  balance the load;
- a two-material assembly agrees part by part;
- the same study solves to the same bytes every run (one thread);
- a study solved end to end says CalculiX ran, in its sidecar and its first line;
- a mesh CalculiX reads differently is caught by the stress cross-check and solved by the built-in solver instead.
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.fea_fixtures import HAVE_FEA, box_volume, heavy_at_import, needs_fea

from cadgen._internal.fea import calculix  # noqa: E402

#: CalculiX and the built-in solver agree to 0.005 % on one mesh (measured on a real part's curved mesh; a straight
#: one like these agrees to CalculiX's seven printed digits).
AGREEMENT = 5e-5
ORDINAL_OF = {f"#o1.f{i}": i for i in range(1, 7)}


def _steel():
    from cadgen._internal.fea.materials import Material

    return Material("steel", 200000.0, 0.3, 250.0, 7.85e-9)


def _aluminium():
    from cadgen._internal.fea.materials import Material

    return Material("6061", 68900.0, 0.33, 276.0, 2.7e-9)


def _ccx_found():
    return calculix.find_ccx()[0] if HAVE_FEA else None


def _fake_ccx(directory: Path) -> Path:
    """A program that says it is CalculiX and fails every job: what a broken install looks like."""
    script = directory / "ccx"
    script.write_text('#!/bin/sh\nif [ "$1" = "-v" ]; then echo "This is Version 9.99"; exit 0; fi\n'
                      'echo " *ERROR in fake: no solver here"\nexit 201\n', encoding="ascii")
    script.chmod(0o755)
    return script


def _cantilever(cells=(20, 2, 2)):
    """The 100 x 10 x 10 mm box clamped at x = 0 (face 1), a roller on y = 0 (face 3), 1 kN down on x = 100 (face 2),
    0.5 MPa on its top (face 6) and 10 g of gravity: every kind of load and hold the static solve takes."""
    from cadgen._internal.fea.study import Fixture, Load

    volume = box_volume((100.0, 10.0, 10.0), cells)
    fixtures = (Fixture(("#o1.f1",)), Fixture(("#o1.f3",), "roller"))
    loads = (Load(("#o1.f2",), "force", vector=(0.0, 0.0, -1000.0)), Load(("#o1.f6",), "pressure", pressure=0.5))
    return volume, _steel(), fixtures, loads, ORDINAL_OF, {"body_loads": [(0.0, 0.0, -98066.5)]}


def _static(volume, material, fixtures, loads, ordinal_of, extra, **more):
    from cadgen._internal.fea import solve

    return solve.solve_linear_static(volume, material, fixtures, loads, ordinal_of, **{**extra, **more})


class Import(unittest.TestCase):
    def test_the_module_is_stdlib_only_at_import(self):
        self.assertEqual(heavy_at_import("cadgen._internal.fea.calculix"), "")

    def test_the_command_line_names_the_solver_that_ran(self):
        from cadgen.results import FeaResult

        result = FeaResult(ok=True, document=Path("part.step"), occurrence="#o1", glb=Path("part.fea.glb"),
                           sidecar=Path("part.fea.json"),
                           mesh={"elements": 480, "dofs": 3075, "size_mm": 5.0, "solver": "calculix 2.23 (C3D10)"})
        first = result.human_lines()[0]
        self.assertTrue(first.endswith(": 480 tets, 3075 DOF, 5.0 mm elements; solver calculix 2.23 (C3D10)"), first)


@needs_fea
class Connectivity(unittest.TestCase):
    def _check(self, space, curved: bool):
        import numpy as np

        nodes, turned = calculix.c3d10_connectivity(space)
        x = space.dof_locations
        a, b, c, d = (x[nodes[:, k]] for k in range(4))
        self.assertTrue((np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) > 0).all())
        self.assertTrue(turned.any() and not turned.all(), "skfem lists elements both ways round")
        for slot, (i, j) in enumerate(calculix.C3D10_EDGES, start=4):
            midpoint = 0.5 * (x[nodes[:, i]] + x[nodes[:, j]])
            gap = np.linalg.norm(x[nodes[:, slot]] - midpoint, axis=1)
            if not curved:
                self.assertLess(float(gap.max()), 1e-12)
            # Bowed or not, each mid-edge node is nearer its own pair's midpoint than any other pair's.
            others = [np.linalg.norm(x[nodes[:, slot]] - 0.5 * (x[nodes[:, p]] + x[nodes[:, q]]), axis=1)
                      for p, q in calculix.C3D10_EDGES if (p, q) != (i, j)]
            self.assertTrue((gap < np.min(others, axis=0)).all())
        return nodes

    def test_corners_turn_to_a_positive_volume_and_mid_edge_nodes_sit_on_their_pairs(self):
        from cadgen._internal.fea.femspace import FemSpace

        space = FemSpace.build(box_volume((4.0, 1.0, 1.0), (4, 1, 1)))
        self._check(space, curved=False)
        self.assertTrue(calculix.straight_elements(space).all())

    def test_a_bowed_mid_edge_node_still_goes_to_its_own_edge(self):
        import numpy as np

        from cadgen._internal.fea.femspace import FemSpace

        volume = box_volume((4.0, 1.0, 1.0), (4, 1, 1))
        nodes = volume.nodes.copy()
        top = np.unique(volume.boundary[volume.boundary_ordinal == 6][:, 3:])   # the top face's mid-edge nodes
        nodes[top, 2] += 0.05                                                    # bowed up, as onto a curved face
        space = FemSpace.build(dataclasses.replace(volume, nodes=nodes))
        self._check(space, curved=True)
        straight = calculix.straight_elements(space)
        self.assertTrue(straight.any() and not straight.all())


@needs_fea
class InputFile(unittest.TestCase):
    def test_the_inp_holds_the_mesh_parts_holds_and_free_loads_in_calculix_widths(self):
        import numpy as np

        from cadgen._internal.fea.femspace import FemSpace

        volume = box_volume((4.0, 1.0, 1.0), (4, 1, 1))
        middle = volume.nodes[volume.tets[:, :4]].mean(axis=1)[:, 0]
        volume = dataclasses.replace(volume, domain=(middle > 2.0).astype(np.int64))
        space = FemSpace.build(volume)
        fixed = np.flatnonzero(np.isclose(space.locations[:, 0], 0.0))
        f = np.zeros(space.dofs)
        f[np.flatnonzero(np.isclose(space.locations[:, 0], 4.0) & (space.component == 2))] = -1.0 / 3.0
        f[fixed[:3]] = 7.0                                   # a load on a held DOF is not written
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "job.inp"
            calculix.write_inp(path, space, [_steel(), _aluminium()], f, fixed)
            text = path.read_text(encoding="ascii")
        lines = text.splitlines()
        self.assertLessEqual(max(len(line) for line in lines), 132)
        for line in lines:
            if not line.startswith("*"):
                self.assertTrue(all(len(field.strip()) <= 20 for field in line.split(",")), line)

        def block(keyword):
            start = next(i for i, line in enumerate(lines) if line.startswith(keyword))
            end = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("*"))
            return [line.split(",") for line in lines[start + 1:end]]

        coordinates = np.array([[float(v) for v in row[1:]] for row in block("*NODE,")])
        self.assertLess(float(np.abs(coordinates - space.dof_locations).max()), 1e-12)
        self.assertEqual(text.count("*ELEMENT, TYPE=C3D10"), 2)
        self.assertIn("*SOLID SECTION, ELSET=PART1, MATERIAL=MATERIAL1", text)
        self.assertIn("6.890000000000e+04, 3.300000000000e-01", text)
        self.assertEqual(len(block("*BOUNDARY")), len(fixed))
        loads = block("*CLOAD")
        self.assertEqual(len(loads), int((f[np.setdiff1d(np.arange(space.dofs), fixed)] != 0).sum()))
        self.assertAlmostEqual(sum(float(row[2]) for row in loads), -1.0 / 3.0 * len(loads), places=12)
        self.assertEqual({int(row[1]) for row in loads}, {3})
        self.assertEqual(text.count("\nS\n"), 2)                # each part's stresses printed

    def test_the_dat_reader_reads_each_block_and_a_bare_three_digit_exponent(self):
        dat = """
                        S T E P       1

 displacements (vx,vy,vz) for set NALL and time  0.1000000E+01

         1  0.000000E+00  1.000000E-03 -2.500000E-01
         2  1.234567-100  0.000000E+00  3.000000E+00

 forces (fx,fy,fz) for set NHELD and time  0.1000000E+01

         1  1.000000E+01 -2.000000E+00  0.000000E+00

 stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set PART0 and time  0.1000000E+01

         1   1  1.0E+00  2.0E+00  3.0E+00  4.0E+00  5.0E+00  6.0E+00

 stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz) for set PART1 and time  0.1000000E+01

         2   4 -1.0E+00 -2.0E+00 -3.0E+00 -4.0E+00 -5.0E+00 -6.0E+00
"""
        printed = calculix.read_dat(dat)
        self.assertEqual(printed["U"].shape, (2, 4))
        self.assertEqual(printed["U"][1, 1], 1.234567e-100)
        self.assertEqual(printed["RF"].tolist(), [[1.0, 10.0, -2.0, 0.0]])
        self.assertEqual(printed["S"][:, :2].tolist(), [[1.0, 1.0], [2.0, 4.0]])


@needs_fea
class Gate(unittest.TestCase):
    """What goes to CalculiX and what stays on the built-in solver, with a ccx that fails if it is ever run."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.fake = str(_fake_ccx(Path(cls.tmp.name)))

    def _solve(self, setting: str, **more):
        with mock.patch.dict(os.environ, {calculix.CCX_ENV: setting}):
            volume, material, fixtures, loads, ordinal_of, extra = _cantilever()
            return _static(volume, more.pop("material", material), fixtures, loads, ordinal_of, extra, **more)

    @unittest.skipIf(sys.platform == "win32", "the stand-in ccx is a shell script")
    def test_a_ccx_that_fails_gives_one_warning_and_the_built_in_answer(self):
        outcome = self._solve(self.fake)
        self.assertFalse(outcome.solver.startswith("calculix"), outcome.solver)
        self.assertEqual(outcome.warnings,
                         ["CalculiX failed (*ERROR in fake: no solver here): solved with the built-in solver"])
        with mock.patch.dict(os.environ, {calculix.CCX_ENV: self.fake}):
            self.assertEqual(calculix.find_ccx()[0].label, "calculix 9.99 (C3D10)")

    @unittest.skipIf(sys.platform == "win32", "the stand-in ccx is a shell script")
    def test_an_orthotropic_material_or_a_thermal_strain_stays_in_house_without_a_word(self):
        import numpy as np

        from cadgen._internal.fea.femspace import FemSpace

        orthotropic = dataclasses.replace(_steel(), orthotropic={
            "E1_MPa": 200000.0, "E2_MPa": 100000.0, "E3_MPa": 100000.0, "nu12": 0.3, "nu13": 0.3, "nu23": 0.3,
            "G12_MPa": 50000.0, "G13_MPa": 50000.0, "G23_MPa": 40000.0})
        space = FemSpace.build(_cantilever()[0])
        strain = np.full(space.basis.dx.shape, 1e-4)
        cases = (("orthotropic", {"material": orthotropic}), ("thermal strain", {"initial_strain": strain, "space": space}))
        for case, more in cases:
            with self.subTest(case=case), mock.patch.object(calculix, "solve_static", side_effect=AssertionError("ran ccx")):
                outcome = self._solve(self.fake, **more)
                self.assertFalse(outcome.solver.startswith("calculix"))
                self.assertEqual(outcome.warnings, [])
        self.assertEqual(calculix.declines(space, orthotropic), "a material is orthotropic or layered")
        self.assertEqual(calculix.declines(space, _steel(), initial_strain=strain),
                         "the study has an initial (thermal) strain")
        self.assertEqual(calculix.declines(space, _steel(), solver="iterative"), "the iterative solver was asked for")
        self.assertEqual(calculix.declines(space, _steel(), supports=mock.Mock(turned=True)),
                         "a roller holds a sloped or curved face")
        self.assertIsNone(calculix.declines(space, [_steel(), _aluminium()]))
        with mock.patch.object(calculix, "DOF_LIMIT", space.dofs - 1):
            self.assertIn("DOF is past", calculix.declines(space, _steel()))

    def test_ccx_0_turns_calculix_off_and_says_so_only_when_asked_for(self):
        with mock.patch.dict(os.environ, {calculix.CCX_ENV: "0"}):
            self.assertEqual(calculix.find_ccx(), (None, ""))
        with mock.patch.object(calculix, "solve_static", side_effect=AssertionError("ran ccx")):
            quiet = self._solve("0")
            asked = self._solve("0", solver="calculix")
        self.assertEqual(quiet.warnings, [])
        self.assertFalse(quiet.solver.startswith("calculix"))
        self.assertEqual(asked.warnings,
                         ["CalculiX was asked for, but CADGEN_CCX=0 turns it off: solved with the built-in solver"])

    def test_a_ccx_setting_that_names_no_program_says_so(self):
        missing = str(Path(self.tmp.name) / "no-such-ccx")
        outcome = self._solve(missing)
        self.assertEqual(outcome.warnings,
                         [f"CADGEN_CCX={missing} is not a CalculiX program that runs: solved with the built-in solver"])


@needs_fea
@unittest.skipUnless(_ccx_found(), "CalculiX (ccx) is not installed; set CADGEN_CCX to its path to run these")
class AgainstBuiltIn(unittest.TestCase):
    """The same mesh, loads and holds through CalculiX and through the built-in direct solver."""

    def _both(self, volume, material, fixtures, loads, ordinal_of, extra):
        from cadgen._internal.fea.femspace import FemSpace

        extra = {**extra, "space": FemSpace.build(volume)}
        ours = _static(volume, material, fixtures, loads, ordinal_of, extra, solver="direct")
        theirs = _static(volume, material, fixtures, loads, ordinal_of, extra)
        self.assertTrue(theirs.solver.startswith("calculix"), theirs.solver)
        self.assertEqual(theirs.warnings, [])
        return ours, theirs

    def assertClose(self, ours: float, theirs: float, scale: float | None = None):
        self.assertLessEqual(abs(theirs - ours), AGREEMENT * (scale if scale is not None else abs(ours)), (ours, theirs))

    def test_a_cantilever_under_every_load_agrees_and_its_reactions_balance(self):
        import numpy as np

        ours, theirs = self._both(*_cantilever())
        moved = float(np.abs(ours.displacement).max())
        self.assertLessEqual(float(np.abs(theirs.displacement - ours.displacement).max()), AGREEMENT * moved)
        self.assertClose(float(ours.von_mises.max()), float(theirs.von_mises.max()))
        self.assertClose(ours.von_mises_gauss_max, theirs.von_mises_gauss_max)
        self.assertLessEqual(float(np.abs(theirs.von_mises - ours.von_mises).max()), AGREEMENT * float(ours.von_mises.max()))
        applied = float(np.linalg.norm(ours.applied))
        self.assertEqual(theirs.applied, ours.applied)
        for mine, its in zip(ours.reactions, theirs.reactions):
            for c in range(3):
                self.assertClose(mine[c], its[c], applied)
        balance = np.sum(theirs.reactions, axis=0) + np.asarray(theirs.applied)
        self.assertLessEqual(float(np.abs(balance).max()), AGREEMENT * applied)

    def test_a_two_material_assembly_agrees_part_by_part(self):
        import numpy as np

        volume, _, fixtures, loads, ordinal_of, extra = _cantilever()
        middle = volume.nodes[volume.tets[:, :4]].mean(axis=1)[:, 0]
        volume = dataclasses.replace(volume, domain=(middle > 50.0).astype(np.int64))
        ours, theirs = self._both(volume, [_steel(), _aluminium()], fixtures, loads, ordinal_of, extra)
        moved = float(np.abs(ours.displacement).max())
        self.assertLessEqual(float(np.abs(theirs.displacement - ours.displacement).max()), AGREEMENT * moved)
        for part in range(2):
            self.assertClose(float(ours.von_mises_parts[part].max()), float(theirs.von_mises_parts[part].max()))

    def test_iterative_to_finish_sooner_gives_way_to_calculix_and_to_fit_in_memory_does_not(self):
        # The fit ladder's iterative rung names the built-in solver it picked; most real parts take it for time.
        sooner = _static(*_cantilever(), solver="iterative", calculix_first=True)
        memory = _static(*_cantilever(), solver="iterative")
        self.assertTrue(sooner.solver.startswith("calculix"), sooner.solver)
        self.assertFalse(memory.solver.startswith("calculix"), memory.solver)

    def test_the_same_study_solves_to_the_same_bytes_every_run(self):
        # One thread: on several, CalculiX's sums land in a different order each run.
        first, again = _static(*_cantilever((40, 4, 4))), _static(*_cantilever((40, 4, 4)))
        self.assertTrue(first.solver.startswith("calculix"), first.solver)
        self.assertEqual(first.displacement.tobytes(), again.displacement.tobytes())
        self.assertEqual(first.von_mises.tobytes(), again.von_mises.tobytes())

    def test_a_solved_study_says_calculix_ran_in_its_sidecar_and_first_line(self):
        from tests.python.support.fea_fixtures import box_step, ends, sidecar, solve

        with tempfile.TemporaryDirectory() as name:
            step = box_step(Path(name), "bar", (60.0, 10.0, 10.0), "x")
            fixed, loaded = ends(step)
            result = solve(step, Path(name) / "bar.fea.glb", {
                "material": "steel", "mesh": {"size_mm": 5.0}, "fixtures": [{"faces": [fixed]}],
                "loads": [{"faces": [loaded], "type": "force", "vector_N": [0, 0, -100]}]})
            written = sidecar(result)
        self.assertTrue(written["mesh"]["solver"].startswith("calculix"), written["mesh"])
        self.assertEqual(written["warnings"], [])
        self.assertTrue(result.human_lines()[0].endswith(f"; solver {written['mesh']['solver']}"), result.human_lines()[0])

    def test_a_mesh_calculix_reads_differently_falls_back_to_the_built_in_solver(self):
        real = calculix.c3d10_connectivity

        def swapped(space):
            nodes, turned = real(space)
            nodes = nodes.copy()
            nodes[:, [4, 5]] = nodes[:, [5, 4]]               # two mid-edge nodes on each other's edges
            return nodes, turned

        original = calculix.write_inp

        def write(*args, **kwargs):
            with mock.patch.object(calculix, "c3d10_connectivity", swapped):
                return original(*args, **kwargs)

        with mock.patch.object(calculix, "write_inp", write):
            outcome = _static(*_cantilever())
        self.assertFalse(outcome.solver.startswith("calculix"), outcome.solver)
        (warning,) = outcome.warnings
        self.assertTrue(warning.startswith("CalculiX failed (")
                        and warning.endswith("): solved with the built-in solver"), warning)


if __name__ == "__main__":
    unittest.main()
