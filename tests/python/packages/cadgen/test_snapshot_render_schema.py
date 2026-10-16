"""Unified display and technical output are closed schemas."""

from __future__ import annotations

import inspect
import tempfile
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen.snapshot_core import (  # noqa: E402
    DISPLAY_APPEARANCES,
    DISPLAY_MODES,
    RENDER_QUALITY_IDS,
    SUPPORTED_OUTPUT_SETTINGS_KEYS,
    SnapshotError,
    load_display_option,
    normalize_common_job,
)


def normalize(**settings: object) -> dict[str, object]:
    return normalize_common_job(
        {"input": "part.step", "outputs": [{"path": "out.png"}], **settings},
        mode="view",
        resolved_cwd=Path("."),
        timestamp="20260907-000000",
    )


class RenderDisplaySchemaTest(unittest.TestCase):
    def test_presets_and_appearance_are_closed(self):
        self.assertEqual({"solid", "render", "xray", "hidden-line", "wireframe"}, set(DISPLAY_MODES))
        self.assertEqual({"light", "dark"}, set(DISPLAY_APPEARANCES))
        self.assertEqual({"preview", "final"}, set(RENDER_QUALITY_IDS))
        for mode in DISPLAY_MODES:
            self.assertEqual({"mode": mode}, load_display_option(mode, cwd=Path(".")))
        for invalid in ("grid", "shaded", "transparent", "hidden_edges", "unshaded", None, True, [], {}):
            with self.subTest(mode=invalid), self.assertRaises(SnapshotError):
                load_display_option({"mode": invalid}, cwd=Path("."))
        for invalid in ("system", "LIGHT", None, False, [], {}):
            with self.subTest(appearance=invalid), self.assertRaises(SnapshotError):
                load_display_option({"appearance": invalid}, cwd=Path("."))

    def test_preset_names_do_not_depend_on_existing_output_directories(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "render").mkdir()
            self.assertEqual({"mode": "render"}, load_display_option("render", cwd=root))

    def test_shared_and_per_mode_settings_stay_sparse(self):
        display = {
            "mode": "solid", "appearance": "dark",
            "camera": {"projection": "perspective", "focalLength": 85},
            "modes": {
                "solid": {"flat": True, "parts": {"colorMode": "by-part", "colors": ["#abc"]},
                          "edges": {"enabled": False}, "grid": {"density": 2}},
                "render": {"lighting": {"quality": "preview", "exposure": -1.25},
                           "background": {"opacity": 0.4}, "floor": {"enabled": False, "finish": "glossy"}},
                "xray": {"opacity": 0.3, "parts": {"colorMode": "single", "color": "#123456"}},
            },
            "clip": {"enabled": True, "axis": "z", "at": 0.5},
            "exploded": {"enabled": True, "amount": 0.5},
        }
        self.assertEqual(display, load_display_option(display, cwd=Path(".")))
        with tempfile.TemporaryDirectory() as tmp:
            import json
            root = Path(tmp)
            (root / "display.json").write_text(json.dumps(display), encoding="utf-8")
            self.assertEqual(display, load_display_option("display.json", cwd=root))

    def test_old_nested_surfaces_are_rejected(self):
        for key in ("render", "guides", "partColor", "surfaces", "edges", "lighting", "floor", "grid"):
            with self.subTest(key=key), self.assertRaises(SnapshotError):
                load_display_option({key: {}}, cwd=Path("."))

    def test_default_job_is_light_solid_without_overriding_groups(self):
        self.assertEqual({"mode": "solid", "appearance": "light"}, normalize()["display"])
        self.assertEqual({"mode": "render", "appearance": "light"}, normalize(display={"mode": "render"})["display"])
        self.assertEqual({"mode": "solid", "appearance": "dark", "modes": {"render": {"background": {"opacity": 0.3}}}},
                         normalize(display={"appearance": "dark", "modes": {"render": {"background": {"opacity": 0.3}}}})["display"])

    def test_animation_and_output_capture_controls_remain_composable(self):
        job = normalize(
            camera={"preset": "front"}, display={"mode": "render"},
            animation={"clip": "spin", "time": 0.5},
            output={"sizeProfile": "diagnostic", "viewLabels": True},
        )
        self.assertEqual(job["camera"], {"preset": "front"})
        self.assertEqual(job["display"]["mode"], "render")
        self.assertEqual(job["animation"], {"clip": "spin", "time": 0.5})
        self.assertEqual(job["output"], {"sizeProfile": "diagnostic", "viewLabels": True})

    def test_public_api_has_one_display_surface_and_no_render_parameter(self):
        from cadgen import step
        signature = inspect.signature(step.snapshot)
        self.assertIn("display", signature.parameters)
        self.assertIn("camera", signature.parameters)
        self.assertNotIn("render", signature.parameters)

    def test_output_is_closed(self):
        output = {
            "sizeProfile": "diagnostic", "padding": 0.1,
            "viewLabels": True, "tightFrame": True, "transparent": True, "renderScale": 2,
        }
        self.assertEqual(set(output), set(SUPPORTED_OUTPUT_SETTINGS_KEYS))
        normalize(output=output)
        with self.assertRaisesRegex(SnapshotError, "output has unknown key"):
            normalize(output={"pixels": 2})
        # One name per setting: the old second spelling of `padding` names its replacement.
        with self.assertRaisesRegex(SnapshotError, r"output\.paddingPercent was removed; use output\.padding"):
            normalize(output={"paddingPercent": 0.1})


if __name__ == "__main__":
    unittest.main()
