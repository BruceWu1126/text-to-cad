"""Kinematics: build123d's joints harvested into the xbf's mechanism (AP242's links, pairs,
ground and configurations) and read back; a chain's links and a prismatic pair; a pair the
built tree contradicts refused by name; poses by a configuration's name and by joint values
placing a part where the closed form says, in a posed STEP that carries no mechanism; what a pose
names wrong refused by name; the structure's ``kinematics`` the same from the file and from the
build; a document served at a pose; the mechanism subtree AP242's names and nothing else."""

from __future__ import annotations

import contextlib
import io
import json
import math
import os
import re
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
from tests.python.support import opengl

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
        path = self.folder / "XBF/hinge.xbf"
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
        self.assertTrue(report["files"][str(self.folder / "XBF/hinge.xbf")]["written"])
        self.assertEqual(document.Loaded(self.folder / "XBF/hinge.xbf").mechanism.configurations["flat"], {"elbow": (-5.0,)})

    def test_poses_by_name_and_by_value_place_the_arm_where_the_closed_form_says(self) -> None:
        self.hinge()
        path = str(self.folder / "XBF/hinge.xbf")
        for pose, q in (("raised", 90.0), ('{"elbow": 45}', 45.0), ("flat", 0.0)):
            out = self.folder / f"posed-{q:g}.step"
            code, _out, err = _run("export", path, str(out), "--no-daemon", "--pose", pose)
            self.assertEqual(code, 0, err)
            posed = document.Loaded(out)
            self.assertTrue(_has_point(_vertices(posed, "arm"), _arm_tip(q)), pose)
            self.assertFalse(_has_point(_vertices(posed, "arm"), _arm_tip(30)), pose)
            self.assertTrue(_has_point(_vertices(posed, "base"), np.array([50.0, 30.0, 10.0])), "the ground stays")
            # The STEP carries none of the mechanism: OCCT writes no AP242 kinematics.
            text = out.read_text(encoding="utf-8", errors="replace")
            self.assertEqual([word for word in ("_PAIR", "MECHANISM", "KINEMATIC") if word in text], [])
        # The build's own STEP is as built, and carries none either.
        built = document.Loaded(self.folder / "hinge.step")
        self.assertTrue(_has_point(_vertices(built, "arm"), _arm_tip(30)))
        self.assertNotIn("MECHANISM", (self.folder / "hinge.step").read_text(encoding="utf-8", errors="replace"))
        # A mesh export at a pose: the arm's node placed as the pair places it.
        glb = self.folder / "posed.glb"
        self.assertEqual(_run("export", path, str(glb), "--no-daemon", "--pose", "raised")[0], 0)
        data = glb.read_bytes()
        length = int.from_bytes(data[12:16], "little")
        nodes = json.loads(data[20:20 + length])["nodes"]
        arm = np.array(next(node for node in nodes if node.get("name") == "arm")["matrix"]).reshape(4, 4).T
        np.testing.assert_allclose((arm @ np.array([120.0, 10.0, 10.0, 1.0]))[:3], _arm_tip(90), atol=1e-6)

    def test_what_a_pose_names_wrong_is_refused_by_name_before_anything_is_written(self) -> None:
        self.hinge()
        path = str(self.folder / "XBF/hinge.xbf")
        out = self.folder / "refused.step"
        for pose, words in (('{"elbw": 10}', ["'elbw'", "elbow"]), ('{"elbow": 150}', ["'elbow'", "150", "[-10, 120]"]),
                            ("folded", ["'folded'", "flat, raised"]), ('{"elbow": [1, 2]}', ["'elbow'", "a number"])):
            code, _out, err = _run("export", path, str(out), "--no-daemon", "--pose", pose)
            self.assertEqual(code, 2, pose)
            for word in words:
                self.assertIn(word, err, pose)
            self.assertFalse(out.exists(), pose)

    def test_a_configuration_naming_no_joint_fails_the_build_and_one_out_of_range_warns(self) -> None:
        # The one harvest error: a configuration naming a joint its assembly does not hold.
        report = self.hinge(flat="elbw=0", ok=False)
        self.assertIn("names no joint 'elbw'", report["error"])
        self.assertIn("elbow", report["error"])
        report = self.hinge(flat="elbow=200")
        self.assertIn("'elbow' at 200 deg is outside its range [-10, 120] deg: the value is left out",
                      " ".join(report["kinematics"]["warnings"]))
        self.assertEqual(document.Loaded(self.folder / "XBF/hinge.xbf").mechanism.configurations["flat"], {})

    def test_a_part_moved_after_it_was_connected_is_refused_by_name(self) -> None:
        # Connected after the compound holding the arm was made: the arm is not where the joint
        # put it, a pair AP242 has no value for. The document is refused, not written.
        report = self.hinge(connect="pass", after=CONNECT, ok=False)
        self.assertIn("joint 'elbow' cannot be kept: hinge/arm is not where it can put it", report["error"])
        self.assertFalse((self.folder / "XBF/hinge.xbf").exists())

    def test_a_snapshot_draws_a_pose_and_refuses_a_bad_one_before_it_renders(self) -> None:
        from cadgen.engine.render import context
        from cadgen.engine.snapshot import SnapshotRefused, snapshot

        self.hinge()
        previous = os.environ.get("CADGEN_ENGINE_MESH")
        os.environ["CADGEN_ENGINE_MESH"] = "inline"  # one part at a time on this thread: no pool
        self.addCleanup(lambda: os.environ.pop("CADGEN_ENGINE_MESH") if previous is None
                        else os.environ.__setitem__("CADGEN_ENGINE_MESH", previous))
        path = self.folder / "XBF/hinge.xbf"
        out = self.folder / "stale.png"
        for pose, words in (({"elbw": 1}, "names no joint 'elbw'"), ({"elbow": 150}, "'elbow' at 150 deg is outside"),
                            ("folded", "no configuration 'folded'")):
            out.write_bytes(b"before")
            with self.assertRaisesRegex(SnapshotRefused, words):
                snapshot(path, out, pose=pose, size=(160, 120), daemon=False)
            self.assertFalse(out.exists(), "a refused snapshot leaves no picture at OUT")
        opengl.require(context.available(), "the pose is refused; drawing it needs an OpenGL context")
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
        ident = server.add(self.folder / "XBF/hinge.xbf")

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
        self.assertEqual(document.Loaded(self.folder / "XBF/hinge.xbf").mechanism.as_built, {"elbow": (30.0,)})
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
        loaded = document.Loaded(self.folder / "XBF/hinge.xbf")
        self.assertEqual(loaded.mechanism.as_built, {"elbow": (60.0,)})
        self.assertTrue(_has_point(_vertices(loaded, "arm"), _arm_tip(60)))
        # A range keys the joint: an edit of it alone, which moves nothing, is no hit of the old
        # joint (a copy of the base hands its joints on: a hit would hand back the first run's).
        source = HINGE.format(connect="base = base.moved(bd.Location()); " + CONNECT, after="pass", flat="elbow=0")
        self.build("hinge", source)
        self.build("hinge", source.replace("(-10, 120)", "(-20, 100)"))
        self.assertEqual(document.Loaded(self.folder / "XBF/hinge.xbf").mechanism.pairs[0].limits, ((-20.0, 100.0),))

    def test_each_placement_of_a_child_holds_its_own_joints(self) -> None:
        self.build("pegs", """
            import build123d as bd
            from cadgen.engine import xbf

            SEEN = {}


            @xbf
            def peg():
                peg = bd.Box(2, 2, 6)
                bd.RigidJoint("base", peg, bd.Location((0, 0, -3)))
                return peg


            @xbf
            def pegs():
                first, second, third = peg(), bd.Pos(10, 0, 0) * peg(), peg()
                bd.RigidJoint("front", first, bd.Location())
                bd.RigidJoint("rear", third, bd.Location())
                SEEN.update(shared=first.joints is third.joints, first=sorted(first.joints), third=sorted(third.joints),
                            parents=[joint.parent is shape for shape in (first, third) for joint in shape.joints.values()])
                return bd.Compound(children=[first, second, third], label="pegs")
            """)
        import sys

        seen = sys.modules["pegs"].SEEN
        self.assertFalse(seen["shared"])
        self.assertEqual((seen["first"], seen["third"]), (["base", "front"], ["base", "rear"]))
        self.assertTrue(all(seen["parents"]), "each placement's joints are on it")


