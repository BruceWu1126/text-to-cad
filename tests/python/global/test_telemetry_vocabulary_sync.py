"""The controls a CAD view counts are one vocabulary in three languages.

The page names what a person used (`@text-to-cad/ui`'s `usage/controls.js`), cadgen notes only names it
knows (`cadgen/analytics.py`: `CONTROLS`), and the receiver refuses any other (`apps/api/src/events.mjs`:
`CONTROLS`). A name one of them lacks is a count lost without a word: the page's never noted, or a whole
batch refused. Read as text, so this needs nothing installed.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
PAGE = ROOT / "packages/ui/src/usage/controls.js"
CADGEN = ROOT / "packages/cadgen/src/cadgen/analytics.py"
RECEIVER = ROOT / "apps/api/src/events.mjs"


def names(path: Path, start: str, end: str) -> list[str]:
    text = path.read_text(encoding="utf-8")
    body = text[text.index(start) + len(start):]
    body = body[:body.index(end)]
    body = re.sub(r"(?m)(?://|#).*$", "", body)  # comments name words that are no control
    return re.findall(r"""['"]([a-z][a-z0-9_]*)['"]""", body)


class TheControlsAreOneVocabulary(unittest.TestCase):
    def test_the_page_cadgen_and_the_receiver_name_the_same_controls(self) -> None:
        page = names(PAGE, "export const CONTROLS = Object.freeze(", "]));")
        cadgen = names(CADGEN, "CONTROLS = frozenset({", "})")
        receiver = names(RECEIVER, "export const CONTROLS = new Set([", "]);")
        self.assertGreater(len(page), 100)
        self.assertEqual(len(page), len(set(page)), "each name once")
        self.assertEqual(page, receiver, "the receiver's list, in the page's order")
        self.assertEqual(sorted(page), sorted(cadgen))
        # A name says what was used, never a value: snake_case words of the page's choosing.
        for name in page:
            self.assertRegex(name, r"^[a-z]+(?:_[a-z]+)*$")


if __name__ == "__main__":
    unittest.main()
