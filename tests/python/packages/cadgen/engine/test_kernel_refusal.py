"""A build that fails inside a kernel call with an OCCT exception carrying no words of its own
(``Standard_Failure``, bare) names that call on its failure line, as the memo saw it; a failure
that says what went wrong is left to say it."""

from __future__ import annotations

import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine import cli

SCRIPT = """
import build123d as bd
from cadgen import model


@model
def plate():
    return bd.Box(0, 60, 5)


@model
def rounded():
    box = bd.Box(10, 10, 10)
    return bd.fillet(box.edges(), 20)
"""


class KernelRefusalTest(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-refusal-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.script = self.folder / "parts.py"
        self.script.write_text(textwrap.dedent(SCRIPT), encoding="utf-8")

    def built(self, name: str) -> dict:
        report = engine_build.build(self.script, [name], cwd=self.folder)[0]
        self.assertFalse(report["ok"])
        return report

    def test_a_zero_size_box_names_the_call_the_kernel_refused(self) -> None:
        report = self.built("plate")
        self.assertIn("Standard_Failure", report["error"])
        self.assertEqual(report["failedAt"], "Solid.make_box(0, 60, 5)")
        self.assertEqual(cli._describe(report, 0.1, None),
                         "failed parts.py::plate at Solid.make_box(0, 60, 5): the kernel refused it")

    def test_a_failure_with_its_own_words_names_no_call(self) -> None:
        report = self.built("rounded")
        self.assertIn("fillet", report["error"])
        self.assertNotIn("failedAt", report)
        self.assertEqual(cli._describe(report, 0.1, None), "failed parts.py::rounded")


if __name__ == "__main__":
    unittest.main()
