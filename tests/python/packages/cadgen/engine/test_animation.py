"""Animations (``animation()``): a GLB's own channels, authored in a model's body, refused by name
where they name nothing or collide, never written into the document, and baked into the GLB the
model declares -- STEP, LINEAR and CUBICSPLINE samplers on translation, rotation and scale,
``pose`` through the mechanism's forward kinematics -- within the tolerance of what the channels
say, their CUBICSPLINE tangents reproducing the curve under an independent Hermite (scipy's).

The two GLBs the core suite plays and validates (``packages/core/src/lib/render/fixtures``:
``animation.glb``, ``motion.glb``) are this module's ``CHANNELS`` and ``MOTION`` baked; a test
holds them to what the baker writes now. ``CADGEN_WRITE_FIXTURES=1`` writes them afresh."""

from __future__ import annotations

import json
import math
import os
import shutil
import struct
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from typing import Any

import numpy as np

from cadgen.engine import build as engine_build
from cadgen.engine import document, mechanism
from tests.python.support.paths import REPO_ROOT

FIXTURES = REPO_ROOT / "packages/core/src/lib/render/fixtures"

# A box whose lid hinges, a latch, a knob and two feet: every path, every interpolation, and a pose.
CHANNELS = '''
import math
import build123d as bd
from cadgen import animation, glb, mechanism_state, xbf

HINGE = bd.Axis((-40, 0, 30), (0, -1, 0))


@xbf("XBF/channels.xbf")
@glb("channels.glb")
def channels():
    body = bd.Box(80, 50, 30, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    lid = bd.Pos(0, 0, 30) * bd.Box(80, 50, 6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    latch = bd.Pos(42, 0, 30) * bd.Cylinder(4, 8)
    knob = bd.Pos(0, 27, 15) * bd.Box(10, 4, 10)
    feet = [bd.Pos(x, 0, -4) * bd.Box(10, 40, 4, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
            for x in (-30, 30)]
    body.label, lid.label, latch.label, knob.label = "body", "lid", "latch", "knob"
    feet[0].label, feet[1].label = "foot_1", "foot_2"
    bd.RevoluteJoint("hinge", body, axis=HINGE, angular_range=(0, 110))
    bd.RigidJoint("hinge", lid, bd.Plane(origin=HINGE.position, z_dir=HINGE.direction).location)
    body.joints["hinge"].connect_to(lid.joints["hinge"], angle=0)
    box = bd.Compound(children=[body, lid, latch, knob, bd.Compound(children=feet, label="feet")], label="channels")
    mechanism_state(box, "open", hinge=100)
    half = (0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4))
    show = animation(box, "showcase", duration=2)
    show.pose(at={0: {}, 1: "open", 2: {}})
    show.channel("latch", path="translation", keys=[(0, (0, 0, 0)), (1, (20, 0, 0)), (2, (0, 0, 0))],
                 interpolation="LINEAR")
    show.rotation("latch", axis=(0, 0, 1), turns=1, interpolation="LINEAR")
    show.channel("knob", path="rotation", keys=[(0, (0, 0, 0, 1)), (1, half)], interpolation="STEP")
    show.hide("knob", at=1.5)
    show.scale("feet/foot_1", to=0.5, interpolation="LINEAR")
    show.stagger("feet/*", by=(0, 0, -10), step=0.5)
    show.channel("body", path="scale", keys=[(0, 1), (2, 1.2)])
    {more}
    return box
'''

# The same box with a motion alone: an AP242 configuration sequence, which every GLB of it carries.
MOTION = '''
import build123d as bd
from cadgen import configuration_sequence, glb, mechanism_state, xbf

HINGE = bd.Axis((-40, 0, 30), (0, -1, 0))


@xbf("XBF/motion.xbf")
@glb("motion.glb")
def motion_box():
    body = bd.Box(80, 50, 30, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    lid = bd.Pos(0, 0, 30) * bd.Box(80, 50, 6, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    body.label, lid.label = "body", "lid"
    bd.RevoluteJoint("hinge", body, axis=HINGE, angular_range=(0, 110))
    bd.RigidJoint("hinge", lid, bd.Plane(origin=HINGE.position, z_dir=HINGE.direction).location)
    body.joints["hinge"].connect_to(lid.joints["hinge"], angle=0)
    box = bd.Compound(children=[body, lid], label="motion_box")
    mechanism_state(box, "open", hinge=100)
    return configuration_sequence(box, "open_close", [(0, {"hinge": 0}), (1, "open"), (2, {"hinge": 0})])
'''


