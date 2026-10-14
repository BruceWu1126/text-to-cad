"""CAM's protocol: the public verbs and their generated CLIs, the operation registry, and the skill held to it.

Stdlib only: nothing here opens a part or plans a toolpath.
"""

from __future__ import annotations

import contextlib
import io
import re
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import heavy_at_import, stub_operations, value_error

from cadgen._internal.cam.operations import OPERATION_NAMES, REGISTRY, built, get_operation

# The design's 55 operations by phase, in its order.
PHASES = {
    1: ("facing", "contour_2d", "pocket_2d", "adaptive_2d", "slot", "bore", "circular", "thread_mill", "chamfer_2d",
        "engrave", "trace", "drill", "spot_drill", "peck", "chip_break", "tap", "rigid_tap", "ream", "boring_cycle",
        "counterbore", "back_bore"),
    2: ("adaptive_3d", "pocket_3d", "parallel", "contour_3d", "scallop", "pencil", "spiral", "radial", "morph",
        "steep_shallow", "ramp", "horizontal", "rest"),
    3: ("lathe_face", "lathe_rough", "lathe_finish", "lathe_groove", "lathe_part", "lathe_thread", "lathe_bore",
        "lathe_drill", "laser_cut", "laser_engrave", "plasma_cut", "waterjet_cut", "wire_contour", "wire_open", "sinker"),
    4: ("indexed", "rotary_wrap", "rotary_parallel", "swarf", "multiaxis_contour", "flowline_5x"),
}


class Surface(unittest.TestCase):
    def test_plan_help_lists_every_option_the_verb_takes(self):
        from cadgen import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            cli.main(["cam", "plan", "--help"])
        for word in ("--job", "--post", "--occurrence", "--verbose", "--json", "TARGET [OUT]"):
            self.assertIn(word, out.getvalue())

    def test_features_help_names_the_tool_direction(self):
        from cadgen import cli

        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.suppress(SystemExit):
            cli.main(["cam", "features", "--help"])
        self.assertIn("--direction", out.getvalue())

    def test_a_minus_direction_is_read_as_the_directions_value(self):
        from cadgen._internal.cli_from_function import generated_parser

        parser = generated_parser(("cadgen.cam", "features"), prog="cadgen cam features")
        self.assertEqual(parser.parse_args(["part.step", "--direction", "-Z"]).direction, "-Z")
        self.assertEqual(parser.parse_args(["part.step", "--direction", "-X", "--json"]).json_output, True)

    def test_the_public_namespace_imports_no_kernel_geometry_or_numerics(self):
        self.assertEqual(heavy_at_import("cadgen.cam", "cadgen.cli.cam_plan", "cadgen.cli.cam_features"), "")



class Registry(unittest.TestCase):
    def test_every_operation_is_registered_in_order_with_its_phase(self):
        self.assertEqual(OPERATION_NAMES, sum(PHASES.values(), ()))
        self.assertEqual(len(OPERATION_NAMES), 55)
        for phase, names in PHASES.items():
            self.assertEqual({REGISTRY[name].phase for name in names}, {phase})
        self.assertEqual((REGISTRY["pocket_2d"].module, REGISTRY["pocket_2d"].cls),
                         ("cadgen._internal.cam.operations.milling_2d", "Pocket2dOperation"))

    def test_an_unknown_name_lists_every_name(self):
        message = value_error(self, get_operation, "pocket")
        self.assertIn("operation: \"pocket\" is not an operation cadgen knows; use one of facing, contour_2d", message)
        self.assertIn("flowline_5x", message)

    def test_an_unbuilt_name_says_its_phase_never_an_import_error(self):
        self.assertIn(f"operation: \"adaptive_3d\" is phase 2, not built yet; built today: {', '.join(built())}",
                      value_error(self, get_operation, "adaptive_3d"))
        self.assertIn("\"lathe_face\" is phase 3, not built yet", value_error(self, get_operation, "lathe_face"))

    def test_an_operation_is_built_once_its_family_module_defines_its_class(self):
        self.assertLessEqual({"facing", "contour_2d", "pocket_2d"}, set(built()))
        before = built()
        with stub_operations("drill"):
            self.assertIn("drill", built())
            self.assertEqual(get_operation("drill").word, "Drill")
        self.assertEqual(built(), before)

    def test_a_plan_error_is_one_of_the_known_kinds(self):
        from cadgen._internal.cam.operations.base import PLAN_ERROR_KINDS, PlanError

        self.assertEqual(PLAN_ERROR_KINDS, ("empty_operation", "too_deep", "unreachable", "tool_too_large",
                                            "tool_too_small", "gouge"))
        self.assertEqual(PlanError("gouge", "operations[0]: x").finding()["type"], "gouge")
        self.assertIn("internal: PlanError kind 'too_big' is not one of empty_operation, too_deep",
                      value_error(self, PlanError, "too_big", "operations[0]: x"))

    def test_the_registry_and_every_built_operation_are_stdlib_only_at_import(self):
        modules = sorted({REGISTRY[name].module for name in built()})
        self.assertEqual(heavy_at_import("cadgen._internal.cam.operations", "cadgen._internal.cam.values", *modules), "")


