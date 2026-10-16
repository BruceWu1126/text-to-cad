"""The kernel memo (README law 18), the construction keys it gives, and the trace that
names what a run made (README law 20, STORE.md §3).

The contract: inside a run, every kernel construction the model's code calls is
a feature; a second run of the same code hands back the SAME live objects the
first run made, restored to the state the operation returned them in, and ends
with the bytes a cold run ends with -- the kernel's own changes to its inputs
replayed on a hit and undone when the operation is gone; two identical
constructions in one run are two objects; an input that changed, by geometry,
placement or attribute, misses, and so does everything downstream of a miss;
a shape no operation of this process made runs its call uncached; and with
the memo off every call still runs and is still a feature. A component is
named by the construction that made it, the same in every process, and its
elements by the features that made them. Small geometry, in process, no store.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import kernel_memo, kernel_trace  # noqa: E402
from cadgen._internal.component_package import _shape_brep_bytes as _bytes  # noqa: E402

COUNTS = ("hits", "misses", "unkeyable", "unreplayable", "errors")


def _delta(before: dict, after: dict) -> dict:
    return {name: after[name] - before[name] for name in COUNTS}


def _run(function, *args):
    """``(result, stats delta, run)`` of calling ``function`` inside a run."""
    before = kernel_memo.stats()
    with kernel_memo.run(None) as run:
        result = function(*args)
    return result, _delta(before, kernel_memo.stats()), run


def _bored_plate(radius=3.0):
    from build123d import Solid

    box = Solid.make_box(20, 20, 8)
    tool = Solid.make_cylinder(radius, 30)
    return box, tool, box.cut(tool)


def _junction_fillets():
    """The sequential-fillet pattern law 18 names: hold a boss's junction edges,
    re-find each one in the CURRENT solid by ``is_same``, fillet it. Returns
    (fillets applied, the result)."""
    from build123d import Box, Pos

    shape = Box(10, 10, 10).fuse(Pos(5, 0, 0) * Box(10, 6, 6))
    roots = [e for e in shape.edges() if 4 < e.length < 6.5]
    roots.sort(key=lambda e: tuple(round(v, 5) for v in e.center()))
    applied = 0
    for edge in roots:
        current = next((c for c in shape.edges() if edge.is_same(c)), None)
        if current is None:
            continue
        shape = shape.fillet(0.5, [current])
        applied += 1
    return applied, shape


class KernelMemoCase(unittest.TestCase):
    def setUp(self) -> None:
        kernel_memo.install()
        kernel_memo.clear()
        self.addCleanup(kernel_memo.clear)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        os.environ.pop("CADGEN_KERNEL_MEMO", None)


class InstallTest(KernelMemoCase):
    def test_install_is_idempotent_and_uninstall_restores(self) -> None:
        from build123d.topology import Face, Mixin3D, Shape, Solid

        self.assertFalse(kernel_memo.install())
        self.assertTrue(getattr(Solid.make_box.__func__, "__kernel_memo__", False))
        self.assertTrue(getattr(Mixin3D.fillet, "__kernel_memo__", False))
        self.assertTrue(getattr(Shape.is_valid.fget, "__kernel_memo__", False))
        self.assertTrue(getattr(Face.__init__, "__kernel_memo__", False))
        self.addCleanup(kernel_memo.install)
        self.assertTrue(kernel_memo.uninstall())
        self.assertFalse(kernel_memo.uninstall())
        self.assertFalse(getattr(Solid.make_box.__func__, "__kernel_memo__", False))
        self.assertFalse(getattr(Mixin3D.fillet, "__kernel_memo__", False))
        self.assertFalse(getattr(Shape.is_valid.fget, "__kernel_memo__", False))

    def test_outside_a_run_nothing_is_memoized_or_traced(self) -> None:
        before = kernel_memo.stats()
        _bored_plate()
        self.assertEqual(_delta(before, kernel_memo.stats()), dict.fromkeys(COUNTS, 0))


class HitTest(KernelMemoCase):
    def test_a_hit_hands_back_the_same_objects_restored(self) -> None:
        from build123d import Location

        def model():
            box, tool, body = _bored_plate()
            body.label = "plate"
            body.move(Location((1, 0, 0)))  # mutates the location in place, after the operation returned
            return box, tool, body

        (box, tool, body), cold, _run1 = _run(model)
        (box2, tool2, body2), hot, _run2 = _run(model)
        self.assertEqual(cold["hits"], 0)
        self.assertGreater(cold["misses"], 0)
        self.assertEqual((hot["misses"], hot["unkeyable"], hot["errors"]), (0, 0, 0))
        self.assertEqual(hot["hits"], cold["misses"])
        self.assertIs(box2, box)
        self.assertIs(tool2, tool)
        self.assertIs(body2, body)
        # Restored to the state the cut returned it in, then moved once by this run.
        self.assertEqual(body2.label, "plate")
        self.assertEqual(tuple(round(v, 9) for v in body2.location.position), (1.0, 0.0, 0.0))
        self.assertEqual(_bytes(body2.wrapped), _bytes(body.wrapped))

    def test_plain_cold_and_hot_runs_agree(self) -> None:
        """Law 18's pressure-test: the geometry, and every is_same along the way."""
        plain_count, plain = _junction_fillets()
        (cold_count, cold), cold_stats, _ = _run(_junction_fillets)
        (hot_count, hot), hot_stats, _ = _run(_junction_fillets)
        self.assertEqual((plain_count, cold_count, hot_count), (6, 6, 6))
        self.assertEqual(_bytes(plain.wrapped), _bytes(cold.wrapped))
        self.assertEqual(_bytes(cold.wrapped), _bytes(hot.wrapped))
        self.assertIs(hot, cold)
        self.assertEqual(cold_stats["hits"], 0)
        self.assertEqual(hot_stats["misses"], 0)
        self.assertEqual(hot_stats["hits"], cold_stats["misses"])
        self.assertEqual((cold_stats["unkeyable"], hot_stats["unkeyable"], hot_stats["errors"]), (0, 0, 0))

    def test_identical_constructions_in_one_run_are_two_objects(self) -> None:
        from build123d import Solid

        def model():
            return Solid.make_box(1, 1, 1), Solid.make_box(1, 1, 1)

        (a, b), _, _ = _run(model)
        self.assertIsNot(a, b)
        self.assertFalse(a == b)
        (a2, b2), hot, _ = _run(model)
        self.assertIs(a2, a)
        self.assertIs(b2, b)
        self.assertEqual(hot["hits"], 2)

    def test_sketch_constructors_and_in_place_clean_hit(self) -> None:
        from build123d import BuildSketch, Circle, Solid, Vector

        def model():
            with BuildSketch() as sketch:
                Circle(4)
            profile = sketch.sketch.faces()[0]
            return profile, Solid.extrude(profile, Vector(0, 0, 4))

        (profile, boss), cold, _ = _run(model)
        (profile2, boss2), hot, _ = _run(model)
        self.assertEqual((cold["unkeyable"], hot["misses"], hot["unkeyable"]), (0, 0, 0))
        self.assertGreater(hot["hits"], 0)
        self.assertIs(boss2, boss)
        # The sketch's face is constructed anew each run and wraps the first run's TShape.
        self.assertTrue(profile2.wrapped.IsPartner(profile.wrapped))
        self.assertEqual(_bytes(boss2.wrapped), _bytes(boss.wrapped))

    def test_an_in_place_clean_that_rebuilds_its_shape_hits(self) -> None:
        """``clean()`` leaves its input wrapping a new TShape: the entry must
        remember the input as it was given, or every hot run misses it and
        everything downstream of it."""
        from build123d import Edge, Face, Location, Solid, Vector, Wire

        def model():
            points = [Vector(0, 0, 0), Vector(5, 0, 0), Vector(10, 0, 0), Vector(10, 5, 0), Vector(0, 5, 0)]
            profile = Face(Wire([Edge.make_line(points[i], points[(i + 1) % 5]) for i in range(5)]))
            prism = Solid.extrude(profile, Vector(0, 0, 4))
            extruded = prism.wrapped.TShape()
            prism.clean()  # merges the two side faces on one plane: a new TShape, in place
            return prism.cut(Solid.make_cylinder(1, 30).moved(Location((5, 2.5, -5)))), extruded

        (bored, extruded), cold, _ = _run(model)
        self.assertEqual(len(bored.faces()), 7, "six faces of the cleaned prism and the bore")
        (bored2, extruded2), hot, _ = _run(model)
        self.assertEqual(extruded2, extruded, "the extrusion hit")
        self.assertIs(bored2, bored)
        self.assertEqual((hot["misses"], hot["unkeyable"]), (0, 0))
        self.assertEqual(hot["hits"], cold["misses"])

    def test_split_results_are_sequences_of_the_same_objects(self) -> None:
        from build123d import Keep, Plane, Solid

        def model():
            body = Solid.make_box(10, 10, 10).moved(Plane.XY.offset(-5).location)
            return body.split(Plane.XY, keep=Keep.BOTH)

        (top, bottom), cold, _ = _run(model)
        (top2, bottom2), hot, _ = _run(model)
        self.assertEqual(hot["misses"], 0)
        self.assertIs(top2, top)
        self.assertIs(bottom2, bottom)
        self.assertEqual(_bytes(top2.wrapped), _bytes(top.wrapped))


