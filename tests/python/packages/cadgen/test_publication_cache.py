"""A worker publishes a component it published before without preparing it again
(STORE.md §3, *Tree*), and that changes no output.

The contract: whatever the worker recalls, a rebuild's tree, its components' bytes and its
provenance entry are those a cold build of the same source writes -- after no edit, and
after one edit that leaves a part alone, appends representations to a part's edges (an
extrusion reusing a profile already consumed), relabels a part, recolours a face, moves a
part, and pins a label on a feature an unchanged part descends from -- with the kernel
memo on and with hits off. With the memo on, a rebuild with no edit prepares no
component, and the parts an edit changed -- in bytes, colours or names -- are prepared
again. A parent that links its children publishes the unchanged ones by their trees, with
no child geometry digested, no link expanded and no clip baked again, and writes what a
cold build writes. Each build is ``python rig.py`` run in this process, its exports and
display jobs where they are submitted (``inline_artifacts``), with a store of its own.
"""

from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.inline_artifacts import inline_artifacts
from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

from cadgen._internal import component_package, kernel_memo  # noqa: E402

MODEL = """\
from cadgen import step
from cadgen import build123d as bd

LID = False
PEG = "peg"
KNOB = (0.8, 0.1, 0.1, 1.0)
SLAB_AT = (0, 30, 0)
CAP = False


@step
def rig():
    bored = bd.Solid.make_box(20, 20, 4).cut(bd.Solid.make_cylinder(3, 10))
    bored.label = "bored"
    profile = bd.Face(bd.Wire.make_circle(4))
    boss = bd.Solid.extrude(profile, bd.Vector(0, 0, 6))
    boss.label = "boss"
    parts = [bored, bd.Pos(30, 0, 0) * boss]
    if LID:
        lid = bd.Solid.extrude(profile, bd.Vector(0, 0, -1))
        lid.label = "lid"
        parts.append(bd.Pos(30, 0, -2) * lid)
    peg = bd.Solid.make_cylinder(1.5, 8)
    peg.label = PEG
    knob = bd.Solid.make_sphere(2)
    knob.cad_face_ordinal_colors = {1: KNOB}
    knob.label = "knob"
    slab = bd.Solid.make_box(5, 5, 1)
    slab.label = "slab"
    base = bd.Solid.make_box(6, 6, 6)
    if CAP:
        twin = bd.Pos(0, 0, 0) * base
        twin.label = "cap"
        bd.Pos(50, 0, 0) * twin
    block = base.cut(bd.Pos(3, 3, 0) * bd.Solid.make_cylinder(1, 20))
    block.label = "block"
    parts += [bd.Pos(-30, 0, 0) * peg, bd.Pos(0, -30, 0) * knob, bd.Pos(*SLAB_AT) * slab,
              bd.Pos(-30, -30, 0) * block]
    return bd.Compound(children=parts, label="rig")
"""

# One edit of each kind, applied together: (i) ``bored`` is left alone; (ii) a second
# extrusion of ``boss``'s profile appends representations to boss's edges; (iii) ``peg`` is
# relabelled; (iv) a face of ``knob`` is recoloured; (v) ``slab`` is moved; (vi) a placement
# ``block`` does not descend from hands on the box block is cut from, labelled: that pins
# the label on the box's feature and renames block's elements, while the memo hands block
# back as the same kernel object, in the same state.
EDITS = (
    ("LID = False", "LID = True"),
    ('PEG = "peg"', 'PEG = "pin"'),
    ("KNOB = (0.8, 0.1, 0.1, 1.0)", "KNOB = (0.1, 0.1, 0.8, 1.0)"),
    ("SLAB_AT = (0, 30, 0)", "SLAB_AT = (0, 40, 0)"),
    ("CAP = False", "CAP = True"),
)
UNCHANGED = {"bored", "pin", "slab"}
CHANGED = {"boss", "lid", "knob", "block"}


