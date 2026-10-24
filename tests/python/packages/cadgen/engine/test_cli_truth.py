"""What the engine's verbs say of what they read, on a tiny model in an engine of the test's own:
a document whose code changed since its build, or whose last run failed, is said once on stderr
by every verb that reads it, and by ``status`` in a word; the verbs go on."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from tests.python.support import engine_daemon

SRC = Path(__file__).resolve().parents[5] / "packages" / "cadgen" / "src"

SCRIPT = """
import build123d as bd
from cadgen import xbf, step

FAIL = {fail}


@xbf("{name}.xbf")
@step("{name}.step")
def {name}():
    assert not FAIL, "blade count must be positive"
    return bd.Box(10, 20, 30)


if __name__ == "__main__":
    {name}()
"""


class Engine(unittest.TestCase):
    """An engine of the class's own, each test a model of its own."""

    extra: dict[str, str] = {}

    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(os.path.realpath(tempfile.mkdtemp(prefix="engine-cli-truth-")))
        cls.addClassCleanup(shutil.rmtree, cls.folder, True)
        engine = Path(tempfile.mkdtemp(prefix="eng-", dir="/tmp" if os.name != "nt" else None)).resolve()
        cls.addClassCleanup(shutil.rmtree, engine, True)
        cls.env = engine_daemon.watched({**os.environ, "PYTHONPATH": str(SRC), "CADGEN_ENGINE_DIR": str(engine),
                                         "CADGEN_STATE_DIR": str(cls.folder / "state"),
                                         "CADGEN_CACHE_DIR": str(cls.folder / "cache"),
                                         "CADGEN_ENGINE_MESH": "inline", **cls.extra},
                                        cls.addClassCleanup)
        cls.addClassCleanup(cls.run_, "-m", "cadgen.engine", "stop")

    @classmethod
    def run_(cls, *args: str):
        return engine_daemon.run([sys.executable, *args], cwd=cls.folder, env=cls.env)

    def cli(self, *args: str):
        return self.run_("-m", "cadgen.engine", *args)

    def write(self, name: str, *, fail: bool = False) -> Path:
        script = self.folder / f"{name}.py"
        script.write_text(textwrap.dedent(SCRIPT.format(name=name, fail=fail)), encoding="utf-8")
        return script

    def built(self, name: str) -> str:
        done = self.run_(str(self.write(name)))
        self.assertEqual(done.returncode, 0, done.stderr)
        return next(line.split()[1] for line in done.stdout.splitlines() if line.strip().startswith("id:"))

    def status_line(self, ident: str) -> str:
        return next(line for line in self.cli("status").stdout.splitlines() if ident in line)

    def verbs(self, ident: str, name: str) -> dict[str, str]:
        """Each verb's stderr, every one of which succeeds."""
        said = {}
        for verb, args in (("export", [ident, str(self.folder / f"{name}.glb")]),
                           ("snapshot", [ident, str(self.folder / f"{name}.png"), "--width", "64", "--height", "48"]),
                           ("inspect", [ident]), ("inspect a ref", [ident, name])):
            done = self.cli(verb.split()[0], *args)
            self.assertEqual(done.returncode, 0, done.stderr)
            said[verb] = done.stderr
        return said


class StaleTest(Engine):
    # A worker retires a second after its build, so a document is soon held with no worker:
    # what the engine checks for staleness.
    extra = {"CADGEN_ENGINE_WORKER_IDLE": "1"}

    def test_a_document_whose_code_changed_is_said_stale_by_every_verb_and_status(self) -> None:
        ident = self.built("vane")
        deadline = time.monotonic() + 60
        while " held " not in self.status_line(ident):  # its worker retired: held with no worker
            self.assertLess(time.monotonic(), deadline, "the worker never retired")
            time.sleep(0.1)
        with (self.folder / "vane.py").open("a", encoding="utf-8") as script:
            script.write("# an edit\n")
        command = f"python {self.folder / 'vane.py'}"
        for verb, said in self.verbs(ident, "vane").items():
            doing = {"export": "exporting", "snapshot": "drawing"}.get(verb, "inspecting")
            self.assertEqual(said.count(f"{doing} the build of vane from before vane.py changed; "
                                        f"run `{command}` to update it\n"), 1, f"{verb}: {said}")
        self.assertIn(f"stale: run `{command}`", self.status_line(ident))


