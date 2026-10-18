"""A build answers with its stored result; its worker finishes display data and files."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

from cadgen.authoring import finished_after_answer  # noqa: E402
from cadgen.cli._run_model import run_model_argv  # noqa: E402
from cadgen.daemon import executors  # noqa: E402

CHILD = """\
from cadgen import step
from cadgen import build123d as bd

@step
def child():
    return bd.Box(4, 3, 2)
"""
PARENT = """\
from cadgen import step
from cadgen import build123d as bd
from child import child

@step
def parent():
    return bd.Compound(children=[bd.Pos(8, 0, 0) * child(), bd.Box(2, 2, 2)])

if __name__ == "__main__":
    parent()
"""


class AnswerAtResult(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="answer-result-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "child.py").write_text(CHILD, encoding="utf-8")
        self.parent = self.root / "parent.py"
        self.parent.write_text(PARENT, encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store")})
        environment.start()
        self.addCleanup(environment.stop)

    def command(self, *flags):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = run_model_argv([str(self.parent), *flags])
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue(), err.getvalue()

    def test_worker_shape_answers_before_files_then_finishes_every_file(self):
        events = []
        executors.set_event_sink(events.append)
        self.addCleanup(executors.set_event_sink, None)
        with finished_after_answer() as held:
            out, _err = self.command("--json")
        (entry,) = [json.loads(line) for line in out.splitlines() if line.startswith('{"ok":')]
        expected = {str(self.root / f"{name}.step"): "saving" for name in ("child", "parent")}
        self.assertEqual(entry["files"], expected)
        self.assertFalse(any(event.get("saved") for event in events))
        self.assertEqual(len(held), 1)
        held[0].finish()
        self.assertEqual(len([event for event in events if event.get("saved")]), 2)
        for path in expected:
            self.assertTrue(Path(path).is_file())

    def test_text_answer_lists_each_file_still_saving(self):
        with finished_after_answer() as held:
            out, _err = self.command()
        try:
            self.assertTrue(out.startswith("built "), out)
            for name in ("child", "parent"):
                self.assertRegex(out, rf"saving .*{name}\.step \(cadgen export .*{name}\.step waits for it\)")
        finally:
            held[0].finish()

    def test_without_a_worker_the_answer_names_saved_files(self):
        out, _err = self.command("--json")
        entry = json.loads(out.splitlines()[-1])
        self.assertEqual(set(entry["files"].values()), {"saved"})
        self.assertTrue(self.parent.with_suffix(".step").is_file())

    def test_a_door_after_the_daemon_answer_reads_the_new_result(self):
        from cadgen._internal.doors import document_target
        from cadgen.store.records import read_record, tree_for_document_hash
        from cadgen.catalog import artifact_file_hash

        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [
            str(REPO_ROOT / "packages/cadgen/src"), os.environ.get("PYTHONPATH", "")]))}
        env.pop("CADGEN_DAEMON_CHILD", None)
        done = subprocess.run([sys.executable, "parent.py", "--json"], cwd=self.root, env=env,
                              capture_output=True, text=True, timeout=600)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        entry = json.loads(done.stdout.splitlines()[-1])
        self.assertEqual(set(entry["files"].values()), {"saving"})
        for name in ("child", "parent"):
            document = document_target(self.root / f"{name}.step", suffixes=(".step",))
            tree = read_record(self.root / f"{name}.py")["tree"]
            self.assertEqual(tree_for_document_hash(artifact_file_hash(document)), tree)

    def test_failed_publication_is_owed_and_the_next_run_says_so_first(self):
        from cadgen.store.records import read_record

        with finished_after_answer() as held:
            self.command()
        with mock.patch("cadgen._internal.generation._Export._publish_step", side_effect=RuntimeError("publish refused")):
            with self.assertRaisesRegex(RuntimeError, "publish refused"):
                held[0].finish()
        entry = read_record(self.parent)["outputs"][str(self.parent.with_suffix(".step"))]
        self.assertGreater(entry["owed"]["since"], 0)
        self.assertEqual(entry["owed"]["error"], "RuntimeError: publish refused")
        _out, err = self.command()
        self.assertIn("owed since", err.splitlines()[0])
        self.assertIn("publish refused", err.splitlines()[0])
        self.assertNotIn("owed", read_record(self.parent)["outputs"][str(self.parent.with_suffix(".step"))])

    def test_step_build_answers_with_the_generated_tree_and_saving_paths(self):
        from cadgen import step
        from cadgen.store.records import read_record

        self.command()
        destination = self.root / "copy.step"
        with finished_after_answer() as held:
            result = step.build(self.parent.with_suffix(".step"), destination)
        try:
            self.assertTrue(result.ok)
            self.assertEqual(result.tree, read_record(destination)["tree"])
            self.assertEqual(result.saving, (destination,))
            self.assertTrue(any(line.startswith("saving ") for line in result.human_lines()))
            self.assertFalse(destination.exists())
        finally:
            for run in held:
                run.finish()


class WorkerAnswer(unittest.TestCase):
    def test_worker_releases_then_reports_finishing_failure_and_exits_failed(self):
        from cadgen.authoring import build_run
        from cadgen.daemon import worker

        frames = []

        def command(*_args):
            with build_run() as run:
                run.exports.append(mock.Mock(finish=mock.Mock(side_effect=RuntimeError("publish refused"))))
            print("built part.step")
            return 0

        with mock.patch.object(worker, "_emit", side_effect=frames.append), \
                mock.patch.object(worker, "_command", side_effect=command), \
                mock.patch.object(worker, "_enter", return_value=True), \
                mock.patch.object(worker, "_park"), mock.patch.object(worker, "_evict_first_party_modules"):
            code = worker._run({"tool": "run", "argv": [], "prog": "python part.py"})
        self.assertEqual(code, 1)
        release = frames.index({"released": 0})
        failures = [index for index, frame in enumerate(frames)
                    if frame.get("stream") == "stderr" and "publish refused" in frame.get("data", "")]
        self.assertEqual(len(failures), 1)
        self.assertGreater(failures[0], release)


if __name__ == "__main__":
    unittest.main()
