"""Label refs resolve to exactly one occurrence, or they raise.

The alias numbering is user-facing -- people paste `#cast_rim:5spoke_2` -- so its order is a
contract, not an implementation detail. The ambiguity rule is the load-bearing one: a bare
label shared by two parts must refuse to resolve rather than silently pick the first, because
picking the first renders the wrong wheel and looks like success.

Occurrence ids are tokens (``o:`` + 12 hex digits) minted from the tree, so a token carries
no position: duplicates are numbered in the order the rows come, which is tree order.
"""

from __future__ import annotations

import unittest

from cadgen.label_refs import (
    LabelResolutionError,
    build_label_aliases,
    resolve_label_selectors,
)
from tests.python.support.tree_ids import node_id, tokenized


def tok(n: int) -> str:
    """A token-shaped occurrence id; the alias builder treats ids as opaque."""
    return f"o:{n:012x}"


O1, O2, O3, O4, O5, O6, O7 = (tok(n) for n in range(1, 8))
F45 = "f:" + "a" * 11 + "5"
E3 = "e:" + "b" * 11 + "3"

ALIAS_CASES = [
    {
        "why": "unique labels get a bare alias and no number",
        "rows": [
            {"id": O1, "name": "eye_shank"},
            {"id": O2, "name": "pressure_tube"},
        ],
        "aliases": {"eye_shank": O1, "pressure_tube": O2},
        "ambiguous": {},
    },
    {
        "why": "duplicates get numbered aliases in row (tree) order; the bare name is ambiguous",
        "rows": [
            {"id": O3, "name": "cast_rim:5spoke"},
            {"id": O7, "name": "cast_rim:5spoke"},
        ],
        "aliases": {"cast_rim:5spoke_1": O3, "cast_rim:5spoke_2": O7},
        "ambiguous": {"cast_rim:5spoke": ["cast_rim:5spoke_1", "cast_rim:5spoke_2"]},
    },
    {
        "why": "numbering follows the rows, never the token's spelling",
        "rows": [
            {"id": tok(0xFFFF), "name": "bolt"},
            {"id": O2, "name": "bolt"},
        ],
        "aliases": {"bolt_1": tok(0xFFFF), "bolt_2": O2},
        "ambiguous": {"bolt": ["bolt_1", "bolt_2"]},
    },
    {
        "why": "a numbered alias never collides with a label an author actually wrote",
        "rows": [
            {"id": O1, "name": "servo_end_mount"},
            {"id": O2, "name": "servo_end_mount"},
            {"id": O3, "name": "servo_end_mount_1"},
        ],
        "aliases": {"servo_end_mount_2": O1, "servo_end_mount_3": O2, "servo_end_mount_1": O3},
        "ambiguous": {"servo_end_mount": ["servo_end_mount_2", "servo_end_mount_3"]},
    },
    {
        "why": "names that cannot be labels are reachable by token only",
        "rows": [
            {"id": O1, "name": tok(4)},
            {"id": O2, "name": "o:" + "c" * 12 + "/" + F45},
            {"id": O3, "name": "m2"},
            {"id": O4, "name": "5spoke"},
            {"id": O5, "name": "has space"},
            {"id": O6, "name": "has.dot"},
            {"id": O7, "name": ""},
        ],
        "aliases": {},
        "ambiguous": {},
    },
    {
        "why": "a name spelled like the retired numeric grammar is an ordinary label now",
        "rows": [
            {"id": O1, "name": "f12"},
            {"id": O2, "name": "o1"},
        ],
        "aliases": {"f12": O1, "o1": O2},
        "ambiguous": {},
    },
    {
        "why": "group (non-leaf) rows are addressable too",
        "rows": [
            {"id": O1, "name": "damper_body"},
            {"id": O2, "name": "pressure_tube"},
        ],
        "aliases": {"damper_body": O1, "pressure_tube": O2},
        "ambiguous": {},
    },
    {
        "why": "a row may carry the render package's `occurrenceId` spelling instead of `id`",
        "rows": [
            {"occurrenceId": O1, "name": "eye_shank"},
            {"id": O2, "name": "pressure_tube"},
        ],
        "aliases": {"eye_shank": O1, "pressure_tube": O2},
        "ambiguous": {},
    },
]


