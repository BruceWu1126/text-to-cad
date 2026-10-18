"""A newer result's export supersedes an older one's (STORE.md §3, *Exports*; §7).

An export writes only while the model's record still names its result: once a newer build of
the model has recorded its result, the older run's files are the newer build's to write. A
build accepts, on the file it saves, the bytes the same model's previous export saved while
its body ran, and still refuses anyone else's. A kept STEP whose bytes the store maps to
another tree is mapped back to its result.

The older run is built in this process, its export jobs held -- submitted, not yet run
(``held_jobs``) -- while the newer build runs through the daemon, as two edits in a row do;
or its job runs here, and the newer result lands while the job writes.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import os
import subprocess
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.fixture_requests import cleanup_after_requests
from tests.python.support.paths import REPO_ROOT, add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

PART = """\
from cadgen import step
from cadgen import build123d as bd

WIDTH = {width}


@step
def part():
    return bd.Box(WIDTH, 3.0, 2.0)


if __name__ == "__main__":
    part()
"""

# The body says it has started, then waits for the test to let it go on.
WAITING_PART = """\
import os
import time
from pathlib import Path

from cadgen import step
from cadgen import build123d as bd

WIDTH = {width}
HERE = Path(__file__).resolve().parent


@step
def part():
    (HERE / "started").write_text("started")
    while not os.path.exists(HERE / "go"):
        time.sleep(0.01)
    return bd.Box(WIDTH, 3.0, 2.0)


if __name__ == "__main__":
    part()
"""

CHILD = """\
from cadgen import step
from cadgen import build123d as bd

DEPTH = {depth}


@step
def child():
    return bd.Box(4.0, 3.0, DEPTH)
"""

PARENT = """\
from cadgen import step
from cadgen import build123d as bd
from child import child


@step
def parent():
    return bd.Compound(children=[bd.Pos(8, 0, 0) * child(), bd.Box(2, 2, 2)], label="parent")


if __name__ == "__main__":
    parent()
"""


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class _HeldJobs:
    """The export and mesh jobs this process's builds submitted, not yet run."""

    def __init__(self) -> None:
        self.pending: list[tuple[dict, object]] = []

    def run(self) -> None:
        """Run them now, here, each as a pool worker would (``artifacts.execute``)."""
        from cadgen.daemon import artifacts

        pending, self.pending = self.pending, []
        for request, future in pending:
            try:
                future.set_result(artifacts.execute(request))
            except Exception as error:  # noqa: BLE001 - the job's failure is its future's, as a worker's is
                future.set_exception(error)


@contextlib.contextmanager
def held_jobs():
    """Every artifact job this process submits runs here -- a display job at once, an export or
    mesh job only when the test runs ``held.run()``: an older run whose files are still being
    written while a newer build runs."""
    from concurrent.futures import Future

    from cadgen.daemon import artifacts

    held = _HeldJobs()

    def submit(request, *, store_root=None):
        future = Future()
        if request["kind"] in ("export", "mesh"):
            held.pending.append((request, future))
            return future
        try:
            future.set_result(artifacts.execute(request))
        except Exception as error:  # noqa: BLE001 - as a worker's
            future.set_exception(error)
        return future

    with mock.patch.object(artifacts, "submit_artifact", side_effect=submit):
        yield held


