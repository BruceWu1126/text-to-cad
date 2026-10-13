"""The job file: one valid job parses the same from every form, and each kind of bad job names its field.

Stdlib only. No operation is built yet, so the four the jobs name are stand-ins (``stub_operations``),
registered the way a built one is.
"""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from tests.python.support.cam_fixtures import heavy_at_import, stub_operations, value_error

from cadgen._internal.cam.job import Holder, parse_job

JOB = {
    "machine": {"kind": "mill", "axes": 3, "post": "grbl", "travel_mm": [800, 600, 120], "max_rpm": 24000,
                "max_feed_mm_min": 5000, "units": "mm"},
    "material": "aluminum-6061",
    "stock": {"type": "box", "margin_mm": [2, 2, 1]},
    "setups": [{"id": "top", "direction": "+Z", "origin": "stock_top_center",
                "fixture": {"type": "vise", "jaw_height_mm": 20}}],
    "tools": [
        {"id": "T1", "type": "flat_endmill", "diameter_mm": 6, "flutes": 3, "flute_length_mm": 18, "stickout_mm": 30,
         "holder": {"diameter_mm": 32, "length_mm": 40}, "material": "carbide"},
        {"id": "T2", "type": "drill", "diameter_mm": 5},
        {"id": "T3", "type": "tap", "diameter_mm": 6, "pitch_mm": 1},
    ],
    "operations": [
        {"op": "facing", "setup": "top", "tool": "T1", "stepdown_mm": 0.5},
        {"op": "pocket_2d", "setup": "top", "tool": "T1", "features": ["#o1.f12"], "stock_to_leave_mm": 0.2},
        {"op": "drill", "tool": "T2", "features": ["#o1.f20", "#o1.f21"], "depth_mm": 8},
    ],
    "view": {"show": {"rapids": True, "stock": "final"}},
}


def changed(path: str, value=None, *, delete: bool = False) -> dict:
    """JOB with the value at a dotted path (``operations.1.tool``) replaced, or deleted."""
    job = copy.deepcopy(JOB)
    *parents, last = path.split(".")
    node = job
    for key in parents:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = int(last) if isinstance(node, list) else last
    if delete:
        del node[last]
    else:
        node[last] = value
    return job


class ValidJob(unittest.TestCase):
    def test_a_job_parses_the_same_from_a_dict_a_file_and_inline_json(self):
        with stub_operations(), tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "job.json"
            path.write_text(json.dumps(JOB), encoding="utf-8")
            job = parse_job(JOB)
            self.assertEqual(parse_job(path), job)
            self.assertEqual(parse_job(str(path)), job)
            self.assertEqual(parse_job(json.dumps(JOB)), job)
        self.assertEqual((job.machine.post, job.machine.travel_mm, job.material.name), ("grbl", (800.0, 600.0, 120.0), "aluminum-6061"))
        self.assertEqual(job.tool("T1").holder, Holder(32.0, 40.0))
        self.assertEqual((job.tool("T3").number, job.tool("T3").size), (3, {"pitch_mm": 1.0}))
        facing, pocket, drill = job.operations
        self.assertEqual((facing.params, facing.features, facing.all), ({"stepdown_mm": 0.5}, (), False))
        self.assertEqual((pocket.features, pocket.stock_to_leave_mm), (("#o1.f12",), 0.2))
        self.assertEqual((drill.setup, drill.params), ("top", {"depth_mm": 8.0}))  # the one setup, unnamed
        self.assertEqual(job.view, {"show": {"rapids": True, "stock": "final"}})
        self.assertEqual(job.source, JOB)

    def test_post_overrides_the_machines_and_stock_from_reads_beside_the_job_file(self):
        with stub_operations(), tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "blank.step").write_text("", encoding="utf-8")
            path = Path(tmp) / "job.json"
            path.write_text(json.dumps(changed("stock", {"from": "blank.step"})), encoding="utf-8")
            job = parse_job(path, post="haas")
            self.assertEqual(job.stock.path, (Path(tmp) / "blank.step").resolve())
        self.assertEqual(job.machine.post, "haas")

    def test_the_parser_is_stdlib_only_at_import(self):
        self.assertEqual(heavy_at_import("cadgen._internal.cam.job"), "")


