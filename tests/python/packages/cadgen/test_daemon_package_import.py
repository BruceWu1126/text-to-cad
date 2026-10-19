"""The daemon package is safe to import from concurrent Viewer request threads."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[4]


class DaemonPackageImportTest(unittest.TestCase):
    def test_client_and_package_can_be_imported_concurrently(self) -> None:
        """Pin the cold-start interleaving that used to expose a partial client.

        The package import used to import server, which imports client. A second
        thread importing client at the same time could therefore form an import
        lock cycle and observe client before compute_version_token was defined.
        The import hook holds both threads at those exact dependency edges.
        """
        script = textwrap.dedent(r"""
            import builtins
            import importlib
            import sys
            import threading

            original_import = builtins.__import__
            package_at_server = threading.Event()
            client_started = threading.Event()
            errors = []

            def hooked_import(name, globals=None, locals=None, fromlist=(), level=0):
                thread = threading.current_thread().name
                if thread == "package" and name == "cadgen.daemon.server":
                    package_at_server.set()
                    if not client_started.wait(5):
                        raise AssertionError("client import did not overlap package import")
                return original_import(name, globals, locals, fromlist, level)

            builtins.__import__ = hooked_import

            def load(name):
                try:
                    importlib.import_module(name)
                except BaseException as error:
                    errors.append(repr(error))

            package = threading.Thread(target=load, args=("cadgen.daemon",), name="package")
            package.start()
            # The old package reaches the blocked server edge. The fixed package
            # has no eager edge and may finish before the client thread starts.
            while package.is_alive() and not package_at_server.wait(0.001):
                pass
            client = threading.Thread(target=load, args=("cadgen.daemon.client",), name="client")
            client.start()
            client_started.set()
            package.join(5)
            client.join(5)
            if package.is_alive() or client.is_alive():
                raise AssertionError("concurrent imports deadlocked")
            if errors:
                raise AssertionError(errors)
            if "cadgen.daemon.server" in sys.modules:
                raise AssertionError("importing the daemon package eagerly imported its server")
        """)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO / "packages/cadgen/src")
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_supervisor_reads_a_jobs_outputs_without_the_loader_or_the_kernel(self) -> None:
        """The ledger learns a job's outputs from the script's text (``static_output_paths``):
        after it, neither the model, cadgen's module loader, the authoring registry nor the
        kernel is in the supervisor's module table."""
        script = textwrap.dedent(r"""
            import sys, tempfile
            from pathlib import Path
            import cadgen.daemon.server
            from cadgen.daemon.jobs import declared_outputs

            with tempfile.TemporaryDirectory() as tmp:
                model = Path(tmp) / "widget.py"
                model.write_text("from cadgen import step\n@step(out='w.step')\ndef widget(): pass\n")
                outputs = declared_outputs(str(model))
            if [Path(p).name for p in outputs] != ["w.step"]:
                raise AssertionError(outputs)
            loaded = {name for name in sys.modules
                      if name.startswith(("build123d", "OCP", "cadgen.authoring", "cadgen._internal.generation"))
                      or "widget" in name}
            if loaded:
                raise AssertionError(f"the supervisor loaded {sorted(loaded)}")
        """)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO / "packages/cadgen/src")
        result = subprocess.run([sys.executable, "-c", script], cwd=REPO, env=env, capture_output=True, text=True,
                                timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
