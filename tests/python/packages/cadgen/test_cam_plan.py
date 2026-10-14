"""``cadgen cam plan`` end to end on the plate: the three files, the GLB's contract, the sidecar, the G-code.

A four-operation job (facing, pocket_2d, contour_2d with tabs, drill) on linuxcnc writes plate.nc, plate.cam.glb and
plate.cam.json; the GLB carries the extras on mesh 0 and one LINES primitive per operation with _T, _KIND, _OP and _TOOL;
the sidecar is the result; the program reads back as the toolpaths planned; a second run writes the same bytes. Then:
an operation that cannot be planned exits 1 with its error finding and no G-code; two setups write two programs;
--post overrides the job's post; the CLI's --json answers ok.
"""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.cam_fixtures import assert_backplot, glb_attribute, plate_step, read_glb

#: The plate's pocket and through holes as `cadgen cam features` names them (test_cam_features pins these).
POCKET, LEFT, RIGHT = "#o1.f19", "#o1.f16", "#o1.f17"

JOB = {
    "machine": {"kind": "mill", "post": "linuxcnc"},
    "material": "aluminum-6061",
    # 3 mm under the part, so the through drill's point (to z -13.8) stays in the stock, clear of the plate under it.
    "stock": {"type": "box", "margin_mm": [2, 2, 1, 3]},
    "setups": [{"id": "top", "fixture": {"type": "plate"}}],
    "tools": [{"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 20,
               "stickout_mm": 24},
              {"id": "T2", "type": "drill", "diameter_mm": 6},
              {"id": "T3", "type": "flat_endmill", "diameter_mm": 25, "flutes": 3, "flute_length_mm": 20}],
    "operations": [
        {"op": "facing", "tool": "T1"},
        {"op": "pocket_2d", "tool": "T1", "features": [POCKET]},
        {"op": "contour_2d", "tool": "T1", "all": True, "tabs": {"count": 4, "width_mm": 5, "height_mm": 2}},
        {"op": "drill", "tool": "T2", "features": [LEFT, RIGHT]},
    ],
}


def run_cli(*args: str) -> tuple[int, str]:
    from cadgen import cli

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = cli.main(["cam", "plan", *args])
    return code, out.getvalue()


