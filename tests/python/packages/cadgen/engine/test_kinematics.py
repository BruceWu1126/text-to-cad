"""Kinematics: build123d's joints harvested into the xbf's mechanism (AP242's links, pairs,
ground and configurations) and read back; a chain's links and a prismatic pair; a gear pair
driving one pair from another; poses by a configuration's name and by joint values placing a
part where the closed form says, in a posed STEP that carries no mechanism; what a pose names
wrong refused by name; the structure's ``kinematics`` the same from the file and from the
build; a document served at a pose."""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import shutil
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np

from cadgen.engine import build as engine_build
from cadgen.engine import cli, document, mechanism
from cadgen.engine.serve import Server

HINGE = """
import build123d as bd
from cadgen.engine import configuration, step, xbf


@xbf
@step(out="hinge.step")
def hinge():
    base = bd.Box(100, 60, 20)
    base.label = "base"
    arm = bd.Box(120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    arm.label = "arm"
    bd.RevoluteJoint("elbow", base, axis=bd.Axis((40, 0, 20), (0, 1, 0)), angular_range=(-10, 120))
    bd.RigidJoint("mount", arm, bd.Location())
    {connect}
    root = bd.Compound(children=[base, arm], label="hinge")
    {after}
    configuration(root, "raised", elbow=90)
    configuration(root, "flat", {flat})
    return root
"""
CONNECT = 'base.joints["elbow"].connect_to(arm.joints["mount"], angle=30)'

CHAIN = """
import build123d as bd
from cadgen.engine import configuration, xbf


@xbf
def lift():
    base = bd.Box(80, 80, 10)
    base.label = "base"
    carriage = bd.Box(30, 30, 10)
    carriage.label = "carriage"
    clamp = bd.Box(10, 30, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.CENTER))
    clamp.label = "clamp"
    arm = bd.Box(5, 40, 5, align=(bd.Align.MIN, bd.Align.MIN, bd.Align.CENTER))
    arm.label = "arm"
    bd.LinearJoint("lift", base, axis=bd.Axis((0, 0, 5), (0, 0, 1)), linear_range=(10, 90))
    bd.RigidJoint("bore", carriage, bd.Location())
    base.joints["lift"].connect_to(carriage.joints["bore"], position=40)
    bd.RigidJoint("side", carriage, bd.Location((15, 0, 0)))
    bd.RigidJoint("back", clamp, bd.Location())
    carriage.joints["side"].connect_to(clamp.joints["back"])
    bd.RevoluteJoint("wrist", clamp, axis=bd.Axis((10, 0, 0), (1, 0, 0)), angular_range=(-90, 90))
    bd.RigidJoint("hub", arm, bd.Location())
    clamp.joints["wrist"].connect_to(arm.joints["hub"], angle=0)
    root = bd.Compound(children=[base, carriage, clamp, arm], label="lift")
    configuration(root, "top", lift=90, wrist=45)
    return root
"""


def _arm_tip(q: float) -> np.ndarray:
    """The arm's corner (120, 10, 10) of its own frame, at elbow ``q``, in closed form: the joint
    frame sits at (40, 0, 20) with X along +Z, Y along +X and Z (the axis) along +Y, and the arm's
    frame is it turned by ``q`` about Z: X' = (sin q, 0, cos q), Y' = (cos q, 0, -sin q)."""
    s, c = math.sin(math.radians(q)), math.cos(math.radians(q))
    return np.array([40 + 120 * s + 10 * c, 10, 20 + 120 * c - 10 * s])


def _vertices(loaded: document.Loaded, name: str) -> np.ndarray:
    """Every vertex of the instance named ``name``, where the document places it."""
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape
    from OCP.BRep import BRep_Tool
    from OCP.TopoDS import TopoDS

    instance = next(i for i in loaded.instances if i.path[-1] == name)
    shape = loaded.prototypes[instance.prototype].shape
    mapped = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_VERTEX, mapped)
    matrix = np.array(instance.matrix).reshape(4, 4).T
    points = []
    for at in range(1, mapped.Extent() + 1):
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(mapped.FindKey(at)))
        points.append((matrix @ np.array([point.X(), point.Y(), point.Z(), 1.0]))[:3])
    return np.array(points)


def _has_point(points: np.ndarray, point: np.ndarray) -> bool:
    return bool(np.min(np.linalg.norm(points - point, axis=1)) < 1e-6)


