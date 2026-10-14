#!/usr/bin/env python3

"""The kernels skill: plugin manifests, recording a model, and a run.

The fixtures' reference volumes are checked against build123d itself, so a
kernel compared with them in `kernels.py check` is compared with OpenCASCADE,
not with a recorded number nobody can re-derive.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path, repo_path

add_repo_path("skills/kernels/scripts")
add_repo_path("skills/kernels/kernels/_shared")

import cadgen_stub  # noqa: E402
from kernel_tools import manifest  # noqa: E402
from kernel_tools.record import trace_model  # noqa: E402
from kernel_tools.run import KernelError, run  # noqa: E402

FIXTURES = repo_path("skills/kernels/fixtures")
REFERENCE = json.loads((FIXTURES / "reference.json").read_text(encoding="utf-8"))

MODEL = textwrap.dedent('''
    from cadgen import build123d as bd
    from cadgen import step


    @step(out="../STEP/pill.step")
    def pill():
        with bd.BuildPart() as part:
            bd.Box(20, 10, 8)
            bd.fillet(part.edges(), radius=2)
        return part.part


    @step
    def block():
        return bd.Box(1, 2, 3)


    if __name__ == "__main__":
        pill()
''')


def setUpModule():
    # recording caches what it reads from real builds under CAD_KERNELS_HOME
    cache = tempfile.mkdtemp()
    patcher = mock.patch.dict(os.environ, {"CAD_KERNELS_HOME": cache})
    patcher.start()
    unittest.addModuleCleanup(patcher.stop)
    unittest.addModuleCleanup(shutil.rmtree, cache)


def _stl_volume(path: Path) -> float:
    data = path.read_bytes()
    (count,) = struct.unpack_from("<I", data, 80)
    total = 0.0
    for t in range(count):
        a, b, c = (struct.unpack_from("<3f", data, 84 + 50 * t + 12 + 12 * k) for k in range(3))
        total += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                  + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return total / 6


class ManifestTests(unittest.TestCase):
    def test_every_plugin_parses(self):
        ids = [k.id for k in manifest.load_all()]
        for kernel_id in ("usd-brep", "sdm", "implicit", "three", "manifold"):
            self.assertIn(kernel_id, ids)
        self.assertFalse([i for i in ids if i.startswith("_")], "templates and _shared are not kernels")

    def test_manifests_are_closed_and_checked(self):
        good = {"id": "x", "label": "X", "input": "program", "output": "stl",
                "command": ["{python}", "run.py", "{program}", "{stl}"]}
        manifest.parse(good, "x")
        cases = {
            "unknown field": ({**good, "colour": "red"}, "x"),
            "must match its folder": (good, "y"),
            "unknown placeholder": ({**good, "command": [*good["command"], "{gpu}"]}, "x"),
            "must pass {stl}": ({**good, "command": ["{python}", "{program}"]}, "x"),
            "output must be one of": ({**good, "output": "obj"}, "x"),
            "timeout must be a positive": ({**good, "timeout": 0}, "x"),
        }
        for message, (raw, folder) in cases.items():
            with self.subTest(message), self.assertRaisesRegex(manifest.ManifestError, message):
                manifest.parse(raw, folder)

    def test_env_placeholders_fall_back_to_the_kernels_folders(self):
        values = {"here": "kernels/sdm", "runtime": "/cache/sdm"}
        command = ["{env:SDM_PY|<runtime>/.venv/bin/python}", "{here}/runner.py"]
        self.assertEqual(manifest.expand(command, values, {}), ["/cache/sdm/.venv/bin/python", "kernels/sdm/runner.py"])
        self.assertEqual(manifest.expand(command, values, {"SDM_PY": "/opt/py"})[0], "/opt/py")
        self.assertEqual(manifest.runtime_home({"CAD_KERNELS_HOME": "/k"}), Path("/k"))


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "src").mkdir()
        self.script = self.tmp / "src" / "parts.py"
        self.script.write_text(MODEL, encoding="utf-8")

    def test_a_file_with_several_models_needs_one_named(self):
        with self.assertRaisesRegex(KernelError, r"declares 2 models; name one: parts.py::block"):
            run(manifest.by_id("implicit"), str(self.script), cwd=self.tmp)

    def test_records_the_named_model(self):
        program = trace_model(f"{self.script}::pill")
        self.assertEqual(program["model"], "pill")
        self.assertEqual(program["root"]["type"], "fillet")
        self.assertEqual(program["root"]["select"], {"all": True})

    def test_unsupported_api_is_named_not_guessed(self):
        self.script.write_text("from cadgen import build123d as bd, step\n\n"
                               "@step\ndef twist():\n    return bd.Helix(1, 2, 3)\n", encoding="utf-8")
        program = trace_model(str(self.script))
        self.assertFalse(program["ok"])
        self.assertIn("Helix", program["error"])

    def test_edges_picked_in_python_are_refused_not_skipped(self):
        # an empty edge list would let a safe_chamfer-style helper skip the chamfer silently
        self.script.write_text(
            "from cadgen import build123d as bd, step\n\n@step\ndef block():\n"
            "    with bd.BuildPart() as part:\n        bd.Box(10, 10, 10)\n"
            "    top = [e for e in part.edges() if e.center().Z > 4]\n"
            "    return bd.chamfer(top, length=1) if top else part.part\n", encoding="utf-8")
        program = trace_model(str(self.script))
        self.assertFalse(program["ok"])
        self.assertIn("looping over part.edges()", program["error"])

    def test_palette_colours_are_cadgens_numbers(self):
        # palette modules do arithmetic on srgb() at import; a stand-in that returned the hex broke every radial part
        self.script.write_text("from cadgen import build123d as bd, step, srgb\n\n"
                               "BLACK = tuple(round(float(c), 5) for c in srgb('#0f1012'))\n\n"
                               "@step\ndef block():\n    return bd.Box(1, 2, 3)\n", encoding="utf-8")
        self.assertTrue(trace_model(str(self.script))["ok"])
        self.assertEqual(cadgen_stub.srgb("#fff", 0.5), (1.0, 1.0, 1.0, 0.5))
        self.assertAlmostEqual(cadgen_stub.srgb("#0f1012")[0], ((15 / 255 + 0.055) / 1.055) ** 2.4)

    def test_a_model_that_fails_to_load_is_reported_not_a_traceback(self):
        self.script.write_text("from cadgen import build123d as bd, step\nraise ValueError('bad palette')\n", encoding="utf-8")
        with self.assertRaisesRegex(KernelError, r"parts.py didn't load: ValueError: bad palette at parts.py:2"):
            run(manifest.by_id("implicit"), str(self.script), cwd=self.tmp)

    @unittest.skipUnless(shutil.which("node"), "the implicit kernel runs on Node.js")
    def test_run_writes_beside_the_models_output(self):
        report = run(manifest.by_id("implicit"), f"{self.script}::pill", cwd=self.tmp)
        out = self.tmp / "STEP" / ".kernels" / "implicit" / "pill.stl"
        self.assertEqual(report["output"], "STEP/.kernels/implicit/pill.stl")
        self.assertAlmostEqual(_stl_volume(out) / REFERENCE["pill"]["volume"], 1, delta=0.01)
        self.assertAlmostEqual(report["volume"], _stl_volume(out), delta=0.5)
        self.assertFalse(list(out.parent.glob("*.program.json")), "the program is scratch")

    def test_a_failed_run_leaves_no_file(self):
        plugin = self.tmp / "liar"
        plugin.mkdir()
        (plugin / "runner.py").write_text(
            "import sys, json\nopen(sys.argv[2], 'w').write('half')\n"
            "print(json.dumps({'ok': False, 'error': 'gave up'}))\n", encoding="utf-8")
        kernel = manifest.parse({"id": "liar", "label": "Liar", "input": "program", "output": "stl",
                                 "command": ["{python}", "{here}/runner.py", "{program}", "{stl}"]},
                                "liar", plugin)
        stale = self.tmp / "STEP" / ".kernels" / "liar" / "pill.stl"
        stale.parent.mkdir(parents=True)
        stale.write_text("last run", encoding="utf-8")
        with self.assertRaisesRegex(KernelError, "Liar: gave up"):
            run(kernel, f"{self.script}::pill", cwd=self.tmp)
        self.assertFalse(stale.exists())


    def test_a_hung_runner_is_stopped(self):
        plugin = self.tmp / "sleeper"
        plugin.mkdir()
        # The runner's child inherits the output pipe: stopping only the runner would wait on it.
        (plugin / "runner.py").write_text(
            "import subprocess, sys, time\nopen(sys.argv[2], 'w').write('half')\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\ntime.sleep(60)\n",
            encoding="utf-8")
        kernel = manifest.parse({"id": "sleeper", "label": "Sleeper", "input": "program", "output": "stl", "timeout": 0.5,
                                 "command": ["{python}", "{here}/runner.py", "{program}", "{stl}"]},
                                "sleeper", plugin)
        started = time.monotonic()
        with self.assertRaisesRegex(KernelError, r"Sleeper gave no result within 0.5 s"):
            run(kernel, f"{self.script}::pill", cwd=self.tmp)
        self.assertLess(time.monotonic() - started, 30, "the runner's children were stopped too")
        self.assertFalse((self.tmp / "STEP" / ".kernels" / "sleeper" / "pill.stl").exists())


def _mesh_volume(mesh) -> float:
    v, t = mesh["vertices"], mesh["triangles"]
    total = 0.0
    for i in range(0, len(t), 3):
        a, b, c = (v[3 * t[i + k]:3 * t[i + k] + 3] for k in range(3))
        total += (a[0] * (b[1] * c[2] - b[2] * c[1]) - a[1] * (b[0] * c[2] - b[2] * c[0])
                  + a[2] * (b[0] * c[1] - b[1] * c[0]))
    return total / 6


class ResolveTests(unittest.TestCase):
    """What the recorder reads from the real build: edges, shells, lofts, sweeps."""

    def program(self, name):
        program = trace_model(str(FIXTURES / f"{name}.py"))
        self.assertTrue(program["ok"], program.get("error"))
        self.assertEqual(program["notes"], [])
        return program["root"]

    def test_edges_picked_by_position_become_tools(self):
        fillet = self.program("rim")
        chamfer = fillet["child"]
        self.assertEqual((fillet["type"], chamfer["type"]), ("fillet", "chamfer"))
        # the plate's top edges are convex (material cut), the boss's foot concave (material added)
        self.assertEqual([c["type"] for c in fillet["tools"]["cut"]["children"]], ["prism"] * 4)
        self.assertEqual(fillet["tools"]["fill"]["type"], "revolve")
        self.assertEqual((chamfer["tools"]["cut"]["type"], chamfer["tools"]["fill"]), ("revolve", None))

    def test_a_shell_cuts_a_cavity_through_its_opening(self):
        root = self.program("cup")
        self.assertEqual(root["type"], "subtract")
        cavity = root["tool"]
        self.assertEqual(cavity["size"], [27.0, 17.0, 15.0])
        self.assertAlmostEqual(cavity["matrix"][11], 1.5)  # floor 1.5 mm thick, top open

    def test_lofts_and_sweeps_carry_closed_meshes(self):
        for name, kind in (("transition", "loft"), ("elbow", "sweep")):
            with self.subTest(name):
                root = self.program(name)
                self.assertEqual(root["type"], kind)
                self.assertAlmostEqual(_mesh_volume(root["mesh"]) / REFERENCE[name]["volume"], 1, delta=5e-4)


class FixtureTests(unittest.TestCase):
    def test_reference_volumes_are_build123ds(self):
        import build123d

        saved = {name: sys.modules.get(name) for name in ("cadgen", "cadgen.build123d", "build123d")}
        cadgen_stub.install(build123d)
        try:
            for name, expected in REFERENCE.items():
                with self.subTest(name):
                    shape = cadgen_stub.load(FIXTURES / f"{name}.py").call()
                    self.assertAlmostEqual(shape.volume, expected["volume"], places=2)
                    self.assertTrue(trace_model(str(FIXTURES / f"{name}.py"))["ok"])
        finally:
            for name, module in saved.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module


if __name__ == "__main__":
    unittest.main()
