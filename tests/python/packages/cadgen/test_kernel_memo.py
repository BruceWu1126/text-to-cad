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

import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import add_repo_path
from tests.python.support.tmp_root import generated_cad_directory

add_repo_path("packages/cadgen/src")

from cadgen._internal import kernel_history, kernel_memo, kernel_trace  # noqa: E402
from cadgen._internal.component_package import _shape_brep_bytes as _bytes  # noqa: E402
from cadgen.cad_ref_syntax import Step, expanded_derivation  # noqa: E402

COUNTS = ("hits", "misses", "unkeyable", "unreplayable", "errors")


def _delta(before: dict, after: dict) -> dict:
    return {name: after[name] - before[name] for name in COUNTS}


def _run(function, *args):
    """``(result, stats delta, run)`` of calling ``function`` inside a run."""
    before = kernel_memo.stats()
    with kernel_memo.run(None) as run:
        result = function(*args)
    return result, _delta(before, kernel_memo.stats()), run


def _named(run, body):
    """The lineage of ``body`` as the run names it, and ``name(shape)``: the name it gives
    a face, edge or vertex of ``body``."""
    from cadgen._internal.entity_ordinals import entity_map

    _key, lineage = run.identify(body)
    maps = {kind: entity_map(body.wrapped, kind) for kind in ("face", "edge", "vertex")}
    plurals = {"face": "faces", "edge": "edges", "vertex": "vertices"}

    def name(shape):
        kind = type(shape).__name__.lower()
        return lineage[plurals[kind]][maps[kind].FindIndex(shape.wrapped) - 1]

    return lineage, name


def _spelled(lineage, name):
    """``(made atom, steps)`` of a name, every folded atom it starts from spelled out."""
    return expanded_derivation(name[2:], lineage["table"])


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

    def test_transient_only_uncached_calls_skip_state_capture_and_keep_geometry_and_lineage(self) -> None:
        from build123d import Location, Solid

        foreign = Solid.make_box(2, 3, 4)

        def model():
            return foreign.moved(Location((1, 2, 3))).translate((2, 0, 0))

        plain = _bytes(model().wrapped)
        identities, traces = [], []
        for enabled in ("1", "1", "0"):
            os.environ["CADGEN_KERNEL_MEMO"] = enabled
            with mock.patch.object(kernel_memo, "_state", wraps=kernel_memo._state) as state_reads:
                body, stats, run = _run(model)
            state_reads.assert_not_called()
            self.assertEqual((stats["hits"], stats["misses"], stats["unkeyable"], stats["errors"]), (0, 0, 2, 0))
            self.assertEqual(_bytes(body.wrapped), plain)
            made = kernel_memo._made_of(body.wrapped.TShape(), run)
            self.assertIsNotNone(made, "uncached results remain registered for lineage")
            self.assertFalse(made.persistent())
            self.assertEqual(run.feature_of(body), 1)
            identities.append(run.identify(body))
            traces.append(run.trace.feature_rows())
            self.assertEqual([feature.op for feature in run.trace.features], ["moved", "translate"])
            self.assertEqual(run.trace.features[1].inputs, [0])
            self.assertTrue(all(feature.history is not None for feature in run.trace.features))
        self.assertEqual(identities, [(None, None)] * 3,
                         "placements of foreign geometry have no construction key or element lineage")
        self.assertEqual(traces, [traces[0]] * 3)