def _run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class _Folder(unittest.TestCase):
    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp(prefix="engine-kinematics-")).resolve()
        self.addCleanup(shutil.rmtree, self.folder, ignore_errors=True)

    def build(self, name: str, source: str, *, ok: bool = True) -> dict:
        script = self.folder / f"{name}.py"
        script.write_text(textwrap.dedent(source), encoding="utf-8")
        os.utime(script, ns=(time.time_ns(), time.time_ns()))
        report = engine_build.build(script, [name], cwd=self.folder)[0]
        self.assertEqual(report["ok"], ok, report)
        return report

    def hinge(self, connect: str = CONNECT, after: str = "pass", flat: str = "elbow=0", ok: bool = True) -> dict:
        return self.build("hinge", HINGE.format(connect=connect, after=after, flat=flat), ok=ok)


class HingeTest(_Folder):
    def test_a_hinge_round_trips_through_the_xbf(self) -> None:
        self.hinge()
        path = self.folder / "hinge.xbf"
        loaded = document.Loaded(path)
        found = loaded.mechanism
        self.assertIsNotNone(found, "the document holds the mechanism")
        self.assertEqual([link.name for link in found.links], ["base", "arm"])
        self.assertEqual(found.ground, 0)
        (pair,) = found.pairs
        self.assertEqual((pair.label, pair.kind, pair.links, pair.limits), ("elbow", "revolute", (0, 1), ((-10.0, 120.0),)))
        # transform_item_1, in the base's frame: the joint's axis through (40, 0, 20) along +Y.
        np.testing.assert_allclose(pair.frames[0][:6], [40, 0, 20, 0, 1, 0], atol=1e-12)
        self.assertEqual(found.as_built, {"elbow": (30.0,)})
        self.assertEqual(found.configurations, {"flat": {"elbow": (0.0,)}, "raised": {"elbow": (90.0,)}})
        # The subtree is AP242's: the pair's entity, found by the GUID the root label carries.
        from OCP.Standard import Standard_GUID

        root = next(label for label in mechanism._children_of(loaded.doc.Main())
                    if label.IsAttribute(Standard_GUID(mechanism.GUID)))
        top = root.FindChild(1, False)
        self.assertEqual(mechanism._string(mechanism._named(top), "entity"), "MECHANISM_REPRESENTATION")
        self.assertEqual(mechanism._string(mechanism._named(top.FindChild(2, False).FindChild(1, False)), "entity"),
                         "REVOLUTE_PAIR_WITH_RANGE")
        self.assertEqual(mechanism._real(mechanism._named(top.FindChild(2, False).FindChild(1, False)),
                                         "upper_limit_actual_rotation"), 120.0)
        # The structure carries it, the same from the file as from the build's tree.
        structure = loaded.structure()
        made = document.tree_structure(document.saved(path), path)
        self.assertEqual(structure["kinematics"], made["kinematics"])
        block = structure["kinematics"]
        self.assertEqual([link["instances"] for link in block["links"]], [[0], [1]])
        self.assertEqual(block["pairs"][0]["type"], "revolute")
        self.assertEqual(block["asBuilt"], {"elbow": 30.0})
        self.assertEqual(block["configurations"], {"flat": {"elbow": 0.0}, "raised": {"elbow": 90.0}})
        self.assertIsNone(block["loop"])
        # As built, the arm stands where connect_to put it.
        self.assertTrue(_has_point(_vertices(loaded, "arm"), _arm_tip(30)))

    def test_an_edit_of_a_joint_rewrites_the_xbf(self) -> None:
        self.hinge()
        report = self.hinge(flat="elbow=-5")
        self.assertTrue(report["files"][str(self.folder / "hinge.xbf")]["written"])
        self.assertEqual(document.Loaded(self.folder / "hinge.xbf").mechanism.configurations["flat"], {"elbow": (-5.0,)})

    def test_poses_by_name_and_by_value_place_the_arm_where_the_closed_form_says(self) -> None:
        self.hinge()
        path = str(self.folder / "hinge.xbf")
        for pose, q in (("raised", 90.0), ('{"elbow": 45}', 45.0), ("flat", 0.0)):
            out = self.folder / f"posed-{q:g}.step"
            code, _out, err = _run("export", path, "--step", str(out), "--pose", pose)
            self.assertEqual(code, 0, err)
            posed = document.Loaded(out)
            self.assertTrue(_has_point(_vertices(posed, "arm"), _arm_tip(q)), pose)
            self.assertFalse(_has_point(_vertices(posed, "arm"), _arm_tip(30)), pose)
            self.assertTrue(_has_point(_vertices(posed, "base"), np.array([50.0, 30.0, 10.0])), "the ground stays")
            # The STEP carries none of the mechanism: OCCT writes no AP242 kinematics.
            text = out.read_text(errors="replace")
            self.assertEqual([word for word in ("_PAIR", "MECHANISM", "KINEMATIC") if word in text], [])
        # The build's own STEP is as built, and carries none either.
        built = document.Loaded(self.folder / "hinge.step")
        self.assertTrue(_has_point(_vertices(built, "arm"), _arm_tip(30)))
        self.assertNotIn("MECHANISM", (self.folder / "hinge.step").read_text(errors="replace"))
        # A mesh export at a pose: the arm's node placed as the pair places it.
        glb = self.folder / "posed.glb"
        self.assertEqual(_run("export", path, "--glb", str(glb), "--pose", "raised")[0], 0)
        data = glb.read_bytes()
        length = int.from_bytes(data[12:16], "little")
        nodes = json.loads(data[20:20 + length])["nodes"]
        arm = np.array(next(node for node in nodes if node.get("name") == "arm")["matrix"]).reshape(4, 4).T
        np.testing.assert_allclose((arm @ np.array([120.0, 10.0, 10.0, 1.0]))[:3], _arm_tip(90), atol=1e-6)

    def test_what_a_pose_names_wrong_is_refused_by_name_before_anything_is_written(self) -> None:
        self.hinge()
        path = str(self.folder / "hinge.xbf")
        out = self.folder / "refused.step"
        for pose, words in (('{"elbw": 10}', ["'elbw'", "elbow"]), ('{"elbow": 150}', ["'elbow'", "150", "[-10, 120]"]),
                            ("folded", ["'folded'", "flat, raised"]), ('{"elbow": [1, 2]}', ["'elbow'", "a number"])):
            code, _out, err = _run("export", path, "--step", str(out), "--pose", pose)
            self.assertEqual(code, 2, pose)
            for word in words:
                self.assertIn(word, err, pose)
            self.assertFalse(out.exists(), pose)

    def test_a_configuration_naming_a_joint_wrong_fails_the_build_by_name(self) -> None:
        report = self.hinge(flat="elbw=0", ok=False)
        self.assertIn("names no joint 'elbw'", report["error"])
        self.assertIn("elbow", report["error"])
        report = self.hinge(flat="elbow=200", ok=False)
        self.assertIn("'elbow' at 200 deg is outside its range [-10, 120]", report["error"])

    def test_a_part_moved_after_it_was_connected_is_refused_by_name(self) -> None:
        # Connected after the compound holding the arm was made: the document places the arm
        # where it was, not where the joint put it.
        report = self.hinge(connect="pass", after=CONNECT, ok=False)
        self.assertIn("is not where joint 'elbow' can put it", report["error"])

    def test_a_snapshot_draws_a_pose_and_refuses_a_bad_one_before_it_renders(self) -> None:
        from cadgen.engine.snapshot import SnapshotRefused, snapshot

        self.hinge()
        previous = os.environ.get("CADGEN_ENGINE_MESH")
        os.environ["CADGEN_ENGINE_MESH"] = "inline"  # one part at a time on this thread: no pool
        self.addCleanup(lambda: os.environ.pop("CADGEN_ENGINE_MESH") if previous is None
                        else os.environ.__setitem__("CADGEN_ENGINE_MESH", previous))
        path = self.folder / "hinge.xbf"
        out = self.folder / "kept.png"
        out.write_bytes(b"before")
        for pose, words in (({"elbw": 1}, "names no joint 'elbw'"), ({"elbow": 150}, "'elbow' at 150 deg is outside"),
                            ("folded", "no configuration 'folded'")):
            with self.assertRaisesRegex(SnapshotRefused, words):
                snapshot(path, out, pose=pose, size=(160, 120), daemon=False)
        self.assertEqual(out.read_bytes(), b"before")
        rest = snapshot(path, self.folder / "rest.png", camera="front", size=(160, 120), daemon=False)
        raised = snapshot(path, self.folder / "raised.png", camera="front", size=(160, 120), daemon=False, pose="raised")
        self.assertIsNone(rest["pose"])
        self.assertEqual(raised["pose"], {"values": {"elbow": 90.0}, "configuration": "raised"})
        self.assertNotEqual((self.folder / "rest.png").read_bytes(), (self.folder / "raised.png").read_bytes())

    def test_a_document_is_served_at_a_pose_and_a_bad_pose_is_refused(self) -> None:
        self.hinge()
        server = Server(port=0, mesh_mode="inline")
        server.start()
        self.addCleanup(server.stop)
        ident = server.add(self.folder / "hinge.xbf")

        def call(path: str, body: dict | None = None) -> dict:
            data = None if body is None else json.dumps(body).encode()
            request = urllib.request.Request(server.url + path, data=data, method="GET" if data is None else "POST")
            with urllib.request.urlopen(request, timeout=60) as answer:
                return json.loads(answer.read())

        plain = call(f"/api/doc/{ident}")
        self.assertEqual(plain["kinematics"]["asBuilt"], {"elbow": 30.0})
        answer = call(f"/api/doc/{ident}/pose", {"pose": {"elbow": 90}})
        self.assertEqual((answer["values"], answer["configuration"]), ({"elbow": 90.0}, None))
        posed = call(f"/api/doc/{answer['id']}")
        self.assertEqual(posed["instances"][0]["matrix"], plain["instances"][0]["matrix"])
        arm = np.array(posed["instances"][1]["matrix"]).reshape(4, 4).T
        np.testing.assert_allclose((arm @ np.array([120.0, 10.0, 10.0, 1.0]))[:3], _arm_tip(90), atol=1e-9)
        self.assertEqual(posed["kinematics"]["pose"], {"values": {"elbow": 90.0}, "configuration": None})
        with self.assertRaises(urllib.error.HTTPError) as refused:
            call(f"/api/doc/{ident}/pose", {"pose": {"elbw": 1}})
        self.assertEqual(refused.exception.code, 400)
        self.assertIn("'elbw'", json.loads(refused.exception.read())["error"])


