"""Motions: definitions in a model's code (``motion``), kept in the document beside the
configurations as AP242's interpolated configuration sequences -- waypoints, a sweep, or a
function's samples, each segment's interpolation linear or synchronous -- read back whole,
patched by a warm build to the cold build's bytes; the removed ``loop`` and ``spline`` refused by
name; and a GLB export baking each motion into a finite glTF clip whose curves stay within the
tolerance of forward kinematics."""

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

from cadgen.engine import document, mechanism
from cadgen.engine import build as engine_build

ARM = """
import math
import build123d as bd
from cadgen.ap242 import configuration_sequence, mechanism_state
from cadgen.engine import xbf


@xbf("XBF/arm.xbf")
def arm():
    base = bd.Box(100, 60, 20)
    link = bd.Pos(40, 0, 25) * bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
    tip = bd.Pos(60, 0, 35) * bd.Box(40, 10, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
    base.label, link.label, tip.label = "base", "link", "tip"
    elbow = bd.Axis((40, 0, 20), (0, 1, 0))
    bd.RevoluteJoint("elbow", base, axis=elbow, angular_range=(-10, 120))
    bd.RigidJoint("mount", link, bd.Plane(origin=elbow.position, z_dir=elbow.direction).location)
    slide = bd.Axis((60, 0, 30), (1, 0, 0))
    bd.LinearJoint("slide", link, axis=slide, linear_range=(0, 80))
    bd.RigidJoint("carriage", tip, bd.Plane(origin=slide.position, z_dir=slide.direction).location)
    link.joints["slide"].connect_to(tip.joints["carriage"], position=0)
    base.joints["elbow"].connect_to(link.joints["mount"], angle=0)
    arm = bd.Compound(children=[base, link, tip], label="arm")
    mechanism_state(arm, "raised", elbow=90)
    {m}configuration_sequence(arm, "lift", [(0, {{"elbow": 0}}), (1.5, "raised"), (3, {{"elbow": 0, "slide": 60}})])
    {m}configuration_sequence(arm, "wave", sweep={{"elbow": (-10, {reach})}}, duration=2, ease="linear")
    {m}{nod}configuration_sequence(arm, "nod", lambda t: {{"elbow": 45 + 20 * math.sin(t), "slide": 40 + 30 * math.sin(2 * t)}}, duration=6)
    {more}
    return arm
"""


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        from cadgen.engine.kernel import memo

        self.folder = Path(tempfile.mkdtemp(prefix="engine-motion-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.script = self.folder / "arm.py"
        self.xbf = self.folder / "XBF/arm.xbf"
        memo.install()
        self.addCleanup(memo.clear)

    def arm(self, *, ok: bool = True, reach: int = 60, more: str = "pass", nod: bool = True,
            motions: bool = True) -> dict:
        text = ARM.format(reach=reach, more=more, nod="" if nod else "# ", m="" if motions else "# ")
        self.script.write_text(textwrap.dedent(text), encoding="utf-8")
        os.utime(self.script, ns=(time.time_ns(), time.time_ns()))
        [report] = engine_build.build(self.script, ["arm"], cwd=self.folder)
        self.assertEqual(report["ok"], ok, report)
        return report

    def cold(self, **values) -> bytes:
        """The arm built by a process that never built it: its bytes."""
        from cadgen.engine.kernel import memo

        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        self.xbf.unlink()
        self.assertFalse(self.arm(**values)["converted"]["patched"])
        return self.xbf.read_bytes()


class AuthoringTest(unittest.TestCase):
    def test_a_motion_is_one_of_waypoints_a_sweep_or_a_function_and_says_what_is_wrong(self) -> None:
        import build123d as bd

        from cadgen.ap242 import configuration_sequence

        box = bd.Box(1, 1, 1)
        for call, error in (
                (lambda: configuration_sequence(box, "m"), "waypoints, a function of time, or sweep=: one of them"),
                (lambda: configuration_sequence(box, "m", [(0, "a")], sweep={"j": (0, 1)}, duration=1), "one of them"),
                (lambda: configuration_sequence(box, "m", [(0, "a")]), "at least two waypoints"),
                (lambda: configuration_sequence(box, "m", [(1, "a"), (2, "b")]), "the first waypoint is at 0 s"),
                (lambda: configuration_sequence(box, "m", [(0, "a"), (0, "b")]), "is not after the one before it"),
                (lambda: configuration_sequence(box, "m", [(0, "a"), (1, "b")], duration=3), "waypoints give their own times"),
                (lambda: configuration_sequence(box, "m", [(0, "a"), (1, {"j": "x"})]), "joint 'j' takes a number"),
                (lambda: configuration_sequence(box, "m", sweep={"j": (0, 1)}), "sweep= takes duration="),
                (lambda: configuration_sequence(box, "m", sweep={"j": 5}, duration=1), "takes \\(from, to\\)"),
                (lambda: configuration_sequence(box, "m", lambda t: {}, duration=0), "duration is a number of seconds above 0"),
                (lambda: configuration_sequence(box, "m", lambda t: {}), "a function of time takes duration="),
                (lambda: configuration_sequence(box, "m", sweep={"j": (0, 1)}, duration=1, ease="bounce"), "ease is one of"),
                (lambda: configuration_sequence(box, "m", sweep={"j": (0, 1)}, duration=1, loop="loop"),
                 "configuration sequence 'm': loop= is gone: a configuration sequence is a finite clip, and a player "
                 "repeats it"),
                (lambda: configuration_sequence(box, "m", sweep={"j": (0, 1)}, duration=1, ease="spline"),
                 "configuration sequence 'm': ease 'spline' is gone: AP242 interpolates a configuration sequence linearly "
                 "or synchronously"),
                (lambda: configuration_sequence(box, "m", sweep={"j": (0, 1)}, duration=1, speed=2), "configuration_sequence\\(\\) takes no speed"),
                (lambda: configuration_sequence("arm", "m", sweep={"j": (0, 1)}, duration=1), "takes the assembly")):
            with self.assertRaisesRegex((TypeError, ValueError), error):
                call()
        self.assertIs(configuration_sequence(box, "m", sweep={"j": (0, 1)}, duration=1), box)


class SampledKeysTest(unittest.TestCase):
    """The keys a motion's function may return, checked at each sample: a coupling by its own or
    kept name, a joint below the assembly by its raw label or ``prefix:raw``; anything else fails
    by name, listing the assembly's joints and couplings."""

    JOINTS = [("elbow", (0, 0)), ("slide", (0, 1)), ("wrist", (1,))]  # wrist is outside path (0,)
    CONTROLS = [((0,), "curl", "hand:curl"), ((1,), "spread", "spread")]

    def sample(self, key: str) -> list:
        convert = lambda values, _at: {"elbow": (float(next(iter(values.values()))),)}  # noqa: E731
        return mechanism._sampled(lambda t: {key: 10.0}, 1.0, convert, {"elbow": [1.0]}, "motion 'm' of arm",
                                  (0,), self.JOINTS, self.CONTROLS)

    def test_accepted_key_forms(self) -> None:
        for key in ("elbow", "slide", "arm:elbow", "a:b:slide", "curl", "hand:curl"):
            with self.subTest(key=key):
                self.assertEqual(self.sample(key)[0], (0.0, None, {"elbow": (10.0,)}))

    def test_a_key_naming_no_joint_fails_by_name(self) -> None:
        for key in ("wrist", "arm:wrist", "elbowx", "spread", "elbow:", ":"):
            with self.subTest(key=key), self.assertRaises(mechanism.KinematicsError) as caught:
                self.sample(key)
            self.assertEqual(str(caught.exception), f"motion 'm' of arm names no joint {key!r}; its joints are "
                                                    "elbow, slide; its couplings are curl, hand:curl")


class DocumentTest(_Folder):
    def test_motions_are_definitions_in_the_document_read_back_whole(self) -> None:
        self.arm()
        loaded = document.Loaded(self.xbf)
        motions = loaded.mechanism.motions
        self.assertEqual(list(motions), ["lift", "nod", "wave"])
        lift = motions["lift"]
        self.assertEqual(lift.keys, [(0.0, None, {"elbow": (0.0,)}), (1.5, "raised", {}),
                                     (3.0, None, {"elbow": (0.0,), "slide": (60.0,)})])
        self.assertEqual(lift.ease, "cubic")
        self.assertEqual(motions["wave"].keys, [(0.0, None, {"elbow": (-10.0,)}), (2.0, None, {"elbow": (60.0,)})])
        self.assertEqual(motions["wave"].ease, "linear")
        # Each segment's interpolation is AP242's: the ease to the next, the last's a discontinuity.
        from OCP.Standard import Standard_GUID

        root = next(label for label in mechanism._children_of(loaded.doc.Main())
                    if label.IsAttribute(Standard_GUID(mechanism.GUID)))
        sequences = {document._label_name(label): [mechanism._string(mechanism._named(segment), "interpolation")
                                                   for segment in mechanism._children_of(label)]
                     for label in mechanism._children_of(root.FindChild(1, False).FindChild(5, False))}
        self.assertEqual(sequences["lift"], ["synchronous_interpolation_type"] * 2 + ["discontinuous_interpolation_type"])
        self.assertEqual(sequences["wave"], ["linear_interpolation_type", "discontinuous_interpolation_type"])
        block = loaded.structure()["kinematics"]["motions"]
        self.assertEqual(block["lift"]["keys"][1], {"t": 1.5, "configuration": "raised"})
        self.assertEqual(block["wave"]["duration"], 2.0)
        # The build is as the joints connect the parts: no motion and no configuration moves it.
        self.assertEqual(loaded.structure()["kinematics"]["asBuilt"]["elbow"], 0.0)

    def test_a_function_is_kept_as_samples_within_the_tolerance_at_times_the_code_chooses(self) -> None:
        self.arm()
        nod = document.Loaded(self.xbf).mechanism.motions["nod"]
        self.assertEqual(nod.ease, "linear", "a function's samples, interpolated linearly")
        times = np.array([t for t, _name, _values in nod.keys])
        self.assertEqual((times[0], times[-1]), (0.0, 6.0))
        self.assertLess(len(times), 200)
        table = np.array([[values["elbow"][0], values["slide"][0]] for _t, _name, values in nod.keys])
        worst = np.zeros(2)
        for t in np.linspace(0, 6, 997):
            got = mechanism._interpolated(times, table, "linear", float(t))
            truth = np.array([45 + 20 * math.sin(t), 40 + 30 * math.sin(2 * t)])
            worst = np.maximum(worst, np.abs(got - truth))
        size = math.dist((-50, -30, -10), (160, 30, 40))  # the arm's bounding box as built
        # Within the tolerance at the checked points; between them, within half as much again.
        self.assertLess(worst[0], math.degrees(mechanism.ANGULAR_TOLERANCE) * 1.5)
        self.assertLess(worst[1], mechanism.LINEAR_TOLERANCE * size * 1.5)
        first = self.xbf.read_bytes()
        self.assertEqual(self.cold(), first, "the same code samples at the same times: the same bytes")

    def test_a_function_with_kinks_is_kept_within_the_tolerance_at_its_kinks_too(self) -> None:
        # A zigzag: straight runs meeting at kinks every 0.685 s, none on the sampler's grid. Its
        # error is largest at a kink, between any two check points.
        zigzag = lambda t: 40 + 60 * 2 * abs(t / 1.37 - math.floor(t / 1.37 + 0.5))  # noqa: E731
        self.arm(more='configuration_sequence(arm, "zigzag", lambda t: {"elbow": 40 + 60 * 2 * abs(t / 1.37 - math.floor(t / 1.37 + '
                      '0.5))}, duration=6)')
        keys = document.Loaded(self.xbf).mechanism.motions["zigzag"].keys
        times = np.array([t for t, _name, _values in keys])
        table = np.array([[values["elbow"][0]] for _t, _name, values in keys])
        kinks = [0.685 * k for k in range(1, 9) if 0.685 * k < 6]
        checked = sorted({*np.linspace(0, 6, 6001).tolist(), *kinks})
        worst = max(abs(float(mechanism._interpolated(times, table, "linear", t)[0]) - zigzag(t)) for t in checked)
        self.assertLessEqual(worst, math.degrees(mechanism.ANGULAR_TOLERANCE), "the samples hold the tolerance everywhere")
        self.assertLess(len(times), 200)

    def test_a_warm_edit_of_a_motion_patches_the_document_to_the_cold_bytes(self) -> None:
        self.arm()
        report = self.arm(reach=90)
        self.assertTrue(report["converted"]["patched"], report["converted"])
        self.assertEqual(report["converted"]["converted"], 0, "a motion edit converts no prototype")
        self.assertEqual(document.Loaded(self.xbf).mechanism.motions["wave"].keys[-1], (2.0, None, {"elbow": (90.0,)}))
        warm = self.xbf.read_bytes()
        self.assertEqual(self.cold(reach=90), warm)
        # A motion removed leaves nothing of itself: the warm file is the cold one's again.
        self.arm(reach=90, nod=False)
        warm = self.xbf.read_bytes()
        self.assertNotIn("nod", document.Loaded(self.xbf).mechanism.motions)
        self.assertEqual(self.cold(reach=90, nod=False), warm)

    def test_what_a_motion_names_wrong_fails_or_warns_by_name(self) -> None:
        report = self.arm(ok=False, more='configuration_sequence(arm, "bad", [(0, "raised"), (1, "lowered")])')
        self.assertIn("names no mechanism state 'lowered'", report["error"])
        report = self.arm(ok=False, more='configuration_sequence(arm, "bad", sweep={"knee": (0, 1)}, duration=1)')
        self.assertIn("configuration sequence 'bad' of arm names no joint 'knee'", report["error"])
        report = self.arm(more='configuration_sequence(arm, "far", sweep={"elbow": (0, 200)}, duration=1)')
        self.assertTrue(any("configuration sequence 'far'" in text and "outside its range" in text and "left out" in text
                            for text in report["kinematics"]["warnings"]), report.get("kinematics"))
        self.assertNotIn("far", document.Loaded(self.xbf).mechanism.motions)

    def test_a_part_of_one_link_and_a_step_carry_no_motion(self) -> None:
        from cadgen.engine.export import translate

        self.arm()
        part = self.folder / "link.xbf"
        translate(self.xbf, None, part, part="link")
        self.assertEqual(document.Loaded(part).mechanism, None, "a part of one link holds no pair, and no motion")
        whole = self.folder / "whole.step"
        translate(self.xbf, None, whole)
        self.assertIsNone(document.Loaded(whole).mechanism, "STEP carries no mechanism and no motion")


MOVES = """
import json
import math
import os
from pathlib import Path

AMPLITUDE = {amplitude}


def nod(scale):
    def at(t):
        return {{"drive": 0.5 + AMPLITUDE * scale * math.sin(t)}}
    return at


def table(t):
    rows = json.loads((Path(__file__).parent / "table.json").read_text())
    return {{"drive": rows["base"] + 0.1 * math.sin(t)}}


def offset(t):
    return {{"drive": 0.5 + 0.1 * math.sin(t) + float(os.environ.get("CADGEN_TEST_MOTION_OFFSET", "0"))}}
"""

HINGE = """
import math

import build123d as bd
from cadgen.ap242 import configuration_sequence, rack_and_pinion_pair
from cadgen.engine import xbf
import moves


@xbf("XBF/hinge.xbf")
def hinge():
    base = bd.Box(100, 60, 20)
    link = bd.Pos(40, 0, 25) * bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
    tip = bd.Pos(60, 0, 35) * bd.Box(40, 10, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
    base.label, link.label, tip.label = "base", "link", "tip"
    base.color = bd.Color({colour})
    elbow = bd.Axis((40, 0, 20), (0, 1, 0))
    bd.RevoluteJoint("elbow", base, axis=elbow, angular_range=(-10, {top}))
    bd.RigidJoint("mount", link, bd.Plane(origin=elbow.position, z_dir=elbow.direction).location)
    slide = bd.Axis((60, 0, 30), (1, 0, 0))
    bd.LinearJoint("slide", link, axis=slide, linear_range=(0, 80))
    bd.RigidJoint("carriage", tip, bd.Plane(origin=slide.position, z_dir=slide.direction).location)
    link.joints["slide"].connect_to(tip.joints["carriage"], position=0)
    base.joints["elbow"].connect_to(link.joints["mount"], angle=0)
    hinge = bd.Compound(children=[base, link, tip], label="hinge")
    rack_and_pinion_pair(hinge, "drive", elbow={ratio}, slide=10)
    configuration_sequence(hinge, "nod", moves.nod({scale}), duration={duration})
    configuration_sequence(hinge, "table", moves.table, duration=2)
    {more}
    return hinge
"""


class MotionMemoTest(unittest.TestCase):
    """A function motion's samples are kept in the worker's memory and reused while what they
    are made of -- the function and what it reaches, the files it opens, the joints it moves,
    the sampling's settings and the engine -- is what it was (``memo.sampled``)."""

    def setUp(self) -> None:
        from unittest import mock

        from cadgen.engine.kernel import memo

        self.folder = Path(tempfile.mkdtemp(prefix="engine-motion-memo-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        self.xbf = self.folder / "XBF/hinge.xbf"
        memo.install()
        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        self.addCleanup(memo.clear)
        patch = mock.patch.dict(os.environ)
        patch.start()
        self.addCleanup(patch.stop)
        for name in ("CADGEN_ENGINE_BODIES", "CADGEN_ENGINE_MEMO", "CADGEN_TEST_MOTION_OFFSET"):
            os.environ.pop(name, None)
        self.values = {"colour": "0.5, 0.5, 0.5", "top": 120, "ratio": 20, "scale": 1, "duration": 6, "more": "pass",
                       "amplitude": 0.1}
        self.write("table.json", json.dumps({"base": 0.4}))

    def write(self, name: str, text: str) -> None:
        path = self.folder / name
        path.write_text(textwrap.dedent(text), encoding="utf-8")
        os.utime(path, ns=(time.time_ns(), time.time_ns()))

    def hinge(self, *, verify: bool = False, **values: Any) -> dict:
        self.values.update(values)
        self.write("moves.py", MOVES.format(**self.values))
        self.write("hinge.py", HINGE.format(**self.values))
        [report] = engine_build.build(self.folder / "hinge.py", ["hinge"], cwd=self.folder, verify=verify)
        self.assertTrue(report["ok"], report)
        self.assertNotIn("kinematics", report, "every motion kept")
        return report

    def statuses(self, report: dict) -> dict[str, str]:
        return {name: found.partition(";")[0] for name, found in report.get("motions", {}).items()}

    def test_an_edit_the_motions_do_not_read_samples_nothing_and_writes_the_cold_bytes(self) -> None:
        first = self.hinge()
        self.assertEqual(self.statuses(first), {"hinge:nod": "sampled: first sampled in this worker",
                                                "hinge:table": "sampled: first sampled in this worker"})
        report = self.hinge(colour="0.8, 0.2, 0.2")
        self.assertEqual(report["bodies"], {"hinge.py::hinge": "run"}, "the body ran")
        self.assertEqual(report["motions"], {"hinge:nod": "reused", "hinge:table": "reused"})
        self.assertEqual((report["memo"]["motions_sampled"], report["memo"]["motions_reused"]), (0, 2))
        warm = self.xbf.read_bytes()
        from cadgen.engine.kernel import memo

        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        self.xbf.unlink()
        self.assertEqual(self.hinge()["memo"]["motions_sampled"], 2)
        self.assertEqual(self.xbf.read_bytes(), warm, "the reused samples are a fresh sampling's, byte for byte")

    def test_an_edit_of_what_a_motion_is_made_of_samples_it_again_and_says_why(self) -> None:
        self.hinge()
        for values, nod, table in (
                ({"scale": 2}, "its function changed", "reused"),                      # a value its closure holds
                ({"amplitude": 0.05}, "its function changed", "reused"),               # a module global it reads
                ({"top": 100}, "the joints it moves changed", "the joints it moves changed"),   # a joint's limit
                ({"ratio": 25}, "the joints it moves changed", "the joints it moves changed"),  # a coupling's ratio
                ({"duration": 5}, "its duration or a tolerance changed", "reused")):  # the sampling
            with self.subTest(values):
                report = self.hinge(**values)
                self.assertEqual(self.statuses(report), {"hinge:nod": f"sampled: {nod}" if nod != "reused" else nod,
                                                         "hinge:table": f"sampled: {table}" if table != "reused"
                                                         else table})
        self.write("table.json", json.dumps({"base": 0.45}))  # a file the function opens
        report = self.hinge()
        self.assertEqual(report["motions"]["hinge:nod"], "reused")
        self.assertEqual(report["motions"]["hinge:table"], f"sampled: {self.folder / 'table.json'} changed")
        self.assertAlmostEqual(document.Loaded(self.xbf).mechanism.motions["table"].keys[0][2]["elbow"][0],
                               (0.45 - 0) * 25, msg="the samples are the file's new values")

    def test_a_verifying_build_samples_again_and_marks_a_motion_it_samples_otherwise(self) -> None:
        self.hinge(more='configuration_sequence(hinge, "offset", moves.offset, duration=2)')
        report = self.hinge(verify=True)
        self.assertEqual(report["verify"]["motions"], {"hinge:nod": "same", "hinge:offset": "same",
                                                       "hinge:table": "same"})
        self.assertEqual(set(self.statuses(report).values()), {"sampled: verifying"})
        self.assertEqual(report["verify"]["mismatches"], [])
        # A function reading what its key cannot see (the environment): a verification finds it.
        os.environ["CADGEN_TEST_MOTION_OFFSET"] = "0.01"
        report = self.hinge(verify=True)
        self.assertTrue(report["verify"]["motions"]["hinge:offset"].startswith("differs: "), report["verify"])
        self.assertEqual(report["verify"]["mismatches"], ["motion hinge:offset"])
        report = self.hinge()
        self.assertEqual(report["motions"]["hinge:nod"], "reused")
        self.assertTrue(report["motions"]["hinge:offset"].startswith("sampled: a verification sampled it otherwise"),
                        report["motions"])

    def test_a_function_holding_what_has_no_canonical_form_is_sampled_on_every_build(self) -> None:
        held = ('configuration_sequence(hinge, "held", (lambda part: lambda t: {"drive": 0.5 + 0.1 * math.sin(t) + 0 * part.volume})'
                '(base), duration=2)')
        self.hinge(more=held)
        report = self.hinge(colour="0.1, 0.2, 0.3")
        self.assertTrue(report["motions"]["hinge:held"].startswith("sampled: its function holds what has no "
                                                                   "canonical form"), report["motions"])
        self.assertIn("closure variable part", report["motions"]["hinge:held"])
        self.assertEqual(report["motions"]["hinge:nod"], "reused")

    def test_builds_taken_from_memory_keep_the_samples_for_the_next_edit(self) -> None:
        from cadgen.engine.kernel import memo

        self.hinge()
        for _ in range(memo.RUNS_KEPT + 1):
            self.assertEqual(self.hinge()["cache"], {"used": True, "from": "memory"})
        report = self.hinge(colour="0.3, 0.3, 0.3")
        self.assertEqual(report["motions"], {"hinge:nod": "reused", "hinge:table": "reused"})


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


def _cubic(times: np.ndarray, rows: np.ndarray, t: float) -> np.ndarray:
    """glTF's CUBICSPLINE at ``t``, as a viewer evaluates it: rows of (in-tangent, value, out-tangent)."""
    keys = rows.reshape(len(times), 3, -1)
    at = max(0, min(int(np.searchsorted(times, t, side="right")) - 1, len(times) - 2))
    span = times[at + 1] - times[at]
    s = (t - times[at]) / span
    return ((2 * s ** 3 - 3 * s ** 2 + 1) * keys[at, 1] + span * (s ** 3 - 2 * s ** 2 + s) * keys[at, 2]
            + (-2 * s ** 3 + 3 * s ** 2) * keys[at + 1, 1] + span * (s ** 3 - s ** 2) * keys[at + 1, 0])


def _rotation(q: np.ndarray) -> np.ndarray:
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def deviations(glb: Path, structure: dict, between: int = 9) -> dict[str, tuple[int, float, float]]:
    """Each clip of ``glb`` sampled at ``between - 1`` times inside every step between two of
    its keys, against forward kinematics through the document's mechanism at those times:
    its key count, its worst origin (mm) and its worst rotation (radians) apart."""
    gltf, read = _glb(glb)
    kinematics = structure["kinematics"]
    instances = {instance["path"]: at for at, instance in enumerate(structure["instances"])}
    root = structure["tree"]["name"]
    found = {}
    for animation in gltf["animations"]:
        timeline = mechanism.Timeline(kinematics, animation["name"])
        times = read(animation["samplers"][0]["input"])[:, 0]
        truths = {}
        worst = [0.0, 0.0]
        for channel in animation["channels"]:
            node = gltf["nodes"][channel["target"]["node"]]
            index = instances[f"{root}/{node['name']}"]
            rows = read(animation["samplers"][channel["sampler"]]["output"])
            for a, b in zip(times, times[1:]):
                for step in range(1, between):
                    t = float(a + (b - a) * step / between)
                    if t not in truths:
                        truths[t] = mechanism.posed_instances(structure, timeline.values(t))
                    truth = np.array(truths[t][index]).reshape(4, 4).T
                    got = _cubic(times, rows, t)
                    if channel["target"]["path"] == "translation":
                        worst[0] = max(worst[0], float(np.linalg.norm(got - truth[:3, 3])))
                    else:
                        turned = _rotation(got).T @ truth[:3, :3]
                        worst[1] = max(worst[1], math.acos(max(-1.0, min(1.0, (np.trace(turned) - 1) / 2))))
        found[animation["name"]] = (len(times), worst[0], worst[1])
    return found


class GlbTest(_Folder):
    def test_a_glb_bakes_every_motion_into_a_clip_within_the_tolerance_of_forward_kinematics(self) -> None:
        from cadgen.engine.export import translate

        self.arm()
        out = self.folder / "arm.glb"
        translate(self.xbf, None, out)
        gltf, read = _glb(out)
        structure = document.Loaded(self.xbf).structure()
        self.assertEqual([animation["name"] for animation in gltf["animations"]], ["lift", "nod", "wave"])
        nodes = {node["name"]: node for node in gltf["nodes"][1:]}
        self.assertIn("matrix", nodes["base"], "a placement no motion moves stands by its matrix")
        for name in ("link", "tip"):
            self.assertNotIn("matrix", nodes[name], "an animated node stands by translation and rotation")
            self.assertEqual(len(nodes[name]["rotation"]), 4)
        for animation in gltf["animations"]:
            self.assertEqual(len(animation["channels"]), 4, "translation and rotation of the link and the tip")
            first = animation["samplers"][0]["input"]
            keys = gltf["accessors"][first]["count"]
            for channel in animation["channels"]:
                sampler = animation["samplers"][channel["sampler"]]
                self.assertEqual((sampler["interpolation"], sampler["input"]), ("CUBICSPLINE", first))
                output = gltf["accessors"][sampler["output"]]
                self.assertEqual(output["count"], 3 * keys, "in-tangent, value, out-tangent per key")
                self.assertEqual(output["type"], {"translation": "VEC3", "rotation": "VEC4"}[channel["target"]["path"]])
            times = read(first)[:, 0]
            self.assertTrue(np.all(np.diff(times) > 0))
            self.assertEqual(gltf["accessors"][first]["min"][0], 0.0)
        extras = {animation["name"]: animation["extras"]["cadgen"] for animation in gltf["animations"]}
        self.assertEqual(extras, {"lift": {"duration": 3.0}, "nod": {"duration": 6.0}, "wave": {"duration": 2.0}})
        self.assertAlmostEqual(read(gltf["animations"][2]["samplers"][0]["input"])[-1, 0], 2.0, places=5,
                               msg="a clip is the motion once: a player repeats it")
        size = math.dist((-50, -30, -10), (160, 30, 40))
        found = deviations(out, structure)
        for name, (keys, linear, angular) in found.items():
            self.assertLess(keys, 200, name)
            # Within the tolerance at the bake's checks; between them, within half as much again.
            self.assertLess(linear, mechanism.LINEAR_TOLERANCE * size * 1.5, (name, found))
            self.assertLess(angular, mechanism.ANGULAR_TOLERANCE * 1.5, (name, found))
        self.assertLess(found["wave"][0], found["nod"][0], "few keys where the motion is simple")
        again = self.folder / "again.glb"
        translate(self.xbf, None, again)
        self.assertEqual(again.read_bytes(), out.read_bytes(), "the same document bakes the same bytes")

    def test_a_posed_glb_keeps_its_clips_and_the_other_exports_ignore_motions(self) -> None:
        from cadgen.engine.export import translate

        self.arm()
        posed = self.folder / "raised.glb"
        translate(self.xbf, None, posed, pose="raised")
        self.assertEqual([animation["name"] for animation in _glb(posed)[0]["animations"]], ["lift", "nod", "wave"])
        moving = {kind: self.folder / f"moving{suffix}" for kind, suffix in (("stl", ".stl"), ("threemf", ".3mf"))}
        for path in moving.values():
            translate(self.xbf, None, path)
        self.arm(motions=False)
        still = self.folder / "still.glb"
        translate(self.xbf, None, still)
        self.assertNotIn("animations", _glb(still)[0], "no motion, no animation")
        for kind, path in moving.items():
            other = self.folder / f"still{path.suffix}"
            translate(self.xbf, None, other)
            self.assertEqual(other.read_bytes(), path.read_bytes(), f"a {kind} is the same with motions or without")

    def test_neither_export_nor_glb_takes_an_animation_argument(self) -> None:
        import inspect

        from cadgen.engine import api
        from cadgen.engine.export import translate
        from cadgen.engine.model import Model

        for function in (translate, api.export, Model.export):
            self.assertNotIn("animate", inspect.signature(function).parameters, function)
        import contextlib
        import io

        from cadgen.engine import cli

        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.build_parser().parse_args(["export", "a.xbf", "a.glb", "--animate", "x"])


if __name__ == "__main__":
    unittest.main()
