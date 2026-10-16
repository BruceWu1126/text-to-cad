"""An all-link parent's STEP spliced from its children's saved files is the file the
writer exports, as far as any reader can tell, and every parent's saved file holds the
result it was written from (``store/exports.py``, ``cadgen.store._splice_step``,
STORE.md §3)."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.documents import assert_same_document, save_step
from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")


class Fixture(unittest.TestCase):
    """Two children built through STEP (groups, exact-axis and arbitrary
    rotations, non-dyadic and out-of-range translations, a compound leaf, face
    and uniform colours, a grandchild link, a name holding '#' and a quote), a
    third whose compound places a member of its own, and the parents that place
    them."""

    @classmethod
    def setUpClass(cls):
        cls.temp = generated_cad_directory(prefix="tree-composition-")
        cls.root = Path(cls.temp.name)
        cls.env = mock.patch.dict(os.environ, {
            "CADGEN_CACHE_DIR": str(cls.root / "store"),
            "CADGEN_COMPONENT_WORKERS": "1", "CADGEN_DAEMON": "0",
        })
        cls.env.start()
        from cadgen.store._splice_step import ChildStep

        cls.a_hash, _, _, a_step = save_step(cls.child_a(), cls.root / "child_a.step", root_name="child_a")
        cls.b_hash, _, _, b_step = save_step(cls.child_b(), cls.root / "child_b.step", root_name="child_b")
        cls.c_hash, _, _, _ = save_step(cls.child_c(), cls.root / "child_c.step", root_name="child_c")
        cls.steps = {cls.a_hash: ChildStep(cls.root / "child_a.step", a_step),
                     cls.b_hash: ChildStep(cls.root / "child_b.step", b_step)}

    @classmethod
    def tearDownClass(cls):
        cls.env.stop()
        cls.temp.cleanup()

    @staticmethod
    def leaf(name, shape, location, color=None, faces=None):
        placed = shape.moved(location)
        placed.label = name
        if color is not None:
            placed.color = color
        if faces is not None:
            placed.cad_face_ordinal_colors = faces
        return placed

    @classmethod
    def child_a(cls):
        import build123d as bd

        box = bd.Solid.make_box(1, 2, 3)
        cylinder = bd.Solid.make_cylinder(0.7, 2.5)
        # Its boxes made where they sit: a compound that places no member itself.
        pair = bd.Compound([bd.Solid.make_box(2, 2, 2, bd.Plane((-1, -1, -1))),
                            bd.Solid.make_box(2, 2, 2, bd.Plane((3, -1, -1)))])
        members = [
            cls.leaf("a", box, bd.Location((0.7, 0.3, 0.9)), color=(0.9, 0.1, 0.1, 1)),
            cls.leaf("b", box, bd.Location(bd.Plane((0.1, 0.2, 0.3), x_dir=(0, 0, -1), z_dir=(1, 0, 0)))),
            cls.leaf("c", cylinder, bd.Location(bd.Plane((5.3, 0.7, -2.1), x_dir=(0, -1, 0), z_dir=(0, 0, -1))),
                     faces={1: (0., 0., 1., 1.)}),
            cls.leaf("d #4 it's", box, bd.Location((0.1, 7.7, 0.0), (0, 0, 37))),
            cls.leaf("e", pair, bd.Location(bd.Plane((1.1, 1.2, 1.3), x_dir=(0, 1, 0), z_dir=(-1, 0, 0)))),
            cls.leaf("f", box, bd.Location((1234.5678901234, 0.000123456789, -1 / 3), (12, 34, 56))),
        ]
        g1 = bd.Compound(children=members[:2], label="g1")
        g3 = bd.Compound(children=members[3:5], label="g3")
        g2 = bd.Compound(children=[members[2], g3], label="g2")
        return bd.Compound(children=[g1, g2, members[5]], label="child_a")

    @classmethod
    def child_b(cls):
        import build123d as bd

        from cadgen.store.materialize import materialize

        first = materialize(cls.a_hash).moved(bd.Location((0.3, 0.1, 0.2)))
        first.label = "a_first"
        second = materialize(cls.a_hash).moved(bd.Location((10.1, -0.7, 2.2)))
        second.label = "a_second"
        own = cls.leaf("own", bd.Solid.make_cylinder(0.7, 2.5), bd.Location((0.05, 0.25, 0.5)))
        return bd.Compound(children=[first, own, second], label="child_b")

    @classmethod
    def child_c(cls):
        import build123d as bd

        # Its compound places its second box itself, a placement the saved document's
        # leaf keeps with the component it was written from.
        pair = bd.Compound([bd.Box(2, 2, 2), bd.Pos(4, 0, 0) * bd.Box(2, 2, 2)])
        return bd.Compound(children=[cls.leaf("pair", pair, bd.Location((0.5, 0.5, 0.5))),
                                     cls.leaf("block", bd.Box(1, 1, 1), bd.Location((-3, 0, 0)))], label="child_c")

    def parent(self, placements=None, *, grouped=False, extra=None):
        import build123d as bd

        from cadgen.store.materialize import materialize

        placements = placements or [
            (self.a_hash, bd.Location((0.1, 0.2, 0.3))),
            (self.b_hash, bd.Location((7.3, -2.2, 0.0))),
            (self.a_hash, bd.Location((0.0, 0.0, 999.95))),
        ]
        links = []
        for index, (tree_hash, location) in enumerate(placements):
            link = materialize(tree_hash).moved(location)
            link.label = f"link{index}"
            links.append(link)
        if grouped:
            links = [bd.Compound(children=links[:2], label="pair"), *links[2:]]
        if extra is not None:
            links.append(extra)
        return bd.Compound(children=links, label="parent")

    def build(self, shape, *, name="parent", force=False, steps=None):
        """Save ``shape`` as a model's build does, its STEP spliced from ``steps`` (each
        linked child's saved file) where the parent is eligible, and verified."""
        result = save_step(shape, self.root / f"{name}.step", root_name=name, force=force, children=steps)
        self.assertEqual({"ok": True}, result[2]["verify"])
        return result

    def parsed_document(self, step):
        from cadgen._internal.step_scene_package import load_step_scene_exact
        from cadgen.store.build import build_document_tree

        return build_document_tree(load_step_scene_exact(step))[0]

    def assertDocumentOf(self, result, step):
        """``step`` holds ``result`` as a cold compile of its bytes reads it: the same
        structure, names, placements and colours, components alike leaf by leaf, and its
        bytes map to that result."""
        from cadgen._internal.step_scene import step_file_hash
        from cadgen.store.records import document_entry_for_hash
        from cadgen.store.trees import flatten, get_tree

        self.assertEqual(result, document_entry_for_hash(step_file_hash(step))["tree"])
        assert_same_document(self, flatten(result), get_tree(self.parsed_document(step)))