class SkillDocs(unittest.TestCase):
    """The skill says what the registry says: every built operation runs today, every other one says its phase, and
    every link in the skill resolves."""

    SKILL = Path(__file__).resolve().parents[4] / "skills" / "cam"

    def _rows(self, path: Path, heading: str) -> list[list[str]]:
        text = path.read_text(encoding="utf-8")
        section = re.split(r"\n#{2,3} ", text.split(heading, 1)[1], maxsplit=1)[0]
        lines = [line for line in section.splitlines() if line.startswith("|") and "---" not in line]
        return [[cell.strip() for cell in line.strip().strip("|").split("|")] for line in lines[1:]]  # past the header

    def test_every_built_operation_has_a_runs_today_row_and_every_other_its_phase(self):
        today = set(built())
        # SKILL.md's "Choose the operations": the operations are the `names` of the second column, the status the
        # fourth.
        status_of: dict[str, list[str]] = {}
        for cells in self._rows(self.SKILL / "SKILL.md", "## Choose the operations"):
            for name in re.findall(r"`([a-z0-9_]+)`", cells[1]):
                status_of.setdefault(name, []).append(cells[3])
        self.assertEqual(sorted(status_of), sorted(OPERATION_NAMES))
        for name in OPERATION_NAMES:
            with self.subTest(table="SKILL.md", operation=name):
                if name in today:
                    self.assertTrue(all(status == "**Runs today**" for status in status_of[name]), status_of[name])
                else:
                    self.assertTrue(all(status.startswith(f"phase {REGISTRY[name].phase}, not built yet")
                                        for status in status_of[name]), status_of[name])
        # job-file.md's operations table: each phase 1 name with its word and status.
        table = {}
        for cells in self._rows(self.SKILL / "references" / "job-file.md", "## `operations`"):
            found = re.fullmatch(r"`([a-z0-9_]+)`", cells[0])
            if found and len(cells) == 4 and cells[1] != "plain word":  # past the second table's header
                table[found.group(1)] = (cells[1], cells[2])
        self.assertEqual(sorted(table), sorted(name for name in OPERATION_NAMES if REGISTRY[name].phase == 1))
        for name, (word, status) in table.items():
            with self.subTest(table="job-file.md", operation=name):
                self.assertEqual(word, REGISTRY[name].word)
                self.assertEqual(status, "runs today" if name in today else "phase 1, not built yet")

    def test_every_plan_error_kind_is_in_the_findings_tables(self):
        from cadgen._internal.cam.operations.base import PLAN_ERROR_KINDS

        for path, heading in ((self.SKILL / "SKILL.md", "## Read the findings"),
                              (self.SKILL / "references" / "findings.md", "### Raised today")):
            codes = {re.fullmatch(r"`([a-z_]+)`", cells[0]).group(1) for cells in self._rows(path, heading)}
            with self.subTest(path=path.name):
                self.assertLessEqual(set(PLAN_ERROR_KINDS) | {"rpm_clamped", "feed_clamped"}, codes)

    def test_every_link_resolves_and_every_reference_is_linked(self):
        def anchors(path: Path) -> set[str]:
            heads = re.findall(r"^#+ (.+)$", path.read_text(encoding="utf-8"), re.M)
            return {re.sub(r"[^a-z0-9 _-]", "", head.lower()).replace(" ", "-") for head in heads}

        pages = sorted(self.SKILL.rglob("*.md"))
        linked = set()
        for page in pages:
            for target in re.findall(r"\]\(([^)\s]+)\)", page.read_text(encoding="utf-8")):
                if re.match(r"[a-z]+:", target):
                    continue
                file, _, anchor = target.partition("#")
                resolved = (page.parent / file).resolve() if file else page
                with self.subTest(page=page.name, link=target):
                    self.assertTrue(resolved.is_file(), f"{target} is not a file")
                    if anchor:
                        self.assertIn(anchor, anchors(resolved))
                linked.add(resolved)
        references = {path.resolve() for path in (self.SKILL / "references").glob("*.md")}
        self.assertEqual(sorted(path.name for path in references - linked), [])

    def test_the_description_is_short_and_the_prose_has_no_em_dash(self):
        text = (self.SKILL / "SKILL.md").read_text(encoding="utf-8")
        description = re.search(r"^description: (.*)$", text, re.M).group(1)
        self.assertLess(len(description), 600)
        for path in [*self.SKILL.rglob("*.md"), *self.SKILL.rglob("*.yaml")]:
            with self.subTest(path=path.name):
                self.assertNotIn("\u2014", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