class CopyIdentityTest(KernelMemoCase):
    """Body identity follows a proven copy, independently of the copy call's memo key."""

    def test_nested_assembly_copies_preserve_each_leafs_deferred_identity(self) -> None:
        import copy
        from build123d import Compound, Location, Solid

        foreign = Solid.make_box(1, 2, 3)

        def primitive():
            return Solid.make_box(4, 5, 6)

        def model(operation, with_foreign, noise):
            if noise:
                Solid.make_sphere(0.5)
            first, second = primitive(), primitive().moved(Location((10, 0, 0)))
            first.label, second.label = "first", "second"
            nested = Compound(children=[second, foreign] if with_foreign else [second])
            assembly = Compound(children=[first, nested])
            if operation == "moved":
                placed = assembly.moved(Location((0.01, 0, 0)))
            elif operation == "rigid":
                placed = assembly.moved(Location((3, 4, 5), (10, 20, 30)))
            elif operation == "located":
                placed = assembly.located(Location((3, 4, 5), (10, 20, 30)))
            else:
                placed = (copy.copy if operation == "copy" else copy.deepcopy)(assembly)
            return assembly, placed, (first, second), (placed.children[0], placed.children[1].children[0])

        for with_foreign in (False, True):
            for operation in ("moved", "rigid", "located", "copy", "deepcopy"):
                kernel_memo.clear()
                plain = _bytes(model(operation, with_foreign, False)[1].wrapped)
                expected = None
                first_copy = None
                for memo, noise in (("1", False), ("1", True), ("0", False)):
                    with self.subTest(foreign=with_foreign, operation=operation, memo=memo, noise=noise):
                        os.environ["CADGEN_KERNEL_MEMO"] = memo
                        (assembly, placed, sources, copies), stats, run = _run(model, operation, with_foreign, noise)
                        self.assertEqual(stats["errors"], 0)
                        self.assertEqual(_bytes(placed.wrapped), plain, "identity bookkeeping preserves ordinary geometry")
                        self.assertEqual([body.label for body in copies], ["first", "second"])
                        # Nothing asked for names before the whole assembly was copied.
                        identities = [run.identify(body) for body in copies]
                        original = [run.identify(body) for body in sources]
                        self.assertEqual(identities, original)
                        self.assertEqual(identities[0], identities[1], "independent identical parts stay one part")
                        self.assertIsNotNone(identities[0][0])
                        expected = identities if expected is None else expected
                        self.assertEqual(identities, expected)
                        for source, body in zip(sources, copies):
                            self.assertIsNot(source, body)
                            self.assertFalse(source.is_same(body), "ordinary build123d descendants are native copies")
                            self.assertEqual(source.volume, body.volume)
                            _key, _lineage, names = run.account(body)
                            self.assertEqual(run.trace.features[names["primitive.make_box1"]].op, "Solid.make_box")
                        if operation in ("moved", "rigid", "copy"):
                            self.assertTrue(assembly.is_same(placed) if operation == "copy"
                                            else assembly.wrapped.IsPartner(placed.wrapped))
                        if with_foreign:
                            foreign_copy = placed.children[1].children[1]
                            self.assertEqual(run.identify(foreign_copy), (None, None))
                            made = kernel_memo._made_of(copies[0].wrapped.TShape(), run)
                            self.assertFalse(made.persistent(), "identity inheritance must not promote an uncached result")
                        if first_copy is not None and memo == "1" and not with_foreign:
                            self.assertIs(copies[0], first_copy, "a hit still returns its original Python object")
                        if first_copy is None:
                            first_copy = copies[0]

    def test_copy_identity_is_frozen_before_later_source_and_copy_effects(self) -> None:
        import copy
        from build123d import Compound, Face, Location, Solid, Vector, Wire

        foreign = Solid.make_box(1, 1, 1)

        def model(with_foreign):
            source = Face(Wire.make_circle(4))
            assembly = Compound(children=[source, foreign] if with_foreign else [source])
            first = assembly.moved(Location((5, 0, 0))).children[0]
            second = copy.deepcopy(first)
            state = kernel_memo._LOCAL.run
            pristine = state.identify(second)
            Solid.extrude(source, Vector(0, 0, 2))
            after_source = state.identify(first), state.identify(second)
            Solid.extrude(first, Vector(0, 0, 3))
            return pristine, after_source, state.identify(first), state.identify(second)

        for with_foreign in (False, True):
            kernel_memo.clear()
            expected = None
            for memo in ("1", "1", "0"):
                with self.subTest(foreign=with_foreign, memo=memo):
                    os.environ["CADGEN_KERNEL_MEMO"] = memo
                    (pristine, after_source, changed, independent), stats, _run_state = _run(model, with_foreign)
                    self.assertEqual(stats["errors"], 0)
                    self.assertIsNotNone(pristine[0])
                    self.assertEqual(after_source, (pristine, pristine))
                    self.assertEqual(independent, pristine, "mutating a copy cannot rename an earlier independent copy")
                    self.assertNotEqual(changed[0], pristine[0], "a changed body has no pristine copy identity")
                    expected = pristine if expected is None else expected
                    self.assertEqual(pristine, expected)

    def test_labels_on_distinct_copied_outputs_pin_only_the_geometry_that_consumes_them(self) -> None:
        from build123d import Compound, Face, Location, Solid, Vector, Wire

        def profile():
            return Face(Wire.make_rect(4, 5))

        def model(copied, shared):
            first = profile()
            second = (first if shared else profile()).moved(Location((10, 0, 0)))
            leaves = (first, second)
            if copied:
                leaves = Compound(children=leaves).moved(Location((1, 0, 0))).children
            state = kernel_memo._LOCAL.run
            before = [state.identify(body)[1] for body in leaves]
            leaves[0].label, leaves[1].label = "left_profile", "right_profile"
            solids = [Solid.extrude(body, Vector(0, 0, 3)) for body in leaves]
            named = [state.account(body) for body in solids]
            joined = solids[0].fuse(solids[1])
            return before, [state.identify(body)[1] for body in leaves], named, state.account(joined)

        (before, after, direct, _joined), _, _ = _run(model, False, False)
        self.assertEqual(before, after)
        for shared in (False, True):
            kernel_memo.clear()
            for memo in ("1", "1", "0"):
                with self.subTest(shared=shared, memo=memo):
                    os.environ["CADGEN_KERNEL_MEMO"] = memo
                    (before, after, named, joined), stats, run = _run(model, True, shared)
                    self.assertEqual(stats["errors"], 0)
                    self.assertEqual(before, after, "consuming labels cannot rename an existing copied leaf")
                    self.assertEqual([account[1] for account in named], [account[1] for account in direct])
                    for label, account in zip(("left_profile", "right_profile"), named):
                        self.assertEqual(run.trace.features[account[2][label]].op, "moved")
                    self.assertIn("left_profile", joined[2])
                    self.assertIn("right_profile", joined[2])
                    self.assertEqual(joined[2]["left_profile"], joined[2]["right_profile"],
                                     "both labels truthfully name distinct outputs of one assembly-copy call")

    def test_real_edits_name_their_changes_and_keep_unchanged_copy_names(self) -> None:
        from build123d import Compound, Location, Solid

        def primitive():
            return Solid.make_box(4, 4, 4)

        def model():
            first, second = primitive(), primitive().moved(Location((10, 0, 0)))
            assembly = Compound(children=[first, Compound(children=[second])])
            copied = assembly.moved(Location((1, 0, 0))).children[1].children[0]
            tool = Solid.make_cylinder(1, 6).moved(Location((12, 2, -1)))
            return second, copied, copied.cut(tool), copied.scale(2)

        for memo in ("1", "1", "0"):
            os.environ["CADGEN_KERNEL_MEMO"] = memo
            (source, copied, cut, scaled), stats, run = _run(model)
            self.assertEqual(stats["errors"], 0)
            original = run.identify(source)
            self.assertEqual(run.identify(copied), original)
            self.assertNotEqual(run.identify(cut)[0], original[0])
            self.assertNotEqual(run.identify(scaled)[0], original[0])
            before, name_before = _named(run, copied)
            after, name_after = _named(run, cut)
            unchanged = [face for face in cut.faces() if any(face.is_same(old) for old in copied.faces())]
            self.assertTrue(unchanged)
            self.assertTrue(all(name_after(face) == name_before(face) for face in unchanged))
            self.assertTrue(any(";M:" in face for face in after["faces"]))
            self.assertNotEqual(after["made"], before["made"])
            self.assertFalse(run.trace.features[-1].history.copies, "scaling is not a whole-body Copy certificate")

    def test_nested_copies_preserve_the_sources_earlier_label_claimants(self) -> None:
        import copy
        from build123d import Compound, Face, Location, Solid, Vector, Wire

        def model():
            profile = Face(Wire.make_rect(4, 5))
            inner = Compound(children=[profile]).moved(Location((1, 0, 0))).children[0]
            inner.label = "A"
            source = Solid.extrude(inner, Vector(0, 0, 3))
            outer = copy.deepcopy(source)
            state = kernel_memo._LOCAL.run
            before = state.identify(outer)
            other = Solid.make_box(2, 3, 4).moved(Location((10, 0, 0)))
            other.label = "A"
            combined = outer.fuse(other)
            return before, state.identify(outer), state.account(combined)

        for memo in ("1", "1", "0"):
            os.environ["CADGEN_KERNEL_MEMO"] = memo
            (before, after, combined), stats, run = _run(model)
            self.assertEqual(stats["errors"], 0)
            self.assertEqual(before, after)
            self.assertEqual(run.trace.features[combined[2]["A"]].op, "moved",
                             "the nested copied profile claimed A before the later box")
            self.assertIn("model.make_box1", combined[2])

    def test_source_changes_within_the_copy_call_withdraw_both_inherited_identities(self) -> None:
        import copy
        from build123d import Solid

        def changing_copy(source):
            result = copy.deepcopy(source)
            # A real scalar change with no history maker reporting it.
            source.vertices()[0].wrapped.TShape().Tolerance(0.02)
            return result

        operation = kernel_memo._wrap("changing_copy", changing_copy, "function")

        def model():
            return operation(Solid.make_box(2, 3, 4))

        for memo in ("1", "1", "0"):
            os.environ["CADGEN_KERNEL_MEMO"] = memo
            body, stats, run = _run(model)
            self.assertEqual(stats["errors"], 0)
            key, lineage = run.identify(body)
            self.assertIsNone(key)
            self.assertEqual(lineage["made"], "model.changing_copy1")
            self.assertFalse(run.trace.features[-1].copies)
            self.assertFalse(run.trace.features[-1].history.copies)