class SavedParentTest(Fixture):
    def test_every_parent_saved_holds_its_result(self):
        import build123d as bd

        coloured = self.parent()
        coloured.children[1].color = (0.2, 0.4, 0.8, 1.0)
        cases = {
            "links": self.parent(),
            "links under a group": self.parent(grouped=True),
            "rotated link": self.parent([(self.a_hash, bd.Location((1, 2, 3), (0, 90, 0)))]),
            "link colour": coloured,
            "own geometry": self.parent(extra=self.leaf("plate", bd.Solid.make_box(5, 5, 1), bd.Location((0, 0, -9)))),
            # The child's leaf holds a compound that places its second box itself.
            "compound placing a member": self.parent([(self.c_hash, bd.Location((0.25, 0.5, 1.0)))]),
        }
        for label, shape in cases.items():
            with self.subTest(label):
                name = label.replace(" ", "_")
                result, _, _, _ = self.build(shape, name=name)
                self.assertDocumentOf(result, self.root / f"{name}.step")


class SplicedStepTest(Fixture):
    """An all-link parent written from its children's saved files is the file the writer
    exports, as far as any reader can tell: its cold compile is the exported file's, and it
    holds the result it was written from (``cadgen.store._splice_step``)."""

    def spliced_parent(self, *, grouped=False, names=("link #1 it's", "b ''#2'' (6')")):
        import build123d as bd

        shape = self.parent([(self.a_hash, bd.Location((0.1, 0.2, 0.3))),
                             (self.b_hash, bd.Location((7.3, -2.2, 0.0)))], grouped=grouped)
        links = shape.children[0].children if grouped else shape.children
        for link, name in zip(links, names):
            link.label = name
        if grouped:
            shape.children[0].label = "grp #3 'q'"
        return shape

    def assertSameAsExported(self, shape, name, spliced):
        """The parent ``shape`` exported through the writer (a forced build splices nothing)
        reads as the file ``spliced`` from its children did, from bytes of its own."""
        from cadgen.store.trees import get_tree

        path = self.root / "exported" / f"{name}.step"
        _, _, exported, exported_hash = save_step(shape, path, root_name=name, force=True)
        self.assertEqual(({"ok": True}, False), (exported["verify"], exported["export"]["spliced"]))
        self.assertNotEqual(spliced, exported_hash)
        assert_same_document(self, get_tree(self.parsed_document(self.root / f"{name}.step")),
                             get_tree(self.parsed_document(path)))

    def test_a_spliced_parent_compiles_to_the_exported_parent(self):
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                name = f"spliced_{int(grouped)}"
                result, _, stats, step_hash = self.build(self.spliced_parent(grouped=grouped), name=name,
                                                         steps=self.steps)
                self.assertTrue(stats["export"]["spliced"])
                self.assertDocumentOf(result, self.root / f"{name}.step")
                self.assertSameAsExported(self.spliced_parent(grouped=grouped), name, step_hash)

    def test_a_splice_is_byte_for_byte_repeatable(self):
        written = []
        for _ in range(2):
            _, _, stats, step_hash = self.build(self.spliced_parent(), name="repeat", steps=self.steps)
            self.assertTrue(stats["export"]["spliced"])
            written.append((step_hash, (self.root / "repeat.step").read_bytes()))
        self.assertEqual(written[0], written[1])

    def test_non_ascii_names_splice_and_read_as_written(self):
        # A lowercase accented name failed every build: the writer's spelling was read
        # otherwise. Spliced or exported, both spell it as a Part 21 directive.
        for grouped in (False, True):
            with self.subTest(grouped=grouped):
                name = f"non_ascii_{int(grouped)}"

                def shape():
                    shape = self.spliced_parent(grouped=grouped, names=("Bügel_ä", "ナット 🔩 it's"))
                    if grouped:
                        shape.children[0].label = "grüppe"
                    return shape

                result, _, stats, step_hash = self.build(shape(), name=name, steps=self.steps)
                self.assertTrue(stats["export"]["spliced"])
                self.assertTrue((self.root / f"{name}.step").read_bytes().isascii())
                self.assertDocumentOf(result, self.root / f"{name}.step")
                self.assertSameAsExported(shape(), name, step_hash)

    def test_ineligible_parents_are_exported(self):
        import build123d as bd

        from cadgen.store._splice_step import ChildStep

        stale = dict(self.steps)
        stale[self.b_hash] = ChildStep(self.steps[self.b_hash].path, "0" * 64)
        cases = {
            "repeated child": (self.parent(), self.steps),
            "rotated link": (self.parent([(self.a_hash, bd.Location((1, 2, 3), (0, 90, 0)))]), self.steps),
            "child file not as pinned": (self.spliced_parent(), stale),
            "name needing escapes": (self.spliced_parent(names=("back\\slash", "b")), self.steps),
        }
        for label, (shape, steps) in cases.items():
            with self.subTest(label):
                name = label.replace(" ", "_")
                result, _, stats, _ = self.build(shape, name=name, steps=steps)
                self.assertFalse(stats["export"]["spliced"])
                self.assertTrue(stats["export"]["unspliced"])
                self.assertDocumentOf(result, self.root / f"{name}.step")

    def test_the_build_assembles_no_document_and_the_export_one_only_when_it_cannot_splice(self):
        import build123d as bd

        from cadgen.store import materialize

        sizes = []
        original = materialize.materialize_descriptor

        def counted(descriptor, **kwargs):
            sizes.append(len(descriptor.get("occurrences") or []))
            return original(descriptor, **kwargs)

        eligible = self.spliced_parent()
        rotated = self.parent([(self.a_hash, bd.Location((1, 2, 3), (0, 90, 0)))])
        with mock.patch.object(materialize, "materialize_descriptor", side_effect=counted):
            _, _, spliced, _ = self.build(eligible, name="assembled_spliced", steps=self.steps)
            self.assertTrue(spliced["export"]["spliced"])
            self.assertEqual(sizes, [])
            _, _, exported, _ = self.build(rotated, name="assembled_rotated", steps=self.steps)
            self.assertFalse(exported["export"]["spliced"])
            self.assertEqual(sizes, [6])