class MissTest(KernelMemoCase):
    def test_a_changed_input_misses_and_so_does_its_downstream(self) -> None:
        (_box, _tool, body), _, _ = _run(_bored_plate, 3.0)
        plain_box, _plain_tool, plain = _bored_plate(4.0)
        (box2, tool2, body2), stats, _ = _run(_bored_plate, 4.0)
        self.assertEqual(stats["hits"], 1, "the unchanged box hits")
        self.assertEqual(stats["misses"], 2, "the changed cylinder misses, and the cut after it")
        self.assertIsNot(body2, body)
        self.assertEqual(_bytes(body2.wrapped), _bytes(plain.wrapped))

    def test_attributes_and_placement_of_inputs_are_in_the_key(self) -> None:
        from build123d import Location, Solid

        def labelled(label):
            box = Solid.make_box(20, 20, 8)
            box.label = label
            return box.cut(Solid.make_cylinder(3, 30))

        _run(labelled, "a")
        second, stats, _ = _run(labelled, "b")
        self.assertEqual(second.label, "b")
        self.assertEqual(stats["misses"], 1, "the cut misses on the relabelled base; its inputs hit")

        def placed(offset):
            box = Solid.make_box(20, 20, 8)
            box.move(Location((offset, 0, 0)))
            return box.cut(Solid.make_cylinder(3, 30))

        far, _, _ = _run(placed, 0.0)
        near, stats, _ = _run(placed, 5.0)
        self.assertEqual(stats["misses"], 1)
        self.assertNotEqual(_bytes(far.wrapped), _bytes(near.wrapped))
        self.assertEqual(_bytes(near.wrapped), _bytes(placed(5.0).wrapped))

    def test_a_generator_argument_runs_uncached_but_correct(self) -> None:
        from build123d import Solid

        def model():
            box = Solid.make_box(20, 20, 8)
            return box, box.fillet(1.0, (edge for edge in box.edges()[:2]))

        (box, filleted), stats, _ = _run(model)
        self.assertEqual(stats["unkeyable"], 1)
        self.assertLess(filleted.volume, box.volume)
        (_box2, filleted2), hot, _ = _run(model)
        self.assertEqual(hot["unkeyable"], 1)
        self.assertIsNot(filleted2, filleted)
        self.assertEqual(_bytes(filleted2.wrapped), _bytes(filleted.wrapped))

    def test_a_foreign_shape_makes_its_call_run_uncached(self) -> None:
        from build123d import Solid

        foreign = Solid.make_box(20, 20, 8)  # made outside any run

        def model():
            return foreign.cut(Solid.make_cylinder(3, 30))

        _, stats, run = _run(model)
        self.assertEqual((stats["misses"], stats["unkeyable"]), (1, 1))
        cut = run.trace.features[-1]
        self.assertEqual(cut.op, "cut")
        self.assertEqual(cut.inputs, [None, None, 0], "the foreign base has no feature; the cylinder does")
        _, again, _ = _run(model)
        self.assertEqual(again["hits"], 1, "the cylinder hits; the cut on the foreign base runs again")
        self.assertEqual(again["unkeyable"], 1)


