"""The kernel memo (README law 18) and the trace behind provenance (STORE.md §3).

The contract: inside a run, every kernel construction the model's code calls is
a feature; a second run of the same code hands back the SAME live objects the
first run made, restored to the state the operation returned them in, and ends
with the bytes a cold run ends with -- the kernel's own changes to its inputs
replayed on a hit and undone when the operation is gone; two identical
constructions in one run are two objects; an input that changed, by geometry,
placement or attribute, misses, and so does everything downstream of a miss;
a shape no operation of this process made runs its call uncached; and with
the memo off every call still runs and is still a feature. Small geometry,
in process, no store.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal import kernel_memo  # noqa: E402
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


def _components(*shapes):
    """The components as the tree build prepares them: by cid, the live shape
    and the identity measured on it."""
    from cadgen._internal.component_package import _component_id, prepare_geometry_component

    components = {}
    for shape in shapes:
        prepared = prepare_geometry_component(shape)
        components[_component_id(prepared["entry"]["contentHash"])] = (shape, prepared["identity"])
    return components


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
        self.assertEqual([feature.id for feature in run1.trace.features], [feature.id for feature in run2.trace.features])
        self.assertEqual([feature.op for feature in run2.trace.features], ["Solid.make_box", "Solid.make_cylinder", "cut"])
        self.assertEqual(run2.trace.features[-1].inputs, [0, 0, 1])


class TraceTest(KernelMemoCase):
    def test_features_name_the_calling_line_and_their_inputs(self) -> None:
        from build123d import Solid

        def model():
            box = Solid.make_box(20, 20, 8)
            tools = [Solid.make_cylinder(1, 30).moved(Solid.make_box(1, 1, 1).location) for _ in range(2)]
            return box.cut(*tools)

        _, _, run = _run(model)
        ids = [feature.id for feature in run.trace.features]
        here = __file__
        box_line = ids[0]
        self.assertRegex(box_line, rf"^fe:Solid\.make_box@{here}:\d+#1$")
        self.assertTrue(all(feature.file == here for feature in run.trace.features))
        self.assertEqual(run.trace.features[0].function, "model")
        # A line that ran twice numbers its runs; the same operation elsewhere starts at 1.
        cylinders = [feature for feature in run.trace.features if feature.op == "Solid.make_cylinder"]
        self.assertEqual([feature.n for feature in cylinders], [1, 2])
        self.assertEqual(len({feature.line for feature in cylinders}), 1)
        cut = run.trace.features[-1]
        self.assertEqual(cut.op, "cut")
        moved = [feature.index for feature in run.trace.features if feature.op == "moved"]
        self.assertEqual(cut.inputs, [0, 0, *moved], "the cut consumed the box and the two placed tools")

    def test_lineage_is_by_geometry(self) -> None:
        """Unchanged: same surface, same extent, inherited. Modified: same
        surface, another extent. Made: anything else."""
        from build123d import Axis, Location, Solid
        from OCP.TopLoc import TopLoc_Location

        from cadgen._internal.entity_ordinals import entity_map
        from cadgen._internal.geometry_fingerprint import geometry_identity

        def model():
            box = Solid.make_box(20, 20, 8)
            tool = Solid.make_cylinder(3, 30).moved(Location((10, 10, -5)))
            return box.cut(tool)

        body, _, run = _run(model)
        features = {feature.index: feature for feature in run.trace.features}
        payload = run.provenance(_components(body))
        unlocated = body.wrapped.Located(TopLoc_Location())
        identity = geometry_identity(unlocated)
        faces, edges = entity_map(unlocated, "face"), entity_map(unlocated, "edge")
        [component] = payload["components"].values()
        self.assertIn(identity["fingerprint"], payload["components"])
        self.assertEqual(features[component["made"]].op, "cut")

        def face_lineage(face):
            return [None if index is None else features[index].op
                    for index in component["faces"][identity["faces"][faces.FindIndex(face.wrapped) - 1]]]

        def edge_lineage(edge):
            return [None if index is None else features[index].op
                    for index in component["edges"][identity["edges"][edges.FindIndex(edge.wrapped) - 1]]]

        bottom = body.faces().sort_by(Axis.Z)[0]
        side = body.faces().sort_by(Axis.X)[0]
        bore = [face for face in body.faces() if face.geom_type.name == "CYLINDER"][0]
        self.assertEqual(face_lineage(bottom), ["Solid.make_box", "cut"], "the hole changed the bottom face's extent")
        self.assertEqual(face_lineage(side), ["Solid.make_box", None], "the cut left the side face alone")
        self.assertEqual(face_lineage(bore), ["Solid.make_cylinder", "cut"], "the bore wall is the cylinder's face, trimmed")
        vertical = body.edges().filter_by(Axis.Z).sort_by(Axis.X)[0]
        rim = [edge for edge in body.edges() if edge.geom_type.name == "CIRCLE"][0]
        self.assertEqual(edge_lineage(vertical), ["Solid.make_box", None], "a box edge the cut never touched")
        self.assertEqual(edge_lineage(rim), ["cut", None], "the hole's rim is a new circle the cut made")

    def test_a_shape_no_feature_made_has_unknown_lineage_even_placed(self) -> None:
        from build123d import Location, Solid

        foreign = Solid.make_box(5, 5, 5)

        def model():
            return foreign.moved(Location((1, 2, 3)))

        placed, _, run = _run(model)
        self.assertEqual([feature.op for feature in run.trace.features], ["moved"])
        payload = run.provenance(_components(placed))
        [component] = payload["components"].values()
        self.assertIsNone(component["made"])
        self.assertEqual(len(component["faces"]), 6)
        self.assertTrue(all(lineage == [None, None] for lineage in component["faces"].values()))
        self.assertTrue(all(lineage == [None, None] for lineage in component["edges"].values()))

    def test_a_component_without_an_identity_names_nothing(self) -> None:
        body, _, run = _run(_bored_plate)
        components = _components(body[2])
        [cid] = components
        self.assertEqual(run.provenance({cid: (components[cid][0], None)})["components"], {})

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
                feature = run.trace.record("Compound", ("parent.py", 1, "parent"), [(shape, None)], [shape])
                self.assertEqual((feature.handles, feature.results), ([None], [None]))
                self.assertEqual(kernel_memo._register(shape, None, feature.index, run, None), [])
            self.assertEqual(run.transient, {})
        self.assertFalse(promise._forced, "the memo read the promise")

    def test_provenance_is_the_same_cold_and_hot(self) -> None:
        import json

        def model():
            box, tool, body = _bored_plate()
            body.label = "plate"
            return body, tool

        cold, _, run1 = _run(model)
        # A build computes its provenance before the worker runs anything else.
        cold_payload = json.dumps(run1.provenance(_components(*cold)), sort_keys=True)
        hot, _, run2 = _run(model)
        self.assertEqual(cold_payload, json.dumps(run2.provenance(_components(*hot)), sort_keys=True))
        self.assertIn('"made": 2', cold_payload)


if __name__ == "__main__":
    unittest.main()