NESTED = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def nested():
    base = bd.Box(100, 60, 20)
    base.label = "base"
    # The arm sits inside a group the assembly places by a location of its own, at elbow 30: no
    # connect_to (which composes locations in each part's own compound) places it.
    group_at = bd.Location((10, 5, 0), (0, 0, 20))
    joint = bd.Axis((40, 0, 20), (0, 1, 0)).location
    arm = (group_at.inverse() * joint * bd.Rotation(0, 0, 30)) * bd.Box(
        120, 20, 10, align=(bd.Align.MIN, bd.Align.CENTER, bd.Align.MIN))
    arm.label = "arm"
    bd.RevoluteJoint("elbow", base, axis=bd.Axis((40, 0, 20), (0, 1, 0)), angular_range=(-10, 120))
    bd.RigidJoint("elbow", arm, arm.location)  # the joint's frame: the arm's own origin
    more = []
    {extra}
    group = bd.Compound(children=[arm], label="arm_group")
    group.location = group_at
    return bd.Compound(children=[base, group, *more], label="nested")
"""
SPARE = """spare = bd.Pos(0, 0, -40) * bd.Box(5, 5, 5)
    spare.label = "spare"
    bd.RigidJoint("elbow", spare, spare.location)
    more.append(spare)"""


MOUNTED = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def mounted():
    base = bd.Box(60, 60, 4)
    base.label = "base"
    # A sub-assembly whose plate holds the inner pair's moving joint, its wheel the rigid one,
    # mounted on the base by a hinge about X at z 10.
    plate = bd.Pos(0, 0, 10) * bd.Box(30, 30, 2)
    plate.label = "plate"
    wheel = bd.Pos(5, 0, 12) * bd.Cylinder(6, 1)
    wheel.label = "wheel"
    sub = bd.Compound(children=[plate, wheel], label="sub")
    spin = bd.RevoluteJoint("spin", plate, axis=bd.Axis((5, 0, 0), (0, 0, 1)), angular_range=(-360, 360))
    bd.RigidJoint("spin", wheel, spin.location)
    mount = bd.RevoluteJoint("mount", base, axis=bd.Axis((0, 0, 10), (1, 0, 0)), angular_range=(-180, 180))
    bd.RigidJoint("mount", sub, mount.location)
    return bd.Compound(children=[base, sub], label="mounted")
"""


