"""A model run as ``python model.py`` must survive its own module eviction.

Before loading a generator, cadgen drops every first-party module from
``sys.modules`` so the closure is freshly executed. Run directly, the model
script IS ``sys.modules['__main__']`` and its ``__file__`` is a first-party
path, so a blanket eviction would take it with everything else.

Nothing re-imports ``__main__`` -- the generator is loaded under its own loader
name -- but ``multiprocessing``'s spawn start method reads
``sys.modules['__main__']`` in ``get_preparation_data``, so evicting it breaks
any process pool started later in the process with ``KeyError: '__main__'``.
"""

from __future__ import annotations

import pathlib
import sys
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


if __name__ == "__main__":
    unittest.main()