class AliasCasesTest(unittest.TestCase):
    def test_every_alias_case_matches(self) -> None:
        for case in ALIAS_CASES:
            with self.subTest(why=case.get("why", "")):
                built = build_label_aliases(case["rows"])
                self.assertEqual(case["aliases"], built["aliases"])
                self.assertEqual(
                    {k: list(v) for k, v in case["ambiguous"].items()},
                    {k: list(v) for k, v in built["ambiguous"].items()},
                )


class AliasOrderingTest(unittest.TestCase):
    def test_duplicates_are_numbered_in_row_order(self) -> None:
        rows = [
            {"id": O7, "name": "bolt"},
            {"id": O2, "name": "bolt"},
            {"id": O5, "name": "bolt"},
        ]
        built = build_label_aliases(rows)
        self.assertEqual(
            {"bolt_1": O7, "bolt_2": O2, "bolt_3": O5},
            built["aliases"],
        )

    def test_a_numbered_alias_never_steals_an_authored_name(self) -> None:
        rows = [
            {"id": O1, "name": "servo_end_mount"},
            {"id": O2, "name": "servo_end_mount"},
            {"id": O3, "name": "servo_end_mount_1"},
        ]
        built = build_label_aliases(rows)
        self.assertEqual(O3, built["aliases"]["servo_end_mount_1"])
        self.assertEqual(O1, built["aliases"]["servo_end_mount_2"])
        self.assertEqual(O2, built["aliases"]["servo_end_mount_3"])


class UnaliasableRowsTest(unittest.TestCase):
    def test_names_that_collide_with_the_token_or_mate_grammar_get_no_alias(self) -> None:
        rows = [
            {"id": O1, "name": tok(4)},
            {"id": O2, "name": F45},
            {"id": O3, "name": "m2"},
            {"id": O4, "name": "5spoke"},
            {"id": O5, "name": "has space"},
            {"id": O6, "name": "has.dot"},
            {"id": O7, "name": ""},
        ]
        built = build_label_aliases(rows)
        self.assertEqual({}, built["aliases"], "none of these may claim an alias")

    def test_rows_without_an_id_are_ignored(self) -> None:
        self.assertEqual({}, build_label_aliases([{"name": "orphan"}])["aliases"])


class ResolutionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.alias_map = build_label_aliases(
            [
                {"id": O1, "name": "eye_shank"},
                {"id": O3, "name": "cast_rim:5spoke"},
                {"id": O7, "name": "cast_rim:5spoke"},
            ]
        )

    def test_a_unique_label_resolves_bare(self) -> None:
        self.assertEqual([O1], resolve_label_selectors(["eye_shank"], self.alias_map))

    def test_a_unique_label_resolves_with_an_element(self) -> None:
        self.assertEqual(
            [f"{O1}/{F45}"], resolve_label_selectors([f"eye_shank/{F45}"], self.alias_map)
        )
        self.assertEqual(
            [f"{O1}/{E3}"], resolve_label_selectors([f"eye_shank/{E3}"], self.alias_map)
        )

    def test_a_leading_hash_is_preserved(self) -> None:
        self.assertEqual([f"#{O1}/{F45}"], resolve_label_selectors([f"#eye_shank/{F45}"], self.alias_map))

    def test_a_numbered_duplicate_resolves(self) -> None:
        self.assertEqual([O3], resolve_label_selectors(["cast_rim:5spoke_1"], self.alias_map))
        self.assertEqual([O7], resolve_label_selectors(["cast_rim:5spoke_2"], self.alias_map))

    def test_a_bare_duplicate_raises_and_names_the_candidates(self) -> None:
        with self.assertRaises(LabelResolutionError) as raised:
            resolve_label_selectors(["cast_rim:5spoke"], self.alias_map)
        message = str(raised.exception)
        self.assertIn("matches 2 occurrences", message)
        self.assertIn("cast_rim:5spoke_1", message)
        self.assertIn("cast_rim:5spoke_2", message)
        self.assertIn(O3, message, "the message must show which part each alias is")

    def test_an_unknown_label_raises_with_a_next_step(self) -> None:
        with self.assertRaises(LabelResolutionError) as raised:
            resolve_label_selectors(["no_such_part"], self.alias_map)
        self.assertIn("no_such_part", str(raised.exception))
        self.assertIn("--mode list", str(raised.exception))

    def test_a_label_with_no_alias_map_at_all_raises(self) -> None:
        with self.assertRaises(LabelResolutionError):
            resolve_label_selectors(["eye_shank"], None)


