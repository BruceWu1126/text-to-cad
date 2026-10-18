"""A model run as ``python model.py`` must survive its own module eviction.

Before loading a generator, cadgen drops every first-party module from
``sys.modules`` so the closure is freshly executed. Run directly, the model
script IS ``sys.modules['__main__']`` and its ``__file__`` is a first-party
path, so the blanket eviction took it with everything else.

Nothing re-imports ``__main__`` — the generator is loaded under its own loader
name — so this looked harmless. It is not: ``multiprocessing``'s spawn start
method reads ``sys.modules['__main__']`` in ``get_preparation_data`` to tell the
child what to set up. The component build engages a spawn-context process pool
once six or more components are missing, so a direct run of any model with a
half-dozen fresh parts died with ``KeyError: '__main__'`` deep inside
``ProcessPoolExecutor``.

The unit tests pin the guard; the end-to-end test is the one that would have
caught it, because the failure is an interaction between two subsystems that
each look correct alone.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

CADGEN_SRC = add_repo_path("packages/cadgen/src")

from cadgen._internal import source_hash as cad_source_hash  # noqa: E402


class MainModuleIsNeverEvictedTest(unittest.TestCase):
    def _fake_main(self) -> pathlib.Path:
        """Stand in for `python model.py`: __main__ with a first-party file."""
        module = type(sys)("__main__")
        path = pathlib.Path("/tmp/some-cad-project/src/widget.py")
        module.__file__ = str(path)
        self._previous = sys.modules.get("__main__")
        sys.modules["__main__"] = module
        self.addCleanup(self._restore)
        return path

    def _restore(self) -> None:
        if self._previous is None:
            sys.modules.pop("__main__", None)
        else:
            sys.modules["__main__"] = self._previous

    def test_blanket_eviction_keeps_main(self) -> None:
        path = self._fake_main()
        with mock.patch.object(
            cad_source_hash, "repo_local_loaded_modules", return_value={"__main__": path}
        ):
            evicted = cad_source_hash.evict_first_party_modules()
        self.assertNotIn("__main__", evicted)
        self.assertIn("__main__", sys.modules)

    def test_foreign_eviction_keeps_main(self) -> None:
        """__main__ is foreign to every project by construction — the script
        lives in the project being built only when that project is the one you
        launched, and the cross-project evictor must not decide otherwise."""
        path = self._fake_main()
        with mock.patch.object(
            cad_source_hash, "repo_local_loaded_modules", return_value={"__main__": path}
        ):
            evicted = cad_source_hash.evict_foreign_first_party_modules(
                ["/tmp/a-completely-different-project/src"]
            )
        self.assertNotIn("__main__", evicted)
        self.assertIn("__main__", sys.modules)


# Eight distinct parts, so the missing-component count clears the threshold at
# which the build engages a spawn-context process pool (six).
_MODEL = textwrap.dedent(
    '''
    """Eight distinct boxes — enough missing components to engage the pool."""
    import build123d as bd

    from cadgen import step


    @step(out="pool_probe.step")
    def pool_probe():
        parts = []
        for index in range(8):
            part = bd.Solid.make_box(3 + index, 5 + index * 0.5, 2 + index * 0.25)
            part = part.moved(bd.Location((index * 20, 0, 0)))
            part.label = f"part_{index}"
            parts.append(part)
        return bd.Compound(children=parts)


    if __name__ == "__main__":
        pool_probe()
    '''
)


class DirectRunEngagesThePoolTest(unittest.TestCase):
    def test_a_direct_run_with_a_full_pool_builds_clean(self) -> None:
        from cadgen.daemon.client import watch_jobs

        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(CADGEN_SRC), *([env["PYTHONPATH"]] if env.get("PYTHONPATH") else [])]
        )
        # Exercise the ordinary direct-script handoff: the daemon worker owns
        # the build and finishes its display data and files after answering.
        env.pop("CADGEN_COMPONENT_WORKERS", None)

        with tempfile.TemporaryDirectory(prefix="cadgen-pool-probe-") as tmp:
            root = pathlib.Path(tmp)
            script = root / "pool_probe.py"
            # encoding= is not optional: _MODEL's docstrings carry em dashes,
            # and write_text without one uses the locale encoding (cp1252 on a
            # Windows runner) while the child reads the file as UTF-8 -- the run
            # died with a SyntaxError about a stray 0x97 byte, not the KeyError
            # this test is about.
            script.write_text(_MODEL, encoding="utf-8")
            # A private store, so the components really are missing and the pool
            # really does engage; a warm shared store would reuse them all and
            # quietly skip the code under test.
            env["CADGEN_CACHE_DIR"] = str(root / "cache")

            proc = subprocess.run(
                [sys.executable, str(script)],
                cwd=str(root), env=env, capture_output=True, text=True, timeout=900,
            )
            # Keep the private store until this request, including its exports,
            # has finished. The script's early answer owns no finishing work.
            output = root / "pool_probe.step"
            scope = {"output": str(output), "store_root": env["CADGEN_CACHE_DIR"]}
            update = watch_jobs(**scope)
            self.assertIsNotNone(update, "the build daemon did not answer its job watch")
            self.assertEqual(len(update["jobs"]), 1, update)
            job = update["jobs"][0]
            self.assertEqual(job["subject"], os.path.realpath(script))
            job_id = job["id"]
            while job["finishedAt"] is None:
                update = watch_jobs(update["jobsCursor"], **scope)
                self.assertIsNotNone(update, "the build daemon disconnected before finishing the request")
                jobs = [row for row in update["jobs"] if row["id"] == job_id]
                self.assertEqual(len(jobs), 1, update)
                job = jobs[0]

            self.assertEqual(
                proc.returncode, 0,
                f"direct run failed:\n{proc.stdout[-4000:]}\n{proc.stderr[-4000:]}",
            )
            self.assertNotIn("KeyError: '__main__'", proc.stderr)
            self.assertEqual((job["state"], job["exit"]), ("done", 0), job)
            self.assertTrue(output.is_file(), job)
            self.assertTrue(output.read_bytes().rstrip().endswith(b"END-ISO-10303-21;"))


if __name__ == "__main__":
    unittest.main()