class ExportSupersession(unittest.TestCase):
    def setUp(self):
        temporary = generated_cad_directory(prefix="supersession-")
        self.root = Path(temporary.name).resolve()
        self.stores: set[str] = set()
        patcher = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store")})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(cleanup_after_requests, temporary, self.stores, environment=dict(os.environ))

    def _command(self, script: Path) -> dict:
        self.stores.add(os.path.realpath(self.root / "store"))
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(filter(None, [
            str(REPO_ROOT / "packages" / "cadgen" / "src"), os.environ.get("PYTHONPATH", "")]))}
        env.pop("CADGEN_DAEMON_CHILD", None)
        return {"args": [sys.executable, script.name], "cwd": str(script.parent), "env": env,
                "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True}

    def build(self, script: Path) -> subprocess.CompletedProcess:
        """``python <script>``, through the daemon, as a person runs it."""
        return subprocess.run(**self._command(script), timeout=600)

    @contextlib.contextmanager
    def held(self, script: Path):
        """A run of ``script`` built in this process, its export jobs held until the test runs
        them (``jobs.run()``, else as the block ends); the run awaits them as the block ends."""
        from cadgen._internal.generation import _selected_specs_for_targets, build_model
        from cadgen.authoring import build_run
        from cadgen.cli_logging import CliLogger

        self.stores.add(os.path.realpath(self.root / "store"))
        logger = CliLogger("cadgen", verbose=False)
        with held_jobs() as jobs, build_run(logger):
            _specs, (spec,) = _selected_specs_for_targets([str(script)])
            build_model(spec, generator=None, force=False, profile=False, logger=logger)
            try:
                yield jobs
            finally:
                jobs.run()

    def finish_waiting(self, process: subprocess.Popen) -> None:
        # Killing an early-answer client would leave its daemon body waiting for go.
        (self.root / "go").touch()
        try:
            process.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)
            raise

    def await_started(self, process: subprocess.Popen) -> None:
        deadline = time.monotonic() + 300
        while not (self.root / "started").exists():
            if process.poll() is not None:
                self.fail(f"the build ended before its body started: {process.communicate()[1][-3000:]}")
            self.assertLess(time.monotonic(), deadline, "the build's body never started")
            time.sleep(0.02)

    def assert_holds_its_result(self, script: Path) -> str:
        """The model's file holds the result its record names: its bytes map to that tree."""
        from cadgen.store.records import read_record, tree_for_document_hash
        from cadgen._internal.doors import await_writes

        await_writes(script.with_suffix(".step"))
        tree = read_record(script)["tree"]
        self.assertEqual(tree_for_document_hash(_digest(script.with_suffix(".step"))), tree)
        return tree

    def test_an_older_runs_export_is_skipped_once_a_newer_result_is_recorded(self):
        from cadgen.store.records import read_record

        script = self.root / "part.py"
        script.write_text(PART.format(width=10.0), encoding="utf-8")
        said = io.StringIO()
        with contextlib.redirect_stderr(said), self.held(script) as jobs:
            older = read_record(script)["tree"]
            script.write_text(PART.format(width=11.0), encoding="utf-8")
            newer = self.build(script)
            self.assertEqual(newer.returncode, 0, newer.stderr[-3000:])
            jobs.run()
        self.assertRegex(said.getvalue(), r"part\.step not written: a newer build of \S*part\.py replaced this result")
        self.assertNotEqual(self.assert_holds_its_result(script), older, "the file holds the newer result")

    def test_a_newer_result_landing_while_an_export_writes_supersedes_it(self):
        from cadgen.store import exports

        script = self.root / "part.py"
        script.write_text(PART.format(width=10.0), encoding="utf-8")
        original = exports.export_step

        def newer_lands(*args, **kwargs):
            result = original(*args, **kwargs)
            script.write_text(PART.format(width=11.0), encoding="utf-8")
            newer = self.build(script)
            self.assertEqual(newer.returncode, 0, newer.stderr[-3000:])
            self.assert_holds_its_result(script)
            return result

        said = io.StringIO()
        with contextlib.redirect_stderr(said), mock.patch.object(exports, "export_step", side_effect=newer_lands), \
                self.held(script) as jobs:
            jobs.run()
        self.assertRegex(said.getvalue(), r"part\.step not written: a newer build")
        self.assertNotIn("changed during the build", said.getvalue())
        self.assert_holds_its_result(script)

    def test_a_newer_result_supersedes_mesh_preparation_and_its_receipt(self):
        from cadgen._internal.doors import await_writes
        from cadgen._internal.mesh_export import run_mesh_exporter
        from cadgen.store.records import read_record

        script = self.root / "part.py"
        source = PART.replace("from cadgen import step", "from cadgen import stl").replace("@step", "@stl")
        script.write_text(source.format(width=10.0), encoding="utf-8")
        document = self.root / "part.stl"
        newer = {}

        def prepared(*args, **kwargs):
            result = run_mesh_exporter(*args, **kwargs)
            script.write_text(source.format(width=11.0), encoding="utf-8")
            built = self.build(script)
            self.assertEqual(built.returncode, 0, built.stderr[-3000:])
            await_writes(document)
            newer.update(digest=_digest(document), record=read_record(script))
            return result

        said = io.StringIO()
        with contextlib.redirect_stderr(said), \
                mock.patch("cadgen._internal.mesh_export.run_mesh_exporter", side_effect=prepared), \
                self.held(script) as jobs:
            jobs.run()
        self.assertRegex(said.getvalue(), r"part\.stl not written: a newer build")
        self.assertEqual(_digest(document), newer["digest"])
        self.assertEqual(read_record(script), newer["record"], "an old receipt cannot enter the newer record")

    def test_a_changed_mesh_recipe_supersedes_an_export_of_the_same_geometry(self):
        from cadgen._internal.mesh_export import run_mesh_exporter
        from cadgen.store.records import read_record, write_record

        script = self.root / "part.py"
        source = PART.replace("from cadgen import step", "from cadgen import stl").replace(
            "@step", "@stl(mesh_tolerance=0.05)")
        script.write_text(source.format(width=10.0), encoding="utf-8")
        document = self.root / "part.stl"
        before = {}
        after = {}

        def newer_recipe(*args, **kwargs):
            prepared = run_mesh_exporter(*args, **kwargs)
            before.update(read_record(script))
            newer = dict(before)
            newer["outputs"] = {**before["outputs"], str(document): {
                "sha256": "", "declared": "stl", "chord": "0.025", "angle": "0.1", "name": "part"}}
            write_record(before["model"], newer)
            command = self._command(script)
            command["args"] = [sys.executable, "-m", "cadgen.cli", "export", str(document)]
            built = subprocess.run(**command, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            after.update(read_record(script))
            return prepared

        said = io.StringIO()
        with contextlib.redirect_stderr(said), \
                mock.patch("cadgen._internal.mesh_export.run_mesh_exporter", side_effect=newer_recipe), \
                self.held(script) as jobs:
            jobs.run()
        self.assertEqual((before["key"], before["tree"]), (after["key"], after["tree"]))
        self.assertEqual(after["outputs"][str(document)]["chord"], "0.025")
        self.assertEqual(read_record(script), after)
        self.assertRegex(said.getvalue(), r"part\.stl not written: a newer build")

    def test_a_foreign_mesh_edit_during_preparation_is_not_overwritten(self):
        from cadgen._internal.mesh_export import run_mesh_exporter
        from cadgen.store.records import read_record

        script = self.root / "part.py"
        source = PART.replace("from cadgen import step", "from cadgen import stl").replace("@step", "@stl")
        script.write_text(source.format(width=10.0), encoding="utf-8")
        document = self.root / "part.stl"

        def edited(*args, **kwargs):
            result = run_mesh_exporter(*args, **kwargs)
            document.write_bytes(b"foreign mesh")
            return result

        with self.assertRaisesRegex(RuntimeError, "mesh file changed during the build"), \
                mock.patch("cadgen._internal.mesh_export.run_mesh_exporter", side_effect=edited), \
                self.held(script) as jobs:
            jobs.run()
        self.assertEqual(document.read_bytes(), b"foreign mesh")
        self.assertIn("mesh file changed", read_record(script)["outputs"][str(document)]["owed"]["error"])

    def test_a_childs_export_under_its_parent_is_skipped_likewise(self):
        from cadgen.store.records import read_record

        (self.root / "child.py").write_text(CHILD.format(depth=2.0), encoding="utf-8")
        parent = self.root / "parent.py"
        parent.write_text(PARENT, encoding="utf-8")
        said = io.StringIO()
        with contextlib.redirect_stderr(said), self.held(parent) as jobs:
            older = {name: read_record(self.root / f"{name}.py")["tree"] for name in ("child", "parent")}
            (self.root / "child.py").write_text(CHILD.format(depth=3.0), encoding="utf-8")
            newer = self.build(parent)
            self.assertEqual(newer.returncode, 0, newer.stderr[-3000:])
            jobs.run()
        for name in ("child", "parent"):
            self.assertRegex(said.getvalue(), rf"{name}\.step not written: a newer build of \S*{name}\.py replaced this result")
            self.assertNotEqual(self.assert_holds_its_result(self.root / f"{name}.py"), older[name], name)

    def test_the_models_previous_export_landing_during_a_build_is_its_own(self):
        script = self.root / "part.py"
        script.write_text(PART.format(width=10.0), encoding="utf-8")
        with self.held(script) as jobs:
            script.write_text(WAITING_PART.format(width=11.0), encoding="utf-8")
            newer = subprocess.Popen(**self._command(script))
            self.addCleanup(self.finish_waiting, newer)
            self.await_started(newer)
            jobs.run()
        # The older run's export landed while the newer build's body runs.
        older = self.assert_holds_its_result(script)
        (self.root / "go").write_text("go", encoding="utf-8")
        _out, err = newer.communicate(timeout=600)
        self.assertEqual(newer.returncode, 0, err[-3000:])
        self.assertNotEqual(self.assert_holds_its_result(script), older, "the newer build saved its own file")

    def test_a_person_editing_the_file_during_a_build_still_stops_its_save(self):
        script = self.root / "part.py"
        script.write_text(PART.format(width=10.0), encoding="utf-8")
        self.assertEqual(self.build(script).returncode, 0)
        self.assert_holds_its_result(script)
        script.write_text(WAITING_PART.format(width=11.0), encoding="utf-8")
        newer = subprocess.Popen(**self._command(script))
        self.addCleanup(self.finish_waiting, newer)
        self.await_started(newer)
        edited = b"ISO-10303-21;\nEND-ISO-10303-21;\n"
        (self.root / "part.step").write_bytes(edited)
        (self.root / "go").write_text("go", encoding="utf-8")
        _out, err = newer.communicate(timeout=600)
        self.assertEqual(newer.returncode, 0, err[-3000:])
        from cadgen._internal.doors import await_writes
        from cadgen.daemon.client import watch_jobs
        from cadgen.store.records import read_record

        document = self.root / "part.step"
        await_writes(document)
        expected = "the STEP file or its annotations changed during the build"
        owed = read_record(script)["outputs"][str(document)]["owed"]
        self.assertIn(expected, owed["error"])
        rows = watch_jobs(output=str(document), store_root=os.environ["CADGEN_CACHE_DIR"])["jobs"]
        self.assertTrue(any(row["state"] == "failed" and expected in (row.get("error") or "") for row in rows), rows)
        self.assertEqual(document.read_bytes(), edited, "the person's file stays after the refused save")
        rerun = self.build(script)
        self.assertEqual(rerun.returncode, 0, rerun.stderr[-3000:])
        self.assertIn("owed since", rerun.stderr.splitlines()[0])
        self.assertIn(expected, rerun.stderr.splitlines()[0])
        await_writes(document)

    def test_a_kept_step_the_store_maps_to_a_compile_is_mapped_back_to_its_result(self):
        from cadgen._internal.doors import document_snapshot
        from cadgen.store.index import remove_entry
        from cadgen.store.records import document_key, tree_for_document_hash

        script = self.root / "part.py"
        script.write_text(PART.format(width=10.0), encoding="utf-8")
        self.assertEqual(self.build(script).returncode, 0)
        result = self.assert_holds_its_result(script)
        document = script.with_suffix(".step")
        # A document-schema change left the bytes unmapped, and a door opened the file before
        # the model's next run: the compile of its bytes stands for it.
        remove_entry("document", document_key(_digest(document)))
        self.assertNotEqual(document_snapshot(document)[1], result)
        rerun = self.build(script)
        self.assertEqual(rerun.returncode, 0, rerun.stderr[-3000:])
        self.assertIn("current", rerun.stdout)
        self.assertEqual(tree_for_document_hash(_digest(document)), result, "the entry names the result again")


if __name__ == "__main__":
    unittest.main()