class EffectTest(KernelMemoCase):
    """The kernel's changes to its inputs: a boolean raises a tool vertex's
    tolerance, an extrusion appends representations to its profile's edges."""

    def test_effects_on_inputs_are_replayed_on_a_hit_and_undone_when_the_operation_is_gone(self) -> None:
        from build123d import Solid

        pristine = _bytes(Solid.make_cylinder(3, 30).wrapped)

        def with_cut():
            box, tool, body = _bored_plate()
            return tool, body

        def without_cut():
            Solid.make_box(20, 20, 8)
            return Solid.make_cylinder(3, 30)  # 3 for the plate's 3.0: one parameter, by value

        (tool, body), cold, _ = _run(with_cut)
        mutated = _bytes(tool.wrapped)
        self.assertNotEqual(mutated, pristine, "the cut changed its tool")
        (tool2, body2), hot, _ = _run(with_cut)
        self.assertIs(tool2, tool)
        self.assertEqual(hot["misses"], 0)
        self.assertEqual(_bytes(tool2.wrapped), mutated, "a hit leaves the tool as the cut left it")
        self.assertEqual(_bytes(body2.wrapped), _bytes(body.wrapped))
        tool3, edited, _ = _run(without_cut)
        self.assertIs(tool3, tool)
        self.assertEqual(edited["hits"], 2)
        self.assertEqual(_bytes(tool3.wrapped), pristine, "with the cut edited out, the tool is as it was made")
        self.assertEqual((cold["unreplayable"], hot["unreplayable"], edited["unreplayable"]), (0, 0, 0))

    def test_an_extruded_profile_ends_as_a_cold_run_leaves_it(self) -> None:
        from build123d import Face, Solid, Vector, Wire

        def model():
            profile = Face(Wire.make_circle(4))
            boss = Solid.extrude(profile, Vector(0, 0, 4))
            return profile, boss

        plain_profile, plain_boss = model()
        (profile, boss), cold, _ = _run(model)
        (profile2, boss2), hot, _ = _run(model)
        self.assertEqual(cold["unkeyable"], 0)
        self.assertEqual(hot["misses"], 0)
        self.assertEqual(_bytes(profile2.wrapped), _bytes(plain_profile.wrapped))
        self.assertEqual(_bytes(boss2.wrapped), _bytes(plain_boss.wrapped))
        self.assertEqual(_bytes(boss2.wrapped), _bytes(boss.wrapped))


