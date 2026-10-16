"""Behavioral parity for the shared Viewer and Python snapshot display schema."""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from tests.python.support.paths import add_repo_path, repo_path

add_repo_path("packages/cadgen/src")
from cadgen.snapshot_core import (
    CAMERA_OPTION_KEYS, DISPLAY_GROUP_KEYS, DISPLAY_MODES, DISPLAY_OPTION_KEYS,
    DISPLAY_MODE_SETTING_KEYS, PART_COLOR_MODES, SnapshotError,
    load_display_option, normalize_common_job, validate_camera_option,
    validate_display_settings_values,
)

VIEW = repo_path("packages/core/src/common/viewSettings.js")
SCENE = repo_path("packages/core/src/common/sceneSettings.js")
CAMERA = repo_path("packages/core/src/common/camera.js")
VALIDATOR = repo_path("packages/core/src/common/snapshotJobValidation.js")


def javascript(expression: str, payload=None):
    script = f"""
import fs from "node:fs";
import * as view from {json.dumps(VIEW.as_uri())};
import * as scene from {json.dumps(SCENE.as_uri())};
import {{ CAMERA_SPEC_KEYS, normalizeCameraSpec }} from {json.dumps(CAMERA.as_uri())};
import {{ validateSnapshotRenderJob }} from {json.dumps(VALIDATOR.as_uri())};
const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const accepts = fn => {{ try {{ fn(); return true; }} catch {{ return false; }} }};
console.log(JSON.stringify({expression}));
"""
    completed = subprocess.run(["node", "--input-type=module", "-e", script],
                               input=json.dumps(payload), text=True, capture_output=True,
                               check=True, cwd=repo_path())
    return json.loads(completed.stdout)


def python_accepts(values, validator):
    result = []
    for value in values:
        try:
            validator(value)
        except SnapshotError:
            result.append(False)
        else:
            result.append(True)
    return result