class JointMemoTest(_Folder):
    def test_a_body_with_joints_is_retained_and_an_edited_joint_runs_it_again(self) -> None:
        # A joint on a part keys the compound that holds it: an unchanged build replays the body.
        self.hinge()
        warm = self.hinge()
        self.assertEqual((warm["memo"]["uncached"], warm["memo"]["bodies_run"], warm["memo"]["bodies_skipped"]),
                         (0, 0, 1), warm["memo"])
        self.assertEqual(document.Loaded(self.folder / "hinge.xbf").mechanism.as_built, {"elbow": (30.0,)})
        # A joint unconnected keys too.
        lone = """
            import build123d as bd
            from cadgen.engine import xbf

            @xbf
            def lone():
                a = bd.Box(10, 10, 10)
                bd.RigidJoint("top", a, bd.Location((0, 0, 5)))
                return bd.Compound(children=[a])
            """
        self.build("lone", lone)
        self.assertEqual(self.build("lone", lone)["memo"]["bodies_skipped"], 1)
        # The angle connected at is the joint's: an edit of it misses, and the arm moves.
        moved = self.hinge(connect=CONNECT.replace("angle=30", "angle=60"))
        self.assertEqual(moved["memo"]["bodies_run"], 1)
        loaded = document.Loaded(self.folder / "hinge.xbf")
        self.assertEqual(loaded.mechanism.as_built, {"elbow": (60.0,)})
        self.assertTrue(_has_point(_vertices(loaded, "arm"), _arm_tip(60)))
        # A range keys the joint: an edit of it alone, which moves nothing, is no hit of the old
        # joint (a copy of the base hands its joints on: a hit would hand back the first run's).
        source = HINGE.format(connect="base = base.moved(bd.Location()); " + CONNECT, after="pass", flat="elbow=0")
        self.build("hinge", source)
        self.build("hinge", source.replace("(-10, 120)", "(-20, 100)"))
        self.assertEqual(document.Loaded(self.folder / "hinge.xbf").mechanism.pairs[0].limits, ((-20.0, 100.0),))


