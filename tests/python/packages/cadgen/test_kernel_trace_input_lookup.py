"""The trace's input index preserves the linear first-owner lookup and its lineage."""

from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from OCP.BRep import BRep_Builder  # noqa: E402
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox  # noqa: E402
from OCP.TopLoc import TopLoc_Location  # noqa: E402
from OCP.TopoDS import TopoDS_Compound  # noqa: E402
from OCP.gp import gp_Trsf, gp_Vec  # noqa: E402

from cadgen._internal import kernel_history, kernel_trace  # noqa: E402


def _compound(*shapes):
    result = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def _moved(shape, x):
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(x, 0, 0))
    return shape.Moved(TopLoc_Location(transform))


def _maps(shape):
    return [kernel_trace._map(shape, topabs) for topabs in kernel_trace._TOPABS]


def _linear_index(given, kind):
    """Test-only reference: ask each original input map, stopping at the first match.
    Return the index interface used by _computed, with the original input's ordinal."""
    owners = [(j, ordinal) for j, maps in given for ordinal in range(1, maps[kind].Extent() + 1)]

    class LinearIndex:
        def FindIndex(self, element):
            offset = 0
            for _j, maps in given:
                ordinal = maps[kind].FindIndex(element)
                if ordinal:
                    return offset + ordinal
                offset += maps[kind].Extent()
            return 0

    return LinearIndex(), owners


def _record(trace, op, inputs, results):
    return trace.record(
        op, ("fixture.py", len(trace.features) + 1, "fixture"),
        [(SimpleNamespace(_wrapped=shape), owner, shape) for shape, owner in inputs],
        [SimpleNamespace(_wrapped=shape) for shape in results],
    )


def _fixture(a, b, c, placed, face, overlap, assembly):
    trace = kernel_trace.Trace(None)
    _record(trace, "Solid.make_box", [], [a])
    _record(trace, "Solid.make_box", [], [b])
    _record(trace, "moved", [(a, 0)], [placed])
    _record(trace, "Compound", [(a, 0), (placed, 2)], [overlap])
    feature = _record(
        trace, "Compound",
        [(face, 0), (a, 0), (None, None), (overlap, 3), (placed, 2), (a.Reversed(), 0), (b, 1)],
        [assembly, None, assembly.Reversed(), a, face, _moved(a, 20), c],
    )
    # Two faces from one source, reported in the opposite order: history ranking,
    # then the passes over their edges and vertices, must remain exactly the same.
    feature.history = kernel_history.History()
    for position in (0, 2, 6):
        result_faces = _maps(feature.results[position])[kernel_trace.FACE]
        faces = _maps(c)[kernel_trace.FACE]
        for ordinal, place in ((1, (2,)), (2, (1,))):
            at = result_faces.FindIndex(faces.FindKey(ordinal))
            feature.history.origins[(position, kernel_trace.FACE, at)] = (
                (1, kernel_trace.FACE, 1, "M", place),
            )
    makers = {a.TShape(): 0, b.TShape(): 1, overlap.TShape(): 3, assembly.TShape(): 4, c.TShape(): 4}
    trace._made_by = makers.get
    return trace, makers.get


class InputLookupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.a = BRepPrimAPI_MakeBox(2, 3, 4).Shape()
        cls.b = BRepPrimAPI_MakeBox(3, 4, 5).Shape()
        cls.c = BRepPrimAPI_MakeBox(4, 5, 6).Shape()
        cls.placed = _moved(cls.a, 10)
        cls.face = _maps(cls.a)[kernel_trace.FACE].FindKey(3)
        cls.overlap = _compound(cls.a, cls.placed)
        cls.assembly = _compound(cls.a, cls.placed, cls.b, cls.c)

    def test_index_matches_linear_lookup_for_overlaps_locations_and_orientations(self):
        handles = [None, self.face, self.overlap, self.a.Reversed(), self.placed, self.b]
        given = [(j, _maps(shape)) for j, shape in enumerate(handles) if shape is not None]
        # Copies, reverse orientations and repeated results retain IsSame semantics;
        # another placement of the same TShape must remain absent.
        queries = [self.assembly, self.assembly.Reversed(), self.face, self.placed,
                   _moved(self.a, 20), self.assembly]
        for kind in range(3):
            indexed, owners = kernel_trace._input_index(given, kind)
            linear, expected = _linear_index(given, kind)
            for result in queries:
                elements = _maps(result)[kind]
                for ordinal in range(1, elements.Extent() + 1):
                    element = elements.FindKey(ordinal)
                    at = indexed.FindIndex(element)
                    was = linear.FindIndex(element)
                    self.assertEqual(owners[at - 1] if at else None,
                                     expected[was - 1] if was else None)
            # Every overlapping face belongs to the earlier subshape input, whose
            # ordinal can differ from the containing assembly's ordinal.
            if kind == kernel_trace.FACE:
                self.assertEqual(owners[indexed.FindIndex(self.face.Reversed()) - 1], (1, 1))

    def test_structures_and_full_accounts_match_the_linear_lookup(self):
        args = (self.a, self.b, self.c, self.placed, self.face, self.overlap, self.assembly)
        indexed, made_by = _fixture(*args)
        reference, reference_made_by = _fixture(*args)
        structure = indexed._structure(4)
        accounts = [indexed.account(shape, made_by) for shape in (self.assembly, self.a, self.c)]
        with mock.patch.object(kernel_trace, "_input_index", _linear_index):
            self.assertEqual(structure, reference._structure(4))
            self.assertEqual(accounts, [reference.account(shape, reference_made_by)
                                        for shape in (self.assembly, self.a, self.c)])
        self.assertIsNone(structure.rules[1])
        self.assertEqual(structure.rules[0], structure.rules[2])
        self.assertTrue(any(rule[0] == "h" for kind in structure.rules[0] for rule in kind))
        self.assertTrue(any(rule[0] == "u" for kind in structure.rules[0] for rule in kind))
        self.assertTrue(all(name is not None for name in accounts[0][0]["faces"]))

    def test_first_geometric_owner_wins_even_when_its_rule_is_none_or_foreign(self):
        for first_rule in (None, ("x",)):
            for history in (False, True):
                with self.subTest(first_rule=first_rule, history=history):
                    trace = kernel_trace.Trace(None)
                    feature = _record(trace, "Compound", [(self.a, None), (self.a, None)], [self.a])
                    if history:
                        feature.history = kernel_history.History()
                        feature.history.origins = {
                            (0, kind, ordinal): ((1, kind, ordinal, "I", (1,)),)
                            for kind, maps in enumerate(_maps(self.a))
                            for ordinal in range(1, maps.Extent() + 1)
                        }

                    def sames(_index, j, _kind, count):
                        if j == 0:
                            return [first_rule] * count
                        return [("s", j, ordinal) for ordinal in range(1, count + 1)]

                    with mock.patch.object(trace, "_sames", sames):
                        indexed = trace._computed(0)
                        with mock.patch.object(kernel_trace, "_input_index", _linear_index):
                            self.assertEqual(indexed, trace._computed(0))
                    for rules in indexed.rules[0]:
                        for ordinal, rule in enumerate(rules, 1):
                            expected = first_rule
                            if expected is None:
                                expected = ("s", 1, ordinal) if history else ("m", ordinal)
                            self.assertEqual(rule, expected)

    def test_a_later_maker_cannot_become_an_earlier_features_input_source(self):
        trace = kernel_trace.Trace(None)
        foreign = _compound(self.a)
        placed = _moved(foreign, 5)
        _record(trace, "moved", [(foreign, None)], [placed])
        # A native topology mutation shares its TShape with the old input handles.
        # Its new registry entry must not make the first feature depend on the second.
        BRep_Builder().Add(placed, self.b)
        _record(trace, "mutate", [(placed, 0)], [placed])
        makers = {placed.TShape(): 1}
        trace._made_by = makers.get
        self.assertEqual(trace._source(0, 0), ("foreign",))
        self.assertEqual(trace._source(1, 0), ("followed", 0, 0, None))
        structure = trace._structure(1)
        self.assertTrue(all(rule == ("x",) for rules in structure.rules[0] for rule in rules))
        self.assertEqual(trace.account(placed.Located(TopLoc_Location()), makers.get), (None, {}))

    def test_requested_identity_result_keeps_global_made_order_and_mixed_fallback(self):
        mixed = _compound(self.a, self.c)
        results = [self.b, self.a, self.c, self.b, mixed, None]

        def fixture():
            trace = kernel_trace.Trace(None)
            _record(trace, "Solid.make_box", [], [self.a])
            feature = _record(trace, "multi", [(self.a, 0)], results)
            feature.history = kernel_history.History()
            trace._made_by = lambda _shape: 1
            return trace, feature

        eager, _feature = fixture()
        expected = eager._structure(1)
        accounts = [eager.account(body, eager._made_by) for body in (self.a, self.b, self.c, mixed)]
        self.assertEqual([rule[1] for rule in expected.rules[2][kernel_trace.FACE]], list(range(7, 13)))
        self.assertEqual(expected.rules[0], expected.rules[3], "repeated new topology keeps its made ordinals")
        for order in ((1, 0, 2, 4), (2, 1, 0, 4), (5, 1, 4, 2, 0)):
            with self.subTest(order=order):
                trace, feature = fixture()
                with mock.patch.object(trace, "_computed", wraps=trace._computed) as computed:
                    for offset, position in enumerate(order):
                        rules = trace._result_rules(1, position)
                        self.assertEqual(None if rules is None else [list(kind) for kind in rules],
                                         expected.rules[position])
                        if offset == 0 and position == 1:
                            self.assertNotIn(1, trace._structures)
                            self.assertTrue(feature.history.result_rules)
                            self.assertNotIn(mock.call(1), computed.call_args_list,
                                             "the requested inherited slot skips every unrelated output")
                self.assertEqual(trace._structure(1), expected)
                self.assertEqual([trace.account(body, trace._made_by) for body in (self.a, self.b, self.c, mixed)],
                                 accounts)
                self.assertNotIn(1, trace._identity_inputs)
                self.assertFalse(feature.history.result_rules, "full completion owns the authoritative table")

    def test_requested_identity_keeps_foreign_masking_overlap_locations_and_lost_sources(self):
        foreign = _compound(self.a)
        left, right = _compound(self.a), _compound(self.a)
        cases = [
            ([(foreign, None), (self.a, 0)], self.a, False, False),
            ([(self.a, 0), (foreign, None)], self.a, True, False),
            ([(self.face, 0), (self.overlap, 2)], self.overlap, True, False),
            ([(self.a, 0), (self.placed, 3), (self.a.Reversed(), 0)],
             _compound(self.a, self.placed), True, False),
            ([(self.a, 1)], self.a, True, True),
        ]
        for inputs, output, known, lost in cases:
            with self.subTest(known=known, lost=lost, placed=output.IsSame(self.overlap)):
                def fixture():
                    trace = kernel_trace.Trace(None)
                    for op, body in (("left", self.a), ("right", self.b),
                                     ("Compound", self.overlap), ("moved", self.placed)):
                        _record(trace, op, [], [body])
                    feature = _record(trace, "Compound", inputs, [output])
                    feature.history = kernel_history.History()
                    # Input claims are captured in the feature, independently
                    # of any later registry maker for these native handles.
                    trace._made_by = lambda _shape: 4
                    return trace

                eager, requested = fixture(), fixture()
                expected = eager._structure(4).rules[0]
                result = requested._result_rules(4, 0)
                self.assertEqual([list(kind) for kind in result], expected)
                self.assertEqual(any(rule != ("x",) for kind in result for rule in kind), known)
                self.assertEqual(4 in requested._structures, lost)

        # Overlapping named containers have different source features even
        # though they hold the very same native face/edge/vertex identities.
        for reverse in (False, True):
            trace = kernel_trace.Trace(None)
            _record(trace, "left", [], [left])
            _record(trace, "right", [], [right])
            inputs = [(left, 0), (right, 1)]
            feature = _record(trace, "Compound", inputs[::-1] if reverse else inputs, [self.a])
            feature.history = kernel_history.History()
            owners = {left.TShape(): 0, right.TShape(): 1, self.a.TShape(): 2}
            account = trace.account(self.a, owners.get)[0]
            self.assertTrue(all(name.startswith("f:fixture." + ("right1." if reverse else "left1."))
                                for name in account["faces"]))

    def test_proven_tables_rebind_warm_without_native_maps_and_release_workspaces(self):
        histories = [kernel_history.History() for _ in range(3)]
        assembly = _compound(self.a, self.b)

        def fixture(prefix, shared):
            trace = kernel_trace.Trace(None)
            if prefix:
                _record(trace, "unrelated", [], [self.c])
            for body, history in zip((self.a, self.b), shared):
                feature = _record(trace, "Solid.make_box", [], [body])
                feature.history = history
            feature = _record(trace, "Compound", [(self.a, prefix), (self.b, prefix + 1)],
                              [assembly, self.a, self.b, None])
            feature.history = shared[2]
            owners = {self.a.TShape(): prefix + 2, self.b.TShape(): prefix + 2,
                      assembly.TShape(): prefix + 2}
            trace._made_by = owners.get
            return trace, feature

        cold, feature = fixture(0, histories)
        with mock.patch.object(kernel_trace, "_map", wraps=kernel_trace._map) as maps:
            cold._result_rules(2, 1)
            mapped = maps.call_count
            self.assertNotIn(2, cold._structures)
            self.assertIn(2, cold._identity_inputs)
            cold._result_rules(2, 2)
            self.assertEqual(maps.call_count - mapped, 6,
                             "a sibling maps only itself and its previously unused source")
        expected = cold.account(self.a, cold._made_by)[0]
        self.assertTrue(histories[2].result_rules)
        self.assertFalse(histories[2].structures)

        def portable(value):
            return value is None or isinstance(value, (str, int)) or isinstance(value, tuple) and all(
                portable(item) for item in value)

        self.assertTrue(all(portable(table) for table in histories[2].result_rules.values()))
        warm, _feature = fixture(1, histories)
        with mock.patch.object(kernel_trace, "_map", side_effect=AssertionError("a warm proof rebuilt native maps")):
            warm._result_rules(3, 1)
            warm._result_rules(3, 2)
        account, names = warm.account(self.a, warm._made_by)
        self.assertEqual(account, expected)
        self.assertEqual(names["fixture.make_box1"], 1, "portable rules bind the current run's source feature")
        cold._result_rules(2, 0)
        cold._result_rules(2, 3)
        self.assertNotIn(2, cold._identity_inputs, "all completed slots release their native index")
        # Loading the last cached table must likewise release a partially used workspace.
        warm._identity(3).owner(kernel_trace.FACE, self.face)
        warm._result_rules(3, 0)
        warm._result_rules(3, 3)
        self.assertNotIn(3, warm._identity_inputs)
        cold._structure(2)
        adopted, _feature = fixture(1, histories)
        with mock.patch.object(kernel_trace, "_map", side_effect=AssertionError("a full warm hit rebuilt maps")):
            adopted._result_rules(3, 1)
        self.assertTrue({1, 2, 3}.issubset(adopted._structures),
                        "a full History hit also completes the current run's source ancestry")
        deferred, _feature = fixture(0, [kernel_history.History() for _ in range(3)])
        deferred._result_rules(2, 1)
        self.assertNotIn(1, deferred._structures)
        self.assertEqual(deferred.given([], lambda _shape: None), [])
        self.assertTrue({0, 1, 2}.issubset(deferred._structures),
                        "even a zero-input call freezes earlier partial accounts before it can mutate")
        self.assertFalse(deferred._results)
        self.assertFalse(deferred._identity_inputs)
        cold.reject_copies(feature)
        self.assertNotIn(2, cold._results)
        self.assertFalse(histories[2].result_rules)
        rejected, rejected_feature = fixture(0, [kernel_history.History() for _ in range(3)])
        rejected._result_rules(2, 1)
        self.assertIn(2, rejected._identity_inputs)
        self.assertTrue(rejected_feature.history.result_rules)
        rejected.reject_copies(rejected_feature)
        self.assertNotIn(2, rejected._results)
        self.assertNotIn(2, rejected._identity_inputs)
        self.assertFalse(rejected_feature.history.result_rules)

    def test_deep_shared_identity_dependencies_and_structure_keys_use_no_recursion(self):
        count = sys.getrecursionlimit() + 25

        def fixture():
            trace = kernel_trace.Trace(None)
            _record(trace, "Solid.make_box", [], [self.a])
            for index in range(1, count):
                inputs = [(self.a, index - 1)]
                if index > 1:
                    inputs.append((self.a, index - 2))
                feature = _record(trace, "pass", inputs, [self.a])
                feature.history = kernel_history.History()
            trace._made_by = lambda _shape: count - 1
            return trace

        eager, requested = fixture(), fixture()
        expected = eager._structure(count - 1).rules[0]
        actual = requested._result_rules(count - 1, 0)
        self.assertEqual([list(kind) for kind in actual], expected)
        self.assertEqual(requested._skey(count - 1), eager._skey(count - 1))
        self.assertEqual(requested.account(self.a, requested._made_by), eager.account(self.a, eager._made_by))
        self.assertFalse(requested._identity_inputs)
        self.assertNotIn(count - 1, requested._structures)


if __name__ == "__main__":
    unittest.main()