class EffectTest(KernelMemoCase):
    """The kernel's changes to its inputs: an extrusion clears its profile's ``Free``
    flag and appends representations to the profile's edges (a boolean, run
    non-destructive, changes none: ``BooleanTest``)."""

    def test_effects_on_inputs_are_replayed_on_a_hit_and_undone_when_the_operation_is_gone(self) -> None:
        from build123d import Face, Solid, Vector, Wire

        pristine = _bytes(Face(Wire.make_circle(4)).wrapped)

        def with_boss():
            profile = Face(Wire.make_circle(4))
            return profile, Solid.extrude(profile, Vector(0, 0, 4))

        def without_boss():
            return Face(Wire.make_circle(4))

        (profile, boss), cold, _ = _run(with_boss)
        mutated = _bytes(profile.wrapped)
        self.assertNotEqual(mutated, pristine, "the extrusion changed its profile")
        (profile2, boss2), hot, _ = _run(with_boss)
        self.assertIs(boss2, boss)
        self.assertTrue(profile2.wrapped.IsPartner(profile.wrapped), "constructed anew, around the first run's TShape")
        self.assertEqual(hot["misses"], 0)
        self.assertEqual(_bytes(profile2.wrapped), mutated, "a hit leaves the profile as the extrusion left it")
        self.assertEqual(_bytes(boss2.wrapped), _bytes(boss.wrapped))
        profile3, edited, _ = _run(without_boss)
        self.assertTrue(profile3.wrapped.IsPartner(profile.wrapped))
        self.assertEqual(edited["hits"], 2)
        self.assertEqual(_bytes(profile3.wrapped), pristine, "with the extrusion edited out, the profile is as it was made")
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

    def test_uncached_mutation_taints_persistent_roots_in_foreign_and_transient_wrappers(self) -> None:
        from build123d import Compound, Location, Solid
        from cadgen.store.materialize import TREE_TAG

        def primitive():
            return Solid.make_box(2, 3, 4)

        # A controlled native mutation through the real uncached call path: a
        # wrapper's changed sub-shape must retire that sub-shape's memoized maker.
        def mutate(wrapper):
            tshape = body.wrapped.TShape()
            tshape.Free(not tshape.Free())
            return wrapper

        uncached_mutation = kernel_memo._wrap("mutate", mutate, "function")
        for transient in (False, True):
            for tag in (TREE_TAG, "__cadgen_component_identity__"):
                with self.subTest(transient=transient, tag=tag):
                    kernel_memo.clear()
                    body, _, made_run = _run(primitive)
                    memoized = kernel_memo._made_of(body.wrapped.TShape(), made_run)
                    self.assertTrue(memoized.persistent())
                    # Built outside a run: the container has no maker, but the
                    # native body it holds is still a persistent memo result.
                    foreign = Compound(children=[body])
                    foreign.__dict__[tag] = "artifact metadata"

                    def model():
                        wrapper = foreign.moved(Location((5, 0, 0))) if transient else foreign
                        state = kernel_memo._LOCAL.run
                        if transient:
                            made = kernel_memo._made_of(wrapper.wrapped.TShape(), state)
                            self.assertIsNotNone(made, "the first uncached call registered the wrapper")
                            self.assertFalse(made.persistent())
                        self.assertEqual(kernel_memo._embedded(wrapper.wrapped, state), [memoized],
                                         "only the persistent body needs protection")
                        return uncached_mutation(wrapper)

                    _, stats, _ = _run(model)
                    self.assertEqual(stats["errors"], 0)
                    fresh, rebuilt, _ = _run(primitive)
                    self.assertEqual((rebuilt["hits"], rebuilt["misses"]), (0, 1))
                    self.assertIsNot(fresh, body, "the uncached mutation retired the embedded body's maker")

    def test_nesting_a_transient_wrapper_preserves_its_embedded_roots_construction_and_lineage(self) -> None:
        from build123d import Compound, Solid

        foreign = Solid.make_box(1, 1, 1)

        def inner():
            body = Solid.make_box(2, 3, 4)
            return body, Compound(children=[body, foreign])

        plain_body, plain_inner = inner()
        plain = _bytes(Compound(children=[plain_inner]).wrapped)
        plain_body_bytes = _bytes(plain_body.wrapped)

        def model(deferred):
            body, wrapper = inner()
            state = kernel_memo._LOCAL.run
            identity = None if deferred else state.identify(body)
            members = set(kernel_memo._mapped(body.wrapped)[1])
            self.assertTrue(wrapper.wrapped.Free())
            with mock.patch.object(kernel_memo, "_state", wraps=kernel_memo._state) as reads:
                outer = Compound(children=[wrapper])
            self.assertFalse(wrapper.wrapped.Free(), "the containing compound changes only its wrapper")
            self.assertEqual({call.args[0] for call in reads.call_args_list}, members,
                             "capture the embedded root, without scalar reads of the wrapper or foreign geometry")
            return body, outer, identity, state.identify(body)

        expected, traces = None, []
        for deferred in (False, True):
            kernel_memo.clear()
            first = None
            for enabled in ("1", "1", "0"):
                with self.subTest(deferred=deferred, enabled=enabled):
                    os.environ["CADGEN_KERNEL_MEMO"] = enabled
                    (body, outer, before, after), stats, run = _run(model, deferred)
                    self.assertEqual(stats["errors"], 0)
                    if before is not None:
                        expected = before if expected is None else expected
                        self.assertEqual(before, expected)
                    # Publication first asks for lineage after nesting, with no
                    # earlier account to warm this run's trace caches.
                    self.assertRegex(after[0], r"^[0-9a-f]{64}$")
                    self.assertIsNotNone(after[1])
                    self.assertEqual(after, expected)
                    self.assertEqual(_bytes(body.wrapped), plain_body_bytes)
                    self.assertEqual(_bytes(outer.wrapped), plain)
                    if first is None:
                        first = body
                    elif enabled == "1":
                        self.assertIs(body, first, "the embedded maker remains a memo hit")
                        self.assertEqual(stats["misses"], 0)
                    traces.append(run.trace.feature_rows())
        self.assertEqual(traces, [traces[0]] * 6)

    def test_an_uncached_change_taints_only_the_affected_embedded_root(self) -> None:
        from build123d import Compound, Solid

        foreign = Solid.make_box(1, 1, 1)

        def primitives():
            return Solid.make_box(2, 3, 4), Solid.make_box(5, 6, 7)

        def mutate(wrapper, overlapping, direct):
            direct.wrapped.Free(not direct.wrapped.Free())
            return wrapper

        uncached_mutation = kernel_memo._wrap("mutate", mutate, "function")

        def model():
            affected, untouched = primitives()
            wrapper = Compound(children=[affected, untouched, foreign])
            overlapping = Compound(children=[affected, foreign])
            state = kernel_memo._LOCAL.run
            before = state.identify(untouched)
            uncached_mutation(wrapper, overlapping, affected)
            self.assertIsNone(kernel_memo.construction_key(affected))
            self.assertEqual(state.identify(untouched), before)
            return affected, untouched

        (affected, untouched), stats, _ = _run(model)
        self.assertEqual(stats["errors"], 0)
        (fresh, same), rebuilt, _ = _run(primitives)
        self.assertEqual((rebuilt["hits"], rebuilt["misses"]), (1, 1))
        self.assertIsNot(fresh, affected)
        self.assertIs(same, untouched, "overlapping wrappers do not retire the unchanged maker")

    def test_embedded_root_topology_replacement_taints_its_maker_with_equal_scalar_rows(self) -> None:
        from build123d import Compound, Solid
        from OCP.BRep import BRep_Builder

        foreign = Solid.make_box(1, 1, 1)
        replacement = Solid.make_box(2, 3, 4)
        builder = BRep_Builder()

        def mutate(wrapper, root, body):
            before = [kernel_memo._state(shape) for shape in kernel_memo._mapped(root.wrapped)[1]]
            free = root.wrapped.Free()
            root.wrapped.Free(True)  # OCCT permits structural edits only to a free shape.
            try:
                builder.Remove(root.wrapped, body.wrapped)
                builder.Add(root.wrapped, replacement.wrapped)
            finally:
                root.wrapped.Free(free)
            after = [kernel_memo._state(shape) for shape in kernel_memo._mapped(root.wrapped)[1]]
            self.assertEqual(after, before, "only native topology identity changes")
            return Compound(wrapper.wrapped)

        uncached_mutation = kernel_memo._wrap("mutate", mutate, "function")

        def model():
            body = Solid.make_box(2, 3, 4)
            root = Compound(children=[body])
            wrapper = Compound(children=[root, foreign])
            state = kernel_memo._LOCAL.run
            before = state.identify(body)
            self.assertIsNotNone(kernel_memo.construction_key(root))
            changed = uncached_mutation(wrapper, root, body)
            self.assertIsNone(kernel_memo.construction_key(root))
            self.assertEqual(state.identify(body), before, "the removed body's own topology and state did not change")
            return changed, Compound(changed.wrapped)

        (changed, alias), stats, run = _run(model)
        self.assertEqual(stats["errors"], 0)
        self.assertTrue(changed.wrapped.IsSame(alias.wrapped))
        self.assertIsNone(run.trace.feature_of(alias))
        self.assertEqual(run.feature_of(alias), len(run.trace.features) - 1,
                         "the transient wrapper's changed topology is registered to the mutation")

    def test_uncached_topology_change_registers_transient_aliases_to_the_mutating_feature(self) -> None:
        from build123d import Compound, Location, Solid
        from OCP.BRep import BRep_Builder

        foreign = Compound(children=[Solid.make_box(2, 3, 4)])
        addition = Solid.make_box(1, 1, 1).moved(Location((10, 0, 0)))
        builder = BRep_Builder()

        def mutate(wrapper):
            builder.Add(wrapper.wrapped, addition.wrapped)
            return Compound(wrapper.wrapped)  # a new object around the changed TShape

        uncached_mutation = kernel_memo._wrap("mutate", mutate, "function")

        def model():
            transient = foreign.moved(Location((0, 5, 0)))
            changed = uncached_mutation(transient)
            return changed, Compound(changed.wrapped)  # no object-level trace to hide a stale maker

        with mock.patch.object(kernel_memo, "_state", wraps=kernel_memo._state) as state_reads:
            (changed, alias), stats, run = _run(model)
        state_reads.assert_not_called()
        self.assertEqual((stats["unkeyable"], stats["unreplayable"], stats["errors"]), (2, 1, 0))
        self.assertEqual(len(alias.solids()), 2)
        self.assertIsNot(changed, alias)
        self.assertTrue(changed.wrapped.IsSame(alias.wrapped))
        self.assertIsNone(run.trace.feature_of(alias), "the fresh alias must use the TShape's maker")
        self.assertEqual(run.feature_of(alias), 1)
        self.assertEqual(run.made_by(alias.wrapped.TShape()), 1)
        # Raw BRep_Builder.Add reports no supported maker history, so retagging
        # the TShape must not invent a name for the native mutation's result.
        self.assertEqual(run.identify(alias), (None, None))


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
    def test_the_kill_switch_turns_hits_off_and_nothing_else(self) -> None:
        """``CADGEN_KERNEL_MEMO=0`` computes every call again: the trace, the keys
        and the names are the ones the memo gives, so identity is the same either way."""
        (_box, _tool, body), _, run = _run(_bored_plate)
        # A build names its components before the worker runs anything else.
        identity = run.identify(body)
        self.assertRegex(identity[0], r"^[0-9a-f]{64}$")
        kernel_memo.clear()
        os.environ["CADGEN_KERNEL_MEMO"] = "0"
        (_box, _tool, body), first, run1 = _run(_bored_plate)
        self.assertEqual(run1.identify(body), identity)
        (_box2, _tool2, body2), second, run2 = _run(_bored_plate)
        self.assertEqual(run2.identify(body2), identity)
        self.assertEqual((first["hits"], second["hits"], second["misses"]), (0, 0, 3))
        self.assertIsNot(body2, body)
        self.assertEqual(run1.trace.feature_rows(), run2.trace.feature_rows())
        self.assertEqual([feature.op for feature in run2.trace.features], ["Solid.make_box", "Solid.make_cylinder", "cut"])
        self.assertEqual(run2.trace.features[-1].inputs, [0, 0, 1])


