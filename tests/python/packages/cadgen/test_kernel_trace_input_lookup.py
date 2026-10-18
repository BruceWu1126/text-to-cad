"""The trace's input index preserves the linear first-owner lookup and its lineage."""

from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
