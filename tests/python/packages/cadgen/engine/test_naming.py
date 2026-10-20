"""Element names as FreeCAD's topological naming keeps them: a face keeps its name unless
the feature history that made it changed. A feature is named by the author's name for its
result (the variable or attribute it is assigned to), so an edit renames only what it
touches."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from cadgen.engine import build as engine_build
from cadgen.engine import document
from cadgen.engine.kernel.trace import folded_atoms

BLOCK = """
import build123d as bd
from cadgen.engine import xbf


def peg(z):
    return bd.Pos(0, 0, z) * bd.Cylinder(1, 30)


@xbf
def block():
    body = bd.Box(30, 20, 10)
{insert}    {relief} = body - bd.Pos(8, 0, 0) * bd.Cylinder(3, 30)
    final = {relief} - peg(0)
    final = final - bd.Pos(-12, 0, 0) * bd.Cylinder(1, 30)
    a, b = bd.Pos(0, 0, 5) * bd.Box(2, 2, 2), bd.Pos(9, 0, 5) * bd.Box(2, 2, 2)
    return final + a + b
"""


class NamingCase(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-naming-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))

    def names(self, text: str, name: str = "block", **fields: str) -> dict[str, list[str]]:
        """Build ``text`` (formatted with ``fields``) afresh and read its first part's names."""
        script = self.folder / f"{name}.py"
        script.write_text(textwrap.dedent(text.format(**fields)), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        reports = engine_build.build(script, [name], cwd=self.folder)
        self.assertTrue(all(report["ok"] for report in reports), reports)
        prototype = document.load(self.folder / f"{name}.xbf").prototypes[0]
        return {"faces": [prototype.face_names[k] for k in sorted(prototype.face_names)],
                "edges": [prototype.edge_names[k] for k in sorted(prototype.edge_names)]}

    def derivations(self, names: dict[str, list[str]], name: str = "block") -> dict[str, list[str]]:
        """``names`` with every folded atom spelled out, from the sidecar's table."""
        sidecar = json.loads((self.folder / f"{name}.xbf.json").read_text(encoding="utf-8"))
        table = sidecar["parts"][0]["table"]

        def spelled(text: str) -> str:
            for atom in folded_atoms(text):
                text = text.replace(atom, spelled(table[atom]))
            return text

        return {kind: [spelled(text) for text in texts] for kind, texts in names.items()}


class AssignedNamesTest(NamingCase):
    def test_a_feature_is_named_by_the_target_its_result_is_assigned_to(self) -> None:
        faces = self.names(BLOCK, insert="", relief="relief")["faces"]
        text = "\n".join(faces)
        # The box: the first assignment to ``body``; the cut: ``relief``; the cylinder it
        # cut with, made in the same statement: ``relief_make_cylinder1``.
        self.assertIn("f:block.body.", text)
        self.assertIn(";M:block.relief", text)
        self.assertIn("f:block.relief_make_cylinder1.", text)
        # A reassignment is the target's next: ``final`` then ``final2``.
        self.assertIn(";M:block.final2", text)
        self.assertRegex(text, r";M:block\.final(;|\n|$)")
        # A call in a return expression is named as before, by function and operation.
        self.assertIn("f:peg.make_cylinder1.", text)
        # A tuple target names each of its values.
        self.assertIn("f:block.a.", text)
        self.assertIn("f:block.b.", text)

    def test_inserting_a_feature_renames_only_what_it_touches(self) -> None:
        before = self.derivations(self.names(BLOCK, insert="", relief="relief"))
        hole = "    body = body - bd.Pos(-6, 5, 0) * bd.Cylinder(1.5, 30)\n"
        after = self.derivations(self.names(BLOCK, insert=hole, relief="relief"))
        # The new hole cuts the box's top and bottom: those two faces take its step and are
        # renamed. Every other face -- the relief's, the peg's, the second cut's, the small
        # boxes' -- keeps its name, though the cuts after it are later calls of ``cut``.
        lost = set(before["faces"]) - set(after["faces"])
        self.assertEqual({name.split(";")[0] for name in lost}, {"f:block.body.5", "f:block.body.6"})
        self.assertEqual(len(set(after["faces"]) - set(before["faces"])), 3)  # those two, and the hole's
        # An edge is renamed only where it is made from one of them (a later cut's rim on
        # the top face): its derivation starts at that face.
        lost_edges = set(before["edges"]) - set(after["edges"])
        self.assertTrue(lost_edges)
        self.assertEqual({name.split(";")[0][2:] for name in lost_edges}, {"block.body.5", "block.body.6"})

    def test_renaming_a_variable_renames_exactly_that_feature(self) -> None:
        # A warm build: every call hits and hands back the same shapes, and the part is
        # still named again, since the code its names come from changed.
        before = self.derivations(self.names(BLOCK, insert="", relief="relief"))
        after = self.derivations(self.names(BLOCK, insert="", relief="arch"))
        for kind in ("faces", "edges"):
            self.assertNotEqual(before[kind], after[kind])
            self.assertEqual([name.replace("block.relief", "block.arch") for name in before[kind]], after[kind])


if __name__ == "__main__":
    unittest.main()