class SettingsTest(KernelMemoCase):
    """An operation is every setting it runs with: what build123d sets on the OCC
    operation object before the call, the class-wide ``SkipClean``, the font file a
    text reads. A hit that ignored one would hand back another operation's geometry."""

    def test_a_boolean_s_fuzzy_value_and_glue_are_in_its_key(self) -> None:
        from build123d import Box, Pos

        def fused(tol, gap=10.3, glue=False):
            a = Box(10, 10, 10)
            b = Pos(gap, 0, 0) * Box(10, 10, 10)
            return a.fuse(b, tol=tol, glue=glue) if tol or glue else a.fuse(b)

        apart, _, _ = _run(fused, 0)
        merged, stats, _ = _run(fused, 0.5)
        self.assertEqual((len(apart.solids()), len(merged.solids())), (2, 1), "the fuzzy fuse closes the gap")
        self.assertEqual(stats["misses"], 1, "the boxes hit; the fuse is another operation")
        self.assertNotEqual(kernel_memo.construction_key(merged), kernel_memo.construction_key(apart))
        touching, _, _ = _run(fused, 0, 10.0)
        glued, stats, _ = _run(fused, 0, 10.0, True)
        self.assertEqual(stats["misses"], 1)
        self.assertNotEqual(kernel_memo.construction_key(glued), kernel_memo.construction_key(touching))

    def test_skip_clean_is_in_the_key(self) -> None:
        from build123d import Box, Pos, SkipClean

        def joined():
            return Box(10, 10, 10).fuse(Pos(10, 0, 0) * Box(10, 10, 10))

        cleaned, _, _ = _run(joined)
        with SkipClean():
            raw, _, _ = _run(joined)
        self.assertEqual((len(cleaned.faces()), len(raw.faces())), (6, 10), "the fuse ran again, uncleaned")
        self.assertNotEqual(kernel_memo.construction_key(raw), kernel_memo.construction_key(cleaned))

    def test_a_text_is_keyed_by_its_font_file_s_bytes_not_its_path(self) -> None:
        import shutil
        import tempfile

        import build123d
        from build123d import Compound
        from build123d.text import FontManager

        bundled = Path(build123d.__file__).parent / "data" / "fonts" / "reliefsingleline" / "ReliefSingleLineCAD-Regular.ttf"
        # The font manager is the kernel's, process-wide: give the bundled face its file back.
        self.addCleanup(lambda: FontManager().register_font(str(bundled), True, True))
        # The kernel may still hold a font it read open: on Windows that file outlives the test.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as folder:
            first, other, same = (Path(folder) / name for name in ("first.ttf", "other.ttf", "same.ttf"))
            shutil.copyfile(bundled, first)
            other.write_bytes(bundled.read_bytes() + bytes(4))  # the same glyphs, other bytes
            shutil.copyfile(bundled, same)

            def text(path):
                return Compound.make_text("cad", 5, font_path=str(path))

            key = kernel_memo.construction_key(_run(text, first)[0])
            self.assertRegex(key, r"^[0-9a-f]{64}$")
            changed, stats, _ = _run(text, other)
            self.assertEqual(stats["hits"], 0, "a font file of other bytes is another text")
            self.assertNotEqual(kernel_memo.construction_key(changed), key)
            kernel_memo.clear()
            self.assertEqual(kernel_memo.construction_key(_run(text, same)[0]), key,
                             "the same bytes at another path are the same font")


