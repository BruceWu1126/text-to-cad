"""Actual function profiles across Python, CLI, workers, cache hits and supplied shapes."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from cadgen import build, stop
from cadgen.engine import cli
from cadgen.engine.profiling import describe


class ProfilingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        temporary = tempfile.TemporaryDirectory(prefix="engine-profile-")
        cls.addClassCleanup(temporary.cleanup)
        cls.root = Path(temporary.name).resolve()
        state = tempfile.TemporaryDirectory(prefix="eng-", dir="/tmp" if os.name != "nt" else None)
        cls.addClassCleanup(state.cleanup)
        cls.enterClassContext(mock.patch.dict(os.environ, {"CADGEN_ENGINE_DIR": state.name}))
        # A warm worker keeps its cwd open on Windows. Stop this class's own engine
        # before removing any fixture directory; all cases still share its warm daemon.
        cls.addClassCleanup(stop)

    def setUp(self) -> None:
        self.folder = self.root / self._testMethodName
        self.folder.mkdir()
        self.enterContext(contextlib.chdir(self.folder))

    def assert_profile(self, report: dict, *, worker: bool = True) -> dict:
        self.assertTrue(report["ok"], report.get("error"))
        profile = report["profile"]
        self.assertEqual((profile["scope"], profile["mode"], profile["builtins"]),
                         ("build_request", "actual_execution", False))
        self.assertEqual(profile["process"] == os.getpid(), not worker)
        self.assertTrue(profile["top_self"])
        for table, timing in (("project_cumulative", "cumulative_seconds"), ("top_self", "self_seconds")):
            rows = profile[table]
            self.assertLessEqual(len(rows), 20)
            self.assertEqual([row[timing] for row in rows], sorted((row[timing] for row in rows), reverse=True))
            for row in rows:
                self.assertGreater(row["calls"], 0)
                self.assertGreaterEqual(row["cumulative_seconds"], row["self_seconds"])
        json.dumps(profile)  # the worker's result and Python's result have the same JSON surface
        return profile

    def test_plain_python_script_profiles_decorated_model_and_preserves_warm_hits(self) -> None:
        script = self.folder / "model.py"
        script.write_text(textwrap.dedent('''
            import json
            import build123d as bd
            from cadgen import xbf

            def make_block():
                return bd.Box(3, 4, 5)

            @xbf("part.xbf")
            def part():
                return make_block()

            if __name__ == "__main__":
                print(json.dumps(part.build(profile=True).models[0]))
                print(json.dumps(part.build(profile=True).models[0]))
                print(json.dumps(part.build().models[0]))
        '''), encoding="utf-8")
        result = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, check=True)
        cold, warm, ordinary = map(json.loads, result.stdout.splitlines())
        first = self.assert_profile(cold)
        second = self.assert_profile(warm)
        self.assertIn("make_block", {row["function"] for row in first["project_cumulative"]})
        self.assertNotIn("make_block", {row["function"] for row in second["project_cumulative"]})
        self.assertEqual(warm["memo"]["bodies_run"], 0)
        self.assertFalse(warm["files"][str(self.folder / "part.xbf")]["written"])
        self.assertNotIn("profile", ordinary)
        self.assertEqual({p.name for p in self.folder.iterdir()}, {"model.py", "part.xbf"})

    def test_code_api_and_cli_json_and_human_output_use_actual_profiles(self) -> None:
        code = "import build123d as bd\ndef make_pin():\n    return bd.Cylinder(2, 5)\nresult = make_pin()"
        first = build(code, model="pin", profile=True, progress=False)
        profile = self.assert_profile(first.report)
        functions = {row["function"] for row in profile["project_cumulative"]}
        self.assertTrue({"pin", "make_pin"} <= functions, functions)
        self.assertFalse(profile["supplied_shape"])
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(["build", code, "--model", "pin", "--profile", "--json"]), 0)
        warm = json.loads(stdout.getvalue())
        self.assert_profile(warm)
        self.assertNotIn("make_pin", {row["function"] for row in warm["profile"]["project_cumulative"]})
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(cli.main(["build", code, "--model", "pin", "--profile"]), 0)
        self.assertIn("project functions by cumulative time", stdout.getvalue())
        self.assertIn("cache hits preserved", stdout.getvalue())
        self.assertEqual(list(self.folder.iterdir()), [])

    def test_supplied_shape_cold_processing_and_failed_code(self) -> None:
        import build123d as bd

        def earlier_construction():
            return bd.Box(2, 3, 4)

        shape = earlier_construction()
        built = build(shape, model="given", profile=True, daemon=False, progress=False)
        profile = self.assert_profile(built.report, worker=False)
        self.assertTrue(profile["supplied_shape"])
        self.assertNotIn("earlier_construction", {row["function"] for row in profile["top_self"]})
        self.assertIn("earlier construction was not profiled", describe(profile))
        self.assertAlmostEqual(built.shape.volume, 24)
        failed = build("raise ValueError('broken')\nresult = None", model="broken", profile=True,
                       daemon=False, progress=False)
        self.assertFalse(failed.ok)
        self.assertIn("broken", {row["function"] for row in failed.report["profile"]["project_cumulative"]})
        ordinary = build(shape, model="given", daemon=False, progress=False)
        self.assertNotIn("profile", ordinary.report)
        self.assertEqual(list(self.folder.iterdir()), [])