class ChainTest(_Folder):
    def test_a_chain_of_rigid_prismatic_and_revolute_joints_makes_its_links(self) -> None:
        self.build("lift", CHAIN)
        loaded = document.Loaded(self.folder / "lift.xbf")
        found = loaded.mechanism
        # The base is the ground; the clamp is rigidly joined to the carriage: one link, which the
        # lift moves, and the wrist turns the arm on it.
        self.assertEqual([(link.name, link.members) for link in found.links],
                         [("base", [(0,)]), ("carriage", [(1,), (2,)]), ("arm", [(3,)])])
        lift, wrist = found.pairs
        self.assertEqual((lift.label, lift.kind, lift.links, lift.limits), ("lift", "prismatic", (0, 1), ((10.0, 90.0),)))
        self.assertEqual((wrist.label, wrist.kind, wrist.links), ("wrist", "revolute", (1, 2)))
        self.assertEqual(found.as_built, {"lift": (40.0,), "wrist": (0.0,)})
        entity = mechanism._string(mechanism._named(
            next(label for label in mechanism._children_of(loaded.doc.Main()) if label.Tag() == mechanism.TAG)
            .FindChild(1, False).FindChild(2, False).FindChild(1, False)), "entity")
        self.assertEqual(entity, "PRISMATIC_PAIR_WITH_RANGE")
        structure = loaded.structure()
        self.assertEqual([link["instances"] for link in structure["kinematics"]["links"]], [[0], [1, 2], [3]])

        def placed(pose: dict) -> list[np.ndarray]:
            values, _named = mechanism.resolve(structure["kinematics"], pose)
            return [np.array(m).reshape(4, 4).T for m in mechanism.posed_instances(structure, values)]

        rest, raised, turned = placed({}), placed({"lift": 70}), placed({"wrist": 90})
        np.testing.assert_allclose(raised[0], rest[0], atol=1e-12)
        for at in (1, 2, 3):  # the carriage, its clamp and the arm on it rise 30 along the lift
            np.testing.assert_allclose(raised[at][:3, 3] - rest[at][:3, 3], [0, 0, 30], atol=1e-9)
        for at in (0, 1, 2):
            np.testing.assert_allclose(turned[at], rest[at], atol=1e-12)
        # The wrist turns the arm a quarter about +X through its hub: the arm's far edge, from the
        # hub, is turned v -> X x v.
        far = np.array([0.0, 40.0, 0.0, 1.0])
        hub = rest[3][:3, 3]
        np.testing.assert_allclose(turned[3][:3, 3], hub, atol=1e-9)
        np.testing.assert_allclose((turned[3] @ far)[:3] - hub, np.cross([1, 0, 0], (rest[3] @ far)[:3] - hub), atol=1e-9)
        top = placed("top")
        np.testing.assert_allclose(top[2][:3, 3] - rest[2][:3, 3], [0, 0, 50], atol=1e-9)
        np.testing.assert_allclose(top[1][:3, 3], [0, 0, 95], atol=1e-9)  # at lift 90 on the axis through z = 5