class TraceTest(KernelMemoCase):
    def test_features_are_named_by_function_operation_and_count(self) -> None:
        """A feature is named among the features its part descends from, in call order:
        a function's calls of one operation count up, wherever they are; a feature the
        part does not descend from counts for nothing; a line is no part of a name."""
        from build123d import Location, Solid
        from OCP.TopLoc import TopLoc_Location

        def model():
            box = Solid.make_box(20, 20, 8)
            Solid.make_box(1, 1, 1)  # a feature no element of the part descends from
            tools = [Solid.make_cylinder(1, 30).moved(Location((5 + 10 * index, 10, -5))) for index in range(2)]
            return box.cut(*tools)

        body, _, run = _run(model)
        features = run.trace.features
        here = Path(__file__).as_posix()  # the trace names a file the same on every OS
        self.assertTrue(all(feature.file == here for feature in features))
        self.assertEqual(features[0].function, "model")
        lineage, names = run.trace.account(body.wrapped.Located(TopLoc_Location()), run.made_by)
        by_index = {index: name for name, index in names.items()}
        self.assertEqual(by_index[0], "model.make_box1")
        self.assertEqual([feature.op for feature in features].count("Solid.make_box"), 2)
        self.assertNotIn("model.make_box2", names, "the run's other box is no feature of the part")
        cylinders = [feature.index for feature in features if feature.op == "Solid.make_cylinder"]
        self.assertEqual([by_index[index] for index in cylinders], ["model.make_cylinder1", "model.make_cylinder2"])
        self.assertEqual(len({features[index].line for index in cylinders}), 1)
        cut = features[-1]
        self.assertEqual((cut.op, lineage["made"], names["model.cut1"]), ("cut", "model.cut1", cut.index))
        moved = [feature.index for feature in features if feature.op == "moved"]
        self.assertEqual(cut.inputs, [0, 0, *moved], "the cut consumed the box and the two placed tools")
        self.assertEqual([by_index[index] for index in moved], ["model.moved1", "model.moved2"])
        row = run.trace.feature_rows()[-1]
        self.assertEqual((row["op"], row["function"], row["inputs"]), ("cut", "model", [0, 0, *moved]))

    def test_an_element_an_operation_leaves_alone_keeps_its_name_and_one_it_changes_says_how(self) -> None:
        """Left alone: the input's element, its name. Changed: the name of what it was and the
        step the operation took -- modified (``M``), or generated from elements of the inputs
        (``G``), named from the first source with the others after it."""
        from build123d import Axis, Location, Solid

        def model():
            box = Solid.make_box(20, 20, 8)
            tool = Solid.make_cylinder(3, 30).moved(Location((10, 10, -5)))
            return box.cut(tool)

        body, _, run = _run(model)
        lineage, name = _named(run, body)
        self.assertEqual(lineage["made"], "model.cut1")
        bottom = body.faces().sort_by(Axis.Z)[0]
        side = body.faces().sort_by(Axis.X)[0]
        bore = [shape for shape in body.faces() if shape.geom_type.name == "CYLINDER"][0]
        self.assertRegex(name(side), r"^f:model\.make_box1\.[1-6]$", "the cut left the side face alone")
        self.assertRegex(name(bottom), r"^f:model\.make_box1\.[1-6];M:model\.cut1$", "the hole changed the bottom")
        self.assertEqual(name(bore), "f:model.make_cylinder1.1;M:model.cut1", "the bore wall: the cylinder's face, trimmed")
        rims = [shape for shape in body.edges() if shape.geom_type.name == "CIRCLE"]
        self.assertEqual(len(rims), 2)
        for rim in rims:
            atom, steps = _spelled(lineage, name(rim))
            self.assertRegex(atom, r"^model\.make_box1\.[1-6]$", "a rim is generated from the box face it is cut in")
            self.assertEqual([(step.op, step.kind, step.feature, step.extras) for step in steps],
                             [("G", "f", "model.cut1", (("f", "model.make_cylinder1.1"),))])
        self.assertEqual(len({name(rim) for rim in rims}), 2)
        self.assertTrue(all(name is not None for plural in ("faces", "edges", "vertices") for name in lineage[plural]))

    def test_a_tool_face_coincident_with_another_tool_s_names_its_own_tool(self) -> None:
        """Two pockets whose floors lie on one plane: each floor is its own tool's face,
        modified -- the kernel says which, where a match by surface would take the first."""
        from build123d import Location, Solid

        def model():
            body = Solid.make_box(30, 10, 10)
            first = Solid.make_box(4, 10, 15).moved(Location((2, 0, 5)))
            second = Solid.make_box(4, 10, 15).moved(Location((20, 0, 5)))
            return body.cut(first, second)

        body, _, run = _run(model)
        lineage, name = _named(run, body)
        floors = [face for face in body.faces() if abs(face.center().Z - 5) < 1e-6 and face.area < 60]
        self.assertEqual(sorted(round(face.center().X) for face in floors), [4, 22])
        for floor in floors:
            tool = "model.make_box2" if floor.center().X < 15 else "model.make_box3"
            self.assertRegex(name(floor), rf"^f:{re.escape(tool)}\.\d+;M:model\.cut1$")

    def test_the_pieces_of_a_face_one_cut_splits_are_one_derivation_told_apart(self) -> None:
        """The slot cuts the top in two: both pieces are the top face modified by the cut,
        the second the cut reported numbered in the step (``;M2:``), where a later operation
        leaves the number alone; an end the slot never reached keeps the box's name."""
        from build123d import Axis, Box

        def model():
            return Box(30, 20, 10) - Box(4, 30, 20)

        body, _, run = _run(model)
        lineage, name = _named(run, body)
        top = sorted(name(face) for face in body.faces() if abs(face.center().Z - 5) < 1e-9)
        self.assertEqual(len(top), 2)
        first = next(name for name in top if ";M:" in name)
        self.assertRegex(first, r"^f:model\.make_box1\.[1-6];M:model\.cut1$")
        self.assertEqual(sorted(top), sorted([first, first.replace(";M:", ";M2:")]))
        ends = [face for face in body.faces().filter_by(Axis.X) if abs(abs(face.center().X) - 15) < 1e-9]
        self.assertEqual(len(ends), 2)
        self.assertTrue(all(re.fullmatch(r"f:model\.make_box1\.[1-6]", name(face)) for face in ends),
                        "an end the slot does not reach is whole")

    def test_an_extrusion_names_its_sides_top_and_bottom_from_the_profile(self) -> None:
        """The profile reaches the extrusion through the builder's in-place ``clean`` and a
        placement outside the patched surface: the bottom is the profile's face as the
        clean left it, the top the same face generated by the extrusion (its last shape),
        each side generated from a profile edge."""
        from build123d import Axis, BuildPart, BuildSketch, Plane, SlotOverall, extrude

        def slot():
            with BuildPart() as part:
                with BuildSketch(Plane.XY):
                    SlotOverall(30, 10)
                extrude(amount=5)
            return part.part

        body, _, run = _run(slot)
        lineage, name = _named(run, body)
        bottom, top = body.faces().filter_by(Axis.Z).sort_by(Axis.Z)
        made, steps = _spelled(lineage, name(bottom))
        self.assertEqual(_spelled(lineage, name(top)), (made, (*steps, Step("G", "f", 0, "slot.extrude1"))))
        self.assertEqual(steps[-1].op, "M", "the profile face as the builder's clean left it")
        for side in body.faces():
            if abs(side.center().Z - 2.5) < 1e-6:
                # The slot's two straight sides are generated from one edge, so one is ;D2.
                last = [step for step in _spelled(lineage, name(side))[1] if step.op != "D"][-1]
                self.assertEqual((last.op, last.kind, last.feature), ("G", "e", "slot.extrude1"))
        self.assertTrue(all(name is not None for plural in ("faces", "edges", "vertices") for name in lineage[plural]))
        self.assertEqual(len(set(lineage["edges"])), len(lineage["edges"]), "a top edge is not its bottom twin")

    def test_a_merged_face_keeps_every_face_it_was_merged_from(self) -> None:
        """Two boxes fused side by side: the clean merges their tops into one face, modified
        from the first box's top with the second's after it."""
        from build123d import Axis, Pos, Solid

        def model():
            return Solid.make_box(10, 10, 10).fuse(Pos(10, 0, 0) * Solid.make_box(10, 10, 10))

        body, _, run = _run(model)
        lineage, name = _named(run, body)
        top = body.faces().sort_by(Axis.Z)[-1]
        self.assertAlmostEqual(top.area, 200.0)
        atom, steps = _spelled(lineage, name(top))
        self.assertRegex(atom, r"^model\.make_box1\.[1-6]$")
        self.assertEqual([(step.op, step.feature) for step in steps], [("M", "model.fuse1")])
        self.assertRegex(steps[0].extras[0][1], r"^model\.make_box2\.[1-6]$")

    def test_a_revolution_names_its_faces_from_the_profile_and_its_end_from_the_face(self) -> None:
        """Each face of a revolution is generated from a profile edge; a partial one begins
        at the profile face itself and ends at the face it generated (its last shape)."""
        from build123d import Axis, Face, Solid

        def model(angle):
            return Solid.revolve(Face.make_rect(3, 4).translate((6, 0, 0)), angle, Axis.Y)

        for angle in (360, 270):
            body, _, run = _run(model, angle)
            lineage, name = _named(run, body)
            ends = []
            for face in body.faces():
                if face.area > 50:
                    self.assertRegex(name(face), r"^f:model\.make_rect1\.[1-4];Ge:model\.revolve1$")
                else:
                    ends.append(name(face))
            self.assertEqual(sorted(ends), [] if angle == 360 else
                             ["f:model.make_rect1.1", "f:model.make_rect1.1;Gf:model.revolve1"])
            self.assertTrue(all(name is not None for plural in ("faces", "edges", "vertices")
                                for name in lineage[plural]))

    def test_an_element_no_maker_reports_is_named_from_its_neighbours(self) -> None:
        """A fillet reports its face (generated from the edge it rounds) and the faces it
        trims, not the edges and vertices bounding the new face: each takes a named parent's
        name and its number among that parent's unnamed elements (the reverse pass)."""
        from build123d import Axis, Solid

        def model():
            box = Solid.make_box(10, 10, 10)
            return box.fillet(2, box.edges().filter_by(Axis.Z)[:1])

        body, _, run = _run(model)
        lineage, name = _named(run, body)
        rounded = [face for face in body.faces() if face.geom_type.name == "CYLINDER"][0]
        self.assertRegex(name(rounded), r"^f:model\.make_box1\.\d+;Ge:model\.fillet1$")
        boundary = [edge for edge in rounded.edges() if edge.geom_type.name == "LINE"]
        for edge in boundary:
            _atom, steps = _spelled(lineage, name(edge))
            self.assertEqual((steps[-1].op, steps[-1].feature), ("U", "model.fillet1"))
        self.assertTrue(all(name is not None for plural in ("faces", "edges", "vertices") for name in lineage[plural]))

    def test_a_wire_s_edges_are_the_edges_it_was_given_and_its_face_is_named_from_them(self) -> None:
        """``Wire`` copies the edges whose vertices it merges and reports nothing: a copy on
        the same curve over the same parameters is the edge it copies. Its face is named from
        its edges (the forward pass), and the extrusion's sides from the edges."""
        from build123d import Axis, Edge, Face, Solid, Vector, Wire

        def model():
            corners = [(0, 0), (10, 0), (10, 5), (0, 5)]
            wire = Wire([Edge.make_line(corners[i], corners[(i + 1) % 4]) for i in range(4)])
            return Solid.extrude(Face(wire), Vector(0, 0, 3))

        body, _, run = _run(model)
        lineage, name = _named(run, body)
        sides = sorted(name(face) for face in body.faces() if abs(face.center().Z - 1.5) < 1e-9)
        self.assertEqual(sides, [f"f:model.make_line{n}.1;Ge:model.extrude1" for n in range(1, 5)])
        bottom = body.faces().sort_by(Axis.Z)[0]
        atom, steps = _spelled(lineage, name(bottom))
        self.assertEqual((atom, [(step.op, step.feature) for step in steps]), ("model.make_line1.1", [("L", "model.face1")]))
        self.assertEqual([extra for _kind, extra in steps[0].extras], [f"model.make_line{n}.1" for n in (2, 3, 4)])

    def test_a_loft_names_its_side_from_both_profiles_and_its_caps_from_their_edges(self) -> None:
        from build123d import Location, Solid, Wire

        def model():
            return Solid.make_loft([Wire.make_circle(5), Wire.make_circle(3).moved(Location((0, 0, 10)))])

        body, _, run = _run(model)
        lineage, name = _named(run, body)
        side = [face for face in body.faces() if face.geom_type.name != "PLANE"][0]
        atom, steps = _spelled(lineage, name(side))
        self.assertEqual((atom, [(step.op, step.kind, step.feature, step.extras) for step in steps]),
                         ("model.make_circle1.1", [("G", "e", "model.make_loft1", (("e", "model.make_circle2.1"),))]))
        caps = sorted(name(face) for face in body.faces() if face.geom_type.name == "PLANE")
        self.assertEqual(caps, ["f:model.make_circle1.1;L:model.make_loft1", "f:model.make_circle2.1;L:model.make_loft1"])

    def test_a_label_pins_the_name_in_what_is_made_of_the_labelled_shape(self) -> None:
        """A shape the model labels and gives another construction names the feature
        that made it -- through build123d's part wrapper and through a placement -- in
        what that construction makes. Placed in an assembly, a labelled shape is an
        occurrence the label names: its own elements keep its construction's names, so
        identical constructions labelled apart are one part, named alike."""
        from build123d import Box, Compound, Cylinder, Pos

        def model():
            pegs = []
            for index in range(2):
                peg = Pos(40 * index, 0, 0) * Cylinder(3, 10)
                peg.label = f"peg:{index}"
                pegs.append(peg)
            boss = Cylinder(2, 4)
            boss.label = "boss"
            plate = Box(20, 20, 2) + Pos(0, 0, 3) * boss
            return Compound(children=[*pegs, plate], label="pair")

        compound, _, run = _run(model)
        first, second, plate = compound.children
        identity = run.identify(first)
        self.assertEqual(run.identify(second), identity, "one construction, labelled apart: one part")
        _key, lineage = identity
        self.assertEqual(lineage["made"], "model.make_cylinder1")
        self.assertEqual(sorted(lineage["faces"]),
                         ["f:model.make_cylinder1.1", "f:model.make_cylinder1.2", "f:model.make_cylinder1.3"])
        _key, plate_lineage = run.identify(plate)
        bosses = [name for name in plate_lineage["faces"] if name.startswith("f:boss.")]
        self.assertTrue(bosses, plate_lineage["faces"])
        self.assertFalse([name for name in plate_lineage["faces"] if "cylinder" in name], plate_lineage["faces"])

    def test_a_label_set_after_a_construction_renames_nothing_it_made(self) -> None:
        """A label pins names only in what is made after it is applied: labelling a shape
        that a construction was already given renames nothing that construction made, and
        the part keeps its key. What is made of the shape after the label is named by it."""
        from build123d import Box, Compound, Cylinder, Pos

        def model(label):
            a = Box(10, 10, 10)
            fused = Pos(30, 0, 0) * (a + Pos(5, 0, 0) * Cylinder(2, 12))
            if label:
                a.label = label
            after = Pos(0, 30, 0) * (a + Pos(5, 0, 0) * Cylinder(1, 12))
            return Compound(children=[a, fused, after])

        found = []
        for label in (None, "lid"):
            compound, _, run = _run(model, label)
            found.append([run.identify(child) for child in compound.children[1:]])
        (plain_fused, plain_after), (fused, after) = found
        self.assertEqual(fused, plain_fused, "made before the label: named and keyed as if there were none")
        self.assertTrue([name for name in fused[1]["faces"] if name.startswith("f:model.make_box1.")], fused[1]["faces"])
        self.assertTrue([name for name in after[1]["faces"] if name.startswith("f:lid.")], after[1]["faces"])
        self.assertNotEqual(after, plain_after, "made after it: the label names the box's feature there")

    def test_a_nested_run_s_features_are_its_own(self) -> None:
        """A child model built inside its parent's body runs in a run of its own:
        its features land in its trace, and the parent's trace goes on without them."""
        from build123d import Solid

        with kernel_memo.run(None) as outer:
            Solid.make_box(1, 1, 1)
            with kernel_memo.run(None) as inner:
                Solid.make_cylinder(1, 2)
            Solid.make_sphere(1)
        self.assertEqual([feature.op for feature in outer.trace.features], ["Solid.make_box", "Solid.make_sphere"])
        self.assertEqual([feature.op for feature in inner.trace.features], ["Solid.make_cylinder"])

    def test_a_shape_no_feature_made_has_no_lineage_even_placed(self) -> None:
        from build123d import Location, Solid

        foreign = Solid.make_box(5, 5, 5)

        def model():
            return foreign.moved(Location((1, 2, 3)))

        placed, _, run = _run(model)
        self.assertEqual([feature.op for feature in run.trace.features], ["moved"])
        self.assertEqual(run.identify(placed), (None, None))

    def test_a_decoded_child_is_made_by_no_feature(self) -> None:
        """A body composes a child as its stored result, decoded (``cadgen.store.materialize``):
        a shape no feature of this run made. The feature that places it has no input
        feature for it, the run names it by nothing, and the parent's own features are
        the same on a hit and on a miss, since the child is decoded on both."""
        from build123d import Compound, Pos, Solid

        from cadgen.store.build import build_tree_from_compound
        from cadgen.store.materialize import materialize

        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": tempfile.mkdtemp(prefix="memo-child-")}):
            tree = build_tree_from_compound(Solid.make_box(1, 2, 3), root_name="child")[0]

            def parent():
                child = materialize(tree, label="child")
                return Compound(children=[Pos(5, 0, 0) * child, Solid.make_box(4, 4, 4)])

            assembly, _, run = _run(parent)
        placed, own = assembly.children
        self.assertEqual([feature.op for feature in run.trace.features], ["moved", "Solid.make_box", "Compound"])
        self.assertEqual(run.trace.features[0].inputs, [None], "the child it placed was made by no feature of this run")
        self.assertEqual(run.identify(placed), (None, None), "a decoded child has no construction key and no lineage")
        self.assertIsNotNone(run.identify(own)[0])

    def test_an_edit_elsewhere_renames_nothing(self) -> None:
        """A name's ordinal counts among the features the part descends from, never
        across the run: another hole in another part renames nothing in this one."""
        from build123d import Box, Compound, Cylinder, Pos

        def bore(body, x, radius):
            return body - Pos(x, 0, 0) * Cylinder(radius, 30)

        def plate(holes):
            body = Box(40, 10, 2)
            for index in range(holes):
                body = bore(body, -15 + 6 * index, 1.0)
            return body

        def bar():
            return bore(Box(20, 8, 4), 0, 1.5)

        def model(holes):
            part, other = plate(holes), bar()
            Compound(children=[part, other])
            return part, other

        lineages = []
        for holes in (1, 2):
            kernel_memo.clear()
            (part, other), _, run = _run(model, holes)
            lineages.append(run.identify(other)[1])
            plate_lineage = run.identify(part)[1]
        self.assertEqual(lineages[0], lineages[1])
        self.assertEqual(lineages[0]["made"], "bore.cut1")
        self.assertIn("f:bore.make_cylinder1.1;M:bore.cut1", lineages[0]["faces"])
        # The plate's own two bores count among its own features.
        self.assertEqual(sorted(name for name in plate_lineage["faces"] if "make_cylinder" in name),
                         ["f:bore.make_cylinder1.1;M:bore.cut1", "f:bore.make_cylinder2.1;M:bore.cut2"])

    def test_every_element_the_run_produced_is_named_through_a_sub_shape_input(self) -> None:
        """``Solid.extrude(bar.faces()[i])``: the tab's base is the bar's face, its edges and
        vertices the bar's, followed from the face to the bar that owns it."""
        from build123d import Axis, Solid, Vector

        from cadgen._internal.entity_ordinals import entity_map

        def model():
            bar = Solid.make_box(20, 8, 4)
            face = bar.faces().sort_by(Axis.X)[-1]
            return bar, Solid.extrude(face, Vector(5, 0, 0))

        (bar, tab), _, run = _run(model)
        _key, lineage = run.identify(tab)
        self.assertTrue(all(name is not None for plural in ("faces", "edges", "vertices") for name in lineage[plural]),
                        lineage)
        faces = entity_map(tab.wrapped, "face")
        by_x = {round(face.center().X, 6): lineage["faces"][faces.FindIndex(face.wrapped) - 1]
                for face in tab.faces().filter_by(Axis.X)}
        _bar_key, bar_lineage = run.identify(bar)
        bar_faces = entity_map(bar.wrapped, "face")
        end = bar.faces().sort_by(Axis.X)[-1]
        self.assertEqual(by_x[20.0], bar_lineage["faces"][bar_faces.FindIndex(end.wrapped) - 1],
                         "the tab's base is the bar's end face, by the bar's name")
        self.assertEqual(by_x[25.0], f"{by_x[20.0]};Gf:model.extrude1", "the cap the extrusion generated from it")

    def test_a_function_named_beyond_ascii_has_a_name_of_its_own(self) -> None:
        """Normalisation is injective: a name that is not an ASCII identifier is its ASCII
        form and a digest of it, never another function's name."""
        namespace: dict = {}
        source = "def \u043a\u043e\u0440\u043f\u0443\u0441(Solid):\n    return Solid.make_box(2, 2, 2)\n"
        exec(compile(source, __file__, "exec"), namespace)  # noqa: S102 - a function of the model's file
        housing = namespace["\u043a\u043e\u0440\u043f\u0443\u0441"]

        def model():
            from build123d import Location, Solid

            return Solid.make_box(2, 2, 2).fuse(housing(Solid).moved(Location((5, 0, 0))))

        from cadgen._internal.component_package import lineage_bytes

        body, _, run = _run(model)
        _key, lineage = run.identify(body)
        lineage_bytes(lineage)  # every name distinct
        features = sorted({name.split(".")[0] for name in lineage["faces"]})
        self.assertEqual(len(features), 2)
        self.assertIn("f:model", features)
        self.assertRegex(next(name for name in features if name != "f:model"), r"^f:_[0-9a-f]{8}$")
        names = {kernel_trace.identifier(text) for text in
                 ("model", "\u043a\u043e\u0440\u043f\u0443\u0441", "<module>", "module", "a:b", "a_b",
                  "a_b_0123abcd", "")}
        self.assertEqual(len(names), 8)
        self.assertTrue(all(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in names))
        self.assertEqual(kernel_trace.identifier("<module>"), "module")
        self.assertEqual(kernel_trace.identifier("bracket"), "bracket")

    def test_a_stored_lineage_reads_back_only_whole(self) -> None:
        """A lineage object reads back as written, or not at all: a name out of the grammar
        or of another kind, two elements named alike, a table entry that is no stepped
        derivation, a folded atom the table does not spell, an entry no name reaches."""
        import copy

        from cadgen._internal.component_package import canonical_json_bytes, read_lineage

        written = {"made": "part.extrude1", "faces": ["f:part.extrude1~0123456789ab", None, "f:part.make_box1.2"],
                   "edges": ["e:part.make_box1.1;M:part.cut1", "e:0a1b2c3d4e5f"], "vertices": [],
                   "table": {"part.extrude1~0123456789ab": "part.make_box1.1;Gf:part.extrude1+e:sk.cut2~abcdefabcdef",
                             "sk.cut2~abcdefabcdef": "sk.make_line1.1;M:sk.cut2"}}
        self.assertEqual(read_lineage(canonical_json_bytes(written)), written)
        edits = {  # (list, key, value): value None drops the key
            "a face named as an edge": ("faces", 2, "e:part.make_box1.2"),
            "a step out of the grammar": ("edges", 0, "e:part.make_box1.1;X:part.cut1"),
            "two names on one line": ("faces", 2, "f:part.make_box1.2\nf:part.make_box1.3"),
            "a name that is no string": ("faces", 2, 7),
            "two faces alike": ("faces", 1, "f:part.make_box1.2"),
            "an entry with no step": ("table", "sk.cut2~abcdefabcdef", "sk.make_line1.1"),
            "an atom the table lacks": ("table", "sk.cut2~abcdefabcdef", None),
            "an entry no name reaches": ("table", "x.cut9~ffffffffffff", "x.make_box1.1;M:x.cut9"),
        }
        for label, (field, key, value) in edits.items():
            changed = copy.deepcopy(written)
            if value is None:
                del changed[field][key]
            else:
                changed[field][key] = value
            with self.subTest(label), self.assertRaises(ValueError):
                read_lineage(canonical_json_bytes(changed))

    def test_names_are_the_same_with_the_memo_off(self) -> None:
        """Hits off, every call runs and its makers report again: the same names."""
        def model():
            return _junction_fillets()[1]

        cold, _, run = _run(model)
        expected = run.identify(cold)[1]
        kernel_memo.clear()
        with mock.patch.dict(os.environ, {"CADGEN_KERNEL_MEMO": "0"}):
            plain, stats, again = _run(model)
        self.assertEqual(stats["hits"], 0)
        self.assertEqual(again.identify(plain)[1], expected)

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

    def test_each_output_of_one_construction_is_its_own_construction(self) -> None:
        """A split kept both ways is one call with two outputs: each output is a
        construction of its own, and so is what is made from each."""
        from build123d import Keep, Plane, Solid

        def halves():
            return Solid.make_torus(10, 2).split(Plane.XZ, keep=Keep.BOTH)

        pieces, _, _ = _run(halves)
        self.assertEqual(len(pieces), 2)
        keys = [kernel_memo.construction_key(piece) for piece in pieces]
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", key or "") for key in keys))
        self.assertNotEqual(keys[0], keys[1])

        def scaled(which):
            return halves()[which].scale(2.0)

        made = []
        for which in (0, 1):
            kernel_memo.clear()
            made.append(kernel_memo.construction_key(_run(scaled, which)[0]))
        self.assertNotEqual(made[0], made[1], "the same operation on the other output")

    def test_a_construction_key_is_the_construction_s_whatever_ran_before(self) -> None:
        """A warm worker's earlier runs leave sub-shapes registered and effects undone:
        none of it is in a key. A, then B, then A gives A the key a cold run gives."""
        from build123d import Axis, Solid, Vector

        def bracket(face_index):
            box = Solid.make_box(10, 10, 10)
            face = box.faces().sort_by(Axis.Z)[face_index]
            Solid.extrude(face, Vector(0, 0, 2 if face_index == -1 else -2))
            return box

        cold = kernel_memo.construction_key(_run(bracket, -1)[0])
        self.assertRegex(cold, r"^[0-9a-f]{64}$")
        _run(bracket, 0)  # an edit extrudes the other face
        warm = kernel_memo.construction_key(_run(bracket, -1)[0])
        self.assertEqual(warm, cold)

    def test_identical_constructions_are_one_construction_whatever_ran_before(self) -> None:
        """Two identical constructions in one run are two objects (HitTest) but one
        construction: the memo tells the calls apart by how many identical ones the run
        made before, and that count -- grown by any part's calls -- is in no key."""
        from build123d import Pos, Solid

        def screws(earlier):
            if earlier:
                Solid.make_cylinder(0.5, 2)  # an identical call another part made first
            return [Pos(4 * index, 0, 0) * Solid.make_cylinder(0.5, 2).fuse(Pos(0, 0, 2) * Solid.make_cylinder(1, 0.5))
                    for index in range(2)]

        first, second = _run(screws, False)[0]
        self.assertFalse(first.wrapped.IsPartner(second.wrapped), "two objects, two bodies")
        key = kernel_memo.construction_key(first)
        self.assertRegex(key or "", r"^[0-9a-f]{64}$")
        self.assertEqual(kernel_memo.construction_key(second), key)
        kernel_memo.clear()
        self.assertEqual([kernel_memo.construction_key(screw) for screw in _run(screws, True)[0]], [key, key])

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


