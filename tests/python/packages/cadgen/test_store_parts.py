"""A part's display data (STORE.md §2): ONE record per part, keyed by the component's geometry
and the producer, naming its display mesh and selector table; derived once, read back bound
to that record, and carried by a view for the CLI's readers; a part that fails to mesh is
named once the rest are stored. A tiny box in a fresh store."""

from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

from cadgen.store import meshes, parts  # noqa: E402
from cadgen.store.build import build_tree_from_compound  # noqa: E402
from cadgen.store.index import entry_path, remove_entry  # noqa: E402
from cadgen.store.objects import object_path  # noqa: E402
from cadgen.store.trees import capture_tree  # noqa: E402


class PartStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = generated_cad_directory(prefix="part-store-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        env = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store"), "CADGEN_DAEMON": "0"})
        env.start()
        self.addCleanup(env.stop)
        from build123d import Solid

        self.tree, _, _ = build_tree_from_compound(Solid.make_box(1, 2, 3), root_name="box")
        self.producer = parts.producer_identity()
        descriptor, _ = capture_tree(self.tree, retain_payloads=False)
        self.cid, self.entry = next(iter(descriptor["components"].items()))
        self.key = parts.part_key(self.entry, self.producer)

    def test_derive_stores_one_record_naming_both_objects_and_a_hit_is_a_read(self) -> None:
        record = parts.derive(self.tree)[self.cid]
        self.assertEqual(parts.probe(self.key), record)
        glb, table = parts.read(self.key, "glb"), parts.read(self.key, "selectors")
        self.assertEqual(meshes.body_facts(glb)["part"], self.key, "the mesh names its part")
        self.assertEqual(hashlib.sha256(table).hexdigest(), record["selectors"]["object"])
        self.assertEqual(json.loads(table)["stats"]["faceCount"], 6)
        self.assertEqual([record["selectors"][name] for name in ("faceCount", "edgeCount", "vertexCount")], [6, 12, 8])
        self.assertEqual([record["glb"][name] for name in ("faceCount", "edgeCount")], [6, 12],
                         "the mesh's faces and edges are the table's")
        written = entry_path("part", self.key).stat().st_mtime_ns
        self.assertEqual(parts.derive(self.tree), {self.cid: record})
        self.assertEqual(entry_path("part", self.key).stat().st_mtime_ns, written, "a second derive writes nothing")
        remove_entry("part", self.key)
        self.assertEqual(parts.derive(self.tree), {self.cid: record}, "the same inputs give the same part")

    def test_a_part_that_fails_to_mesh_is_named_once_the_rest_are_stored(self) -> None:
        from build123d import Box, Compound, Cylinder, Pos

        from cadgen._internal import tessellate

        tree, geometry, _ = build_tree_from_compound(
            Compound(children=[Box(4, 4, 4), Pos(10, 0, 0) * Cylinder(2, 4)]), root_name="pair")
        keys = [parts.part_key(entry, self.producer) for entry in geometry["components"].values()]
        real, failed = tessellate.mesh_component, []

        def mesher(topods, table, face_colors, *, part, **options):
            # Whatever fails, not only OCCT's own refusal: the first part meshed fails every
            # time, as a body its encoding refuses would.
            if failed in ([], [part]):
                failed[:] = [part]
                raise ValueError("invalid tessellation JSON length")
            return real(topods, table, face_colors, part=part, **options)

        def stored():
            return {key: parts.probe(key) is not None for key in keys}

        # A view's job names the component, after storing the other.
        named = r"^component [0-9a-f]{16}: ValueError: invalid tessellation JSON length$"
        with mock.patch.object(tessellate, "mesh_component", side_effect=mesher), \
                self.assertRaisesRegex(tessellate.MeshProductionError, named):
            parts.derive(tree)
        others = {key: key != failed[0] for key in keys}
        self.assertEqual(stored(), others, "the other part was stored before the failure was reported")
        # A build's derivation returns the one failure and fails nothing (what the build then
        # prints: test_build_derives_display).
        with mock.patch.object(tessellate, "mesh_component", side_effect=mesher):
            [_failure] = parts.ensure_display(tree)
        self.assertEqual(stored(), others)
        # An interrupt is not a part's failure.
        with mock.patch.object(tessellate, "mesh_component", side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):
            parts.ensure_display(tree)

    def test_part_keys_carry_their_version_and_an_older_one_is_obsolete(self) -> None:
        digest = self.key.rsplit("-v", 1)[0]
        older, newer = f"{digest}-v{parts.PART_VERSION - 1}", f"{digest}-v{parts.PART_VERSION + 1}"
        self.assertTrue(parts.valid_key(self.key))
        self.assertTrue(parts.obsolete_key(older))
        self.assertFalse(parts.obsolete_key(self.key))
        self.assertFalse(parts.obsolete_key(newer), "a newer cadgen's part is not this one's to retire")
        self.assertFalse(parts.valid_key(older))
        self.assertIsNone(parts.probe(older))
        other = {**self.producer, "ocp": self.producer["ocp"] + ".1"}
        self.assertNotEqual(parts.part_key(self.entry, other), self.key, "another kernel derives another part")

    def test_a_read_is_bound_to_its_record_and_the_admitted_bytes(self) -> None:
        glb = parts.derive(self.tree)[self.cid]["glb"]
        self.assertIsNotNone(parts.read(self.key, "glb", expected_object=glb["object"], max_bytes=glb["byteLength"]))
        self.assertIsNone(parts.read(self.key, "glb", expected_object="f" * 64))
        self.assertIsNone(parts.read(self.key, "glb", max_bytes=glb["byteLength"] - 1))
        # Same-size damage: a probe stats objects only, a read checks what it serves.
        record = parts.probe(self.key)
        other = ("1" if self.key[0] == "0" else "0") + self.key[1:]
        body = object_path(glb["object"])
        body.write_bytes(body.read_bytes().replace(self.key.encode(), other.encode()))
        table = object_path(record["selectors"]["object"])
        damaged = bytearray(table.read_bytes())
        damaged[-1] ^= 1
        table.write_bytes(bytes(damaged))
        self.assertEqual(parts.probe(self.key), record)
        self.assertIsNone(parts.read(self.key, "glb"), "a body naming another part is not this part's")
        self.assertIsNone(parts.read(self.key, "selectors"), "a table that is not its address is no table")

    def test_a_view_carries_each_parts_selector_table_and_nothing_else(self) -> None:
        from cadgen.store.view import SELECTOR_TABLE_SUFFIX, export_view

        parts.derive(self.tree)
        view_dir = export_view(self.tree, self.root / "view")
        descriptor = json.loads((view_dir / "assembly.json").read_text(encoding="utf-8"))
        entry = descriptor["components"][self.cid]
        self.assertEqual(entry["part"], self.key)
        self.assertEqual(entry["selectors"], f"components/{self.cid}{SELECTOR_TABLE_SUFFIX}")
        self.assertEqual((view_dir / entry["selectors"]).read_bytes(), parts.read(self.key, "selectors"))
        self.assertEqual(sorted(path.name for path in (view_dir / "components").iterdir()),
                         sorted([f"{self.cid}.brep", f"{self.cid}{SELECTOR_TABLE_SUFFIX}"]))

    def test_the_cli_resolves_a_ref_to_the_row_the_table_names(self) -> None:
        from cadgen import assembly_lookup, lookup
        from cadgen.store.view import export_view

        parts.derive(self.tree)
        view_dir = export_view(self.tree, self.root / "view")
        descriptor = json.loads((view_dir / "assembly.json").read_text(encoding="utf-8"))
        occurrence = descriptor["occurrences"][0]["id"]
        flat = lookup.build_selector_index({"tables": {"occurrenceColumns": ["id"]}, "occurrences": [[occurrence]]})
        merged = assembly_lookup.merge_assembly_entities(
            assembly_lookup.merge_assembly_occurrences(flat, descriptor, view_dir), descriptor, view_dir)
        table = json.loads(parts.read(self.key, "selectors"))
        columns = table["tables"]["faceColumns"]
        for row in table["faces"]:
            face = dict(zip(columns, row))
            ref = f"{occurrence}/{face['token']}"
            found = lookup.lookup_selector(ref, merged)
            self.assertIsNotNone(found, f"{ref} resolves")
            self.assertEqual(found[0], "face")
            self.assertEqual(found[1]["ordinal"], face["ordinal"])
            self.assertEqual(found[1]["tangentGroup"], face["tangentGroup"])
            # The one leaf qualifies a bare element token.
            self.assertEqual(lookup.lookup_selector(face["token"], merged), found)
        vertex_token = dict(zip(table["tables"]["vertexColumns"], table["vertices"][0]))["token"]
        vertex = lookup.lookup_selector(f"{occurrence}/{vertex_token}", merged)
        self.assertEqual(vertex[0], "vertex")
        self.assertIsNone(lookup.lookup_selector(f"{occurrence}.f1", merged), "the ordinal spelling is no ref")

    def test_a_body_and_its_step_read_back_are_one_part(self) -> None:
        """A part is keyed by its geometry (STORE.md §2): the body a build holds and the same
        body read back from the STEP it wrote -- other bytes, faces in another order -- share
        the fingerprint, the part key and each face's token."""
        from build123d import Solid, export_step, import_step

        from cadgen._internal.geometry_fingerprint import geometry_identity

        step = self.root / "box.step"
        export_step(Solid.make_box(1, 2, 3), str(step))
        read_back = import_step(str(step))
        tree, _, _ = build_tree_from_compound(read_back, root_name="box")
        descriptor, _ = capture_tree(tree, retain_payloads=False)
        cid, entry = next(iter(descriptor["components"].items()))
        self.assertNotEqual(cid, self.cid, "the read-back is another component (other bytes)")
        self.assertEqual(entry["fingerprint"], self.entry["fingerprint"])
        self.assertEqual(parts.part_key(entry, self.producer), self.key, "one part")
        before, after = parts.derive(self.tree)[self.cid], parts.derive(tree)[cid]
        self.assertEqual(before, after, "one record: the read-back derives nothing")
        self.assertEqual(sorted(geometry_identity(read_back.wrapped)["faces"]),
                         sorted(geometry_identity(Solid.make_box(1, 2, 3).wrapped)["faces"]))

    def test_an_export_at_a_tolerance_meshes_the_bytes_it_decodes(self) -> None:
        """The part of a body and of its read-back is one, so its stored table lists the
        elements of whichever derived it; an export at another tolerance builds the table
        for the bytes it decodes, and its mesh is the same whichever body derived the part
        (README law 5). A sphere: its seam is drawn and its two pole edges are not, and the
        read-back lists the three in another order, so a table of the wrong decode would
        draw a pole and hide the seam."""
        from build123d import Solid, export_step, import_step

        from cadgen._internal.topology_extract import extract_topology_facts
        from cadgen.store.meshes import EDGE_CLASSES, decode_payload

        def edge_classes(entry: dict) -> list[str]:
            return [edge["class"] for edge in extract_topology_facts(parts._decoded(entry)[0])["edges"]]

        body = Solid.make_sphere(3)
        step = self.root / "sphere.step"
        export_step(body, str(step))
        read_back = import_step(str(step))
        held, _, _ = build_tree_from_compound(body, root_name="sphere")
        read, _, _ = build_tree_from_compound(read_back, root_name="sphere")
        held_entry = next(iter(capture_tree(held, retain_payloads=False)[0]["components"].values()))
        read_entry = next(iter(capture_tree(read, retain_payloads=False)[0]["components"].values()))
        key = parts.part_key(held_entry, self.producer)
        self.assertEqual(parts.part_key(read_entry, self.producer), key, "one part")
        self.assertEqual(sorted(edge_classes(held_entry)), sorted(edge_classes(read_entry)))
        self.assertNotEqual(edge_classes(held_entry), edge_classes(read_entry),
                            "the read-back lists its edges in another order: the fixture tells a table of "
                            "the wrong decode apart")

        parts.derive(held)
        from_held_table = parts.mesh_at(read_entry, key, 2e-3, 0.5)
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store-alone")}):
            build_tree_from_compound(read_back, root_name="sphere")
            parts.derive(read)
            alone = parts.mesh_at(read_entry, key, 2e-3, 0.5)
        self.assertEqual(from_held_table, alone, "the read-back meshes differently once the body derived its part")
        self.assertEqual([EDGE_CLASSES[int(row[3])] for row in decode_payload(alone).edges], ["seam"],
                         "the seam is drawn as the seam and the poles are not drawn")