class CapacityTest(KernelMemoCase):
    def test_the_memo_is_bounded_and_a_rerun_past_the_bound_is_still_right(self) -> None:
        from build123d import Solid

        def model():
            body = Solid.make_box(20, 20, 8)
            for index in range(4):
                body = body.cut(Solid.make_cylinder(1, 30).translate((3 + 4 * index, 10, -5)))
            return body

        plain = _bytes(model().wrapped)
        with mock.patch.object(kernel_memo, "CAPACITY", 5):
            first, _, _ = _run(model)
            self.assertLessEqual(kernel_memo.stats()["entries"], 5)
            second, _, _ = _run(model)
        self.assertEqual(_bytes(first.wrapped), plain)
        self.assertEqual(_bytes(second.wrapped), plain)


class KillSwitchTest(KernelMemoCase):
    def test_the_kill_switch_keeps_the_trace(self) -> None:
        os.environ["CADGEN_KERNEL_MEMO"] = "0"
        (_box, _tool, body), first, run1 = _run(_bored_plate)
        (_box2, _tool2, body2), second, run2 = _run(_bored_plate)
        self.assertEqual((first["hits"], first["misses"], second["hits"], second["misses"]), (0, 0, 0, 0))
        self.assertIsNot(body2, body)
        self.assertEqual([feature.name for feature in run1.trace.features], [feature.name for feature in run2.trace.features])
        self.assertEqual([feature.op for feature in run2.trace.features], ["Solid.make_box", "Solid.make_cylinder", "cut"])
        self.assertEqual(run2.trace.features[-1].inputs, [0, 0, 1])
        # Nothing memoized made the body, so it has no construction key; its lineage stands.
        key, lineage = run2.identify(body2)
        self.assertIsNone(key)
        self.assertEqual(lineage["made"], "_bored_plate.cut1")