class HistoryCaptureTest(KernelMemoCase):
    """What the kernel's makers report while a feature runs (``kernel_history``): recorded
    for the feature that ran them, composed through their chain, kept with the memo's entry."""

    def test_a_maker_a_nested_call_runs_belongs_to_the_enclosing_feature(self) -> None:
        """``split`` runs build123d's own boolean (a patched call, nested) and its makers: one
        feature, whose history is the splitter's and the clean's, from the torus's face."""
        from build123d import Keep, Plane, Solid

        def model():
            return Solid.make_torus(10, 2).split(Plane.XZ, keep=Keep.BOTH)

        halves, _, run = _run(model)
        self.assertEqual(len(halves), 2)
        self.assertEqual([feature.op for feature in run.trace.features], ["Solid.make_torus", "split"])
        origins = run.trace.features[1].history.origins
        face = 2  # kernel_history.KINDS: vertex, edge, face
        self.assertTrue(origins, "the split's pieces were reported")
        self.assertIn((0, face, 1, "M"), {source[:4] for found in origins.values() for source in found},
                      "each half's face is the torus's one face, modified")

    def test_a_copy_is_the_same_elements(self) -> None:
        """``located`` places a copy (``BRepBuilderAPI_Copy``): every element of the result is
        its input's element at the same ordinal, copied -- not modified."""
        from build123d import Location, Solid

        def model():
            return Solid.make_box(2, 3, 4).located(Location((10, 0, 0)))

        _placed, _, run = _run(model)
        located = run.trace.features[-1]
        self.assertEqual(located.op, "located")
        origins = located.history.origins
        for kind, count in enumerate((8, 12, 6)):  # a box's vertices, edges and faces
            for ordinal in range(1, count + 1):
                self.assertEqual(origins.get((0, kind, ordinal)), ((0, kind, ordinal, "I", (1,)),), (kind, ordinal))

    def test_no_maker_is_asked_about_a_shape_it_never_saw_and_a_hit_asks_none(self) -> None:
        """Every query is about a sub-shape of the maker's own inputs: none raises. A hot run
        hands back the miss's shapes and its history with them, running no maker."""
        from build123d import (Axis, Box, Cylinder, Edge, Face, Keep, Location, Plane, Pos, Rot, Solid, Vector,
                               Wire)

        def model():
            body = Box(30, 20, 10) - Pos(5, 0, 0) * Cylinder(3, 30) + Pos(0, 0, 5) * Box(10, 10, 4)
            body = body.fillet(1.0, body.edges().filter_by(Axis.Z)[:2])
            body = body.chamfer(0.5, None, body.edges().filter_by(Axis.X)[:1])
            square = Wire([Edge.make_line((0, 0), (8, 0)), Edge.make_line((8, 0), (8, 4)),
                           Edge.make_line((8, 4), (0, 4)), Edge.make_line((0, 4), (0, 0))])
            tab = Solid.extrude(Face(square), Vector(0, 0, 3))
            ring = Solid.revolve(Face(square).moved(Location((10, 0, 0))), 270, Axis.Y)
            loft = Solid.make_loft([Wire.make_circle(4), Wire.make_circle(2).moved(Location((0, 0, 6)))])
            halves = Rot(0, 0, 15) * Solid.make_box(4, 4, 4)
            return body, tab, ring, loft, halves.split(Plane.YZ, keep=Keep.TOP), body.mirror(Plane.XZ)

        before = kernel_history.stats()
        cold, _, run = _run(model)
        middle = kernel_history.stats()
        self.assertGreater(middle["queries"], before["queries"])
        self.assertEqual((middle["raised"], middle["errors"]), (before["raised"], before["errors"]))
        hot, stats, again = _run(model)
        self.assertEqual(stats["misses"], 0)
        self.assertEqual(kernel_history.stats()["queries"], middle["queries"], "a hit runs no maker")
        self.assertTrue(all(second.history is first.history
                            for first, second in zip(run.trace.features, again.trace.features)))


