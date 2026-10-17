"""A child a parent's body calls is one build, wherever it happens (STORE.md §9a).

Every decorated function goes through the one pipeline: decoded from its stored result
when the store holds one for its inputs as they are, else run -- serially, in the
caller's process, as a build of its own with scopes of its own. So a child built inside
its parent is the child built alone (same tree, same closure), a parent is the same build
whether its child was decoded or run, a child's data file is the child's input and not its
parent's, a child runs once per run however often it is called, and its failure reaches the
body as itself and fails the run.
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

from cadgen.daemon import executors  # noqa: E402

CHILD = """\
from pathlib import Path

from cadgen import step
from cadgen import build123d as bd


@step
def child():
    size = float(Path(__file__).with_name("size.txt").read_text(encoding="utf-8"))
    return bd.Box(size, 3.0, 2.0)
"""

PARENT = """\
from cadgen import step
from cadgen import build123d as bd
from child import child


@step
def parent():
    piece = child()
    again = child()
    return bd.Compound(children=[bd.Pos(8, 0, 0) * piece, bd.Pos(-8, 0, 0) * again], label="parent")
"""

GRAND = """\
from cadgen import step
from cadgen import build123d as bd
from child import child
from parent import parent


@step
def grand():
    return bd.Compound(children=[parent(), bd.Pos(0, 20, 0) * child()], label="grand")
