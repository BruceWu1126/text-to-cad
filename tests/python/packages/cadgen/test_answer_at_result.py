"""A build answers with its stored result; what its result owes -- its files, its display data --
is build-pool jobs, its daemon worker's supervisor's to run, so the worker is free at once."""

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

from cadgen.authoring import supervised  # noqa: E402
from cadgen.cli._run_model import run_model_argv  # noqa: E402
from cadgen.daemon import artifacts, executors  # noqa: E402

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

    @contextlib.contextmanager
    def supervised(self):
        """The command under a daemon worker's supervisor: the jobs its runs owe are handed over
        (here, collected as ``(request, model)``) and never awaited."""
        handed: list[tuple[dict, str]] = []
        with supervised(lambda request, model: handed.append((request, model))):
            yield handed

    def run_handed(self, handed):
        """Run the jobs a build handed over, as the supervisor does: each from its request alone."""
        for request, _model in handed:
            artifacts.execute(request)

    def test_a_worker_build_answers_saving_and_its_handed_jobs_alone_write_every_file(self):
        events = []
        executors.set_event_sink(events.append)
        self.addCleanup(executors.set_event_sink, None)
        with self.supervised() as handed:
            out, _err = self.command("--json")
        (entry,) = [json.loads(line) for line in out.splitlines() if line.startswith('{"ok":')]
        expected = {str(self.root / f"{name}.step"): "saving" for name in ("child", "parent")}
        self.assertEqual(entry["files"], expected)
        self.assertFalse(any(event.get("saved") for event in events))
        exports = [request for request, _model in handed if request["kind"] == "export"]
        self.assertEqual([request["step"] for request in exports], list(expected), "children first")
        self.assertEqual({model for _request, model in handed},
                         {f"{self.root / name}.py::{name}" for name in ("child", "parent")})
        for path in expected:
            self.assertFalse(Path(path).exists(), "nothing is written on the build's thread")
        self.run_handed(handed)
        from cadgen.catalog import artifact_file_hash
        from cadgen.store.records import read_record, tree_for_document_hash

        for name in ("child", "parent"):
            step = self.root / f"{name}.step"
            record = read_record(self.root / f"{name}.py")
            self.assertEqual(tree_for_document_hash(artifact_file_hash(step)), record["tree"])
            self.assertEqual(record["outputs"][str(step)]["sha256"], artifact_file_hash(step))

    def test_text_answer_lists_each_file_still_saving(self):
        with self.supervised() as handed:
            out, _err = self.command()
        self.assertTrue(out.startswith("built "), out)
        for name in ("child", "parent"):
            self.assertRegex(out, rf"saving .*{name}\.step \(cadgen export .*{name}\.step waits for it\)")
        self.run_handed(handed)

    def test_the_command_a_saving_line_offers_quotes_a_path_with_a_space(self):
        import shutil

        spaced = self.root / "with space"
        spaced.mkdir()
        for name in ("child.py", "parent.py"):
            shutil.copyfile(self.root / name, spaced / name)
        out, err = io.StringIO(), io.StringIO()
        with self.supervised() as handed, contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(run_model_argv([str(spaced / "parent.py")]), 0, err.getvalue())
        self.assertRegex(out.getvalue(), r"saving .*with space/parent\.step \(cadgen export '.*with space/parent\.step' waits for it\)")
        self.run_handed(handed)

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
        from cadgen.store.exports import PublicationRefused
        from cadgen.store.records import read_record

        step = self.parent.with_suffix(".step")
        with self.supervised() as handed:
            self.command()
        step.write_bytes(b"another writer's document")  # what the build found is gone
        [request] = [request for request, _model in handed if request.get("step") == str(step)]
        with self.assertRaisesRegex(PublicationRefused, "changed during the build"):
            artifacts.execute(request)
        entry = read_record(self.parent)["outputs"][str(step)]
        self.assertGreater(entry["owed"]["since"], 0)
        self.assertIn("PublicationRefused: ", entry["owed"]["error"])
        self.assertEqual(step.read_bytes(), b"another writer's document")
        step.unlink()
        _out, err = self.command()
        self.assertIn("owed since", err.splitlines()[0])
        self.assertIn("changed during the build", err.splitlines()[0])
        self.assertNotIn("owed", read_record(self.parent)["outputs"][str(step)])
        self.assertTrue(step.is_file())

    def test_step_build_answers_with_the_generated_tree_and_saving_paths(self):
        from cadgen import step
        from cadgen.store.records import read_record

        self.command()
        destination = self.root / "copy.step"
        with self.supervised() as handed:
            result = step.build(self.parent.with_suffix(".step"), destination)
        self.assertTrue(result.ok)
        self.assertEqual(result.tree, read_record(destination)["tree"])
        self.assertEqual(result.saving, (destination,))
        self.assertTrue(any(line.startswith("saving ") for line in result.human_lines()))
        self.assertFalse(destination.exists())
        self.run_handed(handed)
        self.assertTrue(destination.is_file())


class WorkerAnswer(unittest.TestCase):
    def run_worker(self, command, answer: str) -> tuple[int, list[dict]]:
        """``command`` as a worker's job, the supervisor answering each hand-off with ``answer``."""
        from cadgen.daemon import worker

        frames: list[dict] = []
        with mock.patch.object(worker, "_emit", side_effect=frames.append), \
                mock.patch.object(worker, "_tool_main", return_value=command), \
                mock.patch.object(worker, "_enter", return_value=True), \
                mock.patch.object(worker.sys, "stdin", io.StringIO(answer)), \
                mock.patch.object(worker, "_park"), mock.patch.object(worker, "_evict_first_party_modules"):
            return worker._run({"tool": "run", "argv": [], "prog": "python part.py"}), frames

    def test_a_worker_hands_each_owed_job_to_its_supervisor_and_answers_with_its_exit(self):
        """Each job is handed over as an ``owes`` frame, the model named as its events name it,
        and the build goes on only once the supervisor says the job is in its ledger; nothing
        is left for the worker after the command's exit."""
        from cadgen.authoring import build_run

        request = {"kind": "display", "tree": "a" * 64, "cids": ["1" * 16], "producer": {}}

        def command(_argv, **_kwargs):
            with build_run() as run:
                self.assertIsNone(run.owe(request, model="/w/part.py::part"))
            print("built part.step")
            return 0

        code, frames = self.run_worker(command, '{"kind": "owed", "job": "epoch:job-2"}\n')
        self.assertEqual(code, 0)
        self.assertEqual([frame for frame in frames if "owes" in frame],
                         [{"owes": {"request": request, "model": "/w/part.py"}}])

    def test_a_build_fails_saying_so_when_its_supervisor_refuses_a_job(self):
        from cadgen.authoring import build_run

        def command(_argv, **_kwargs):
            with build_run() as run:
                run.owe({"kind": "display"}, model="/w/part.py::part")
            return 0

        code, frames = self.run_worker(command, '{"kind": "owed", "error": "ValueError: no"}\n')
        self.assertEqual(code, 1)
        said = "".join(frame.get("data", "") for frame in frames if frame.get("stream") == "stderr")
        self.assertIn("did not take a job this build owes: ValueError: no", said)


if __name__ == "__main__":
    unittest.main()
