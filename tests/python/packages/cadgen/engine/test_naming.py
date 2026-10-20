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
from unittest import mock

from cadgen.engine import build as engine_build
from cadgen.engine import document
from cadgen.engine.kernel import memo
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


PAIR = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def pair():
    fork = bd.Box(20, 10, 10)
    fork = fork - bd.Pos(0, 0, 5) * bd.Cylinder(3, 4)
{append}    fork.label = "fork"
    twin = fork.mirror(bd.Plane.YZ).moved(bd.Location((40, 0, 0)))
    twin.label = "twin"
    return bd.Compound(children=[fork, twin])
"""


BAKED = """
import build123d as bd
from build123d.topology.shape_core import downcast
from cadgen.engine import xbf

OFFSET = {offset}


def bake(shape, kind):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_Copy, BRepBuilderAPI_GTransform, BRepBuilderAPI_Transform
    from OCP.gp import gp_GTrsf
    from OCP.TopLoc import TopLoc_Location

    occt = shape.wrapped
    if kind == "copy":
        made = BRepBuilderAPI_Copy(occt)
    elif kind == "gtransform":
        made = BRepBuilderAPI_GTransform(occt.Located(TopLoc_Location()), gp_GTrsf(occt.Location().Transformation()),
                                         True)
    else:
        made = BRepBuilderAPI_Transform(occt.Located(TopLoc_Location()), occt.Location().Transformation(), True)
    return bd.Solid(downcast(made.Shape()))


@xbf
def wheel():
    hub = bd.Cylinder(10, 5) - bd.Cylinder(3, 10)
    return bake(bd.Pos(30, 0, 0) * hub, "transform")


@xbf
def baked():
    hub = bd.Cylinder(10, 5) - bd.Cylinder(4, 10)
    copied = bake(hub, "copy")
    stretched = bake(bd.Pos(0, 30, 0) * hub, "gtransform")
    return bd.Compound(children=[wheel().moved(bd.Location((0, 0, OFFSET))), copied.moved(bd.Location((60, 0, 0))),
                                 stretched])
"""


class NamingCase(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-naming-")).resolve()
        self.addCleanup(lambda: shutil.rmtree(self.folder, ignore_errors=True))

    def names(self, text: str, name: str = "block", part: int = 0, **fields: str) -> dict[str, list[str]]:
        """Build ``text`` (formatted with ``fields``) and read one part's names."""
        script = self.folder / f"{name}.py"
        script.write_text(textwrap.dedent(text.format(**fields)), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        reports = engine_build.build(script, [name], cwd=self.folder)
        self.assertTrue(all(report["ok"] for report in reports), reports)
        prototype = document.load(self.folder / f"{name}.xbf").prototypes[part]
        return {"faces": [prototype.face_names[k] for k in sorted(prototype.face_names)],
                "edges": [prototype.edge_names[k] for k in sorted(prototype.edge_names)]}

    def derivations(self, names: dict[str, list[str]], name: str = "block", part: int = 0) -> dict[str, list[str]]:
        """``names`` with every folded atom spelled out, from the sidecar's table."""
        sidecar = json.loads((self.folder / f"{name}.xbf.json").read_text(encoding="utf-8"))
        table = sidecar["parts"][part]["table"]

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


class LabelTest(NamingCase):
    def test_a_label_names_the_feature_that_first_made_the_labelled_shape(self) -> None:
        def both(append: str) -> list[dict[str, list[str]]]:
            fields = {"name": "pair", "append": append}
            found = [self.names(PAIR, part=0, **fields)]
            found.append(self.names(PAIR, part=1, **fields))
            return [self.derivations(names, "pair", part) for part, names in enumerate(found)]

        direct, twin = both("")
        # The twin is made of the labelled shape after it was labelled: the label names the
        # feature that first made it, the box, never the cut that last did.
        self.assertTrue(any(name.startswith("f:fork.") for name in twin["faces"]), twin["faces"])
        self.assertFalse(any(name.startswith("f:fork.") for name in direct["faces"]))
        hole = "    fork = fork - bd.Pos(6, 0, 0) * bd.Cylinder(1, 30)\n"
        direct_after, twin_after = both(hole)
        # A cut appended after the label's shape was made takes nothing over: the twin
        # loses exactly the names the direct part does -- the faces the new hole cuts.
        lost_direct = set(direct["faces"]) - set(direct_after["faces"])
        lost_twin = set(twin["faces"]) - set(twin_after["faces"])
        self.assertTrue(lost_direct)
        self.assertEqual(len(lost_twin), len(lost_direct), (lost_twin, lost_direct))


class RawMakerTest(NamingCase):
    def parts(self, offset: int) -> list[dict[str, list[str]]]:
        return [self.derivations(self.names(BAKED, name="baked", part=part, offset=offset), "baked", part)
                for part in range(3)]

    def assert_named_from_the_hub(self, parts: list[dict[str, list[str]]]) -> None:
        wheel, copied, stretched = parts
        for names, step in ((wheel, ";M:bake.made"), (copied, None), (stretched, ";M:bake.made")):
            # Every face and edge named, from the hub's own names: a placement or a copy
            # made past build123d keeps them, a transformation with its step.
            self.assertTrue(names["faces"] and names["edges"])
            for name in names["faces"] + names["edges"]:
                self.assertRegex(name, r"^[fe]:(wheel|baked)\.hub")
                if step is not None:
                    self.assertIn(step, name)
                else:
                    self.assertNotIn("bake.", name)

    def test_a_shape_placed_or_copied_by_a_raw_maker_keeps_its_names(self) -> None:
        first = self.parts(0)
        self.assert_named_from_the_hub(first)
        # A build that runs the root again and replays the child keeps every name, the
        # child's replayed transformation included.
        engine_build._PARTS.clear()
        self.assertEqual(self.parts(5), first)

    def test_a_maker_that_reports_nothing_names_by_ordinal(self) -> None:
        composed = memo._history

        def silent(feature, makers):
            return None if feature.op in ("transform", "gtransform", "copy") else composed(feature, makers)

        with mock.patch.object(memo, "_history", silent):
            self.assert_named_from_the_hub(self.parts(0))


if __name__ == "__main__":
    unittest.main()