def _glb(path: Path) -> tuple[dict, Any]:
    """A GLB's JSON chunk, and a reader of its accessors (as float64 rows)."""
    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    gltf = json.loads(data[20:20 + length])
    blob = data[28 + length:28 + length + struct.unpack_from("<I", data, 20 + length)[0]]

    def read(at: int) -> np.ndarray:
        accessor = gltf["accessors"][at]
        view = gltf["bufferViews"][accessor["bufferView"]]
        width = {"SCALAR": 1, "VEC3": 3, "VEC4": 4}[accessor["type"]]
        return np.frombuffer(blob, "<f4", accessor["count"] * width, view.get("byteOffset", 0)).reshape(
            accessor["count"], width).astype(float)

    return gltf, read


def _sample(times: np.ndarray, rows: np.ndarray, interpolation: str, t: float, rotation: bool) -> np.ndarray:
    """A glTF sampler at ``t``, as the specification (and three.js) evaluates it."""
    if interpolation == "CUBICSPLINE":
        keys = rows.reshape(len(times), 3, -1)
        values = keys[:, 1]
    else:
        values = rows
    if t <= times[0]:
        return values[0]
    if t >= times[-1]:
        return values[-1]
    at = int(np.searchsorted(times, t, side="right")) - 1
    span = times[at + 1] - times[at]
    s = (t - times[at]) / span
    if interpolation == "STEP":
        return values[at]
    if interpolation == "LINEAR":
        a, b = values[at], values[at + 1]
        if not rotation:
            return a + s * (b - a)
        dot = float(np.dot(a, b))
        b, dot = (-b, -dot) if dot < 0 else (b, dot)
        theta = math.acos(min(1.0, dot))
        return a if theta < 1e-9 else (math.sin((1 - s) * theta) * a + math.sin(s * theta) * b) / math.sin(theta)
    found = ((2 * s ** 3 - 3 * s ** 2 + 1) * keys[at, 1] + span * (s ** 3 - 2 * s ** 2 + s) * keys[at, 2]
             + (-2 * s ** 3 + 3 * s ** 2) * keys[at + 1, 1] + span * (s ** 3 - s ** 2) * keys[at + 1, 0])
    return found / np.linalg.norm(found) if rotation else found


