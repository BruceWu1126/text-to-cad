"""A model script is plain Python. One inside a package -- folders holding an ``__init__.py``
-- is that package's module, imported by its dotted name as ``python -m`` imports it: its
relative imports at any depth, a module beside it by its own name and the package by its name
all resolve, in a build, run as ``python model.py``, and in the warm worker build after build;
an edit to a module it imports re-runs its body. A loose script is the module its stem names."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine import previous, read_xbf

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

MODEL = """
import build123d as bd
from cadgen.engine import xbf

import helper                       # beside the script, by its own name
import proj.common.dims as dims     # the package, by its name
from .lib import WIDTH              # one level: its own package
from ..common.dims import HEIGHT    # two levels: its package's parent


@xbf
def bracket():
    return bd.Box(WIDTH, 10, HEIGHT) - bd.Cylinder(helper.RADIUS, dims.HEIGHT * 2)
"""

PACKAGE = {"proj/__init__.py": "", "proj/common/__init__.py": "", "proj/common/dims.py": "HEIGHT = 5\n",
           "proj/parts/__init__.py": "", "proj/parts/lib.py": "WIDTH = 20\n", "proj/parts/helper.py": "RADIUS = 2\n",
           "proj/parts/model.py": MODEL}


def write(root: Path, files: dict[str, str]) -> None:
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        # A rewrite within the same clock tick still reads as a new file.
        os.utime(path, ns=(time.time_ns(), time.time_ns()))


class PackageScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="engine-package-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        write(self.root, PACKAGE)
        self.script = self.root / "proj/parts/model.py"
        self.xbf = self.root / "proj/parts/XBF/bracket.xbf"

    def build(self, script: Path) -> dict:
        [report] = engine_build.build(script, cwd=self.root)
        self.assertTrue(report["ok"], report.get("error"))
        return report

    def test_a_script_in_a_package_is_its_module_and_an_edit_to_what_it_imports_reruns_it(self) -> None:
        cold = self.build(self.script)
        self.assertEqual(cold["bodies"], {"model.py::bracket": "run"})
        module = sys.modules["proj.parts.model"]
        self.assertEqual((Path(module.__file__), module.__package__), (self.script, "proj.parts"))
        self.assertIs(sys.modules["proj.parts"].model, module)
        # The closure keys every module the load imported by its path, the packages' too.
        modules = {path for path, _digest in previous.of(self.xbf)["modules"]}
        self.assertLessEqual({"../lib.py", "../helper.py", "../../common/dims.py", "../../__init__.py"}, modules)
        warm = self.build(self.script)
        self.assertEqual(warm["bodies"], {"model.py::bracket": "replayed"})
        self.assertFalse(warm["files"][str(self.xbf)]["written"])
        write(self.root, {"proj/common/dims.py": "HEIGHT = 7\n"})
        edited = self.build(self.script)
        self.assertEqual(edited["bodies"], {"model.py::bracket": "run"})
        self.assertIn("../../common/dims.py changed", edited["cache"]["changed"])
        self.assertAlmostEqual(read_xbf(self.xbf).bounding_box().size.Z, 7)

    def test_a_loose_script_is_the_module_its_stem_names(self) -> None:
        # In the package's tree, but its own folder holds no __init__.py.
        write(self.root, {"proj/tools/holes.py": "RADIUS = 1\n", "proj/tools/plate.py": """
            import build123d as bd
            from cadgen.engine import xbf

            import holes


            @xbf
            def plate():
                return bd.Box(10, 10, 2) - bd.Cylinder(holes.RADIUS, 4)
        """})
        script = self.root / "proj/tools/plate.py"
        self.build(script)
        module = sys.modules["plate"]
        self.assertEqual((Path(module.__file__), module.__package__), (script, ""))

    def test_two_edits_in_one_second_run_the_second_not_a_pyc_another_tool_left(self) -> None:
        """CPython takes a ``.pyc`` as current by the source's whole-second mtime and its size: an
        edit of the same length inside the second a tool compiled the file would run that tool's
        bytecode. The worker compiles the model's own modules from the bytes on disk."""
        import py_compile

        def same_second_edit(relative: str, old: str, new: str) -> None:
            path = self.root / relative
            stat = path.stat()
            py_compile.compile(str(path), doraise=True)  # another tool compiles the first edit
            text = path.read_text(encoding="utf-8")
            assert len(old) == len(new) and text.count(old) == 1
            path.write_text(text.replace(old, new), encoding="utf-8")  # the same length...
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))  # ...inside the same second

        self.build(self.script)
        same_second_edit("proj/common/dims.py", "HEIGHT = 5", "HEIGHT = 8")  # a module the script imports
        self.build(self.script)
        self.assertAlmostEqual(read_xbf(self.xbf).bounding_box().size.Z, 8)
        same_second_edit("proj/parts/model.py", "bd.Box(WIDTH, 10,", "bd.Box(WIDTH, 30,")  # the script itself
        self.build(self.script)
        self.assertAlmostEqual(read_xbf(self.xbf).bounding_box().size.Y, 30)
        # A loose script is compiled from its source too.
        write(self.root, {"proj/tools/plate.py": """
            import build123d as bd
            from cadgen.engine import xbf


            @xbf
            def plate():
                return bd.Box(10, 10, 2)
        """})
        loose = self.root / "proj/tools/plate.py"
        plate = self.root / "proj/tools/XBF/plate.xbf"
        self.build(loose)
        same_second_edit("proj/tools/plate.py", "bd.Box(10, 10, 2)", "bd.Box(10, 10, 6)")
        self.build(loose)
        self.assertAlmostEqual(read_xbf(plate).bounding_box().size.Z, 6)


class PackageScriptRunTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="engine-package-run-")).resolve()
        self.state = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        self.env = {**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(self.state),
                    "CADGEN_ENGINE_IDLE": "60", "CADGEN_ENGINE_MESH": "inline"}
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        self.python("-m", "cadgen.engine", "stop")
        deadline = time.monotonic() + 20
        while any(self.state.glob("*.sock")) and time.monotonic() < deadline:
            time.sleep(0.05)
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.state, ignore_errors=True)

    def python(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, *args], cwd=self.root, env=self.env, capture_output=True, text=True,
                              timeout=300)

    def run_script(self) -> str:
        done = self.python("proj/parts/model.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        return done.stdout

    def test_running_a_script_in_a_package_builds_it_in_the_warm_worker_across_edits(self) -> None:
        write(self.root, PACKAGE)
        self.assertIn("bodies 1 run", self.run_script())
        write(self.root, {"proj/common/dims.py": "HEIGHT = 7\n"})
        edited = self.run_script()
        self.assertIn("bodies 1 run", edited)
        self.assertIn("rebuilt: ../../common/dims.py changed", edited)
        workers = json.loads(self.python("-m", "cadgen.engine", "status", "--json").stdout)["workers"]
        self.assertEqual([(Path(worker["script"]).name, worker["builds"]) for worker in workers], [("model.py", 2)],
                         "one warm worker built both, not the script's own process")


if __name__ == "__main__":
    unittest.main()