class PublicationCacheCase(unittest.TestCase):
    def setUp(self) -> None:
        self.enterContext(inline_artifacts())
        temporary = generated_cad_directory(prefix="publication-cache-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.script = self.root / "rig.py"
        self.script.write_text(MODEL, encoding="utf-8")
        self.stores = 0
        self.environment = self.enterContext(mock.patch.dict(os.environ))
        os.environ.pop("CADGEN_KERNEL_MEMO", None)
        kernel_memo.install()
        self.addCleanup(self.cold)

    def store(self) -> None:
        """A store of its own for the builds that follow."""
        self.stores += 1
        os.environ["CADGEN_CACHE_DIR"] = str(self.root / f"store-{self.stores}")

    def cold(self) -> None:
        """This process as a fresh worker: nothing memoized, prepared, digested, expanded,
        measured or reduced."""
        from cadgen._internal import animation_bake
        from cadgen.store import bounds, build, materialize, trees

        kernel_memo.clear()
        build.clear_prepared()
        materialize.clear_held_digests()
        trees._reset_metadata_capture_cache()
        bounds.clear()
        with animation_bake._REDUCED_LOCK:
            animation_bake._REDUCED.clear()

    def build(self, *flags: str) -> tuple[dict, dict, list[str]]:
        """``(record, provenance entry, labels of the components it prepared)`` of
        ``python rig.py flags``."""
        from cadgen.cli._run_model import run_model_argv
        from cadgen.store.build import prepared_stats
        from cadgen.store.provenance import read_provenance
        from cadgen.store.records import read_record

        prepared: list[str] = []
        original = component_package.prepare_geometry_component

        def prepare(shape, *args, **kwargs):
            prepared.append(str(getattr(shape, "label", "")))
            return original(shape, *args, **kwargs)

        errors = prepared_stats()["errors"]
        err = io.StringIO()
        with mock.patch.object(component_package, "prepare_geometry_component", side_effect=prepare), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run_model_argv([str(self.script), *flags])
        self.assertEqual(code, 0, err.getvalue())
        self.assertEqual(prepared_stats()["errors"], errors, "checking a remembered component failed")
        record, provenance = read_record(self.script), read_provenance(self.script)
        self.assertIsNotNone(record)
        self.assertIsNotNone(provenance)
        return record, provenance, prepared

    def cold_build(self) -> tuple[dict, dict]:
        """The same source built by a fresh worker into a fresh store."""
        self.cold()
        self.store()
        record, provenance, _prepared = self.build()
        return record, provenance

    def assert_built_as_cold(self, hot: tuple[dict, dict], cold: tuple[dict, dict]) -> None:
        from cadgen.store.objects import read_object
        from cadgen.store.trees import get_tree

        (hot_record, hot_provenance), (cold_record, cold_provenance) = hot, cold
        self.assertEqual((hot_record["tree"], hot_record["key"]), (cold_record["tree"], cold_record["key"]))
        components = get_tree(cold_record["tree"])["components"]
        self.assertTrue(all(read_object(entry["brep"]) for entry in components.values()))
        for field in ("tree", "features", "components", "occurrences"):
            self.assertEqual(hot_provenance[field], cold_provenance[field], field)

    def check(self, *, memo: bool) -> None:
        if not memo:
            os.environ["CADGEN_KERNEL_MEMO"] = "0"
        self.cold()
        self.store()
        first, first_provenance, prepared = self.build()
        everything = {"bored", "boss", "peg", "knob", "slab", "block"}
        self.assertEqual(set(prepared), everything)
        again, again_provenance, prepared = self.build("--force")
        self.assert_built_as_cold((again, again_provenance), (first, first_provenance))
        self.assertEqual(set(prepared), set() if memo else everything,
                         "a rebuild with no edit prepares no component")
        text = self.script.read_text(encoding="utf-8")
        for old, new in EDITS:
            self.assertIn(old, text)
            text = text.replace(old, new)
        self.script.write_text(text, encoding="utf-8")
        edited, edited_provenance, prepared = self.build()
        self.assertEqual(set(prepared), CHANGED if memo else CHANGED | UNCHANGED,
                         "an edit prepares the parts it changed, and only those")
        self.assert_built_as_cold((edited, edited_provenance), self.cold_build())


class PublicationCache(PublicationCacheCase):
    def test_a_rebuild_recalls_what_no_edit_changed_and_writes_what_a_cold_build_writes(self) -> None:
        self.check(memo=True)

    def test_with_hits_off_every_rebuild_prepares_every_component_and_writes_the_same(self) -> None:
        self.check(memo=False)

    def test_what_a_worker_remembers_is_bounded_and_never_outlives_the_memo(self) -> None:
        """Past its bounds the least recently recalled component is forgotten, and one the
        memo no longer holds -- which no hit can hand back -- is forgotten when the next
        publication remembers what it prepared."""
        from cadgen.store import build

        self.cold()
        self.store()
        with mock.patch.object(build, "_REMEMBERED_BYTES", 1):
            self.build()
        self.assertEqual(build.prepared_stats()["entries"], 0)
        with mock.patch.object(build, "_REMEMBERED_ENTRIES", 2):
            self.build("--force")
        self.assertEqual(build.prepared_stats()["entries"], 2)
        kernel_memo.clear()
        _record, _provenance, prepared = self.build("--force")
        self.assertEqual(len(prepared), 6, "a fresh memo hands back no shape a component was remembered by")
        self.assertEqual(build.prepared_stats()["entries"], 6, "what the memo forgot is forgotten")

    def test_a_change_the_memo_could_not_replay_recalls_nothing_published_before_it(self) -> None:
        """A change the memo cannot replay, could not capture, or saw under an uncached call
        on a wrapper of memoized geometry leaves a state its ledger notes no row for, on
        whatever shapes share the sub-shape it was on: after one, every component is
        prepared again (here the memo's count of them is moved, as the change would)."""
        for count in ("unreplayable", "errors", "tainted"):
            with self.subTest(count=count):
                self.cold()
                self.store()
                self.build()
                kernel_memo._STATS[count] += 1
                _record, _provenance, prepared = self.build("--force")
                self.assertEqual(len(prepared), 6, "every component is prepared again")
                _record, _provenance, prepared = self.build("--force")
                self.assertEqual(prepared, [], "what was prepared after it is recalled")


IMPORTING = """\
from pathlib import Path

from cadgen import build123d as bd
from cadgen import compound_from_instances, read_step, step

PIN = 1.0


def unified(shape):
    from OCP.ShapeFix import ShapeFix_Shape
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain

    fix = ShapeFix_Shape(shape.wrapped)
    fix.SetPrecision(1e-6)
    fix.Perform()
    unify = ShapeUpgrade_UnifySameDomain(fix.Shape(), True, True, False)
    unify.Build()
    result = bd.Solid(unify.Shape())
    assert result.is_valid
    result.label = "bored"
    return result


@step
def rig():
    vendor = read_step(Path(__file__).parent / "vendor.step")
    bored = unified(vendor.cut(bd.Solid.make_cylinder(1, 30)))
    pair = compound_from_instances("pair", [(bored, bd.Location((0, 0, 0)), "left"),
                                            (vendor, bd.Location((20, 0, 0)), "right")])
    peg = bd.Solid.make_cylinder(PIN, 5)
    peg.label = "peg"
    return bd.Compound(children=[pair, bd.Pos(0, 30, 0) * peg], label="rig")
"""


class ImportedPublication(PublicationCacheCase):
    """A model reading a STEP file, placing what it makes of it with
    ``compound_from_instances`` and fixing it with OCCT makers of its own: an edit elsewhere
    prepares nothing made of the read, a file with other bytes prepares what is made of it
    again, and each rebuild writes what a cold build writes. Each store holds the file's
    compile before a build reads it, so what a build prepares is its own."""

    def setUp(self) -> None:
        super().setUp()
        from tests.python.support.inline_artifacts import inline_compiles

        self.enterContext(inline_compiles())
        self.script.write_text(IMPORTING, encoding="utf-8")
        self.vendor(4)

    def store(self) -> None:
        super().store()
        self.compile()

    def compile(self) -> None:
        from cadgen import step

        with contextlib.redirect_stderr(io.StringIO()):
            self.assertTrue(step.compile(self.root / "vendor.step").ok)

    def vendor(self, height: float) -> None:
        from cadgen import build123d as bd
        from cadgen.step_export import export_build123d_step_file

        part = bd.Solid.make_box(8, 6, height)
        part.label = "vendor_part"
        export_build123d_step_file(part, self.root / "vendor.step")
        if self.stores:
            self.compile()

    def test_a_rebuild_recalls_what_is_made_of_an_unchanged_read_and_writes_what_a_cold_build_writes(self) -> None:
        self.cold()
        self.store()
        _record, _provenance, prepared = self.build()
        self.assertEqual(set(prepared), {"bored", "vendor_part", "peg"})
        self.script.write_text(IMPORTING.replace("PIN = 1.0", "PIN = 1.5"), encoding="utf-8")
        before = kernel_memo.stats()
        edited, edited_provenance, prepared = self.build()
        self.assertEqual(kernel_memo.stats()["unkeyable"], before["unkeyable"])
        self.assertEqual(prepared, ["peg"], "nothing made of the read is prepared again")
        self.assert_built_as_cold((edited, edited_provenance), self.cold_build())
        self.vendor(5)
        changed, changed_provenance, prepared = self.build()
        self.assertEqual(set(prepared), {"bored", "vendor_part"}, "another file's bytes are another tree")
        self.assert_built_as_cold((changed, changed_provenance), self.cold_build())


# A parent placing child models a lib module builds: two placements of an assembly child,
# three of a part child grouped, a leaf of its own, and a clip moving the group.
PARTS = """\
from cadgen import build123d as bd

SIZE = 4.0


def bracket():
    plate = bd.Solid.make_box(SIZE, 6, 1)
    plate.label = "plate"
    boss = bd.Pos(1, 1, 1) * bd.Solid.make_cylinder(0.8, 3)
    boss.label = "boss"
    return bd.Compound(children=[plate, boss], label="bracket")


def pin():
    pin = bd.Solid.make_cylinder(0.5, 5)
    pin.label = "pin"
    return pin
"""

CHILD = """\
from cadgen import step

from lib import parts


@step
def {name}():
    return parts.{name}()
"""

ROOT = """\
import cadgen
from cadgen import build123d as bd
from cadgen import step

from bracket import bracket
from pin import pin


def _lift(t, m):
    m.get("#pins").translate((0, 0, 2 * t))


@step(animation={"lift": cadgen.clip(_lift, duration=1, label="Lift")})
def rig():
    parts = []
    for index in range(2):
        placed = bd.Pos(index * 20, 0, 0) * bracket()
        placed.label = f"bracket_{index}"
        parts.append(placed)
    pins = []
    for index in range(3):
        placed = bd.Pos(index * 5, 10, 0) * pin()
        placed.label = f"pin_{index}"
        pins.append(placed)
    parts.append(bd.Compound(children=pins, label="pins"))
    base = bd.Solid.make_box(50, 20, 1)
    base.label = "base"
    parts.append(bd.Pos(-5, -5, -1) * base)
    return bd.Compound(children=parts, label="rig")
"""


class LinkedPublication(PublicationCacheCase):
    """A parent rebuilt with its children unchanged publishes them by their trees: its partner
    checks digest no child geometry the memo hands back unchanged, its flattens expand no link
    to an unchanged child again, and its bake of clips whose key and tree it has stored does
    not run -- yet its tree, components and provenance, and its record's keyframes, are those a
    cold build writes, after no edit and after an edit of one child."""

    def setUp(self) -> None:
        super().setUp()
        (self.root / "lib").mkdir()
        (self.root / "lib" / "__init__.py").write_text("", encoding="utf-8")
        (self.root / "lib" / "parts.py").write_text(PARTS, encoding="utf-8")
        for name in ("bracket", "pin"):
            (self.root / f"{name}.py").write_text(CHILD.format(name=name), encoding="utf-8")
        self.script.write_text(ROOT, encoding="utf-8")

    def counted(self, *flags: str) -> tuple[dict, dict, list[str], dict[str, int]]:
        """:meth:`build`, and how much of the children's publication it did again."""
        from cadgen._internal import animation_bake
        from cadgen.store.materialize import held_digest_stats
        from cadgen.store.trees import expansion_stats

        before = {**held_digest_stats(), **expansion_stats()}
        bake = animation_bake.bake_document_animation
        with mock.patch.object(animation_bake, "bake_document_animation", side_effect=bake) as baked:
            record, provenance, prepared = self.build(*flags)
        after = {**held_digest_stats(), **expansion_stats()}
        work = {name: after[name] - before[name] for name in ("read", "trusted", "expanded", "reused")}
        work["baked"] = baked.call_count
        return record, provenance, prepared, work

    def test_a_rebuild_publishes_unchanged_children_by_their_trees_and_writes_what_a_cold_build_writes(self) -> None:
        self.cold()
        self.store()
        first = self.counted()
        self.assertEqual(set(first[2]), {"plate", "boss", "pin", "base"})
        self.counted("--force")  # the worker's first rebuild verifies what its cold build wrote
        record, provenance, prepared, work = self.counted("--force")
        self.assertEqual(prepared, [], "a rebuild with no edit prepares no component")
        self.assertEqual((work["read"], work["expanded"], work["baked"]), (0, 0, 0),
                         "and reads no child's geometry, expands no link and bakes nothing again")
        self.assertEqual(work["trusted"], 2 * 2 + 3, "each placed child leaf's digest is the one taken before")
        self.assertGreaterEqual(work["reused"], 5, "each link's expansion is the one made before")
        self.assert_built_as_cold((record, provenance), first[:2])
        self.assertEqual(record["animation"], first[0]["animation"])

        parts = self.root / "lib" / "parts.py"
        parts.write_text(parts.read_text(encoding="utf-8").replace("SIZE = 4.0", "SIZE = 4.5"), encoding="utf-8")
        edited, edited_provenance, prepared, work = self.counted()
        self.assertEqual(prepared, ["plate"], "an edit of one part prepares that part alone")
        self.assertEqual(work["baked"], 1, "a new result bakes its clips")
        self.assertGreaterEqual(work["reused"], 3, "the unchanged child's links are expanded as before")
        cold_record, cold_provenance = self.cold_build()
        self.assert_built_as_cold((edited, edited_provenance), (cold_record, cold_provenance))
        self.assertEqual(edited["animation"], cold_record["animation"])


if __name__ == "__main__":
    unittest.main()