def _rotation(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _turn(q_a: np.ndarray, q_b: np.ndarray) -> float:
    return 2 * math.acos(min(1.0, abs(float(np.dot(q_a / np.linalg.norm(q_a), q_b / np.linalg.norm(q_b))))))


def _smooth(s: float) -> float:
    s = min(1.0, max(0.0, s))
    return s * s * (3 - 2 * s)


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        from cadgen.engine.kernel import memo

        self.folder = Path(tempfile.mkdtemp(prefix="engine-animation-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        memo.install()
        self.addCleanup(memo.clear)

    def build(self, source: str, name: str, *, ok: bool = True, stem: str = "model") -> dict:
        script = self.folder / f"{stem}.py"
        script.write_text(textwrap.dedent(source), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        [report] = engine_build.build(script, [name], cwd=self.folder)
        self.assertEqual(report["ok"], ok, report.get("error") or report)
        return report

    def channels(self, more: str = "pass", ok: bool = True) -> dict:
        return self.build(CHANNELS.replace("{more}", more), "channels", ok=ok)


class AuthoringTest(unittest.TestCase):
    def test_a_builder_call_says_what_is_wrong_with_it(self) -> None:
        import build123d as bd

        from cadgen import animation

        box = bd.Box(1, 1, 1)
        show = animation(box, "show", duration=2)
        for call, error in (
                (lambda: animation("box", "a", duration=1), "takes the assembly"),
                (lambda: animation(box, "a", duration=0), "duration is a number of seconds above 0"),
                (lambda: show.translation("a", by=(1, 0, 0), end=3), "end 3 s is outside the animation's 0 to 2 s"),
                (lambda: show.translation("a", by=(1, 0, 0), to=(0, 0, 0)), "takes by= or to=: one of them"),
                (lambda: show.translation("a", by=(1, 0, 0), interpolation="cubic"),
                 "interpolation is one of CUBICSPLINE, LINEAR, STEP, not 'cubic'"),
                (lambda: show.translation("a", by=(1, 0, 0), start=1, end=1), "end 1 s is not after start 1 s"),
                (lambda: show.rotation("a", axis=(0, 0, 0), by=10), "has no direction"),
                (lambda: show.rotation("a", axis=(0, 0, 1), by=10, turns=1), "turns=, by= or to=: one of them"),
                (lambda: show.channel("a", path="weights", keys=[(0, 1)]), "path is one of translation, rotation, scale"),
                (lambda: show.channel("a", path="scale", keys=[(1, 1), (0.5, 2)]), "is not after the one before it"),
                (lambda: show.channel("a", path="rotation", keys=[(0, (0, 0, 0, 2))]), "is not a unit quaternion"),
                (lambda: show.channel("a", path="translation", keys=[]), "has no key"),
                (lambda: show.scale("a", to=-1), "a factor of 0 or more"),
                (lambda: show.hide("a", at=5), "at 5 s is outside"),
                (lambda: show.pose(at={3: "open"}), "a time 3 s is outside"),
                (lambda: show.stagger("a", by=(0, 0, 1), step=1, start=1.5), "the first move's end 2.5 s is outside"),
                (lambda: show.explode("a", distance=5), "direction= or radial=: one of them"),
                (lambda: show.translation(3, by=(0, 0, 1)), "parts is a name, a path or a glob")):
            with self.subTest(error=error), self.assertRaisesRegex((TypeError, ValueError), error):
                call()
        self.assertIs(show.rotation("a", axis=(0, 0, 1), turns=1).hide("a", at=1), show, "calls chain")


class HarvestTest(_Folder):
    def test_what_an_animation_names_wrong_fails_the_build_by_name(self) -> None:
        for more, error in (
                ('show.translation("lidd", by=(1, 0, 0))', "channels has no part or assembly lidd; nearest: lid"),
                ('show.translation("wheel*", by=(1, 0, 0))', "the glob 'wheel\\*' matches no part or assembly"),
                ('show.channel("latch", path="translation", keys=[(1.5, (0, 0, 0)), (2, (1, 0, 0))])',
                 "both drive the translation of channels/latch"),
                ('show.pose(at={1.5: "shut"})', "no configuration 'shut'"),
                ('show.translation("knob", by=(0, 0, 5), start=0, end=1)',
                 "the translation of channels/knob is STEP by .* and CUBICSPLINE by"),
                ('show.rotation("latch", axis=(1, 0, 0), to=10, start=1, end=2)',
                 "both drive the rotation of channels/latch"),
        ):
            with self.subTest(more=more):
                report = self.channels(more, ok=False)
                self.assertRegex(report["error"], error)

    def test_an_animation_named_as_a_configuration_sequence_is_refused(self) -> None:
        report = self.build(MOTION.replace('    return configuration_sequence(box', '    from cadgen import animation\n'
                                           '    animation(box, "open_close", duration=1).translation("lid", by=(0, 0, 1))\n'
                                           '    return configuration_sequence(box'), "motion_box", ok=False)
        self.assertIn("animation 'open_close' has the name of a configuration sequence of the mechanism", report["error"])

    def test_the_document_is_the_same_bytes_with_an_animation_or_without(self) -> None:
        import build123d as bd

        from cadgen import animation

        def shape(animated: bool) -> Any:
            base, arm = bd.Box(10, 10, 10), bd.Pos(20, 0, 0) * bd.Box(5, 5, 5)
            base.label, arm.label = "base", "arm"
            assembly = bd.Compound(children=[base, arm], label="pair")
            if animated:
                animation(assembly, "slide", duration=1).translation("arm", by=(30, 0, 0)).hide("base", at=1)
            return assembly

        written = []
        for animated in (False, True):
            tree = document.collect(shape(animated), "pair")
            self.assertEqual(tree.animations is not None, animated)
            path = self.folder / f"pair-{animated}.xbf"
            document.save(document.to_xcaf(tree, document.Kept()), path, compare=False)
            written.append(path.read_bytes())
        self.assertEqual(written[0], written[1], "the xbf holds no animation")


class GlbTest(_Folder):
    def test_a_declared_glb_bakes_every_path_and_interpolation_within_the_tolerance(self) -> None:
        self.channels()
        out = self.folder / "channels.glb"
        gltf, read = _glb(out)
        names = [node["name"] for node in gltf["nodes"]]
        [showcase] = [animation for animation in gltf["animations"] if animation["name"] == "showcase"]
        self.assertEqual(showcase["extras"], {"cadgen": {"duration": 2.0}})
        found = {}
        for channel in showcase["channels"]:
            sampler = showcase["samplers"][channel["sampler"]]
            times = read(sampler["input"])[:, 0]
            self.assertEqual((times[0], times[-1]), (0.0, 2.0), "every sampler runs the whole animation")
            found[(names[channel["target"]["node"]], channel["target"]["path"])] = (
                sampler["interpolation"], times, read(sampler["output"]))
        self.assertEqual({key: value[0] for key, value in found.items()}, {
            ("lid", "translation"): "CUBICSPLINE", ("lid", "rotation"): "CUBICSPLINE",
            ("latch", "translation"): "LINEAR", ("latch", "rotation"): "LINEAR",
            ("knob", "rotation"): "STEP", ("knob", "scale"): "STEP",
            ("feet/foot_1", "translation"): "CUBICSPLINE", ("feet/foot_1", "scale"): "LINEAR",
            ("feet/foot_2", "translation"): "CUBICSPLINE", ("body", "scale"): "CUBICSPLINE"})

        def at(node: str, path: str, t: float) -> np.ndarray:
            interpolation, times, rows = found[(node, path)]
            return _sample(times, rows, interpolation, t, path == "rotation")

        structure = document.Loaded(self.folder / "XBF/channels.xbf").structure()
        instances = {instance["path"].split("/", 1)[1]: instance for instance in structure["instances"]}
        size = math.dist((-45, -29, -4), (46, 29, 36))
        worst = [0.0, 0.0]
        for t in np.linspace(0, 2, 81):
            # The lid by forward kinematics: the hinge eased 0 -> 100 -> 0 degrees.
            hinge = 100 * (_smooth(t) if t <= 1 else 1 - _smooth(t - 1))
            truth = np.array(mechanism.posed_instances(structure, {"hinge": (hinge,)})[
                structure["instances"].index(instances["lid"])]).reshape(4, 4).T
            worst[0] = max(worst[0], float(np.linalg.norm(at("lid", "translation", t) - truth[:3, 3])))
            turned = _rotation(at("lid", "rotation", t)).T @ truth[:3, :3]
            worst[1] = max(worst[1], math.acos(max(-1.0, min(1.0, (np.trace(turned) - 1) / 2))))
            # The latch: out 20 mm and back, linearly, turning once about its own axis at constant speed.
            np.testing.assert_allclose(at("latch", "translation", t), [42 + 20 * (1 - abs(1 - t)), 0, 30], atol=1e-3)
            spun = _rotation(at("latch", "rotation", t))
            angle = math.atan2(spun[1, 0], spun[0, 0]) % (2 * math.pi)
            self.assertLess(min(abs(angle - (math.pi * t) % (2 * math.pi)),
                                2 * math.pi - abs(angle - (math.pi * t) % (2 * math.pi))), 1e-3, t)
            # The knob: its quarter turn held until 1 s, then its scale 0 from 1.5 s.
            self.assertAlmostEqual(_turn(at("knob", "rotation", t), np.array([0, 0, 0, 1.0])),
                                   0.0 if t < 1 else math.pi / 2, places=5)
            np.testing.assert_allclose(at("knob", "scale", t), [0.0 if t >= 1.5 else 1.0] * 3)
            # The feet: dropped 10 mm in turn, the first shrinking to half over the whole.
            for foot, start in (("feet/foot_1", 0.0), ("feet/foot_2", 0.5)):
                expected = -4 - 10 * _smooth((t - start) / 0.5)
                self.assertLess(abs(at(foot, "translation", t)[2] - expected), mechanism.LINEAR_TOLERANCE * size, (foot, t))
            np.testing.assert_allclose(at("feet/foot_1", "scale", t), [1 - 0.25 * t] * 3, atol=1e-6)
            self.assertLess(abs(at("body", "scale", t)[0] - (1 + 0.2 * _smooth(t / 2))), 1e-3, t)
        self.assertLess(worst[0], mechanism.LINEAR_TOLERANCE * size * 1.5, worst)
        self.assertLess(worst[1], mechanism.ANGULAR_TOLERANCE * 1.5, worst)
        nodes = {node["name"]: node for node in gltf["nodes"][1:]}
        self.assertNotIn("matrix", nodes["knob"], "an animated node stands by translation and rotation")

    def test_cubicspline_tangents_reproduce_the_curve_under_scipy(self) -> None:
        """The baked in- and out-tangents, read by scipy's ``CubicHermiteSpline`` over each step (a
        glTF key has a tangent on each side, scipy one per knot), stay within the tolerance of the
        eased drop the stagger says, at every hundredth of a second; rotations are compared with
        glTF's own evaluation above, never scipy's quaternion splines (another convention)."""
        from scipy.interpolate import CubicHermiteSpline

        self.channels()
        gltf, read = _glb(self.folder / "channels.glb")
        [showcase] = [animation for animation in gltf["animations"] if animation["name"] == "showcase"]
        names = [node["name"] for node in gltf["nodes"]]
        size = math.dist((-45, -29, -4), (46, 29, 36))
        checked = 0
        for channel in showcase["channels"]:
            sampler = showcase["samplers"][channel["sampler"]]
            if sampler["interpolation"] != "CUBICSPLINE" or channel["target"]["path"] != "translation":
                continue
            node = names[channel["target"]["node"]]
            if not node.startswith("feet/"):
                continue
            start = 0.0 if node == "feet/foot_1" else 0.5
            times = read(sampler["input"])[:, 0]
            keys = read(sampler["output"]).reshape(len(times), 3, 3)
            for at in range(len(times) - 1):
                spline = CubicHermiteSpline(times[at:at + 2], keys[at:at + 2, 1], [keys[at, 2], keys[at + 1, 0]])
                for t in np.arange(times[at], times[at + 1], 0.01):
                    expected = np.array([-30 if node == "feet/foot_1" else 30, 0, -4 - 10 * _smooth((t - start) / 0.5)])
                    self.assertLess(float(np.linalg.norm(spline(t) - expected)),
                                    mechanism.LINEAR_TOLERANCE * size, (node, t))
                    checked += 1
        self.assertGreater(checked, 300)

    def test_a_glb_from_the_xbf_alone_carries_the_motions_only_and_an_edit_of_one_rewrites_the_glb(self) -> None:
        from cadgen.engine.export import translate

        self.build(MOTION.replace("    return configuration_sequence(box", "    from cadgen import animation\n"
                                  "    animation(box, 'drop', duration=1).translation('lid', by=(0, 0, 5))\n"
                                  "    return configuration_sequence(box"), "motion_box")
        declared, _read = _glb(self.folder / "motion.glb")
        self.assertEqual([animation["name"] for animation in declared["animations"]], ["open_close", "drop"])
        again = self.folder / "again.glb"
        translate(self.folder / "XBF/motion.xbf", None, again)
        self.assertEqual([animation["name"] for animation in _glb(again)[0]["animations"]], ["open_close"])
        before = (self.folder / "motion.glb").read_bytes()
        report = self.build(MOTION.replace("    return configuration_sequence(box", "    from cadgen import animation\n"
                                           "    animation(box, 'drop', duration=1).translation('lid', by=(0, 0, 9))\n"
                                           "    return configuration_sequence(box"), "motion_box")
        self.assertTrue(report["files"][str(self.folder / "motion.glb")]["written"])
        self.assertNotEqual((self.folder / "motion.glb").read_bytes(), before)


class FixturesTest(_Folder):
    """The core suite's animated GLBs are what the baker writes: their animations, key for key."""

    def test_the_core_suites_glb_fixtures_are_the_bakers(self) -> None:
        for source, name, glb, fixture in ((CHANNELS.replace("{more}", "pass"), "channels", "channels.glb",
                                            "animation.glb"), (MOTION, "motion_box", "motion.glb", "motion.glb")):
            with self.subTest(fixture=fixture):
                self.build(source, name, stem=name)
                made = self.folder / glb
                if os.environ.get("CADGEN_WRITE_FIXTURES") == "1":
                    shutil.copyfile(made, FIXTURES / fixture)
                ours, read_ours = _glb(made)
                theirs, read_theirs = _glb(FIXTURES / fixture)
                self.assertEqual(ours["animations"], theirs["animations"])
                self.assertEqual([node["name"] for node in ours["nodes"]], [node["name"] for node in theirs["nodes"]])
                for animation in ours["animations"]:
                    for sampler in animation["samplers"]:
                        for key in ("input", "output"):
                            np.testing.assert_allclose(read_ours(sampler[key]), read_theirs(sampler[key]), atol=1e-4)


if __name__ == "__main__":
    unittest.main()