class NestedTest(_Folder):
    def test_joints_in_a_nested_assembly_pair_by_label_and_pose_exactly(self) -> None:
        self.build("nested", NESTED.format(extra="pass"))
        path = self.folder / "XBF/nested.xbf"
        loaded = document.Loaded(path)
        found = loaded.mechanism
        # The pair is the two joints labelled `elbow`; its value is the placement's, the arm unmoved.
        self.assertEqual([(link.name, link.members) for link in found.links], [("base", [(0,)]), ("arm", [(1, 0)])])
        (pair,) = found.pairs
        self.assertEqual((pair.label, pair.kind, pair.links), ("elbow", "revolute", (0, 1)))
        self.assertAlmostEqual(found.as_built["elbow"][0], 30.0, places=9)
        self.assertTrue(_has_point(_vertices(loaded, "arm"), _arm_tip(30)))
        self.assertEqual(loaded.structure()["kinematics"],
                         document.tree_structure(document.saved(path), path)["kinematics"])
        for pose, q in (('{"elbow": 90}', 90.0), ('{"elbow": -10}', -10.0)):
            out = self.folder / "posed.step"
            code, _out, err = _run("export", str(path), str(out), "--no-daemon", "--pose", pose)
            self.assertEqual(code, 0, err)
            self.assertTrue(_has_point(_vertices(document.Loaded(out), "arm"), _arm_tip(q)), pose)

    def test_a_mounted_sub_assembly_carries_its_inner_pairs_base(self) -> None:
        # The plate holds the inner pair's moving joint and no pair moves it: it is the
        # sub-assembly's, so posing the mount carries it (not left on the ground).
        self.build("mounted", MOUNTED)
        path = self.folder / "XBF/mounted.xbf"
        found = document.Loaded(path).mechanism
        links = {link.name: link.members for link in found.links}
        self.assertEqual(links, {"base": [(0,)], "sub": [(1,), (1, 0)], "wheel": [(1, 1)]})
        self.assertEqual({pair.label: pair.links for pair in found.pairs}, {"mount": (0, 1), "spin": (1, 2)})
        out = self.folder / "posed.step"
        code, _out, err = _run("export", str(path), str(out), "--no-daemon", "--pose", '{"mount": 90}')
        self.assertEqual(code, 0, err)
        plate = _vertices(document.Loaded(out), "plate")
        # A quarter turn about +X through z 10: the plate's corner (15, 15, 11) stands at (15, -1, 25).
        self.assertTrue(_has_point(plate, np.array([15.0, -1.0, 25.0])), plate)
        self.assertFalse(_has_point(plate, np.array([15.0, 15.0, 11.0])), "the plate left where it was built")

    def test_a_moving_joint_matching_two_rigid_joints_alike_warns_and_pairs_with_neither(self) -> None:
        # A second rigid `elbow` as near the base's as the arm's: the pair is ambiguous.
        report = self.build("nested", NESTED.format(extra=SPARE))
        self.assertIn("joint 'elbow' on nested/base matches the rigid joints 'elbow'",
                      " ".join(report["kinematics"]["warnings"]))
        # The warning is the build's report: the document holds what AP242 can say, no pair.
        kinematics = document.Loaded(self.folder / "XBF/nested.xbf").structure()["kinematics"]
        self.assertEqual((kinematics["pairs"], kinematics["warnings"]), ([], []))

    def test_connect_to_wins_over_a_label(self) -> None:
        # The hinge connects `elbow` to the arm's `mount`; a spare's rigid `elbow` pairs with nothing.
        self.hinge(connect=CONNECT + "; " + SPARE.replace("\n    ", "; ").replace("more.append(spare)", "pass"),
                   after='root = bd.Compound(children=[base, arm, spare], label="hinge")')
        loaded = document.Loaded(self.folder / "XBF/hinge.xbf")
        self.assertEqual([(pair.label, pair.links) for pair in loaded.mechanism.pairs], [("elbow", (0, 1))])
        self.assertEqual([link["instances"] for link in loaded.structure()["kinematics"]["links"]], [[0, 2], [1]])