class SharedDisplayContractParityTests(unittest.TestCase):
    def test_exported_closed_key_sets_and_enums_match(self):
        expected = {
            "VIEW_MODE_VALUES": DISPLAY_MODES,
            "VIEW_SETTINGS_KEYS": DISPLAY_OPTION_KEYS,
            "VIEW_COLOR_MODE_VALUES": PART_COLOR_MODES,
            **{f"VIEW_{name.upper()}_KEYS": keys for name, keys in DISPLAY_GROUP_KEYS.items()
               if name not in {"clip", "exploded"}},
        }
        actual = javascript("Object.fromEntries(cases.map(key => [key, view[key]]))", list(expected))
        for name, values in expected.items():
            with self.subTest(export=name):
                self.assertEqual(set(actual[name]), set(values))

        actual_modes = javascript("view.VIEW_MODE_SETTING_KEYS")
        self.assertEqual({key: set(value) for key, value in actual_modes.items()}, DISPLAY_MODE_SETTING_KEYS)

    def test_display_values_match_in_both_languages(self):
        def setting(mode, **values):
            return {"modes": {mode: values}}

        valid = [
            {}, *({"mode": mode} for mode in DISPLAY_MODES), {"appearance": "dark"},
            *({group: {}} for group in DISPLAY_GROUP_KEYS),
            {"camera": {"projection": "perspective", "focalLength": 20}},
            {"camera": {"projection": "orthographic", "focalLength": 200}},
            setting("solid", flat=True, parts={"colorMode": "by-part", "colors": ["#aBc", "#123456"]}),
            setting("solid", edges={"enabled": False, "color": "#abc"}, grid={"enabled": True, "density": 4}),
            setting("render", lighting={"quality": "preview", "exposure": -5, "rotation": -180, "size": 0.25, "fill": 0}),
            setting("render", lighting={"quality": "final", "exposure": 5, "rotation": 180, "size": 3, "fill": 1}),
            setting("render", background={"opacity": 0.35}, floor={"placement": "origin", "finish": "glossy", "enabled": False}),
            setting("xray", opacity=0, parts={"colorMode": "single", "color": "#fff"}),
            setting("xray", opacity=1, edges={"color": "#fff"}, axes={"enabled": True, "opacity": 0.5}),
            setting("wireframe", grid={"density": 0.25}, edges={"color": "#fff"}),
            {"clip": {"axis": "z", "at": -12.5, "invert": True}}, {"clip": {"enabled": False, "at": 3}},
            {"exploded": {"enabled": True, "amount": 1}},
        ]
        invalid = [
            None, [], True, "solid", {"render": {}}, {"guides": {}}, {"partColor": {}},
            *({old: {}} for old in ("surfaces", "edges", "lighting", "background", "floor", "grid", "axes")),
            *({"mode": value} for value in (None, [], {}, True, "", "SOLID", "grid", "hidden_line")),
            *({"appearance": value} for value in (None, [], {}, True, "system")),
            *({group: value} for group in DISPLAY_GROUP_KEYS for value in (None, [], True, "off", {"typo": 1}, {"enabled": 0})),
            *({"camera": {"focalLength": value}} for value in (None, "50", True, 19.9, 200.1)),
            {"camera": {"enabled": True}}, {"modes": []}, {"modes": {"grid": {}}},
            setting("solid", lighting={}), setting("render", grid={}), setting("wireframe", parts={}),
            setting("solid", flat=1), setting("xray", opacity=True), setting("xray", opacity=1.01),
            setting("xray", edges={"enabled": False}), setting("render", background={"enabled": True}),
            * (setting("solid", parts={"colors": value}) for value in (None, [], "#fff", ["white"], ["#fff"] * 51)),
            setting("solid", parts={"colorMode": "by_part"}), setting("solid", edges={"visibility": "all"}),
            setting("render", lighting={"quality": None}), setting("render", lighting={"exposure": -5.01}),
            setting("render", lighting={"rotation": -180.1}), setting("render", lighting={"size": 0.24}),
            setting("render", lighting={"fill": "0.5"}), setting("render", floor={"placement": "auto"}),
            setting("render", floor={"finish": "shiny"}), setting("render", floor={"finish": True}),
            setting("render", background={"opacity": -0.1}), setting("render", background={"color": "white"}),
            setting("hidden-line", grid={"density": 4.01}), setting("solid", axes={"enabled": 1}),
            {"clip": {"axis": "w"}}, {"clip": {"offset": 0.5}}, {"clip": {"offsets": {"x": 0.5}}},
            {"exploded": {"amount": 1.01}},
            *({"clip": {"at": value}} for value in (None, "3", True, [])),
        ]
        cases = valid + invalid
        python = python_accepts(cases, lambda value: validate_display_settings_values(value, source_label="parity"))
        shared = javascript("cases.map(value => accepts(() => view.normalizeViewSettings(value)))", cases)
        for index, value in enumerate(cases):
            with self.subTest(value=value):
                self.assertEqual(index < len(valid), python[index])
                self.assertEqual(python[index], shared[index])

    def test_python_packets_resolve_to_the_same_mode_and_overrides(self):
        cases = [
            {}, {"mode": "render"}, {"mode": "xray"}, {"mode": "hidden-line"}, {"mode": "wireframe"},
            {"mode": "solid", "modes": {"render": {"lighting": {"exposure": 1}}}},
            {"mode": "render", "modes": {"render": {"floor": {"enabled": False}, "background": {"opacity": 0.35}}}},
            {"mode": "solid", "camera": {"projection": "perspective"}, "modes": {"solid": {"grid": {"enabled": True, "color": "#abc"}}}},
            {"mode": "render", "modes": {"render": {"floor": {"finish": "glossy", "placement": "origin"}}}},
        ]
        packets = [normalize_common_job({"input": "part.step", "outputs": [{"path": "out.png"}], "display": value},
                                      mode="view", resolved_cwd=Path("."), timestamp="parity")["display"] for value in cases]
        expected = javascript("cases.map(value => view.resolveViewSettings(value))", cases)
        actual = javascript("cases.map(value => view.resolveViewSettings(value))", packets)
        self.assertEqual(expected, actual)
        self.assertEqual("perspective", actual[1]["camera"]["projection"])
        self.assertEqual("orthographic", actual[0]["camera"]["projection"])
        self.assertFalse(actual[5]["lighting"]["enabled"])
        self.assertFalse(actual[6]["floor"]["enabled"])
        self.assertEqual(0.35, actual[6]["background"]["opacity"])
        self.assertTrue(actual[7]["grid"]["enabled"])
        self.assertEqual("#aabbcc", actual[7]["grid"]["color"])
        self.assertEqual("lowest", actual[1]["floor"]["placement"])
        self.assertEqual("glossy", actual[8]["floor"]["finish"])
        self.assertEqual("origin", actual[8]["floor"]["placement"])
        placements = javascript("cases.map(value => scene.resolveViewSceneSettings({display:value}).render.configuration.backdrop.groundPlacement)", [packets[1], packets[8]])
        self.assertEqual(["lowest", "origin"], placements)

    def test_public_camera_keys_match_the_shared_pose_fields(self):
        self.assertEqual(set(javascript('CAMERA_SPEC_KEYS.filter(key => !["projection", "focalLength"].includes(key))')), set(CAMERA_OPTION_KEYS))

    def test_camera_pose_and_projection_have_one_home(self):
        cases = [
            {"preset": "front"}, {"orthographicHalfHeight": 12},
            {"position": [1, 2, 3], "target": [0, 0, 0]},
            {"projection": "orthographic"}, {"focalLength": 85},
            {"position": [1, 2, "3"]}, {"up": None}, {"direction": [0, 0, 0]},
            {"zoom": "2"}, {"orthographicHalfHeight": False}, {"typo": 1},
        ]
        python = python_accepts(cases, lambda value: validate_camera_option(value, source_label="parity"))
        shared = javascript("cases.map(camera => accepts(() => { validateSnapshotRenderJob({camera}); normalizeCameraSpec(camera, {strict:true}); }))", cases)
        self.assertEqual([True] * 3 + [False] * 8, python)
        self.assertEqual(python, shared)


if __name__ == "__main__":
    unittest.main()