class BadJobs(unittest.TestCase):
    CASES = (
        ("no job", None, "a job is required: --job job.json"),
        ("unknown top-level key", changed("units", "mm"), "job: unknown keys ['units']; a job takes machine, material"),
        ("unknown machine key", changed("machine.spindle", 1), "machine: unknown keys ['spindle']"),
        ("wrong machine kind", changed("machine.kind", "drill_press"), 'machine.kind: "drill_press" is not one of mill, router'),
        ("wrong axes", changed("machine.axes", 6), "machine.axes: a mill has 3, 4 or 5 axes, got 6"),
        ("missing post", changed("machine.post", delete=True),
         "machine.post: required, and never guessed: a wrong post can crash a machine. Name the controller's dialect, "
         "one of grbl, linuxcnc, fanuc, haas or mach3 for a mill"),
        ("unknown post", changed("machine.post", "marlin"), 'machine.post: "marlin" is not a post cadgen knows'),
        ("post for another machine", changed("machine.post", "fanuc_lathe"), "machine.post: fanuc_lathe writes for a lathe, not a mill"),
        ("5-axis post on a 3-axis mill", changed("machine.post", "haas_umc"),
         "machine.post: haas_umc writes for a 5-axis mill, and this one has 3 axes"),
        ("wrong units", changed("machine.units", "cm"), 'machine.units: "cm" is not one of mm or inch'),
        ("unknown material", changed("material", "unobtainium"), 'material: "unobtainium" is not one of aluminum-6061'),
        ("custom material without numbers", changed("material", {"name": "bronze"}),
         "material: 'bronze' is not in the table, so give its sfm and chipload_mm"),
        ("stock a lathe cannot hold", changed("machine", {"kind": "lathe", "post": "fanuc_lathe"}),
         "stock.type: a lathe starts from cylinder or from, not box"),
        ("negative margin", changed("stock.margin_mm", [2, -1, 1]), "stock.margin_mm[1]: must be 0 or more, got -1"),
        ("unknown fixture", changed("setups.0.fixture.type", "clamp"), 'setups[0].fixture.type: "clamp" is not one of vise'),
        ("unknown origin", changed("setups.0.origin", "machine_home"), 'setups[0].origin: "machine_home" is not one of stock_top_center'),
        ("direction off the axes", changed("setups.0.direction", "Z"), 'setups[0].direction: "Z" is not one of +X'),
        ("unknown tool type", changed("tools.0.type", "router_bit"), 'tools[0].type: "router_bit" is not one of flat_endmill'),
        ("tool id", changed("tools.1.id", "drill5"), 'tools[1].id: name the tool by its slot in the machine\'s tool table, like "T1"'),
        ("tool missing its size", changed("tools.2.pitch_mm", delete=True), "tools[2].pitch_mm: a tap needs its pitch_mm"),
        ("tool key of another type", changed("tools.1.pitch_mm", 1), "tools[1]: unknown keys ['pitch_mm']; a drill takes"),
        ("negative tool size", changed("tools.0.diameter_mm", -6), "tools[0].diameter_mm: must be > 0, got -6"),
        ("unknown operation", changed("operations.1.op", "pocket"), "operations[1].op: 'pocket' is not an operation cadgen knows"),
        ("unbuilt operation", changed("operations.1.op", "adaptive_3d"), "operations[1].op: 'adaptive_3d' is phase 2, not built yet"),
        ("unknown operation key", changed("operations.1.stepover", 0.4),
         "operations[1]: unknown keys ['stepover']; pocket_2d takes op, setup, tool"),
        ("dangling tool", changed("operations.2.tool", "T9"), 'operations[2].tool: "T9" is not a tool in this job; tools: T1, T2 or T3'),
        ("dangling setup", changed("operations.0.setup", "bottom"), 'operations[0].setup: "bottom" is not a setup in this job'),
        ("tool type mismatch", changed("operations.1.tool", "T3"),
         "operations[1].tool: T3 is a tap, and pocket_2d cuts with a flat_endmill, bull_endmill or ball_endmill"),
        ("no target", changed("operations.1.features", delete=True), "operations[1]: name what pocket_2d cuts: features or region"),
        ("a target it does not take", changed("operations.0.features", ["#o1.f3"]), "operations[0].features: facing takes all or region, not features"),
        ("not a feature selector", changed("operations.1.features", ["#o1"]), 'operations[1].features[0]: "#o1" is an occurrence'),
        ("negative depth", changed("operations.2.depth_mm", -3), "operations[2].depth_mm: must be > 0, got -3"),
        ("negative stock to leave", changed("operations.1.stock_to_leave_mm", -0.1), "operations[1].stock_to_leave_mm: must be 0 or more"),
        ("wrong coolant", changed("operations.1.coolant", "oil"), 'operations[1].coolant: "oil" is not one of off, flood, mist or air'),
        ("unknown view key", changed("view.show.paths", True), "view.show: unknown keys ['paths']"),
    )

    def test_each_bad_job_names_its_field_and_what_was_wrong(self):
        with stub_operations():
            for label, job, fragment in self.CASES:
                with self.subTest(label):
                    self.assertIn(fragment, value_error(self, parse_job, job))

    def test_a_post_override_is_checked_like_the_machines(self):
        with stub_operations():
            self.assertIn("--post: fanuc_wire writes for a wire_edm, not a mill", value_error(self, parse_job, JOB, post="fanuc_wire"))


if __name__ == "__main__":
    unittest.main()
