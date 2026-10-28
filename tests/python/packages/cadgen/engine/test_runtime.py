"""Caller Python identities, mutable dependencies and shared-server workers across venvs."""
from __future__ import annotations

import io
import json
import os
import subprocess
import socket
import sys
import tempfile
import time
import unittest
import venv
from pathlib import Path
from unittest import mock

from cadgen.engine import runtime
from cadgen.engine.daemon import Pool, Worker
from tests.python.packages.cadgen.engine.test_pool import Fake


class RuntimeTest(unittest.TestCase):
    def _editable_project(self, root, layout=""):
        project, site = root / "project", root / "site"
        source = project / layout / "dependency.py"
        source.parent.mkdir(parents=True)
        source.write_text("WIDTH=1\n", encoding="utf-8")
        egg, wheel = source.parent / "custom_dependency.egg-info", site / "custom_dependency-1.dist-info"
        egg.mkdir()
        wheel.mkdir(parents=True)
        (egg / "PKG-INFO").write_text("Name: custom_dependency\nVersion: 1\n", encoding="utf-8")
        (egg / "SOURCES.txt").write_text("dependency.py\n", encoding="utf-8")
        (wheel / "METADATA").write_text("Name: Custom-Dependency\nVersion: 1\n", encoding="utf-8")
        (wheel / "direct_url.json").write_text(json.dumps({"url": project.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8")
        (site / "custom_editable.pth").write_text("# editable import hook\n", encoding="utf-8")
        (wheel / "RECORD").write_text("custom_editable.pth,,\ncustom_dependency-1.dist-info/METADATA,,\n"
                                    "custom_dependency-1.dist-info/direct_url.json,,\n", encoding="utf-8")
        return project, site, source, egg, wheel

    def test_plain_script_and_safe_path_worker_agree_on_editable_project(self):
        with tempfile.TemporaryDirectory() as folder:
            project, site, _, egg, wheel = self._editable_project(Path(folder).resolve())
            script = project / "snapshot.py"
            script.write_text("import json, os, sys\nfrom importlib import metadata\nfrom cadgen.engine import runtime\n"
                              "execution = runtime.describe()\n"
                              "if not sys.flags.safe_path:\n"
                              "    from cadgen.engine.daemon import Worker\n"
                              "    worker = Worker(__file__, dict(os.environ), execution=execution)\n"
                              "    worker.stop()\n"
                              "print(json.dumps({'execution':execution, 'metadata': [str(d._path) "
                              "for d in metadata.distributions() if d.name.lower().replace('_','-') == 'custom-dependency']}))\n",
                              encoding="utf-8")
            env = {name: value for name, value in os.environ.items() if name != "PYTHONSAFEPATH"}
            paths = [str(site), str(Path(runtime.__file__).resolve().parents[2])]
            paths.extend(os.path.abspath(path) for path in env.get("PYTHONPATH", "").split(os.pathsep) if path)
            env.update(PYTHONPATH=os.pathsep.join(paths), PYTHONDONTWRITEBYTECODE="1",
                       CADGEN_ENGINE_DIR=str(project.parent / "engine"), CADGEN_STATE_DIR=str(project.parent / "state"),
                       CADGEN_CACHE_DIR=str(project.parent / "cache"))
            plain, worker = [json.loads(subprocess.check_output([sys.executable, *flags, str(script)],
                                                               cwd=project, env=env, text=True))
                             for flags in ([], ["-P"])]
            self.assertEqual(plain["metadata"], [str(egg), str(wheel)])
            self.assertEqual(worker["metadata"], [str(wheel)])
            self.assertEqual(plain["execution"], worker["execution"])

    def test_editable_metadata_layouts_canonicalize_without_losing_live_mutations(self):
        from importlib import metadata

        self.addCleanup(runtime._records.clear)
        for layout in ("", "src", "custom/python"):
            with self.subTest(layout=layout), tempfile.TemporaryDirectory() as folder:
                project, site, source, egg, wheel = self._editable_project(Path(folder).resolve(), layout)
                (egg / "PKG-INFO").write_text("Name: Custom_.-Dependency\nVersion: 1\n", encoding="utf-8")
                direct = {"url": (project / ".." / project.name).as_uri(), "dir_info": {"editable": True}}
                (wheel / "direct_url.json").write_text(json.dumps(direct), encoding="utf-8")
                source_dist, installed = metadata.Distribution.at(egg), metadata.Distribution.at(wheel)
                with mock.patch.object(runtime.metadata, "distributions", return_value=[source_dist, installed]) as discover:
                    first = runtime.dependencies()
                    for distributions in ([installed], [installed, source_dist]):
                        discover.return_value = distributions
                        self.assertEqual(first, runtime.dependencies())
                    discover.return_value = [source_dist, installed]
                    for target in (source, source.with_suffix(".so"), site / "custom_editable.pth", wheel / "METADATA"):
                        before = runtime.dependencies()
                        stamp = target.stat() if target.exists() else None
                        content = target.read_text(encoding="utf-8") + "Summary: changed\n" if target == wheel / "METADATA" else (
                            "WIDTH=2\n" if target == source else "changed\n")
                        target.write_text(content, encoding="utf-8")
                        if stamp is not None:
                            os.utime(target, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                        self.assertNotEqual(before, runtime.dependencies(), target)
                    # A new RECORD entry is picked up even with a warm inventory cache.
                    (site / "another.py").write_text("VALUE=1\n", encoding="utf-8")
                    before = runtime.dependencies()
                    with (wheel / "RECORD").open("a", encoding="utf-8") as record:
                        record.write("another.py,,\n")
                    self.assertNotEqual(before, runtime.dependencies())
                    # Replacing an equivalent source record must keep its precedence over
                    # other installations, rather than skipping it and promoting a later one.
                    other_info = site / "other" / wheel.name
                    other_info.mkdir(parents=True)
                    (other_info / "METADATA").write_text("Name: custom-dependency\nVersion: 1\n", encoding="utf-8")
                    other = metadata.Distribution.at(other_info)
                    discover.return_value = [installed]
                    canonical = runtime.dependencies()
                    discover.return_value = [source_dist, other, installed]
                    self.assertEqual(canonical, runtime.dependencies())
                    discover.return_value = [other]
                    distinct = runtime.dependencies()
                    discover.return_value = [other, source_dist, installed]
                    self.assertEqual(distinct, runtime.dependencies())
                    self.assertNotEqual(distinct, canonical)

    def test_distinct_or_installed_legacy_metadata_keeps_discovery_precedence(self):
        from importlib import metadata

        self.addCleanup(runtime._records.clear)
        for difference in ("version", "root", "not-editable", "no-direct-url", "installed-egg", "no-sources", "nested-venv", "installed-site"):
            with self.subTest(difference=difference), tempfile.TemporaryDirectory() as folder:
                layout = {"nested-venv": ".venv/lib", "installed-site": "site-packages"}.get(difference, "")
                project, _, _, egg, wheel = self._editable_project(
                    Path(folder).resolve(), layout)
                if difference == "version":
                    (egg / "PKG-INFO").write_text("Name: custom-dependency\nVersion: 2\n", encoding="utf-8")
                elif difference in ("root", "not-editable"):
                    direct = {"url": (project.parent / "other" if difference == "root" else project).as_uri(),
                              "dir_info": {"editable": difference == "root"}}
                    (wheel / "direct_url.json").write_text(json.dumps(direct), encoding="utf-8")
                elif difference == "no-direct-url":
                    (wheel / "direct_url.json").unlink()
                elif difference == "installed-egg":
                    (egg / "installed-files.txt").write_text("../dependency.py\n", encoding="utf-8")
                elif difference == "no-sources":
                    (egg / "SOURCES.txt").unlink()
                source_dist, installed = metadata.Distribution.at(egg), metadata.Distribution.at(wheel)
                with mock.patch.object(runtime.metadata, "distributions", return_value=[source_dist, installed]) as discover:
                    first = runtime.dependencies()
                    discover.return_value = [source_dist]
                    self.assertEqual(first, runtime.dependencies())
                    discover.return_value = [installed, source_dist]
                    self.assertNotEqual(first, runtime.dependencies())

    def test_actual_interpreter_is_lexical_and_not_virtual_env(self):
        prefix = str(Path(tempfile.gettempdir()) / "venv")
        python = str(Path(prefix) / "bin" / "python")
        with mock.patch.object(sys, "executable", python), mock.patch.object(sys, "prefix", prefix), \
                mock.patch.object(runtime, "dependencies", return_value="deps"):
            found = runtime.describe({"VIRTUAL_ENV": "/wrong", "PYTHONPATH": os.pathsep.join(("src", ""))})
        self.assertEqual(found["python"], python)
        self.assertEqual(found["prefix"], prefix)
        self.assertEqual(found["startup"]["PYTHONPATH"], os.pathsep.join((str(Path.cwd() / "src"), str(Path.cwd()))))

    def test_release_and_native_document_compatibility_are_independent(self):
        with mock.patch.object(runtime, "dependencies", return_value="deps"):
            found = runtime.describe()
        for field, value, words in (("release", found["release"] + ".1", "same release"),
                                    ("compatibility", {"format": "other"}, "native kernel/document")):
            wrong = {**found, field: value}
            wrong["fingerprint"] = runtime.digest({key: item for key, item in wrong.items() if key != "fingerprint"})
            with self.assertRaisesRegex(ValueError, words):
                runtime.validate(wrong)

    def test_distribution_version_and_editable_source_changes_invalidate(self):
        import importlib.metadata as metadata

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "src" / "custom.py"
            source.parent.mkdir()
            source.write_text("WIDTH=1\n", encoding="utf-8")
            info = root / "custom-1.dist-info"
            info.mkdir()
            (info / "METADATA").write_text("Name: custom\nVersion: 1\n", encoding="utf-8")
            (info / "RECORD").write_text("custom-1.dist-info/METADATA,,\n", encoding="utf-8")
            (info / "direct_url.json").write_text(json.dumps({"url": root.as_uri(), "dir_info": {"editable": True}}), encoding="utf-8")
            with mock.patch.object(runtime.metadata, "distributions", side_effect=lambda: metadata.Distribution.discover(path=[folder])):
                first = runtime.dependencies()
                self.assertEqual(first, runtime.dependencies())
                source.write_text("WIDTH=2\n", encoding="utf-8")
                second = runtime.dependencies()
                self.assertNotEqual(first, second)
                (info / "METADATA").write_text("Name: custom\nVersion: 2\n", encoding="utf-8")
                self.assertNotEqual(second, runtime.dependencies())

    def test_runtime_inventory_reads_quoted_record_paths_and_preserves_legacy_fallback(self):
        from importlib import metadata

        self.addCleanup(runtime._records.clear)
        for inventory in ("record", "legacy", "empty-record"):
            with self.subTest(inventory=inventory), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                source, stub = root / "custom,geometry.py", root / "custom.pyi"
                source.write_text("WIDTH=1\n", encoding="utf-8")
                stub.write_text("WIDTH: int\n", encoding="utf-8")
                info = root / "custom-1.dist-info"
                info.mkdir()
                (info / "METADATA").write_text("Name: custom\nVersion: 1\n", encoding="utf-8")
                if inventory == "record":
                    (info / "RECORD").write_text(
                        '"custom,geometry.py",,\ncustom.pyi,,\nmissing.py,,\ncustom-1.dist-info/METADATA,,\n',
                        encoding="utf-8")
                elif inventory == "empty-record":
                    (info / "RECORD").write_text("", encoding="utf-8")
                dist = metadata.Distribution.at(info)
                listing = mock.PropertyMock(side_effect=AssertionError("full distribution inventory walked")) if (
                    inventory == "record") else mock.PropertyMock(return_value=[
                        Path(source.name), Path(stub.name), Path("missing.py"), Path("custom-1.dist-info/METADATA")])
                runtime._records.clear()
                with mock.patch.object(runtime.metadata, "distributions", return_value=[dist]), mock.patch.object(
                        type(dist), "files", listing):
                    first = runtime.dependencies()
                    stub.write_text("WIDTH: float\n", encoding="utf-8")
                    self.assertEqual(first, runtime.dependencies(), "type-only stubs do not affect execution")
                    stamp = source.stat()
                    source.write_text("WIDTH=2\n", encoding="utf-8")
                    os.utime(source, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    changed = runtime.dependencies()
                    self.assertNotEqual(first, changed, "a quoted runtime path retains mutation-time validation")
                    (root / "missing.py").write_text("VALUE=1\n", encoding="utf-8")
                    reappeared = runtime.dependencies()
                    self.assertNotEqual(changed, reappeared, "a recorded missing source can reappear")
                    source.unlink()
                    removed = runtime.dependencies()
                    self.assertNotEqual(reappeared, removed, "removing runtime source invalidates the snapshot")
                    runtime._records.clear()
                    self.assertEqual(removed, runtime.dependencies(), "cold and warm inventories agree after deletion")
                    source.write_text("WIDTH=2\n", encoding="utf-8")
                    self.assertNotEqual(removed, runtime.dependencies(), "restoring source invalidates the snapshot")
                self.assertEqual(listing.call_count, 0 if inventory == "record" else 2)

    def test_pool_keeps_venvs_warm_and_replaces_changed_dependencies(self):
        def start(script, environment, cwd, execution):
            worker = Fake(script, environment, cwd)
            worker.execution = execution
            return worker

        pool = Pool(4, start)
        self.addCleanup(pool.stop)
        with mock.patch.object(runtime, "dependencies", return_value="deps"):
            first = runtime.describe()
        second = {**first, "prefix": "/other", "fingerprint": "other"}
        a = pool.lease("a.py", {}, execution=first)
        pool.release(a)
        b = pool.lease("a.py", {}, execution=second)
        pool.release(b)
        self.assertIs(pool.lease("a.py", {}, execution=first), a)
        pool.release(a)
        changed = {**first, "dependencies": "updated", "fingerprint": "updated"}
        newer = pool.lease("a.py", {}, execution=changed)
        pool.release(newer)
        self.assertTrue(a.stopped)
        self.assertFalse(b.stopped)
        self.assertIsNot(newer, a)
        self.assertEqual(len(pool.workers()), 2)

    def test_worker_checks_actual_import_before_model_execution_and_never_injects_server_code(self):
        with mock.patch.object(runtime, "dependencies", return_value="deps"):
            found = runtime.describe()
        found = {**found, "checkout": False}
        found["fingerprint"] = runtime.digest({key: value for key, value in found.items() if key != "fingerprint"})
        other = {**found, "prefix": "/wrong"}
        other["fingerprint"] = runtime.digest({key: value for key, value in other.items() if key != "fingerprint"})
        process = mock.Mock(stdout=io.BytesIO((json.dumps({"ready": 123, "execution": other}) + "\n").encode()),
                            stderr=io.BytesIO(), stdin=io.BytesIO())
        with mock.patch("cadgen.engine.daemon.subprocess.Popen", return_value=process) as launched:
            with self.assertRaisesRegex(ValueError, "actual Python environment"):
                Worker("model.py", {"CADGEN_ENGINE_WORKER": "spoof"}, execution=found)
        self.assertEqual(launched.call_args.args[0][:4], [found["python"], "-P", "-m", "cadgen.engine.worker"])
        self.assertNotIn("PYTHONPATH", launched.call_args.kwargs["env"])
        self.assertEqual(launched.call_args.kwargs["env"]["CADGEN_ENGINE_WORKER"], "1")

    def test_an_interpreter_without_cadgen_fails_without_source_injection(self):
        from cadgen.engine.daemon import WorkerGone

        with tempfile.TemporaryDirectory(prefix="cadgen-missing-python-") as folder:
            root = Path(folder)
            location = root / "venv"
            venv.EnvBuilder(with_pip=False).create(location)
            python = location / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
            env.update(CADGEN_ENGINE_DIR=str(root / "engine"), CADGEN_CACHE_DIR=str(root / "cache"),
                       CADGEN_STATE_DIR=str(root / "state"))
            with mock.patch.dict(os.environ, env, clear=True):
                found = runtime.describe(env)
                found.update(python=str(python), prefix=str(location), checkout=False)
                found["fingerprint"] = runtime.digest({name: value for name, value in found.items() if name != "fingerprint"})
                with self.assertRaisesRegex(WorkerGone, "No module named 'cadgen'"):
                    Worker(str(root / "never-executed.py"), env, cwd=str(root), execution=found)

    def test_native_handover_and_returned_shape_refuse_before_reading_bytes(self):
        from cadgen.engine import api, daemon

        engine = daemon.Daemon(cap=1)
        self.addCleanup(engine.pool.stop)
        execution = runtime.describe()
        wrong = {**execution, "compatibility": {"format": "incompatible"}}
        wrong["fingerprint"] = runtime.digest({name: value for name, value in wrong.items() if name != "fingerprint"})
        answers = []
        with mock.patch.object(engine.models, "hand_over", side_effect=AssertionError("native bytes were read")):
            engine.handle({"op": "shape", "execution": wrong, "xbf": "not-base64"}, answers.append)
        self.assertIn("native kernel/document", answers[-1]["failed"])
        handle = api.Document("cube", Path("cube.py"), {"id": "cube"}, Path.cwd())
        with mock.patch.object(api, "_ask", return_value={"done": {"compatibility": wrong["compatibility"]},
                                                        "payload": "not-base64"}):
            with self.assertRaisesRegex(ValueError, "native kernel/document"):
                handle._transferred()

    def test_restored_shape_uses_server_document_worker_without_pythonpath(self):
        from tests.python.support import engine_daemon

        with tempfile.TemporaryDirectory(prefix="cadgen-restored-shape-") as folder:
            root = Path(folder)
            env = {name: value for name, value in os.environ.items() if name != "PYTHONPATH"}
            env.update(CADGEN_PORT=str(engine_daemon.free_port()), CADGEN_ENGINE_DIR=str(root / "engine"),
                       CADGEN_STATE_DIR=str(root / "state"), CADGEN_CACHE_DIR=str(root / "cache"),
                       CADGEN_ENGINE_STANDBY="0")
            program = """import build123d as bd
from cadgen.engine import api, client
try:
    with client.connect(start=True): pass
    original = api.hand_over(bd.Box(1, 1, 1), 'restored')
    client.request({'op': 'stop'}, start=False)
    assert client.wait_gone()
    with client.connect(start=True): pass
    restored = api.Document(original.name, original.script, original.report, original.cwd)
    assert abs(restored.shape.volume - 1) < 1e-9
finally:
    client.request({'op': 'stop'}, start=False)
    client.wait_gone()
"""
            result = engine_daemon.run([sys.executable, "-c", program], env=env, cwd=Path.cwd())
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_cadgen_snapshot_uses_actual_source_independently_of_editable_metadata_order(self):
        from importlib import metadata

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "src" / "cadgen" / "engine" / "runtime.py"
            source.parent.mkdir(parents=True)
            source.write_text("VALUE=1\n", encoding="utf-8")
            locations = [root / "cadgen-0.8.0.dist-info", root / "cadgen.egg-info"]
            for info in locations:
                info.mkdir()
                name = "METADATA" if info.name.endswith("dist-info") else "PKG-INFO"
                (info / name).write_text("Name: cadgen\nVersion: 0.8.0\n", encoding="utf-8")
            distributions = [metadata.Distribution.at(path) for path in locations]
            with mock.patch.object(runtime, "__file__", str(source)), \
                    mock.patch.object(runtime.metadata, "distributions", return_value=distributions) as discover:
                first = runtime.dependencies()
                discover.return_value = list(reversed(distributions))
                self.assertEqual(first, runtime.dependencies())
                source.write_text("VALUE=2\n", encoding="utf-8")
                self.assertNotEqual(first, runtime.dependencies())

    def test_live_result_refuses_changed_installed_kernel_before_serializing(self):
        from types import SimpleNamespace
        from cadgen.engine import build, worker

        current = {**runtime.compatibility(), "OCP": "7.10.0"}
        source = mock.Mock(structure={"prototypes": [{"key": "part"}]})
        source.shape_data.side_effect = AssertionError("old native objects serialized")
        with mock.patch.object(build, "held", return_value=source), \
                mock.patch.object(runtime, "compatibility", return_value=current), \
                mock.patch.dict(sys.modules, {"OCP": SimpleNamespace(__version__="7.9.3.1")}):
            with self.assertRaisesRegex(ValueError, "imported native kernel"):
                worker._result({"script": "model.py", "name": "cube"})
        source.shape_data.assert_not_called()

    def test_windows_change_query_closes_its_handle_on_failure_and_never_masks_failure(self):
        import ctypes

        class Basic(ctypes.Structure):
            _fields_ = [("ChangeTime", ctypes.c_longlong)]

        opened = mock.Mock(return_value=123)
        query = mock.Mock(return_value=False)
        close = mock.Mock(return_value=True)
        refused = mock.Mock(side_effect=lambda path: OSError(f"cannot verify dependency: {path}"))
        with mock.patch.object(runtime, "_windows_files", return_value=(opened, query, close, Basic, refused)):
            with self.assertRaisesRegex(OSError, "cannot verify dependency"):
                runtime._windows_change(Path("dependency.py"))
        close.assert_called_once_with(123)
        self.assertEqual(opened.call_args.args[1:7], (0x80, 7, None, 3, 0, None))
        opened.return_value = ctypes.c_void_p(-1).value
        close.reset_mock()
        with mock.patch.object(runtime, "_windows_files", return_value=(opened, query, close, Basic, refused)):
            with self.assertRaisesRegex(OSError, "cannot verify dependency"):
                runtime._windows_change(Path("dependency.py"))
        close.assert_not_called()


class CrossVenvTest(unittest.TestCase):
    def test_two_project_venvs_share_server_and_documents_but_keep_their_dependencies(self):
        from cadgen.engine import client
        from tests.python.support import engine_daemon

        with tempfile.TemporaryDirectory(prefix="cadgen-project-python-") as folder:
            root = Path(folder)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            private = {"CADGEN_ENGINE_DIR": str(root / "engine"), "CADGEN_STATE_DIR": str(root / "state"),
                       "CADGEN_CACHE_DIR": str(root / "cache"), "CADGEN_PORT": str(port)}
            env = {**os.environ, **private, "PYTHONPATH": str(Path(runtime.__file__).resolve().parents[2]),
                   "CADGEN_ENGINE_STANDBY": "0", "CADGEN_ENGINE_WORKERS": "4", "CADGEN_ENGINE_WORKER_RSS_MB": "0"}
            server = subprocess.Popen([sys.executable, "-m", "cadgen.engine", "server"], env=env,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
            self.addCleanup(lambda: server.kill() if server.poll() is None else None)
            with mock.patch.dict(os.environ, env, clear=True):
                deadline = time.monotonic() + 60
                while not client.request({"op": "status"}, start=False):
                    if server.poll() is not None:
                        self.fail(server.stderr.read())
                    if time.monotonic() > deadline:
                        self.fail("private test server did not answer")
                    time.sleep(.01)
                try:
                    project = root / "model.py"
                    project.write_text("from cadgen import model\nimport json\n@model\ndef cube():\n"
                                       "    import build123d as bd\n    from custom_dependency import WIDTH\n"
                                       "    return bd.Box(WIDTH,1,1)\n"
                                       "if __name__ == '__main__':\n    r=cube.build()\n"
                                       "    print(json.dumps({'id':r.models[0]['id'],'fresh':r.fresh,"
                                       "'key':r.models[0]['closure']['key'],'cache':r.models[0]['cache'],'width':r.models[0].get('delta',{}).get('bounds',{}).get('now',[None])[0]}))\n", encoding="utf-8")
                    interpreters, sites = [], []
                    for index in (1, 2):
                        location = root / f"venv{index}"
                        venv.EnvBuilder(with_pip=False).create(location)
                        python = location / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                        site = Path(subprocess.check_output([str(python), "-c", "import sysconfig;print(sysconfig.get_path('purelib'))"], text=True).strip())
                        # Share immutable heavy CAD wheels without installing/downloading them twice.
                        import sysconfig
                        (site / "cad-test-dependencies.pth").write_text(sysconfig.get_path("purelib") + "\n", encoding="utf-8")
                        (site / "custom_dependency.py").write_text(f"WIDTH={index}\n", encoding="utf-8")
                        info = site / f"custom_dependency-{index}.dist-info"
                        info.mkdir()
                        (info / "METADATA").write_text(f"Name: custom-dependency\nVersion: {index}\n", encoding="utf-8")
                        (info / "RECORD").write_text(f"custom_dependency.py,,\ncustom_dependency-{index}.dist-info/METADATA,,\n", encoding="utf-8")
                        interpreters.append(python)
                        sites.append(site)
                    def build(python):
                        result = engine_daemon.run([str(python), str(project)], env=env, cwd=root)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        return json.loads(result.stdout.splitlines()[-1])
                    a, b = [build(python) for python in interpreters]
                    self.assertEqual(a["id"], b["id"])
                    self.assertNotEqual(a["key"], b["key"])
                    self.assertEqual((a["width"], b["width"]), (1, 2))
                    self.assertTrue(a["fresh"] and b["fresh"])
                    again = build(interpreters[0])
                    self.assertFalse(again["fresh"])
                    self.assertEqual(a["key"], again["key"])
                    self.assertTrue(again["cache"]["used"])
                    # A same-sized edit can retain Python's integer-mtime pyc timestamp. A new
                    # dependency snapshot still replaces that worker and imports the changed code.
                    changed_file = sites[0] / "custom_dependency.py"
                    stamp = changed_file.stat()
                    changed_file.write_text("WIDTH=3\n", encoding="utf-8")
                    os.utime(changed_file, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    changed = build(interpreters[0])
                    self.assertTrue(changed["fresh"])
                    self.assertEqual(changed["width"], 3)
                    self.assertNotEqual(changed["key"], again["key"])
                    status = client.request({"op": "status"}, start=False)
                    self.assertEqual(status["pid"], server.pid)
                    self.assertEqual(len(status["workers"]), 2)
                finally:
                    client.request({"op": "stop"}, start=False)
                    server.communicate(timeout=30)
