"""Couplings: ``couple(assembly, name, *, range=(0, 1), lock=False, **joints)``, one control driving
several joints by ratio from the as-built pose. Validated by name; kept in the document's mechanism
(``:6 "couplings"``) and read back identically; served in the structure's ``kinematics.couplings``;
forward kinematics at a coupling's value the joints set by hand, on ``--pose`` of an export and a
snapshot; a door's turn and travel from one control; a locked coupling its joints' only control;
configurations and motions naming a coupling resolved to pair values at document time; a warm edit
patched to the cold build's bytes, the elements' names unchanged."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

import numpy as np

from cadgen.engine import build as engine_build
from cadgen.engine import cli, document, mechanism

# The spec's finger: four bones along +X, a knuckle across each joint between two, straight as
# built, and one control curling all three knuckles.
FINGER = """
import build123d as bd
from cadgen.engine import configuration, couple, motion, xbf

CURL = {{"index_mcp_flexion": 90, "index_pip": 100, "index_dip": 70}}  # degrees at a full curl
BONES = {{"base": 30, "proximal": 40, "middle": 25, "distal": 20}}  # lengths along +X


@xbf
def index_finger():
    bones, x = [], -BONES["base"]
    for name, length in BONES.items():
        bone = bd.Pos(x, 0, 0) * bd.Box(length, 16, 12, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
        bone.label = name
        bones.append(bone)
        x += length
    for joint, parent, child in zip(CURL, bones, bones[1:]):
        knuckle = bd.Axis(child.location.position, (0, 1, 0))
        bd.RevoluteJoint(joint, parent, axis=knuckle, angular_range=(0, CURL[joint]))
        bd.RigidJoint(joint, child, bd.Plane(origin=knuckle.position, z_dir=knuckle.direction).location)
        parent.joints[joint].connect_to(child.joints[joint], angle=0)  # as built: straight
    finger = bd.Compound(children=bones, label="index_finger")
    {couple}
    {extra}
    return motion(finger, "grip", sweep={{"index_curl": (0, 1)}}, duration=1.5, loop="pingpong")
"""
CURL = 'couple(finger, "index_curl", **CURL)'
RATIOS = {"index_mcp_flexion": 90, "index_pip": 100, "index_dip": 70}  # the finger's, at a full curl
BY_HAND = {"index_mcp_flexion": 54, "index_pip": 60, "index_dip": 42}  # its joints at index_curl 0.6

# The spec's hypercar door: a helical hinge, a cylindrical joint per side, one control turning
# and sliding both doors.
HYPERCAR = """
import build123d as bd
from cadgen.engine import configuration, couple, xbf

SWEEP = {"left": -70, "right": 70}  # each door's turn when open, degrees
TRAVEL = 40  # and its travel along the hinge, mm


@xbf
def hypercar():
    chassis = bd.Box(4400, 900, 400, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    chassis.label = "chassis"
    doors = []
    for side, y in (("left", 475), ("right", -475)):
        door = bd.Pos(400, y, 500) * bd.Box(1000, 50, 600)
        door.label = f"door_{side}"
        hinge = bd.Axis((800, 450 if side == "left" else -450, 500), (0.3, 0, 1))  # a helical hinge
        bd.CylindricalJoint(f"door_{side}", chassis, axis=hinge, angular_range=tuple(sorted((0, SWEEP[side]))),
                            linear_range=(0, TRAVEL))
        bd.RigidJoint(f"door_{side}", door, bd.Plane(origin=hinge.position, z_dir=hinge.direction).location)
        chassis.joints[f"door_{side}"].connect_to(door.joints[f"door_{side}"], angle=0, position=0)  # shut
        doors.append(door)
    car = bd.Compound(children=[chassis, *doors], label="hypercar")
    couple(car, "doors", door_left=(SWEEP["left"], TRAVEL), door_right=(SWEEP["right"], TRAVEL))
    return configuration(car, "open", doors=1)  # shut is as built
"""

# A gear train: three wheels on a plate, one locked control turning them at fixed ratios.
TRAIN = """
import build123d as bd
from cadgen.engine import configuration, couple, motion, xbf

RATIOS = {{"sun": 1.0, "idler": -2.0, "ring": 0.5}}


@xbf
def train():
    plate = bd.Box(200, 60, 5, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MAX))
    plate.label = "plate"
    wheels = []
    for at, name in enumerate(RATIOS):
        centre = (30 + 60 * at, 0, 0)
        wheel = bd.Pos(*centre) * bd.Cylinder(20, 8, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        wheel.label = name
        bd.RevoluteJoint(name, plate, axis=bd.Axis(centre, (0, 0, 1)), angular_range=(-720, 720))
        bd.RigidJoint(name, wheel, bd.Plane(origin=centre, z_dir=(0, 0, 1)).location)
        plate.joints[name].connect_to(wheel.joints[name], angle=0)
        wheels.append(wheel)
    train = bd.Compound(children=[plate, *wheels], label="train")
    couple(train, "drive", range=(0, 360), lock=True, **RATIOS)
    configuration(train, "quarter", drive=90)
    {extra}
    return motion(train, "run", sweep={{"drive": (0, 360)}}, duration=4, ease="linear", loop="loop")
"""

# Two fingers, one function's: each placement's joints and coupling labelled alike.
HAND = """
import build123d as bd
from cadgen.engine import configuration, couple, xbf


def finger(name):
    bones, x = [], 0
    for bone_name, length in (("proximal", 40), ("middle", 25), ("distal", 20)):
        bone = bd.Pos(x, 0, 0) * bd.Box(length, 16, 12, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
        bone.label = bone_name
        bones.append(bone)
        x += length
    for joint, parent, child, at in (("pip", bones[0], bones[1], 40), ("dip", bones[1], bones[2], 65)):
        knuckle = bd.Axis((at, 0, 0), (0, 1, 0))
        bd.RevoluteJoint(joint, parent, axis=knuckle, angular_range=(0, 100))
        bd.RigidJoint(joint, child, bd.Plane(origin=knuckle.position, z_dir=knuckle.direction).location)
        parent.joints[joint].connect_to(child.joints[joint], angle=0)
    found = bd.Compound(children=bones, label=name)
    return couple(found, "curl", pip=100, dip=70)


@xbf
def hand():
    palm = bd.Box(10, 60, 12, align=(bd.Align.MAX, bd.Align.CENTER, bd.Align.CENTER))
    palm.label = "palm"
    hand = bd.Compound(children=[palm, finger("index"), bd.Pos(0, 20, 0) * finger("middle")], label="hand")
    return configuration(hand, "fist", curl=1)
"""


def _vertices(loaded: document.Loaded, name: str) -> np.ndarray:
    """Every vertex of the instance named ``name``, where the document places it."""
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopoDS import TopoDS
    from OCP.TopTools import TopTools_IndexedMapOfShape

    instance = next(i for i in loaded.instances if i.path[-1] == name)
    mapped = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(loaded.prototypes[instance.prototype].shape, TopAbs_VERTEX, mapped)
    matrix = np.array(instance.matrix).reshape(4, 4).T
    points = [BRep_Tool.Pnt_s(TopoDS.Vertex_s(mapped.FindKey(at))) for at in range(1, mapped.Extent() + 1)]
    return np.array(sorted((matrix @ np.array([p.X(), p.Y(), p.Z(), 1.0]))[:3].tolist() for p in points))


def _run(*argv: str) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, err.getvalue()


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        from cadgen.engine.kernel import memo

        self.folder = Path(tempfile.mkdtemp(prefix="engine-couplings-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)
        memo.install()
        self.addCleanup(memo.clear)

    def build(self, name: str, source: str, *, ok: bool = True) -> dict:
        script = self.folder / f"{name}.py"
        script.write_text(textwrap.dedent(source), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        report = engine_build.build(script, [name], cwd=self.folder)[0]
        self.assertEqual(report["ok"], ok, report)
        return report

    def finger(self, couple: str = CURL, extra: str = "pass", ok: bool = True) -> dict:
        return self.build("index_finger", FINGER.format(couple=couple, extra=extra), ok=ok)

    def cold(self, name: str, source: str) -> bytes:
        """The model built by a process that never built it: its xbf's bytes."""
        from cadgen.engine.kernel import memo

        memo.clear()
        engine_build._WRITTEN.clear()
        engine_build._PARTS.clear()
        (self.folder / f"XBF/{name}.xbf").unlink()
        self.assertFalse(self.build(name, source)["converted"]["patched"])
        return (self.folder / f"XBF/{name}.xbf").read_bytes()


class AuthoringTest(unittest.TestCase):
    def test_couple_says_what_is_wrong_by_name_and_returns_the_assembly(self) -> None:
        import build123d as bd

        from cadgen.engine import couple

        box = bd.Box(1, 1, 1)
        for call, error in (
                (lambda: couple("arm", "c", j=1), "takes the assembly"),
                (lambda: couple(box, " ", j=1), "a coupling's name is a non-empty string"),
                (lambda: couple(box, "c"), "coupling 'c' drives no joint"),
                (lambda: couple(box, "c", j="x"), "joint 'j' takes a ratio"),
                (lambda: couple(box, "c", j=float("nan")), "joint 'j' takes a ratio"),
                (lambda: couple(box, "c", range=5, j=1), "range is \\(lower, upper\\)"),
                (lambda: couple(box, "c", range=(1, 0), j=1), "lower below upper"),
                (lambda: couple(box, "c", range=(0.2, 1), j=1), "its range holds 0, the as-built pose"),
                (lambda: couple(box, "c", lock="yes", j=1), "lock is True or False")):
            with self.assertRaisesRegex((TypeError, ValueError), error):
                call()
        self.assertIs(couple(box, "c", range=(-1, 2), lock=True, j=2, k=(1, 3)), box)
        self.assertEqual(box.__dict__[mechanism.COUPLINGS]["c"], ({"j": (2.0,), "k": (1.0, 3.0)}, (-1.0, 2.0), True))


class FingerTest(_Folder):
    XBF = "XBF/index_finger.xbf"

    def test_a_coupling_round_trips_through_the_xbf_and_is_served(self) -> None:
        report = self.finger()
        self.assertNotIn("kinematics", report, "no warning")
        path = self.folder / self.XBF
        loaded = document.Loaded(path)
        found = loaded.mechanism
        self.assertEqual(found.couplings, {"index_curl": mechanism.Coupling(
            "index_curl", {label: (float(ratio),) for label, ratio in RATIOS.items()}, (0.0, 1.0), False)})
        self.assertEqual(found.as_built, {label: (0.0,) for label in RATIOS})
        # The motion sweeping the coupling is its joints' values, resolved at document time.
        self.assertEqual(found.motions["grip"].keys, [(0.0, None, {label: (0.0,) for label in RATIOS}),
                                                      (1.5, None, {label: (float(r),) for label, r in RATIOS.items()})])
        # The subtree: the engine's own entity at :6, its terms a state's rows.
        from OCP.Standard import Standard_GUID

        root = next(label for label in mechanism._children_of(loaded.doc.Main())
                    if label.IsAttribute(Standard_GUID(mechanism.GUID)))
        held = root.FindChild(1, False).FindChild(6, False).FindChild(1, False)
        data = mechanism._named(held)
        self.assertEqual((mechanism._string(data, "entity"), mechanism._real(data, "lower_limit"),
                          mechanism._real(data, "upper_limit"), data.HasInteger(mechanism._text("lock"))),
                         ("cadgen_coupling", 0.0, 1.0, False))
        # The structure serves it, the same from the file as from the build's tree.
        block = loaded.structure()["kinematics"]
        self.assertEqual(block["couplings"], {"index_curl": {"range": [0.0, 1.0], "lock": False, "terms": [
            [label, 0, float(ratio)] for label, ratio in RATIOS.items()]}})
        self.assertEqual(block, document.tree_structure(document.saved(path), path)["kinematics"])

    def test_forward_kinematics_at_a_coupling_value_is_the_joints_set_by_hand(self) -> None:
        self.finger()
        path = self.folder / self.XBF
        structure = document.Loaded(path).structure()
        values, named = mechanism.resolve(structure["kinematics"], {"index_curl": 0.6})
        by_hand, _named = mechanism.resolve(structure["kinematics"], BY_HAND)
        self.assertIsNone(named)
        for label in RATIOS:
            self.assertAlmostEqual(values[label][0], by_hand[label][0], places=12)
        np.testing.assert_allclose(mechanism.posed_instances(structure, values),
                                   mechanism.posed_instances(structure, by_hand), atol=1e-9)
        # A posed STEP: the distal bone where the joints set by hand put it, and moved from as built.
        posed = {}
        for name, pose in (("coupled", '{"index_curl": 0.6}'), ("by_hand", json.dumps(BY_HAND))):
            out = self.folder / f"{name}.step"
            code, err = _run("export", str(path), str(out), "--no-daemon", "--pose", pose)
            self.assertEqual(code, 0, err)
            posed[name] = _vertices(document.Loaded(out), "distal")
        np.testing.assert_allclose(posed["coupled"], posed["by_hand"], atol=1e-6)
        self.assertGreater(np.abs(posed["coupled"] - _vertices(document.Loaded(path), "distal")).max(), 10.0)

    def test_what_a_coupling_pose_names_wrong_is_refused_by_name_before_anything_is_written(self) -> None:
        self.finger()
        path = str(self.folder / self.XBF)
        out = self.folder / "refused.step"
        for pose, words in (('{"index_curl": 1.5}', ["coupling 'index_curl' at 1.5 is outside its range [0, 1]"]),
                            ('{"index_curl": 0.5, "index_pip": 10}',
                             ["sets coupling 'index_curl' and its joint 'index_pip': set one of them"]),
                            ('{"index_curl": [0.5, 1]}', ["coupling 'index_curl' takes a number"]),
                            ('{"crul": 1}', ["names no joint 'crul'", "its couplings: index_curl"])):
            code, err = _run("export", path, str(out), "--no-daemon", "--pose", pose)
            self.assertEqual(code, 2, pose)
            for word in words:
                self.assertIn(word, err, pose)
            self.assertFalse(out.exists(), pose)

    def test_a_snapshot_draws_a_coupling_as_its_joints_set_by_hand(self) -> None:
        from cadgen.engine.render import context
        from cadgen.engine.snapshot import SnapshotRefused, snapshot

        self.finger()
        previous = os.environ.get("CADGEN_ENGINE_MESH")
        os.environ["CADGEN_ENGINE_MESH"] = "inline"  # one part at a time on this thread: no pool
        self.addCleanup(lambda: os.environ.pop("CADGEN_ENGINE_MESH") if previous is None
                        else os.environ.__setitem__("CADGEN_ENGINE_MESH", previous))
        path = self.folder / self.XBF
        out = self.folder / "stale.png"
        out.write_bytes(b"before")
        with self.assertRaisesRegex(SnapshotRefused, "--pose: .*coupling 'index_curl' at 2 is outside its range"):
            snapshot(path, out, pose={"index_curl": 2}, size=(160, 120), daemon=False)
        self.assertFalse(out.exists(), "a refused snapshot leaves no picture at OUT")
        # The posed structure the picture is drawn from: every instance where the joints set by hand put it.
        structure = document.Loaded(path).structure()
        coupled = mechanism.posed_structure(structure, {"index_curl": 0.6})
        by_hand = mechanism.posed_structure(structure, BY_HAND)
        np.testing.assert_allclose([i["matrix"] for i in coupled["instances"]],
                                   [i["matrix"] for i in by_hand["instances"]], atol=1e-9)
        reason = context.available()
        if reason is not None:
            self.skipTest(f"the posed structure is checked; drawing it needs an OpenGL context: {reason}")
        drawn = {}
        for name, pose in (("rest", None), ("coupled", {"index_curl": 0.6}), ("by_hand", BY_HAND)):
            report = snapshot(path, self.folder / f"{name}.png", camera="front", size=(160, 120), daemon=False,
                              pose=pose)
            drawn[name] = (self.folder / f"{name}.png").read_bytes()
            if name == "coupled":
                self.assertAlmostEqual(report["pose"]["values"]["index_pip"], 60.0, places=9)
        self.assertEqual(drawn["coupled"], drawn["by_hand"], "the coupled picture is the joints set by hand")
        self.assertNotEqual(drawn["coupled"], drawn["rest"])

    def test_a_warm_edit_of_a_coupling_patches_to_the_cold_bytes_and_renames_nothing(self) -> None:
        path = self.folder / self.XBF
        without = FINGER.format(couple="return finger", extra="")  # no coupling, nor the motion naming it
        self.build("index_finger", without)
        bare = document.Loaded(path).structure()
        self.finger()
        names = document.Loaded(path).structure()
        self.assertEqual([(p["name"], p["faceNames"], p["edgeNames"]) for p in names["prototypes"]],
                         [(p["name"], p["faceNames"], p["edgeNames"]) for p in bare["prototypes"]],
                         "a coupling renames no element")
        edited = 'couple(finger, "index_curl", range=(-0.1, 1), index_mcp_flexion=80, index_pip=100, index_dip=70)'
        report = self.finger(couple=edited)
        self.assertTrue(report["converted"]["patched"], report["converted"])
        self.assertEqual(report["converted"]["converted"], 0, "a coupling edit converts no prototype")
        self.assertEqual(document.Loaded(path).mechanism.couplings["index_curl"].ratios["index_mcp_flexion"], (80.0,))
        warm = path.read_bytes()
        self.assertEqual(self.cold("index_finger", FINGER.format(couple=edited, extra="pass")), warm)
        # A coupling made locked, then removed (the motion naming it with it): each warm file the cold one's.
        locked = edited.replace("range=", "lock=True, range=")
        self.finger(couple=locked)
        self.assertTrue(document.Loaded(path).mechanism.couplings["index_curl"].lock)
        warm = path.read_bytes()
        self.assertEqual(self.cold("index_finger", FINGER.format(couple=locked, extra="pass")), warm)
        self.build("index_finger", without)
        warm = path.read_bytes()
        self.assertEqual(document.Loaded(path).mechanism.couplings, {})
        self.assertEqual(self.cold("index_finger", without), warm)


class DoorTest(_Folder):
    def test_one_control_turns_and_slides_a_cylindrical_joint(self) -> None:
        self.build("hypercar", HYPERCAR)
        loaded = document.Loaded(self.folder / "XBF/hypercar.xbf")
        found = loaded.mechanism
        self.assertEqual([(pair.label, pair.kind) for pair in found.pairs],
                         [("door_left", "cylindrical"), ("door_right", "cylindrical")])
        self.assertEqual(found.couplings["doors"].ratios, {"door_left": (-70.0, 40.0), "door_right": (70.0, 40.0)})
        # The configuration naming it holds its joints' values: the turn and the travel from shut.
        for label, turn in (("door_left", -70.0), ("door_right", 70.0)):
            np.testing.assert_allclose(found.configurations["open"][label], (turn, 40.0), atol=1e-9)
        kinematics = loaded.structure()["kinematics"]
        values, _named = mechanism.resolve(kinematics, {"doors": 0.5})
        np.testing.assert_allclose(values["door_left"], (-35.0, 20.0), atol=1e-9)
        np.testing.assert_allclose(values["door_right"], (35.0, 20.0), atol=1e-9)
        half = self.folder / "half.step"
        code, err = _run("export", str(self.folder / "XBF/hypercar.xbf"), str(half), "--no-daemon", "--pose",
                         '{"doors": 0.5}')
        self.assertEqual(code, 0, err)
        by_hand = self.folder / "by_hand.step"
        self.assertEqual(_run("export", str(self.folder / "XBF/hypercar.xbf"), str(by_hand), "--no-daemon", "--pose",
                              '{"door_left": [-35, 20], "door_right": [35, 20]}')[0], 0)
        for door in ("door_left", "door_right"):
            np.testing.assert_allclose(_vertices(document.Loaded(half), door),
                                       _vertices(document.Loaded(by_hand), door), atol=1e-6)

    def test_a_ratio_short_of_a_joint_s_values_leaves_the_joint_out_and_warns(self) -> None:
        report = self.build("hypercar", HYPERCAR.replace("door_left=(SWEEP[\"left\"], TRAVEL)", "door_left=70"))
        self.assertIn("coupling 'doors' of hypercar: joint 'door_left' (cylindrical) takes 2 ratios (angle, position), "
                      "not 70: the joint is left out of it", report["kinematics"]["warnings"])
        self.assertEqual(list(document.Loaded(self.folder / "XBF/hypercar.xbf").mechanism.couplings["doors"].ratios),
                         ["door_right"])


class LockTest(_Folder):
    def test_a_locked_coupling_is_its_joints_only_control(self) -> None:
        report = self.build("train", TRAIN.format(extra='configuration(train, "jammed", idler=10)'))
        self.assertIn("configuration 'jammed' of train: joint 'idler' moves only with the locked coupling 'drive'; "
                      "set 'drive': the value is left out", report["kinematics"]["warnings"])
        loaded = document.Loaded(self.folder / "XBF/train.xbf")
        self.assertTrue(loaded.mechanism.couplings["drive"].lock)
        quarter = {"sun": (90.0,), "idler": (-180.0,), "ring": (45.0,)}
        self.assertEqual(loaded.mechanism.configurations["quarter"], quarter)
        kinematics = loaded.structure()["kinematics"]
        self.assertEqual(mechanism.resolve(kinematics, {"drive": 10})[0],
                         {"sun": (10.0,), "idler": (-20.0,), "ring": (5.0,)})
        with self.assertRaisesRegex(mechanism.KinematicsError,
                                    "joint 'idler' moves only with the locked coupling 'drive'"):
            mechanism.resolve(kinematics, {"idler": 5})
        # The document's own values pose it: a configuration, and the motion baked into a GLB.
        self.assertEqual(mechanism.resolve(kinematics, "quarter")[0]["ring"], (45.0,))
        timeline = mechanism.Timeline(kinematics, "run")
        self.assertEqual(timeline.values(1.0), quarter)
        from cadgen.engine.export import translate

        glb = self.folder / "train.glb"
        translate(self.folder / "XBF/train.xbf", None, glb)
        self.assertGreater(glb.stat().st_size, 0)

    def test_a_locked_joint_another_coupling_drives_fails_the_build_by_name(self) -> None:
        report = self.build("train", TRAIN.format(extra='couple(train, "trim", ring=1)'), ok=False)
        self.assertIn("joint 'ring' is driven by the locked coupling 'drive' and by 'trim'", report["error"])


class HarvestTest(_Folder):
    def test_a_coupling_naming_no_joint_or_named_as_one_fails_the_build_by_name(self) -> None:
        report = self.finger(extra='couple(finger, "spread", knuckle=1)', ok=False)
        self.assertIn("coupling 'spread' of index_finger names no joint 'knuckle'; its joints are index_dip, "
                      "index_mcp_flexion, index_pip", report["error"])
        report = self.finger(extra='couple(finger, "index_pip", index_mcp_flexion=1)', ok=False)
        self.assertIn("coupling 'index_pip' of index_finger has a joint's name", report["error"])
        report = self.finger(extra='configuration(finger, "fist", crul=1)', ok=False)
        self.assertIn("configuration 'fist' of index_finger names no joint 'crul'", report["error"])
        self.assertIn("its couplings are index_curl", report["error"])

    def test_each_placement_s_coupling_is_kept_by_its_path_and_a_configuration_sets_them_all(self) -> None:
        self.build("hand", HAND)
        path = self.folder / "XBF/hand.xbf"
        found = document.Loaded(path).mechanism
        self.assertEqual(sorted(found.couplings), ["hand/index:curl", "hand/middle:curl"])
        index = found.couplings["hand/index:curl"]
        self.assertEqual(index.ratios, {"hand/index/proximal:pip": (100.0,), "hand/index/middle:dip": (70.0,)},
                         "its own placement's joints alone")
        self.assertEqual(found.configurations["fist"], {
            "hand/index/proximal:pip": (100.0,), "hand/index/middle:dip": (70.0,),
            "hand/middle/proximal:pip": (100.0,), "hand/middle/middle:dip": (70.0,)})
        kinematics = document.Loaded(path).structure()["kinematics"]
        values, _named = mechanism.resolve(kinematics, {"hand/index:curl": 0.5})
        self.assertEqual([values[label] for label in ("hand/index/proximal:pip", "hand/index/middle:dip",
                                                      "hand/middle/proximal:pip")], [(50.0,), (35.0,), (0.0,)])
        # A part of the document keeps its share of a coupling: the terms on the pairs it keeps (the
        # middle and distal bones, moved by the knuckle between them; the proximal stands with the palm).
        from cadgen.engine.export import translate

        part = self.folder / "index.xbf"
        translate(path, None, part, part="hand/index")
        kept = document.Loaded(part).mechanism
        self.assertEqual([pair.label for pair in kept.pairs], ["hand/index/middle:dip"])
        self.assertEqual(kept.couplings, {"hand/index:curl": mechanism.Coupling(
            "hand/index:curl", {"hand/index/middle:dip": (70.0,)}, (0.0, 1.0), False)})


if __name__ == "__main__":
    unittest.main()
