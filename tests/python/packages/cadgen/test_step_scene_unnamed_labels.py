from __future__ import annotations

import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.cad_test_roots import IsolatedCadRoots
from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

from build123d import Box, Compound, Location, Solid  # noqa: E402

from cadgen.step_export import _create_bin_xcaf_doc  # noqa: E402
from cadgen._internal.step_scene_loader import _load_occurrence_tree_from_xcaf_doc, _normalize_label_name  # noqa: E402
from cadgen.step_export import build_build123d_step_scene, export_build123d_step_file  # noqa: E402
from cadgen._internal.step_scene_loader import _normalize_label_name, load_step_scene  # noqa: E402


class UnnamedLabelSceneTests(unittest.TestCase):
    """A shape whose XCAF labels are not all named must still build a scene.

    This is a SEGFAULT regression, not a wrong-answer one, so there is nothing to assert
    against on failure -- the process dies with SIGSEGV and no traceback, and the test run
    reports a crashed worker. TDF_Label.FindAttribute does not return false for an absent
    attribute in this OCP build; it dereferences null. _label_name now asks IsAttribute first.

    A plain ``Compound(children=[...])`` is the shape that produces unnamed labels: XCAF makes
    a child label per solid, and nothing names them unless the model was authored through
    AssemblyHelper. Both spellings are covered here because only the unnamed one ever crashed,
    and a fix that quietly stopped reading real names would pass the unnamed case alone.
    """

    def _build(self, shape):
        """The reader's walk of the XCAF document of ``shape``, as the writer assembles it:
        the one place the labels' names are read."""
        with tempfile.TemporaryDirectory() as directory:
            return _load_occurrence_tree_from_xcaf_doc(Path(directory) / "scene.step", _create_bin_xcaf_doc(shape))

    def _grid(self, count: int):
        return [Location((index * 30.0, 0.0, 0.0)) * Box(20, 20, 10) for index in range(count)]

    def test_compound_of_unnamed_solids_builds(self) -> None:
        scene = self._build(Compound(children=self._grid(3)))
        self.assertIsNotNone(scene)

    def test_named_labels_still_resolve(self) -> None:
        children = self._grid(2)
        for index, child in enumerate(children):
            child.label = f"block_{index}"
        scene = self._build(Compound(children=children))
        self.assertIsNotNone(scene)

    def test_single_unnamed_solid_builds(self) -> None:
        scene = self._build(Box(20, 20, 10))
        self.assertIsNotNone(scene)

    def test_vendor_unicode_label_spellings_normalize_to_authored_text(self) -> None:
        escaped = r"\X2\51F853F0\X0\-\X2\62C94F38\X0\4_1_2_3_4"
        mojibake = "å\x9c\x86è§\x922_1_2"
        self.assertEqual(_normalize_label_name(escaped), "凸台-拉伸4_1_2_3_4")
        self.assertEqual(_normalize_label_name(mojibake), "圆角2_1_2")
        self.assertEqual(_normalize_label_name(r"\X4\0001F680\X0\ mount"), "🚀 mount")
        self.assertEqual(_normalize_label_name("café Ã©"), "café Ã©")
        self.assertEqual(_normalize_label_name("bracket"), "bracket")

    def test_an_xcaf_label_entry_is_no_name(self) -> None:
        """OCCT names an unnamed label after its own address; that is not a name.

        The spellings and the counter-examples are the viewer's (``productName.js``),
        so a STEP reads the same on both sides."""
        for entry in ("=>[0:1:1:2]", "=> [0:1:1:2]", "[0:1:1:2]", "0:1:1:2", "0:1", " =>[0:1:1:12] "):
            self.assertIsNone(_normalize_label_name(entry), entry)
        for name in ("l_bracket", "Panel:1", "M3x8:2", "base", "1:2 bracket", "=>bracket", "[bracket]"):
            self.assertEqual(_normalize_label_name(name), name, name)

    def test_single_part_file_reads_back_with_its_authored_name(self) -> None:
        """A written single part is read back under the name its author gave it.

        A part that carries a placement -- every occurrence a build materializes does,
        at identity or not -- is written by XCAF as an assembly of one instance: the
        instance label is named after its entry (``=>[0:1:1:2]``) and only the prototype
        it refers to carries the label. Read back, the entry must give way to that name,
        so ``#plain_box.f1`` resolves and an assembly that places this file through
        ``read_step`` writes ``plain_box``, not the entry, as the product name."""
        part = Box(20, 20, 10).moved(Location())
        part.label = "plain_box"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plain_box.step"
            export_build123d_step_file(part, path)
            scene = load_step_scene(path)
        (root,) = scene.roots
        (leaf,) = root.children
        self.assertEqual((leaf.name, leaf.source_name), ("plain_box", "plain_box"))
        self.assertIsNone(root.name, "the wrapper the writer added has no authored name")
        self.assertEqual(scene.prototype_names[leaf.prototype_key], "plain_box")

    def test_an_assembly_placing_the_file_names_its_products_after_the_part(self) -> None:
        """Composed through ``read_step``, the part writes its name, never an id or entry.

        Read through the store, the wrapper XCAF added has no name and the canonical tree
        gives it its occurrence id (``o1``). Placed twice in an assembly, each wrapper is
        still one placed part, so it carries the part's name: the composed STEP names its
        products ``assy`` and ``part``, not ``=>[0:1:1:2]`` (the old reading) or ``o1``."""
        from cadgen._internal.step_scene_mesh import import_step

        with generated_cad_directory(prefix="unnamed-labels-") as directory:
            root = Path(directory)
            environment = {"CADGEN_CACHE_DIR": str(root / "store"), "CADGEN_DAEMON": "0"}
            with mock.patch.dict(os.environ, environment):
                part = Box(20, 20, 10).moved(Location())
                part.label = "part"
                part_path = root / "part.step"
                export_build123d_step_file(part, part_path)
                placed = import_step(part_path)
                assembly = Compound(
                    children=[placed.moved(Location((0, 0, 0))), placed.moved(Location((50, 0, 0)))],
                    label="assy",
                )
                assembly_path = root / "assy.step"
                export_build123d_step_file(assembly, assembly_path)
            products = re.findall(r"PRODUCT\('([^']*)'", assembly_path.read_text(encoding="utf-8"))
        self.assertEqual(set(products), {"assy", "part"}, products)