class VerbsTest(Engine):
    def test_a_document_whose_last_run_failed_is_said_to_be_its_previous_good_build(self) -> None:
        ident = self.built("rotor")
        self.assertEqual(self.cli("inspect", ident).returncode, 0)  # served: the engine follows its builds
        self.write("rotor", fail=True)
        self.assertEqual(self.run_("rotor.py").returncode, 1)
        command = f"python {self.folder / 'rotor.py'}"
        for verb, said in self.verbs(ident, "rotor").items():
            doing = {"export": "exporting", "snapshot": "drawing"}.get(verb, "inspecting")
            self.assertEqual(said.count(f"the last run of rotor failed; {doing} its previous good build; "
                                        f"fix it and run `{command}` to update it\n"), 1, f"{verb}: {said}")
        self.assertTrue(self.status_line(ident).endswith("serving its previous good build"))

    def test_a_failed_run_prints_the_models_frames_once_then_the_line_naming_it(self) -> None:
        self.write("stator", fail=True)
        done = self.run_("stator.py")
        self.assertEqual((done.returncode, done.stdout), (1, ""))
        lines = done.stderr.splitlines()
        self.assertEqual(lines[0], "Traceback (the model's frames):")
        self.assertEqual(lines[-2:], ["AssertionError: blade count must be positive", "failed stator.py::stator"])
        self.assertEqual(done.stderr.count("Traceback"), 1, done.stderr)

    def test_a_step_cadgen_exported_holds_no_closure_because_the_format_carries_none(self) -> None:
        ident = self.built("hub")
        self.assertEqual(self.cli("export", ident, str(self.folder / "hub-out.step")).returncode, 0)
        closure = self.cli("inspect", "hub-out.step", "--query", "closure")
        self.assertEqual(closure.returncode, 1)
        self.assertEqual(closure.stderr, "cadgen inspect: hub-out.step holds no build closure: a STEP file carries none, "
                                         "only an .xbf does; inspect the model's document by its id for its closure, or "
                                         "name a ref in the file (cadgen inspect hub-out.step REF)\n".replace(
                                             "cadgen inspect:", "python -m cadgen.engine inspect:"))
        summary = self.cli("inspect", "hub-out.step")
        self.assertEqual(summary.returncode, 0, summary.stderr)
        self.assertEqual(summary.stdout.splitlines()[-1], "closure     none: a STEP file carries none, only an .xbf "
                                                          "does (a model's document, inspected by its id, has one)")

    def test_a_part_ref_answers_where_the_part_stands_and_its_world_bounds(self) -> None:
        (self.folder / "arm.py").write_text(textwrap.dedent("""
            import build123d as bd
            from cadgen import model


            @model
            def arm():
                part = bd.Pos(30, 5, 0) * bd.Rot(0, 0, 90) * bd.Box(4, 8, 2)
                part.label = "link"
                return bd.Compound(children=[part], label="arm")


            if __name__ == "__main__":
                arm()
            """), encoding="utf-8")
        done = self.run_("arm.py")
        self.assertEqual(done.returncode, 0, done.stderr)
        ident = next(line.split()[1] for line in done.stdout.splitlines() if line.strip().startswith("id:"))
        shown = self.cli("inspect", ident, "arm/link")
        self.assertEqual(shown.returncode, 0, shown.stderr)
        found = json.loads(shown.stdout)
        self.assertEqual(found["placement"], {"origin": [30.0, 5.0, 0.0], "x": [0.0, 1.0, 0.0], "y": [-1.0, 0.0, 0.0],
                                              "z": [0.0, 0.0, 1.0]})
        self.assertEqual(found["bounds"], {"min": [26.0, 3.0, -1.0], "max": [34.0, 7.0, 1.0], "size": [8.0, 4.0, 2.0]})


if __name__ == "__main__":
    unittest.main()