class GearTest(_Folder):
    def test_a_gear_pair_drives_one_pair_from_another(self) -> None:
        import build123d as bd

        base = bd.Box(100, 20, 10)
        base.label = "base"
        arms = []
        for name, x in (("left", -30), ("right", 30)):
            arm = bd.Box(4, 30, 4, align=(bd.Align.CENTER, bd.Align.MIN, bd.Align.CENTER))
            arm.label = name
            bd.RevoluteJoint(name, base, axis=bd.Axis((x, 0, 10), (0, 0, 1)), angular_range=(-180, 180))
            bd.RigidJoint("hub", arm, bd.Location())
            base.joints[name].connect_to(arm.joints["hub"], angle=0)
            arms.append(arm)
        tree = document.collect(bd.Compound(children=[base, *arms], label="gears"), "gears")
        # build123d's joints cannot say a coupling: the gear pair is put in the mechanism here.
        tree.mechanism.pairs.append(mechanism.Pair("mesh", "gear", (1, 2), None, (), ratio=-2.0))
        path = self.folder / "gears.xbf"
        document.save(document.to_xcaf(tree), path, compare=False)
        loaded = document.Loaded(path)
        gear = loaded.mechanism.pairs[-1]
        self.assertEqual((gear.label, gear.kind, gear.links, gear.ratio), ("mesh", "gear", (1, 2), -2.0))
        structure = loaded.structure()
        self.assertEqual(structure["kinematics"]["pairs"][-1]["drives"], ["left", "right"])
        values, _named = mechanism.resolve(structure["kinematics"], {"left": 30})
        self.assertEqual(values["right"], (-60.0,))
        driven = np.array(mechanism.posed_instances(structure, values)[2]).reshape(4, 4).T
        # The right arm's +Y edge, turned -60 about +Z through (30, 0, 10).
        tip = driven @ np.array([0.0, 30.0, 0.0, 1.0])
        np.testing.assert_allclose(tip[:3], [30 + 30 * math.sin(math.radians(60)), 30 * math.cos(math.radians(60)), 10],
                                   atol=1e-9)
        with self.assertRaisesRegex(mechanism.KinematicsError, "'right' is driven by 'left' through gear 'mesh'"):
            mechanism.resolve(structure["kinematics"], {"right": 10})
        with self.assertRaisesRegex(mechanism.KinematicsError, "gear 'mesh' driving 'right' .* outside its range"):
            mechanism.resolve(structure["kinematics"], {"left": 120})


if __name__ == "__main__":
    unittest.main()