PIVOT = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def bike():
    # Two parts placed where they stand, a revolute pair between them by label: no connect_to.
    frame = bd.Box(200, 40, 20)
    frame.label = "frame"
    engine = bd.Pos(60, 0, -40) * bd.Box(60, 30, 30)
    engine.label = "engine"
    point = (40, 0, -30)
    bd.RevoluteJoint("pivot", frame, axis=bd.Axis(point, (0, 1, 0)), angular_range=(-5, 12))
    bd.RigidJoint("pivot", engine, {at})
    parts = [frame, engine]
    {extra}
    return bd.Compound(children=parts, label="bike")
"""
ON_AXIS = "bd.Plane(origin=point, z_dir=(0, 1, 0)).location"
# A shock fastened to the frame at one end and to the engine at the other: rigid joints make the
# three one body, and the pivot between frame and engine cannot move.
SHOCK = """shock = bd.Pos(100, 0, -10) * bd.Box(10, 10, 30)
    shock.label = "shock"
    for name, part, at in (("upper", frame, (100, 0, 0)), ("lower", engine, (100, 0, -20))):
        bd.RigidJoint(name, part, bd.Location(at))
        bd.RigidJoint(name, shock, bd.Location(at))
    parts.append(shock)"""


TURNED = """
import build123d as bd
from cadgen.engine import xbf