class PinnedChildStepsTest(unittest.TestCase):
    def test_only_a_record_that_still_pins_the_tree_resolves_its_saved_step(self):
        from cadgen._internal.generation import _pinned_child_steps
        from cadgen.store._splice_step import ChildStep
        from cadgen.store.records import write_record

        scratch = generated_cad_directory(prefix="pinned-child-steps-")
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        step = str(root / "current.step")
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(root / "store")}):
            # A record is keyed as it is written (store/results.py): the keys are read back.
            current = write_record(root / "current.py", {"tree": "a" * 64, "outputs": {
                str(root / "current.stl"): {"sha256": "1" * 64}, step: {"sha256": "1" * 64}}})
            # Rebuilt since the parent composed it: its record holds another key.
            write_record(root / "moved.py", {"tree": "b" * 64, "closure": {"hash": "moved again"},
                                             "outputs": {str(root / "moved.step"): {"sha256": "2" * 64}}})
            mesh = write_record(root / "mesh_only.py", {"tree": "c" * 64, "outputs": {}})
            # Its export has not landed: the output is pending.
            pending = write_record(root / "pending.py", {"tree": "d" * 64,
                                                         "outputs": {str(root / "pending.step"): {"sha256": ""}}})
            scene = mock.Mock(store_children=[
                {"model": str(root / "current.py"), "key": current},
                {"model": str(root / "moved.py"), "key": "k-moved"},
                {"model": str(root / "mesh_only.py"), "key": mesh},
                {"model": str(root / "pending.py"), "key": pending},
            ])
            self.assertEqual(_pinned_child_steps(scene), {"a" * 64: ChildStep(Path(step), "1" * 64)})


if __name__ == "__main__":
    unittest.main()