class XcafLabelEntryTests(unittest.TestCase):
    """Where a STEP gave a product or a usage no name, OCCT writes its label's address there.

    cadgen reads that as no name, in one place (``_normalize_label_name``), so every reader of
    the document -- its tree and view, ``read_scene``, the rows ``--mode list`` prints -- names
    the part by what the STEP does call it, else by what an unnamed node is called (a part
    ``part``, a root ``model``), never by the address.
    """

    def test_an_entry_is_no_name_and_a_name_holding_one_is_a_name(self) -> None:
        for entry in ("0:1:1:2", "[0:1:1:2]", "=>[0:1:1:2]", "=> [ 0:1:1:12 ]", " =>[0:1:1:2] ", "0:1"):
            self.assertIsNone(_normalize_label_name(entry), entry)
        for name in ("bracket 0:1:1:2", "0:1:1:2 spare", "=>[0:1:1:2] cover", "gear 3:1", "M3:0.5"):
            self.assertEqual(_normalize_label_name(name), name)

    def test_a_single_part_document_reads_its_part_name_not_the_entry(self) -> None:
        import json

        from cadgen import read_scene
        from cadgen._internal.doors import document_snapshot
        from cadgen.assembly_lookup import assembly_occurrence_rows
        from cadgen.step_export import export_build123d_step_file
        from cadgen.store.index import write_entry
        from cadgen.store.trees import get_tree, put_tree
        from cadgen.store.view import view_dir_for

        roots = IsolatedCadRoots(self, prefix="xcaf-label-entry-")
        # A placed single part, as cadgen saves one: OCCT wraps it in a root product it names
        # `=>[0:1:1:2]` and names the usage the same; the part's own product is `l_bracket`,
        # and the file reads as that part, placed.
        part = Location((1.0, 2.0, 3.0)) * Box(10, 8, 4)
        part.label = "l_bracket"
        path = roots.cad_root / "l_bracket.step"
        export_build123d_step_file(part, path)
        self.assertIn("PRODUCT('=>[0:1:1:2]'", path.read_text(encoding="utf-8"))
        document, tree = document_snapshot(path)

        # What a cadgen from before the entry rule left for these bytes -- a tree naming the root
        # and the part `=>[0:1:1:2]` -- is a miss, never served: the bytes compile again.
        stale = get_tree(tree)
        stale["label"] = stale["assembly"]["root"]["name"] = stale["occurrences"][0]["name"] = "=>[0:1:1:2]"
        write_entry("document", document, {"schemaVersion": 4, "tree": put_tree(stale), "kind": "step"})
        self.assertEqual((document, tree), document_snapshot(path))

        view = view_dir_for(tree, document_hash=document)
        descriptor = json.loads((view / "assembly.json").read_text(encoding="utf-8"))
        from cadgen._internal.tree_tokens import is_occurrence_token

        # The wrapper reads as no name, and a file of one placed part is that part.
        root = descriptor["assembly"]["root"]
        self.assertEqual([], root["children"])
        self.assertTrue(is_occurrence_token(root["id"]))
        self.assertEqual("l_bracket", root["name"])
        self.assertEqual([(root["id"], "l_bracket")], [(row["id"], row["name"]) for row in descriptor["occurrences"]])
        self.assertEqual([(root["id"], "l_bracket")],
                         [(row["id"], row["name"]) for row in assembly_occurrence_rows(descriptor, None)])
        scene = read_scene(path)
        self.assertEqual("l_bracket", scene.roots[0].label)
        self.assertEqual(["l_bracket"], [leaf.label for leaf in scene.leaves()])

    def test_an_unnamed_occurrence_never_shows_a_siblings_label(self) -> None:
        from cadgen import read_scene
        from cadgen.step_export import export_build123d_step_file

        roots = IsolatedCadRoots(self, prefix="xcaf-borrowed-name-")
        # Four occurrences of one shared product, which the writer names after the last
        # occurrence's label: `motor`. Two labels read as no name -- an XCAF entry and a
        # shape kind -- and those occurrences read as unnamed parts, not `motor`.
        box = Box(4, 4, 4)
        children = []
        for index, label in enumerate(["=>[0:1:1:3]", "bracket", "SOLID", "motor"]):
            child = Solid(box.wrapped.Moved(Location((6.0 * index, 0.0, 0.0)).wrapped))  # one TShape: one product
            child.label = label
            children.append(child)
        path = roots.cad_root / "names.step"
        export_build123d_step_file(Compound(children=children, label="names"), path)
        self.assertEqual(2, path.read_text(encoding="utf-8").count("PRODUCT("))  # the root, and `motor`
        self.assertIn("PRODUCT('motor'", path.read_text(encoding="utf-8"))

        scene = read_scene(path)
        leaves = list(scene.leaves())
        self.assertEqual(["part", "bracket", "part", "motor"], [leaf.label for leaf in leaves])
        self.assertEqual(4, len({leaf.ref for leaf in leaves}), "each occurrence its own token")
        self.assertEqual(leaves[3].ref, scene.resolve("#motor").ref)


if __name__ == "__main__":
    unittest.main()
