"""CAM's protocol: the public verbs and their generated CLIs, and the operation registry.

Stdlib only: nothing here opens a part or plans a toolpath.
"""

from __future__ import annotations

import contextlib
import io
import unittest

from tests.python.support.cam_fixtures import heavy_at_import


class Surface(unittest.TestCase):
    def test_plan_help_lists_every_option_the_verb_takes(self):
        from cadgen import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            cli.main(["cam", "plan", "--help"])
        for word in ("--job", "--post", "--occurrence", "--verbose", "--json", "TARGET [OUT]"):
            self.assertIn(word, out.getvalue())

    def test_the_public_namespace_imports_no_kernel_geometry_or_numerics(self):
        self.assertEqual(heavy_at_import("cadgen.cam", "cadgen.cli.cam_plan", "cadgen.cli.cam_features"), "")


if __name__ == "__main__":
    unittest.main()