class PartRecordTest(unittest.TestCase):
    """A record a reader cannot trust is no record: to every reader the store lacks the part."""

    def setUp(self) -> None:
        from tests.python.support.tessellation import part_fixture, store_part

        self.tmp = generated_cad_directory(prefix="part-record-")
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(Path(self.tmp.name) / "store")})
        env.start()
        self.addCleanup(env.stop)
        self.key = part_fixture()["key"]
        self.record = store_part()

    def test_a_damaged_record_or_a_missing_object_is_a_miss(self) -> None:
        from cadgen.store.index import write_entry

        self.assertEqual(parts.probe(self.key), self.record)
        glb, table = self.record["glb"], self.record["selectors"]
        for label, damaged in (
            ("decoded size", {**self.record, "glb": {**glb, "decodedBytes": 1}}),
            ("byte length", {**self.record, "glb": {**glb, "byteLength": glb["byteLength"] + 4}}),
            ("edge points", {**self.record, "glb": {**glb, "edgePointCount": 1}}),
            ("no unmeshed face counted", {**self.record, "glb": {**glb, "unmeshedFaceCount": 0}}),
            ("unmeshed past the faces", {**self.record, "glb": {**glb, "unmeshedFaceCount": 2}}),
            ("unknown field", {**self.record, "glb": {**glb, "schemaVersion": 1}}),
            ("negative count", {**self.record, "selectors": {**table, "faceCount": -1}}),
            ("no table", {"glb": glb}),
            ("another field", {**self.record, "part": self.key}),
        ):
            with self.subTest(label=label):
                write_entry("part", self.key, damaged)
                self.assertIsNone(parts.probe(self.key))
        for raw in (b" " * (parts.MAX_INDEX_BYTES + 1), b"[" * 2000 + b"0" + b"]" * 2000):
            entry_path("part", self.key).write_bytes(raw)
            self.assertIsNone(parts.probe(self.key))
        write_entry("part", self.key, self.record)
        object_path(table["object"]).unlink()
        self.assertIsNone(parts.probe(self.key), "a record names objects the store holds")


if __name__ == "__main__":
    unittest.main()