class TraceTest(KernelMemoCase):
    def test_features_are_named_by_function_operation_and_count(self) -> None:
        from build123d import Solid

        def model():
            box = Solid.make_box(20, 20, 8)
            tools = [Solid.make_cylinder(1, 30).moved(Solid.make_box(1, 1, 1).location) for _ in range(2)]
            return box.cut(*tools)

        _, _, run = _run(model)
        names = [feature.name for feature in run.trace.features]
        here = Path(__file__).as_posix()  # the trace names a file the same on every OS
        self.assertEqual(names[0], "model.make_box1")
        self.assertTrue(all(feature.file == here for feature in run.trace.features))
        self.assertEqual(run.trace.features[0].function, "model")
        # A function's calls of one operation count up, wherever they are; a line is no part of a name.
        cylinders = [feature for feature in run.trace.features if feature.op == "Solid.make_cylinder"]
        self.assertEqual([feature.name for feature in cylinders], ["model.make_cylinder1", "model.make_cylinder2"])
        self.assertEqual(len({feature.line for feature in cylinders}), 1)
        self.assertEqual(names.count("model.make_box2"), 1, "the boxes the tools are placed by count on")
        cut = run.trace.features[-1]
        self.assertEqual((cut.op, cut.name), ("cut", "model.cut1"))
        moved = [feature.index for feature in run.trace.features if feature.op == "moved"]
        self.assertEqual(cut.inputs, [0, 0, *moved], "the cut consumed the box and the two placed tools")
        row = run.trace.feature_rows()[-1]
        self.assertEqual((row["name"], row["op"], row["function"]), ("model.cut1", "cut", "model"))
        self.assertEqual(row["inputs"][:2], ["model.make_box1", "model.make_box1"])

    def test_elements_are_named_by_the_feature_that_made_them(self) -> None:
        """Unchanged: same surface, same extent, inherited. Modified: same
        surface, another extent, the name kept and the modifier recorded. Made:
        anything else, named by the feature that made it."""
        from build123d import Axis, Location, Solid

        from cadgen._internal.entity_ordinals import entity_map

        def model():
            box = Solid.make_box(20, 20, 8)
            tool = Solid.make_cylinder(3, 30).moved(Location((10, 10, -5)))
            return box.cut(tool)

        body, _, run = _run(model)
        _key, lineage = run.identify(body)
        self.assertEqual(lineage["made"], "model.cut1")
        faces, edges = entity_map(body.wrapped, "face"), entity_map(body.wrapped, "edge")

        def face(shape):
            return lineage["faces"][faces.FindIndex(shape.wrapped) - 1]

        def edge(shape):
            return lineage["edges"][edges.FindIndex(shape.wrapped) - 1]

        bottom = body.faces().sort_by(Axis.Z)[0]
        side = body.faces().sort_by(Axis.X)[0]
        bore = [shape for shape in body.faces() if shape.geom_type.name == "CYLINDER"][0]
        self.assertRegex(face(bottom), r"^f:model\.make_box1\.[1-6]$")
        self.assertEqual(lineage["modified"][face(bottom)], "model.cut1", "the hole changed the bottom face's extent")
        self.assertRegex(face(side), r"^f:model\.make_box1\.[1-6]$")
        self.assertNotIn(face(side), lineage["modified"], "the cut left the side face alone")
        self.assertEqual(face(bore), "f:model.make_cylinder1.1", "the bore wall is the cylinder's face, trimmed")
        self.assertEqual(lineage["modified"][face(bore)], "model.cut1")
        vertical = body.edges().filter_by(Axis.Z).sort_by(Axis.X)[0]
        rims = sorted(edge(shape) for shape in body.edges() if shape.geom_type.name == "CIRCLE")
        self.assertRegex(edge(vertical), r"^e:model\.make_box1\.\d+$", "a box edge the cut never touched")
        self.assertEqual(rims, ["e:model.cut1.1", "e:model.cut1.2"], "the hole's rims are new circles the cut made")
        self.assertEqual(len(set(lineage["faces"])), len(lineage["faces"]))
        self.assertTrue(all(name is not None for plural in ("faces", "edges", "vertices") for name in lineage[plural]))

    def test_each_piece_of_a_split_element_is_numbered(self) -> None:
        from build123d import Axis, Box

        from cadgen._internal.entity_ordinals import entity_map

        def model():
            return Box(30, 20, 10) - Box(4, 30, 20)

        body, _, run = _run(model)
        _key, lineage = run.identify(body)
        faces = entity_map(body.wrapped, "face")
        # The slot cuts the box's top in two: two pieces of one face of the box, numbered.
        top = sorted(lineage["faces"][faces.FindIndex(shape.wrapped) - 1]
                     for shape in body.faces() if abs(shape.center().Z - 5) < 1e-9)
        self.assertEqual(len(top), 2)
        stem = top[0].rsplit(".", 1)[0]
        self.assertRegex(stem, r"^f:model\.make_box1\.[1-6]$")
        self.assertEqual(top, [f"{stem}.1", f"{stem}.2"])
        self.assertEqual(lineage["modified"][top[0]], "model.cut1")
        ends = [lineage["faces"][faces.FindIndex(shape.wrapped) - 1] for shape in body.faces().filter_by(Axis.X)]
        self.assertTrue(all(name.count(".") == 2 for name in ends), "an end the slot does not reach is whole")

    def test_two_places_of_one_shape_are_two_elements_and_an_in_place_operation_is_followed(self) -> None:
        """An extrusion's caps are one TShape at two places: the bottom is the
        profile's face, passed through, and the top is a face the extrusion made.
        The profile reaches the extrusion through an in-place ``clean`` and a
        placement the builder makes outside the patched surface: the lineage
        follows both back to the face the profile made."""
        from build123d import Axis, BuildPart, BuildSketch, Plane, SlotOverall, extrude

        from cadgen._internal.entity_ordinals import entity_map

        def slot():
            with BuildPart() as part:
                with BuildSketch(Plane.XY):
                    SlotOverall(30, 10)
                extrude(amount=5)
            return part.part

        body, _, run = _run(slot)
        _key, lineage = run.identify(body)
        faces = entity_map(body.wrapped, "face")
        bottom, top = (lineage["faces"][faces.FindIndex(shape.wrapped) - 1]
                       for shape in body.faces().filter_by(Axis.Z).sort_by(Axis.Z))
        self.assertEqual((bottom, top), ("f:slot.face1.1", "f:slot.extrude1.5"))
        self.assertTrue(all(name is not None for plural in ("faces", "edges", "vertices") for name in lineage[plural]))
        self.assertEqual(len(set(lineage["edges"])), len(lineage["edges"]), "a top edge is not its bottom twin")

    def test_a_label_pins_the_name_of_what_the_author_labelled(self) -> None:
        """A shape the model labels and hands on names the feature that made it,
        through build123d's part wrapper and through a placement."""
        from build123d import Compound, Cylinder, Pos

        def model():
            peg = Pos(40, 0, 0) * Cylinder(3, 10)
            peg.label = "peg"
            return Compound(children=[peg], label="pair")

        compound, _, run = _run(model)
        [peg] = compound.children
        _key, lineage = run.identify(peg)
        self.assertEqual(sorted(lineage["faces"]), ["f:peg.1", "f:peg.2", "f:peg.3"])
        self.assertIn("peg", [feature.name for feature in run.trace.features])

    def test_a_shape_no_feature_made_has_no_lineage_even_placed(self) -> None:
        from build123d import Location, Solid

        foreign = Solid.make_box(5, 5, 5)

        def model():
            return foreign.moved(Location((1, 2, 3)))

        placed, _, run = _run(model)
        self.assertEqual([feature.op for feature in run.trace.features], ["moved"])
        self.assertEqual(run.identify(placed), (None, None))

    def test_a_child_model_promise_is_placed_not_read(self) -> None:
        """A body places a child's promise (``Pos * child()``, then
        ``Compound(children=...)``) without reading it, and so must the memo:
        the feature that placed it has no handle for it and the run registers
        nothing for it. Reading it would force the child inside the parent's
        body, which the reference-scene path must never see
        (test_reference_assemblies builds the real thing). The same goes for
        any shape whose geometry is a property that forces, like a reference
        assembly: the memo reads a shape's own state and nothing else."""
        from build123d import Compound

        from cadgen.store.lazy import LazyCompound

        promise = LazyCompound.__new__(LazyCompound)
        promise.__dict__.update({"_lazy_shape": None, "_lazy_forcing": False})
        Compound.__init__(promise, None, label="child")
        self.assertFalse(promise._forced)

        class Assembly(Compound):
            @property
            def _wrapped(self):
                raise AssertionError("the memo forced the assembly")

            @_wrapped.setter
            def _wrapped(self, shape):
                self.__dict__["_promised"] = shape

        assembly = Assembly(None, label="assembly")
        with kernel_memo.run(None) as run:
            for shape in (promise, assembly):
                given = run.trace.given([shape], lambda _shape: None)
                feature = run.trace.record("Compound", ("parent.py", 1, "parent"), given, [shape])
                self.assertEqual((feature.handles, feature.results), ([None], [None]))
                self.assertEqual(kernel_memo._register(shape, None, feature.index, run, None), [])
            self.assertEqual(run.transient, {})
        self.assertFalse(promise._forced, "the memo read the promise")

    def test_an_element_the_kernel_leaves_unbounded_is_signed_in_a_window(self) -> None:
        """A hyperbola's natural bounds are the kernel's infinity, the finite
        float 2e100, where its Cosh overflows: the signature samples a fixed
        window instead, the same for every trim of the curve or surface."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace
        from OCP.Geom import Geom_Hyperbola, Geom_SurfaceOfLinearExtrusion
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        def hyperbola(major: float):
            return Geom_Hyperbola(gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), major, 1.0)

        self.assertEqual(hyperbola(2.0).LastParameter(), 2e100)
        short = kernel_trace.edge_signature(BRepBuilderAPI_MakeEdge(hyperbola(2.0), -1.0, 1.0).Edge())
        long = kernel_trace.edge_signature(BRepBuilderAPI_MakeEdge(hyperbola(2.0), -2.0, 0.5).Edge())
        other = kernel_trace.edge_signature(BRepBuilderAPI_MakeEdge(hyperbola(3.0), -1.0, 1.0).Edge())
        self.assertEqual(short[0], long[0])
        self.assertNotEqual(short[1], long[1])
        self.assertNotEqual(short[0], other[0])

        surface = Geom_SurfaceOfLinearExtrusion(hyperbola(2.0), gp_Dir(0, 0, 1))
        self.assertEqual(surface.Bounds(), (-2e100, 2e100, -2e100, 2e100))
        low = kernel_trace.face_signature(BRepBuilderAPI_MakeFace(surface, -1.0, 1.0, 0.0, 2.0, 1e-6).Face())
        high = kernel_trace.face_signature(BRepBuilderAPI_MakeFace(surface, -0.5, 0.5, 5.0, 9.0, 1e-6).Face())
        self.assertEqual(low[0], high[0])
        self.assertNotEqual(low[1], high[1])

    def test_names_and_construction_keys_are_the_same_cold_and_hot(self) -> None:
        def model():
            box, tool, body = _bored_plate()
            body.label = "plate"
            return body

        cold, _, run1 = _run(model)
        # A build names its components before the worker runs anything else.
        cold_identity = run1.identify(cold)
        hot, stats, run2 = _run(model)
        self.assertGreater(stats["hits"], 0)
        self.assertEqual(run2.identify(hot), cold_identity)
        self.assertRegex(cold_identity[0], r"^[0-9a-f]{64}$")


class ConstructionKeyTest(KernelMemoCase):
    def test_a_construction_key_follows_the_construction(self) -> None:
        (_box, _tool, body), _, _run1 = _run(_bored_plate)
        key = kernel_memo.construction_key(body)
        kernel_memo.clear()
        (_box, _tool, again), _, _run2 = _run(_bored_plate)
        self.assertEqual(kernel_memo.construction_key(again), key, "the same construction, made again")
        (_box, _tool, wider), _, _run3 = _run(_bored_plate, 4.0)
        self.assertNotEqual(kernel_memo.construction_key(wider), key, "another parameter")
        from build123d import Solid

        self.assertIsNone(kernel_memo.construction_key(Solid.make_box(1, 1, 1)), "made outside a run")

    def test_a_construction_key_is_the_same_in_another_process(self) -> None:
        import subprocess
        import sys
        import tempfile

        script = (
            "from cadgen._internal import kernel_memo\n"
            "from build123d import Solid\n"
            "with kernel_memo.run(None):\n"
            "    body = Solid.make_box(20, 20, 8).cut(Solid.make_cylinder(3.0, 30))\n"
            "print(kernel_memo.construction_key(body))\n"
        )
        with tempfile.TemporaryDirectory() as folder:
            # The model's own code is a file of its own: a feature needs a first-party frame.
            path = Path(folder) / "model.py"
            path.write_text(script, encoding="utf-8")
            env = {**os.environ, "PYTHONPATH": str(Path(kernel_memo.__file__).resolve().parents[2])}
            there = subprocess.run([sys.executable, str(path)], env=env, capture_output=True, text=True, check=True)
        (_box, _tool, body), _, _run1 = _run(_bored_plate)
        self.assertRegex(there.stdout.strip(), r"^[0-9a-f]{64}$")
        self.assertEqual(there.stdout.strip(), kernel_memo.construction_key(body))


if __name__ == "__main__":
    unittest.main()
