"""CAM's protocol: the public verbs and their generated CLIs, and the operation registry.

Stdlib only: nothing here opens a part or plans a toolpath.
"""

from __future__ import annotations

import contextlib
import io
import unittest

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
        self.assertIn("operation: 'pocket' is not an operation cadgen knows; use one of facing, contour_2d", message)
        self.assertIn("flowline_5x", message)

    def test_an_unbuilt_name_says_its_phase_never_an_import_error(self):
        self.assertIn("operation: 'adaptive_3d' is phase 2, not built yet; built today: none yet",
                      value_error(self, get_operation, "adaptive_3d"))
        self.assertIn("'facing' is phase 1, not built yet", value_error(self, get_operation, "facing"))

    def test_an_operation_is_built_once_its_family_module_defines_its_class(self):
        with stub_operations("facing"):
            self.assertEqual(built(), ("facing",))
            self.assertEqual(get_operation("facing").word, "Face")
            self.assertIn("built today: facing", value_error(self, get_operation, "pocket_2d"))
        self.assertEqual(built(), ())

    def test_the_registry_and_every_built_operation_are_stdlib_only_at_import(self):
        modules = sorted({REGISTRY[name].module for name in built()})
        self.assertEqual(heavy_at_import("cadgen._internal.cam.operations", "cadgen._internal.cam.values", *modules), "")


if __name__ == "__main__":
    unittest.main()