@xbf
def bike():
    # The pivot's part is turned: build123d makes the revolute's frame of the axis in that part's
    # frame, so its X is not the X a plane of the world datum has.
    frame = bd.Rot(0, 0, 30) * bd.Box(200, 40, 20)
    frame.label = "frame"
    engine = bd.Pos(60, 0, -40) * bd.Box(60, 30, 30)
    engine.label = "engine"
    point = (40, 0, -30)
    bd.RevoluteJoint("pivot", frame, axis=bd.Axis(point, (0, 1, 0)), angular_range=(-5, 12))
    bd.RigidJoint("pivot", engine, {at})
    return bd.Compound(children=[frame, engine], label="bike")
"""


class RefusalTest(_Folder):
    def test_a_child_frame_at_the_revolute_s_own_frame_is_joint_zero(self) -> None:
        # A rigid frame made of the world datum turns about the axis from the revolute's own by the
        # angle between their X's: outside a narrow range, no value of the pair, and refused.
        report = self.build("bike", TURNED.format(at="bd.Plane(origin=point, z_dir=(0, 1, 0)).location"), ok=False)
        self.assertIn("joint 'pivot' cannot be kept", report["error"])
        # At the revolute's own frame (its part's placement times its relative axis): joint zero.
        report = self.build("bike", TURNED.format(at='frame.location * frame.joints["pivot"].relative_axis.location'))
        self.assertNotIn("kinematics", report)
        self.assertEqual(document.Loaded(self.folder / "XBF/bike.xbf").mechanism.as_built, {"pivot": (0.0,)})

    def test_a_rigid_joint_off_the_revolute_axis_is_refused_by_name(self) -> None:
        report = self.build("bike", PIVOT.format(at="bd.Location((55, 0, -30))", extra="pass"), ok=False)  # 15 mm off
        self.assertIn("joint 'pivot' cannot be kept: bike/engine is not where it can put it", report["error"])
        self.assertIn("not one revolute motion apart within its range", report["error"])
        self.assertFalse((self.folder / "XBF/bike.xbf").exists(), "a refused document is not written")

    def test_two_unmoved_parts_with_a_revolute_between_them_are_two_links(self) -> None:
        report = self.build("bike", PIVOT.format(at=ON_AXIS, extra="pass"))
        self.assertNotIn("kinematics", report)
        found = document.Loaded(self.folder / "XBF/bike.xbf").mechanism
        self.assertEqual([(link.name, link.members) for link in found.links], [("frame", [(0,)]), ("engine", [(1,)])])
        self.assertEqual(found.as_built, {"pivot": (0.0,)})
        self.assertEqual(found.warnings, [])

    def test_a_pair_inside_one_rigid_body_is_refused_by_name(self) -> None:
        report = self.build("bike", PIVOT.format(at=ON_AXIS, extra=SHOCK), ok=False)
        self.assertIn("joint 'pivot' cannot be kept: it joins bike/frame to bike/engine, which rigid joints make one "
                      "link with 1 other parts (the unmoved ground): the pair cannot move", report["error"])


class ChainTest(_Folder):
    def test_a_chain_of_rigid_prismatic_and_revolute_joints_makes_its_links(self) -> None:
        self.build("lift", CHAIN)
        loaded = document.Loaded(self.folder / "XBF/lift.xbf")
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


# Every kind of pair, coupler and motion segment on one plate: a pinion geared to a wheel and
# driving a rack, a lead screw whose turn drives its own travel and a slide, a free ball.
BENCH = """
import math

import build123d as bd
from cadgen.engine import configuration, couple, motion, xbf


