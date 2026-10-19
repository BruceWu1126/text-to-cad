"""``cadgen store why``'s text: what each line says of a record's closure and children."""

from __future__ import annotations

import contextlib
import io
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.cli import store as store_cli  # noqa: E402


class WhyTextTest(unittest.TestCase):
    def test_a_sliced_file_no_name_is_reached_of_says_so(self) -> None:
        """A child model's script, imported for the call and sliced to nothing by the
        boundary rule, read as ``kid.py[]``."""
        self.assertEqual(store_cli._closure_file_label("kid.py", {"kid.py": []}, {}), "kid.py (no names reached)")
        self.assertEqual(store_cli._closure_file_label("lib/geo.py", {"lib/geo.py": ["plane"]}, {}), "lib/geo.py[plane]")
        self.assertEqual(store_cli._closure_file_label("boss.py", {}, {}), "boss.py")

    def test_a_stale_child_has_no_current_key_to_show(self) -> None:
        """A child that does not stand has no key now: the line said ``now None``."""
        verdict = mock.Mock()
        verdict.stale = True
        verdict.reason.return_value = "child stale: kid.py"
        verdict.clauses = [{"clause": 3, "stale": True, "children": [
            {"model": "kid.py::kid", "stale": True, "why": "child stale", "recorded": "966ef6a7fe5b1234", "current": None},
        ]}]
        out = io.StringIO()
        with mock.patch.object(store_cli, "stale", return_value=verdict), \
                mock.patch.object(store_cli, "read_record", return_value=None), \
                contextlib.redirect_stdout(out):
            store_cli._why_one("boss.py::boss", False)
        self.assertIn("kid.py::kid  key 966ef6a7fe5b  now -  child stale", out.getvalue())
        self.assertNotIn("None", out.getvalue())


if __name__ == "__main__":
    unittest.main()
