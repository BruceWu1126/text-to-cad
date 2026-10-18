"""Builds through one daemon agree byte for byte, however many run at once.

Every build runs on the daemon (STORE.md §9), the test run's here: a build of a
model whose worker is busy runs on an extra worker beside it. Four builds of one part at
once must write exactly what one build alone writes -- every file the build writes and
its store package, by sha256 -- and ``--profile``, parsed where the user typed it, is
acted on by the worker that runs the body, whose report names the model's own slow
function.

Fixtures are written here rather than copied from ``models/``.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
CADGEN_SRC = REPO_ROOT / "packages" / "cadgen" / "src"
sys.path.insert(0, str(CADGEN_SRC))

PART = """from build123d import Box, BuildPart, Cylinder, Locations, Mode

from cadgen import step
@step
def model():
    with BuildPart() as part:
        Box(30, 20, 10)
        with Locations((0, 0, 0)):
            Cylinder(4, 40, mode=Mode.SUBTRACT)
    return part.part


if __name__ == "__main__":
    model()
"""


def _run(argv: list[str], cwd: pathlib.Path) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(CADGEN_SRC), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])])
    # Four small concurrent fixtures under a known, bounded budget: host-sized defaults can
    # legitimately refuse that concurrency, and memory admission has tests of its own.
    env["CADGEN_MEMORY_MB"] = "8192"
    proc = subprocess.run([sys.executable, *argv], cwd=str(cwd), env=env, capture_output=True, text=True,
                          timeout=600)
    return proc.returncode, proc.stdout + proc.stderr


# Fields that record WHEN a build ran rather than WHAT it produced.
_VOLATILE_JSON_FIELDS = {"generatedAt"}


def _canonical(value):
    if isinstance(value, dict):
        return {k: _canonical(v) for k, v in sorted(value.items()) if k not in _VOLATILE_JSON_FIELDS}
    if isinstance(value, list):
        return [_canonical(v) for v in value]
    return value


def _digest(path: pathlib.Path) -> str:
    """sha256 of a built file, with build-time stamps masked out of JSON."""
    raw = path.read_bytes()
    if path.suffix == ".json":
        try:
            return hashlib.sha256(json.dumps(_canonical(json.loads(raw)), sort_keys=True).encode()).hexdigest()
        except ValueError:
            pass  # not JSON after all; fall through to the raw bytes
    return hashlib.sha256(raw).hexdigest()


def _manifest(root: pathlib.Path) -> dict[str, str]:
    """Digest of every built file for the models in ``root``: the model-folder outputs (.step
    documents and their sidecars) and the store packages they resolve to."""
    from cadgen.catalog import result_view_dir

    out: dict[str, str] = {}
    for artifact in sorted(root.rglob("*")):
        if not artifact.is_file():
            continue
        rel = artifact.relative_to(root).as_posix()
        if artifact.suffix in {".step", ".stp"} or rel.endswith(".step.json"):
            out[rel] = _digest(artifact)
        if artifact.suffix in {".step", ".stp"}:
            package = result_view_dir(artifact)
            if package.is_dir():
                for entry in sorted(package.rglob("*")):
                    if entry.is_file():
                        out[f"<store>/{entry.relative_to(package.parent).as_posix()}"] = _digest(entry)
    return out


class ParallelBuilds(unittest.TestCase):
    maxDiff = None

    def _tree(self, name: str, source: str) -> pathlib.Path:
        tmp = pathlib.Path(tempfile.mkdtemp(prefix="tmp-parallel-builds-")).resolve()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / name).write_text(source, encoding="utf-8")
        return tmp

    def test_four_builds_at_once_write_what_one_build_alone_writes(self):
        alone = self._tree("widget.py", PART)
        code, output = _run(["widget.py"], alone)
        self.assertEqual(code, 0, output)
        expected = _manifest(alone)
        self.assertTrue(expected, "the build produced nothing to compare")

        trees = [self._tree("widget.py", PART) for _ in range(4)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda tree: _run(["widget.py"], tree), trees))
        for index, (tree, (code, output)) in enumerate(zip(trees, results)):
            with self.subTest(build=index):
                self.assertEqual(code, 0, output)
                self.assertEqual(_manifest(tree), expected)

    def test_profile_reaches_the_worker_that_runs_the_body(self):
        slow = (
            "from cadgen import build123d as bd\n"
            "from cadgen import step\n"
            "def plan_holes():\n"
            "    total = 0\n"
            "    for i in range(300_000):\n"
            "        total += i * i\n"
            "    return total\n"
            "@step\n"
            "def plate():\n"
            "    plan_holes()\n"
            "    return bd.Box(10, 10, 2)\n"
            "if __name__ == '__main__':\n"
            "    plate()\n"
        )
        code, output = _run(["plate.py", "--profile"], self._tree("plate.py", slow))
        self.assertEqual(code, 0, output)
        self.assertRegex(output, re.compile(r"^\[cadgen\] +\S+ +1 call +plate\.py:3 plan_holes$", re.MULTILINE))


if __name__ == "__main__":
    unittest.main()