class BooleanTest(KernelMemoCase):
    """Every boolean the layer sees runs non-destructive (``BOPAlgo_Options``): the kernel
    copies the sub-shapes it changes and shares the rest, so a boolean's inputs are left as
    they were given, and lineage by identity (``kernel_trace``) follows what it left alone."""

    def test_a_boolean_leaves_its_inputs_as_given_and_shares_what_it_leaves_alone(self) -> None:
        from build123d import Axis, Solid

        pristine = _bytes(Solid.make_cylinder(3, 30).wrapped)
        (box, tool, body), stats, _ = _run(_bored_plate)
        self.assertEqual(stats["unreplayable"], 0)
        self.assertEqual(_bytes(tool.wrapped), pristine, "the cut left its tool as it was given")
        side = box.faces().sort_by(Axis.X)[-1]
        bottom = box.faces().sort_by(Axis.Z)[0]
        self.assertTrue(any(face.wrapped.IsSame(side.wrapped) for face in body.faces()),
                        "a face the cut leaves alone keeps its TShape in the result")
        self.assertFalse(any(face.wrapped.IsPartner(bottom.wrapped) for face in body.faces()),
                         "a face the cut modifies is a new one")


class BuildAgreementTest(unittest.TestCase):
    """A model built cold, then hot in one process (``--force`` runs the body again, every
    construction a hit), stores one tree of one set of component bytes when its body reuses
    an input an operation changed: a boolean's tool after the boolean, a profile face after
    an extrusion (README law 18's pressure-test, at the store)."""

    TOOL_REUSED = """\
from cadgen import step
from cadgen import build123d as bd


@step
def plate():
    body = bd.Solid.make_box(20, 20, 8)
    tool = bd.Solid.make_cylinder(3, 30)
    bored = body.cut(tool)
    bored.label = "bored"
    peg = bd.Pos(30, 0, 0) * tool  # the tool again, after the cut
    peg.label = "peg"
    return bd.Compound(children=[bored, peg], label="plate")
"""

    PROFILE_REUSED = """\
from cadgen import step
from cadgen import build123d as bd


@step
def boss():
    profile = bd.Face(bd.Wire.make_circle(4))
    body = bd.Solid.extrude(profile, bd.Vector(0, 0, 4))
    body.label = "body"
    lid = bd.Solid.extrude(profile, bd.Vector(0, 0, -1))  # the profile again, after the extrusion
    lid.label = "lid"
    return bd.Compound(children=[body, bd.Pos(0, 0, -2) * lid], label="boss")
"""

    def setUp(self) -> None:
        temporary = generated_cad_directory(prefix="memo-build-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        env = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.root / "store")})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("CADGEN_KERNEL_MEMO", None)
        kernel_memo.install()
        kernel_memo.clear()
        self.addCleanup(kernel_memo.clear)

    def build(self, script: Path, *flags: str) -> tuple[dict, dict]:
        """``(record, memo stats delta)`` of ``python script.py flags``, run in this process."""
        from cadgen.cli._run_model import run_model_argv
        from cadgen.store.records import read_record

        before = kernel_memo.stats()
        err = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            code = run_model_argv([str(script), *flags])
        self.assertEqual(code, 0, err.getvalue())
        record = read_record(script)
        self.assertIsNotNone(record, f"{script.name} has no record")
        return record, _delta(before, kernel_memo.stats())

    def components(self, record: dict) -> dict[str, bytes]:
        """Each component of the record's tree, by cid: its encoded BREP."""
        from cadgen.store.objects import read_object
        from cadgen.store.trees import get_tree

        return {cid: read_object(entry["brep"]) for cid, entry in get_tree(record["tree"])["components"].items()}

    def assert_cold_and_hot_agree(self, name: str, source: str) -> None:
        script = self.root / f"{name}.py"
        script.write_text(source, encoding="utf-8")
        cold, cold_stats = self.build(script)
        cold_components = self.components(cold)
        self.assertEqual(len(cold_components), 2)
        self.assertEqual((cold_stats["hits"], cold_stats["unreplayable"], cold_stats["errors"]), (0, 0, 0))
        hot, hot_stats = self.build(script, "--force")
        self.assertEqual((hot_stats["misses"], hot_stats["unreplayable"], hot_stats["errors"]), (0, 0, 0))
        self.assertEqual(hot_stats["hits"], cold_stats["misses"], "every construction of the cold run hit")
        self.assertEqual((hot["tree"], hot["key"]), (cold["tree"], cold["key"]))
        self.assertEqual(self.components(hot), cold_components)

    def test_a_tool_reused_after_its_boolean_builds_the_same_cold_and_hot(self) -> None:
        self.assert_cold_and_hot_agree("plate", self.TOOL_REUSED)

    def test_a_profile_reused_after_its_extrusion_builds_the_same_cold_and_hot(self) -> None:
        self.assert_cold_and_hot_agree("boss", self.PROFILE_REUSED)


if __name__ == "__main__":
    unittest.main()