"""


class SerialChildren(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="serial-children-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "size.txt").write_text("4.0", encoding="utf-8")
        for name, source in (("child", CHILD), ("parent", PARENT), ("grand", GRAND)):
            (self.root / f"{name}.py").write_text(source, encoding="utf-8")
        self.child, self.parent, self.grand = (self.root / f"{name}.py" for name in ("child", "parent", "grand"))
        self.stores = 0
        self.use_store(self.root / "store")
        self.events: list[dict] = []
        executors.set_event_sink(self.events.append)
        self.addCleanup(executors.set_event_sink, None)

    def use_store(self, store: Path) -> None:
        env = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(store), "CADGEN_DAEMON": "0"})
        env.start()
        self.addCleanup(env.stop)

    def fresh_store(self) -> None:
        self.stores += 1
        self.use_store(self.root / f"store-{self.stores}")

    def build(self, script: Path, *flags: str) -> tuple[int, str]:
        from cadgen.cli._run_model import run_model_argv

        self.events.clear()
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run_model_argv([str(script), *flags])
        return code, err.getvalue()

    def done(self) -> list[str]:
        return [Path(e["model"]).stem for e in self.events if e.get("state") == "done"]

    def record(self, script: Path) -> dict:
        from cadgen.store.records import read_record

        record = read_record(script)
        self.assertIsNotNone(record, f"{script.name} has no record")
        return record

    def test_a_child_built_inside_its_parent_is_the_child_built_alone(self):
        code, err = self.build(self.child)
        self.assertEqual(code, 0, err)
        alone = self.record(self.child)
        self.fresh_store()
        code, err = self.build(self.parent)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.done()), ["child", "parent"])
        nested = self.record(self.child)
        self.assertEqual(nested["tree"], alone["tree"])
        self.assertEqual(nested["closure"]["hash"], alone["closure"]["hash"])
        self.assertEqual(sorted(nested["closure"]["files"]), sorted(alone["closure"]["files"]))
        self.assertIn("size.txt", nested["closure"]["files"])
        self.assertTrue((self.root / "child.step").is_file(), "the child built inside its parent wrote no STEP")

    def test_a_parent_is_the_same_build_whether_its_child_was_run_or_decoded(self):
        code, err = self.build(self.parent)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.done()), ["child", "parent"])
        miss, document = self.record(self.parent), (self.root / "parent.step").read_bytes()
        # --force rebuilds the root only: the child is current, so it is decoded.
        code, err = self.build(self.parent, "--force")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.done(), ["parent"])
        self.assertEqual([Path(e["model"]).stem for e in self.events if e.get("state") == "current"], ["child"])
        hit = self.record(self.parent)
        self.assertEqual(hit["tree"], miss["tree"])
        self.assertEqual(hit["closure"]["hash"], miss["closure"]["hash"])
        self.assertEqual(hit["children"], miss["children"])
        self.assertEqual((self.root / "parent.step").read_bytes(), document)

    def test_a_childs_data_file_is_its_own_input_and_the_parent_follows_the_child(self):
        from cadgen.store.gate import stale

        code, err = self.build(self.parent)
        self.assertEqual(code, 0, err)
        parent, child = self.record(self.parent), self.record(self.child)
        self.assertNotIn("size.txt", parent["closure"]["files"])
        self.assertIn("size.txt", child["closure"]["files"])
        self.assertEqual({c["model"] for c in parent["children"]}, {f"{self.child}::child"})
        self.assertEqual({c["key"] for c in parent["children"]}, {child["key"]})
        (self.root / "size.txt").write_text("6.0", encoding="utf-8")
        self.assertEqual(stale(self.child).reason(), "closure changed: size.txt")
        verdict = stale(self.parent)
        self.assertTrue(verdict.stale)
        self.assertFalse(next(c for c in verdict.clauses if c["clause"] == 2)["stale"], "the parent's own closure is unchanged")
        self.assertRegex(verdict.reason(), r"^child stale: child\.py")
        code, err = self.build(self.parent)
        self.assertEqual(code, 0, err)
        self.assertEqual(sorted(self.done()), ["child", "parent"])
        self.assertNotEqual(self.record(self.parent)["tree"], parent["tree"])
        self.assertFalse(stale(self.parent).stale)

    def test_a_child_runs_once_per_run_however_often_it_is_called(self):
        code, err = self.build(self.grand)
        self.assertEqual(code, 0, err)
        # grand calls child and parent, and parent calls child twice: one build of each.
        self.assertEqual(sorted(self.done()), ["child", "grand", "parent"])
        self.assertEqual({c["model"] for c in self.record(self.grand)["children"]},
                         {f"{self.parent}::parent", f"{self.child}::child"})
        self.assertEqual({c["model"] for c in self.record(self.parent)["children"]}, {f"{self.child}::child"})
        parents = {Path(e["model"]).stem: e.get("parent") for e in self.events if e.get("state") == "done"}
        self.assertEqual(Path(parents["child"]).name, "parent.py", "the child's build is reported under its first caller")
        self.assertEqual(Path(parents["parent"]).name, "grand.py")

    def test_a_childs_failure_reaches_the_body_as_itself_and_fails_the_run(self):
        self.child.write_text(
            "from cadgen import step\n\n\n@step\ndef child():\n    raise ValueError('the size is wrong on purpose')\n",
            encoding="utf-8",
        )
        self.parent.write_text(
            "from cadgen import step\nfrom cadgen import build123d as bd\nfrom child import child\n\n\n"
            "@step\ndef parent():\n    try:\n        child()\n    except ValueError as exc:\n"
            "        raise RuntimeError(f'caught {type(exc).__name__}: {exc}')\n    return bd.Box(1, 1, 1)\n",
            encoding="utf-8",
        )
        code, err = self.build(self.parent)
        self.assertNotEqual(code, 0)
        self.assertIn("caught ValueError: the size is wrong on purpose", err)
        self.assertEqual([Path(e["model"]).stem for e in self.events if e.get("state") == "failed"], ["child", "parent"])

    def test_a_model_may_not_depend_on_itself(self):
        (self.root / "left.py").write_text(
            "from cadgen import step\nfrom cadgen import build123d as bd\n\n\n@step\ndef left():\n"
            "    from right import right\n    return right()\n", encoding="utf-8")
        (self.root / "right.py").write_text(
            "from cadgen import step\nfrom cadgen import build123d as bd\n\n\n@step\ndef right():\n"
            "    from left import left\n    return left()\n", encoding="utf-8")
        code, err = self.build(self.root / "left.py")
        self.assertNotEqual(code, 0)
        self.assertIn("a model may not depend on itself", err)


class QueuedExports(SerialChildren):
    """A call's files are queued as its result is recorded and finished by the run that
    owns the command, children first: a child's STEP is written while its parent's body
    goes on, the command's result is printed once every file is written, and an export
    that fails fails the command, keeps the file on disk and is owed to the next run,
    which exports it without running the body (STORE.md §3, *Exports*; §9a)."""

    def test_a_childs_file_lands_after_its_done_and_before_the_result_is_printed(self):
        from cadgen.cli._run_model import run_model_argv

        class Stdout(io.StringIO):
            """Notes, as the result line is printed, how many files had been saved."""

            def write(self, text):
                if text.startswith('{"ok":'):
                    saved_first.append(len([e for e in self.events if e.get("saved")]))
                return super().write(text)

        saved_first: list[int] = []
        out = Stdout()
        out.events = self.events
        self.events.clear()
        err = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(run_model_argv([str(self.grand), "--json"]), 0, err.getvalue())
        events = list(self.events)
        for name in ("child", "parent", "grand"):
            done = next(i for i, e in enumerate(events) if e.get("state") == "done" and Path(e["model"]).stem == name)
            saved = next(i for i, e in enumerate(events) if e.get("saved") and Path(e["model"]).stem == name)
            self.assertLess(done, saved, f"{name}: its result is recorded before its file is written")
            self.assertTrue((self.root / f"{name}.step").is_file())
        order = [Path(e["model"]).stem for e in events if e.get("saved")]
        self.assertEqual(order, ["child", "parent", "grand"], "children's files land first, as queued")
        self.assertEqual(saved_first, [3], "the result line follows the last file written")

    def test_a_failed_export_fails_the_command_and_the_next_run_writes_the_file_without_a_run(self):
        from cadgen.store import exports

        code, err = self.build(self.parent)
        self.assertEqual(code, 0, err)
        before = {name: (self.root / f"{name}.step").read_bytes() for name in ("child", "parent")}
        (self.root / "size.txt").write_text("5.0", encoding="utf-8")
        writer = exports.export_step

        def failing(tree, out, **kwargs):
            if Path(out).name == "child.step":
                raise RuntimeError("the export failed on purpose")
            return writer(tree, out, **kwargs)

        with mock.patch.object(exports, "export_step", side_effect=failing):
            code, err = self.build(self.parent)
        self.assertNotEqual(code, 0)
        self.assertIn("the export failed on purpose", err)
        self.assertEqual(sorted(self.done()), ["child", "parent"], "both bodies ran before the export failed")
        self.assertEqual((self.root / "child.step").read_bytes(), before["child"], "the file on disk stays")
        self.assertNotEqual((self.root / "parent.step").read_bytes(), before["parent"], "the other export landed")
        self.assertEqual(self.record(self.child)["outputs"][str(self.root / "child.step")]["sha256"], "",
                         "the record owes the file")
        code, err = self.build(self.parent)
        self.assertEqual(code, 0, err)
        self.assertEqual(self.done(), [], "nothing ran again")
        self.assertIn("child.step was never written; writing it again", err)
        self.assertNotEqual((self.root / "child.step").read_bytes(), before["child"])
        self.assertEqual(self.record(self.child)["outputs"][str(self.root / "child.step")]["sha256"],
                         __import__("hashlib").sha256((self.root / "child.step").read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