class TokenPassThroughTest(unittest.TestCase):
    """Resolution must not touch anything that is not a label: token refs, mate refs, and
    opaque strings (the retired ``o1.2.f7`` grammar among them) pass through byte for byte."""

    UNTOUCHED = [O1, f"{O1}/{F45}", f"{O2}/{E3}", F45, E3, "v:" + "d" * 12, "m1", f"#{O2}/{F45}", "o1.2.f7"]

    def test_token_and_opaque_selectors_pass_through_byte_for_byte(self) -> None:
        alias_map = build_label_aliases([{"id": O1, "name": "eye_shank"}])
        self.assertEqual(self.UNTOUCHED, resolve_label_selectors(self.UNTOUCHED, alias_map))

    def test_token_selectors_resolve_without_an_alias_map(self) -> None:
        # A model with no labels at all must keep working exactly as before.
        self.assertEqual(self.UNTOUCHED, resolve_label_selectors(self.UNTOUCHED, None))


class OneNameRuleTest(unittest.TestCase):
    """Every API that takes a ``#name`` reads it one way: ``read_scene`` and snapshots,
    clips (``m.get``), material assignments and mates. A name two nodes share is refused
    in each with the same numbered aliases, and an alias names the same node in each.
    Clips once moved every node of a shared name: a clip on a beam moved its whole arm."""

    # A root group `arm` holding a base and a beam also labelled `arm`.
    DESCRIPTOR, _ = tokenized({
        "occurrences": [{"id": "o1.1"}, {"id": "o1.2"}],
        "assembly": {"root": {"id": "o1", "name": "arm", "children": [
            {"id": "o1.1", "name": "base", "children": []},
            {"id": "o1.2", "name": "arm", "children": []},
        ]}},
    })

    def test_a_shared_name_is_refused_alike_and_its_aliases_name_one_node(self) -> None:
        from cadgen._internal.animation_bake import animation_targets
        from cadgen._internal.kinematics_resolve import _instance_tree_ids, _occurrence_id_for_ref
        from cadgen._internal.source_sidecar import resolve_materials

        root = self.DESCRIPTOR["assembly"]["root"]
        base, beam = (str(child["id"]) for child in root["children"])
        aliases = build_label_aliases(
            [{"id": root["id"], "name": "arm"}, {"id": base, "name": "base"}, {"id": beam, "name": "arm"}]
        )
        clips = animation_targets(self.DESCRIPTOR)
        tree = _instance_tree_ids(self.DESCRIPTOR)

        def material(target: str) -> dict:
            declaration = {"definitions": {"x": {}}, "assignments": [{"targets": [target], "material": "x"}]}
            return resolve_materials(self.DESCRIPTOR, declaration)

        def mate(target: str) -> str:
            return _occurrence_id_for_ref(target, what="child", mate="lift", source_ref="arm", tree=tree)

        for api, call in {
            "read_scene": lambda: resolve_label_selectors(["#arm"], aliases),
            "clip": lambda: clips.resolve("#arm"),
            "material": lambda: material("#arm"),
            "mate": lambda: mate("#arm"),
        }.items():
            with self.subTest(api=api), self.assertRaises(ValueError) as caught:
                call()
            # Numbered root first, then the beam: tree order, the same in every API.
            self.assertIn(f"label 'arm' matches 2 occurrences; use one of: #arm_1 ({root['id']}), #arm_2 ({beam})",
                          str(caught.exception))

        self.assertEqual([f"#{beam}"], resolve_label_selectors(["#arm_2"], aliases))
        self.assertEqual((beam,), clips.resolve("#arm_2"))
        self.assertEqual([beam], list(material("#arm_2")["assignments"]))
        self.assertEqual(beam, mate("#arm_2"))
        self.assertEqual(base, mate("#base"))
        self.assertEqual(node_id(self.DESCRIPTOR, "base"), base)


if __name__ == "__main__":
    unittest.main()