class Plan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from cadgen._internal.cam.posts.base import PostBase
        from cadgen.cam import plan

        tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(tmp.cleanup)
        cls.dir = Path(tmp.name)
        cls.step = plate_step(cls.dir)
        cls.job_file = cls.dir / "job.json"
        cls.job_file.write_text(json.dumps(JOB), encoding="utf-8")
        with mock.patch.object(PostBase, "programs", autospec=True, side_effect=PostBase.programs) as spy:
            cls.result = plan(cls.step, job=str(cls.job_file))
        cls.toolpaths = spy.call_args.args[3]
        cls.gltf, cls.binary = read_glb(cls.result.glb)

    def test_writes_the_program_the_glb_and_the_sidecar(self):
        r = self.result
        self.assertTrue(r.ok, r.findings)
        self.assertEqual((r.nc, r.glb, r.sidecar),
                         ((self.dir / "plate.nc",), self.dir / "plate.cam.glb", self.dir / "plate.cam.json"))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if p.is_file()),
                         ["job.json", "plate.cam.glb", "plate.cam.json", "plate.nc", "plate.step"])
        self.assertEqual([op["op"] for op in r.operations], ["facing", "pocket_2d", "contour_2d", "drill"])
        self.assertEqual(r.summary["tool_changes"], 1)
        self.assertIn("rapids at 5000 mm/min", r.warnings[0])

    def test_the_glb_carries_the_result_on_mesh_0_and_one_timed_line_set_per_operation(self):
        gltf, binary = self.gltf, self.binary
        extras = gltf["meshes"][0]["extras"]
        self.assertEqual((extras["generator"], extras["document"], extras["occurrence"]), ("cadgen cam", "plate.step", "#o1"))
        self.assertEqual(len(extras["operations"]), 4)
        self.assertEqual(extras["playback"]["duration_s"], self.result.summary["cycle_time_s"])
        names = [node["name"] for node in gltf["nodes"]]
        self.assertEqual(names, ["part", "stock", "fixture:top", "tool:T1", "tool:T2", "path:0:facing",
                                 "path:1:pocket_2d", "path:2:contour_2d", "path:3:drill"])
        for index in range(4):
            mesh = 5 + index
            with self.subTest(op=index):
                primitive = gltf["meshes"][mesh]["primitives"][0]
                self.assertEqual(primitive["mode"], 1)
                count = gltf["accessors"][primitive["attributes"]["POSITION"]]["count"]
                self.assertEqual(count % 2, 0)
                t, kind, op, tool = (glb_attribute(gltf, binary, mesh, name) for name in ("_T", "_KIND", "_OP", "_TOOL"))
                self.assertEqual((len(t), len(kind), len(op), len(tool)), (count, count, count, count))
                self.assertEqual(set(op), {float(index)})
                # The tool cutting each segment, as its index in extras.tools: T1 mills, T2 drills.
                self.assertEqual(set(tool), {1.0 if index == 3 else 0.0})
                self.assertTrue(set(kind) <= {0.0, 1.0, 2.0, 3.0, 4.0, 5.0})
                self.assertTrue(all(a <= b for a, b in zip(t, t[1:])), "_T goes back")
        self.assertIn(5.0, glb_attribute(gltf, binary, 8, "_KIND"))   # the drill's cycles
        self.assertAlmostEqual(max(glb_attribute(gltf, binary, 8, "_T")), extras["playback"]["duration_s"], delta=1e-3)

    def test_the_sidecar_is_the_result(self):
        sidecar = json.loads(self.result.sidecar.read_text(encoding="utf-8"))
        r = json.loads(json.dumps({"summary": self.result.summary, "operations": self.result.operations,
                                   "findings": self.result.findings, "warnings": self.result.warnings}))
        self.assertEqual({key: sidecar[key] for key in r}, r)
        self.assertEqual((sidecar["ok"], sidecar["document"], sidecar["post"]), (True, "plate.step", "linuxcnc"))
        self.assertEqual(sidecar["files"], {"nc": ["plate.nc"], "glb": "plate.cam.glb"})
        self.assertEqual(sidecar["job"], JOB)

    def test_no_rapid_comes_down_onto_the_material(self):
        # The stock top is Z0 (the saw left it, give or take): the facing rapids to Z1 and feeds the last millimetre.
        lines = self.result.nc[0].read_text(encoding="ascii").splitlines()
        first = lines.index("G0 Z1")
        self.assertLess(first, lines.index("(Op 1: pocket_2d with T1)"))
        self.assertTrue(lines[first + 1].startswith("G1 Z-1 F"), lines[first + 1])
        # The pocket after it starts from the faced top, Z-1: it rapids to Z0 and helixes in from there.
        pocket = lines[lines.index("(Op 1: pocket_2d with T1)"):]
        self.assertEqual(pocket[2], "G0 Z0")
        self.assertTrue(pocket[3].startswith("G3 "), pocket[3])

    def test_the_program_reads_back_as_the_planned_toolpaths(self):
        self.assertEqual(sorted({tp.op for tp in self.toolpaths}), [0, 1, 2, 3])
        assert_backplot(self, self.result.nc[0].read_text(encoding="ascii"), "linuxcnc", self.toolpaths)

    def test_the_same_job_writes_the_same_bytes(self):
        from cadgen.cam import plan

        files = (*self.result.nc, self.result.glb, self.result.sidecar)
        before = [path.read_bytes() for path in files]
        plan(self.step, job=str(self.job_file))
        self.assertEqual([path.read_bytes() for path in files], before)

    def test_an_operation_that_cannot_be_planned_exits_1_with_its_finding_and_no_program(self):
        out = self.dir / "bad" / "plate.cam.glb"
        out.parent.mkdir()
        (out.parent / "plate.nc").write_text("stale", encoding="ascii")
        job = dict(JOB, operations=[JOB["operations"][0], {"op": "pocket_2d", "tool": "T3", "features": [POCKET]}])
        code, text = run_cli(str(self.step), str(out), "--job", json.dumps(job), "--json")
        self.assertEqual(code, 1)
        answer = json.loads(text)
        self.assertFalse(answer["ok"])
        [error] = [f for f in answer["findings"] if f["severity"] == "error"]
        self.assertEqual(error["type"], "tool_too_large")
        # Its items name the operation by its index (and the pocket by its selector), not in a sentence to parse.
        self.assertEqual([(item["operation"], item.get("ref")) for item in error["items"]], [(1, POCKET)])
        self.assertEqual(sorted(p.name for p in out.parent.iterdir()), ["plate.cam.json"])
        self.assertFalse(json.loads((out.parent / "plate.cam.json").read_text(encoding="utf-8"))["ok"])

    def test_a_job_file_at_the_sidecars_path_is_refused_before_it_is_overwritten(self):
        from cadgen.cam import plan

        folder = self.dir / "clash"
        folder.mkdir()
        job = folder / "plate.cam.json"
        job.write_text(json.dumps(JOB), encoding="utf-8")
        with self.assertRaises(ValueError) as caught:
            plan(self.step, folder / "plate.cam.glb", job=str(job))
        self.assertIn("plate.cam.json is where this plan writes its sidecar", str(caught.exception))
        self.assertEqual(json.loads(job.read_text(encoding="utf-8")), JOB)

    def test_two_setups_write_two_programs(self):
        from cadgen.cam import plan

        out = self.dir / "two" / "plate.cam.glb"
        job = dict(JOB, setups=[{"id": "top"}, {"id": "under", "direction": "-Z"}],
                   operations=[{"op": "facing", "setup": "top", "tool": "T1"},
                               {"op": "facing", "setup": "under", "tool": "T1"}])
        result = plan(self.step, out, job=job)
        self.assertEqual([p.name for p in result.nc], ["plate.top.nc", "plate.under.nc"])
        self.assertIn("(Setup: under, tool axis -Z)", result.nc[1].read_text(encoding="ascii"))
        setups = read_glb(result.glb)[0]["meshes"][0]["extras"]["setups"]
        self.assertEqual([s["quaternion"] for s in setups], [[0.0, 0.0, 0.0, 1.0], [1.0, 0.0, 0.0, 0.0]])

    def test_post_overrides_the_jobs_post(self):
        from cadgen.cam import plan

        result = plan(self.step, self.dir / "grbl" / "plate.cam.glb", job=JOB, post="grbl")
        self.assertIn("(Post: grbl,", result.nc[0].read_text(encoding="ascii"))
        self.assertEqual(result.summary["post"], "grbl")

    def test_the_cli_answers_json(self):
        out = self.dir / "cli" / "plate.cam.glb"
        code, text = run_cli(str(self.step), str(out), "--job", str(self.job_file), "--json")
        self.assertEqual(code, 0)
        answer = json.loads(text)
        self.assertTrue(answer["ok"])
        self.assertEqual(answer["nc"], [str(out.with_name("plate.nc"))])


if __name__ == "__main__":
    unittest.main()
