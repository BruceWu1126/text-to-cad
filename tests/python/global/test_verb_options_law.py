"""cadgen's law 22: a verb's option is about the picture or the path, never the model.

`export` converts a document into files: a document in, destinations out. What a file holds
-- its parts, their placement, colours and motion -- is what the code built, so neither the
command, the root function nor `Model.export` takes an option that selects a part or a state.
A posed or partial file is a function in the model's code (`cadgen.ap242.posed`, a part's own
model). This pins the surface where such an option would come back: the parser `cadgen export`
runs and the two Python signatures. Nothing here needs the CAD kernel.
"""

from __future__ import annotations

import argparse
import inspect
import unittest

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

import cadgen  # noqa: E402
from cadgen.cli.export import build_parser  # noqa: E402
from cadgen.engine.model import Model  # noqa: E402

LAW = ("law 22 (packages/cadgen/README.md): \"A verb's option is about the picture or the path, never the "
       "model. `export` takes a document and destinations: what the file holds, its parts and their "
       "placement, colours and motion, is what the code built, so no option selects a part or a state. "
       "[...] Anything that changes what a document is, is a function in the model's code.\"")


class ExportTakesADocumentAndDestinations(unittest.TestCase):
    def test_the_command_takes_src_dst_and_json_alone(self) -> None:
        parser = build_parser()
        found = {(action.dest, tuple(action.option_strings), action.nargs) for action in parser._actions
                 if not isinstance(action, argparse._HelpAction)}
        self.assertEqual(found, {("source", (), None), ("destinations", (), "+"), ("json", ("--json",), 0)},
                         f"`cadgen export` takes SRC DST [DST ...] [--json] alone; {LAW}")

    def test_the_function_takes_a_source_destinations_and_its_seams_alone(self) -> None:
        parameters = inspect.signature(cadgen.export).parameters.values()
        self.assertEqual([(p.name, p.kind, p.default) for p in parameters], [
            ("source", inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.empty),
            ("destinations", inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.empty),
            ("log", inspect.Parameter.KEYWORD_ONLY, None),
            ("daemon", inspect.Parameter.KEYWORD_ONLY, True),
            ("progress", inspect.Parameter.KEYWORD_ONLY, None),
        ], f"cadgen.export is (source, *destinations, log=None, daemon=True, progress=None); {LAW}")

    def test_a_model_exports_itself_to_destinations_alone(self) -> None:
        parameters = inspect.signature(Model.export).parameters.values()
        self.assertEqual([(p.name, p.kind) for p in parameters], [
            ("self", inspect.Parameter.POSITIONAL_OR_KEYWORD),
            ("destinations", inspect.Parameter.VAR_POSITIONAL),
        ], f"Model.export is (*destinations); {LAW}")


if __name__ == "__main__":
    unittest.main()