@xbf
def bench():
    plate = bd.Box(300, 200, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MAX))
    plate.label = "plate"
    parts = [plate]

    def mount(joint, part, at, axis, **connect):
        bd.RigidJoint(joint.label, part, bd.Plane(origin=at, z_dir=axis).location)
        joint.connect_to(part.joints[joint.label], **connect)
        parts.append(part)

    for name, at, limits in (("pinion", (0, 0, 0), (-360, 360)), ("wheel", (60, 0, 0), (-720, 720))):
        wheel = bd.Pos(*at) * bd.Cylinder(20, 8, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        wheel.label = name
        mount(bd.RevoluteJoint(name, plate, axis=bd.Axis(at, (0, 0, 1)), angular_range=limits), wheel, at, (0, 0, 1),
              angle=0)
    for name, at, axis in (("rack", (0, 40, 0), (1, 0, 0)), ("slide", (100, -40, 0), (0, 1, 0))):
        bar = bd.Pos(*at) * bd.Box(40, 10, 10, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
        bar.label = name
        mount(bd.LinearJoint(name, plate, axis=bd.Axis(at, axis), linear_range=(-50, 50)), bar, at, axis, position=0)
    screw = bd.Pos(-60, 0, 0) * bd.Cylinder(5, 60, align=(bd.Align.CENTER, bd.Align.CENTER, bd.Align.MIN))
    screw.label = "screw"
    mount(bd.CylindricalJoint("screw", plate, axis=bd.Axis((-60, 0, 0), (0, 0, 1)), angular_range=(-720, 720),
                              linear_range=(0, 10)), screw, (-60, 0, 0), (0, 0, 1), angle=0, position=0)
    ball = bd.Pos(0, -60, 10) * bd.Sphere(10)
    ball.label = "ball"
    mount(bd.BallJoint("ball", plate, bd.Location((0, -60, 10)), angular_range=((-30, 30), (-30, 30), (-30, 30))),
          ball, (0, -60, 10), (0, 0, 1), angles=(0, 0, 0))
    bench = bd.Compound(children=parts, label="bench")
    couple(bench, "drive", pinion=1, wheel=-0.5, rack=0.1)
    couple(bench, "feed", screw=(36, 1), slide=2)
    configuration(bench, "home", drive=10, ball=(5, 0, 0))
    motion(bench, "run", [(0, "home"), (1, {"drive": 100}), (2, {"drive": -100, "feed": 5})], ease="linear")
    motion(bench, "ease", sweep={"feed": (0, 10)}, duration=1)
    return motion(bench, "wobble", lambda t: {"ball": (20 * math.sin(math.pi * t), 0, 0)}, duration=2)
"""
# AP242's names, as Part 21 writes them (edition 4's domain model's where Part 21 has none):
# the entities the subtree may hold, each attribute it may set, and the enumerations' values.
AP242_ENTITIES = {
    "MECHANISM_REPRESENTATION", "KINEMATIC_LINK", "MECHANISM_STATE_REPRESENTATION",
    "INTERPOLATED_CONFIGURATION_SEQUENCE", "INTERPOLATED_CONFIGURATION_SEGMENT", "SCREW_PAIR",
    "KinematicPairWithMotionCoupling",
    *(pair + suffix for pair in ("REVOLUTE_PAIR", "PRISMATIC_PAIR", "CYLINDRICAL_PAIR", "SPHERICAL_PAIR")
      for suffix in ("", "_WITH_RANGE"))}
AP242_ATTRIBUTES = {
    "transform_item_1", "transform_item_2", "t_parameter", "interpolation", "pitch",
    "Kind", "CouplingRatios", "Scales", "CouplingTypes",
    *(f"{side}_limit_{value}" for side in ("lower", "upper")
      for value in ("actual_rotation", "actual_translation", "yaw", "pitch", "roll"))}
AP242_VALUES = {
    "interpolation": {"discontinuous_interpolation_type", "synchronous_interpolation_type",
                      "linear_interpolation_type", "cubic_interpolation_type"},
    "Kind": {"gear_pair", "rack_and_pinion_pair", "two_joint_coupler", "three_joint_coupler"}}
# The container's own: what the root says of the subtree, and the attribute types that hold it.
CONTAINER_ATTRIBUTES = {"schema", "angle_unit", "length_unit"}
OCAF_ATTRIBUTES = {"TDataStd_Name", "TDataStd_NamedData", "TDataStd_ReferenceList", "TDataStd_RealArray",
                   "TDataStd_IntegerArray", "XCAFDoc_AssemblyItemRef"}


class ConformanceTest(_Folder):
    def test_the_mechanism_subtree_holds_ap242_names_and_nothing_else(self) -> None:
        from OCP.Standard import Standard_GUID
        from OCP.TDF import TDF_AttributeIterator, TDF_ChildIterator

        report = self.build("bench", BENCH)
        self.assertNotIn("kinematics", report, "no warning")
        loaded = document.Loaded(self.folder / "XBF/bench.xbf")
        found = loaded.mechanism
        self.assertEqual(sorted(found.couplings), ["drive", "feed"])
        self.assertEqual(sorted(found.motions), ["ease", "run", "wobble"])
        root = next(label for label in mechanism._children_of(loaded.doc.Main())
                    if label.IsAttribute(Standard_GUID(mechanism.GUID)))
        labels = [root]
        walk = TDF_ChildIterator(root, True)
        while walk.More():
            labels.append(walk.Value())
            walk.Next()
        entities, kinds, interpolations = set(), set(), set()
        for label in labels:
            where = document._entry(label)
            types = set()
            attributes = TDF_AttributeIterator(label)
            while attributes.More():
                types.add(attributes.Value().DynamicType().Name())
                attributes.Next()
            self.assertLessEqual(types - {"TDataStd_UAttribute"} if label.IsEqual(root) else types, OCAF_ATTRIBUTES,
                                 where)
            if "TDataStd_NamedData" not in types:
                continue
            data = mechanism._named(label)
            allowed = CONTAINER_ATTRIBUTES if label.IsEqual(root) else {"entity", *AP242_ATTRIBUTES}
            # Every key the label holds is an allowed one: of each kind, the count OCCT dumps is
            # the allowed keys it has (OCP iterates none of the maps), and of the others none.
            dumped = io.BytesIO()
            data.DumpJson(dumped)
            sizes = {kind: int(size)
                     for kind, size in re.findall(rb'"(\w+)->Map\(\)\.Size": (\d+)', dumped.getvalue())}
            for kind, one in ((b"Strings", data.HasString), (b"Reals", data.HasReal),
                              (b"ArraysOfReals", data.HasArrayOfReals)):
                self.assertEqual(sizes.pop(kind, 0), sum(bool(one(mechanism._text(key))) for key in allowed),
                                 (where, kind))
            self.assertEqual({kind: size for kind, size in sizes.items() if size}, {}, where)
            if label.IsEqual(root):
                continue
            entity = mechanism._string(data, "entity")
            self.assertIn(entity, AP242_ENTITIES, where)
            entities.add(entity)
            for key, values in AP242_VALUES.items():
                value = mechanism._string(data, key)
                if value is not None:
                    self.assertIn(value, values, (where, key))
            kinds.add(mechanism._string(data, "Kind"))
            interpolations.add(mechanism._string(data, "interpolation"))
            types_ = mechanism._string(data, "CouplingTypes")
            if types_ is not None:
                self.assertLessEqual(set(types_.split(",")), {"rotation", "translation"}, where)
        # The walk saw every kind the bench makes: a vacuous pass proves nothing.
        self.assertLessEqual({"REVOLUTE_PAIR_WITH_RANGE", "PRISMATIC_PAIR_WITH_RANGE", "CYLINDRICAL_PAIR_WITH_RANGE",
                              "SPHERICAL_PAIR_WITH_RANGE", "SCREW_PAIR", "KinematicPairWithMotionCoupling",
                              "INTERPOLATED_CONFIGURATION_SEGMENT"}, entities)
        self.assertEqual(kinds - {None}, {"gear_pair", "rack_and_pinion_pair", "two_joint_coupler"})
        self.assertEqual(interpolations - {None}, {"discontinuous_interpolation_type",
                                                   "synchronous_interpolation_type", "linear_interpolation_type"})


if __name__ == "__main__":
    unittest.main()
