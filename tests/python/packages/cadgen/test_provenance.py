"""The provenance entry and ``cadgen store where`` (STORE.md §3; README law 20).

A build writes, beside its record, which operation at which line of the model's
code made each component of its document and each face and edge of it, and
``store where`` answers a ref from it: through the model's own entry, and
through a pinned child's for a part placed from a child model. It knows the
file's bytes only while they are the ones it describes, says so otherwise, goes
with ``store forget``, is never evicted, and a warm rerun writes the same entry
a cold run did. Real CLIs, transient builds and one private warm daemon, a temp
store.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
import time
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path
from tests.python.support.tmp_root import generated_cad_directory
from tests.python.support.warm_daemon import served, warm_entries

add_repo_path("packages/cadgen/src")

PIN = """
    from cadgen import step
    from cadgen import build123d as bd


    @step(out="../STEP/pin.step")
    def pin():
        return bd.Cylinder(radius=2.0, height=12.0)


    if __name__ == "__main__":
        pin()
"""

GEO = """
    from cadgen import build123d as bd


    def bore(body, radius):
        return body - bd.Cylinder(radius, 30)
"""

ARM = """
    from cadgen import label_shape, step
    from cadgen import build123d as bd
    from lib.geo import bore
    from pin import pin


    @step(out="../STEP/arm.step")
    def arm():
        bar = bd.Box(40, 8, 4)
        bar = bore(bar, 1.5)
        bar = label_shape(bd.fillet(bar.edges().filter_by(bd.Axis.Z), 1.0), "bar")
        left = bd.Pos(-15, 0, 2) * pin()
        left.label = "pin_left"
        right = bd.Pos(15, 0, 2) * pin()
        right.label = "pin_right"
        return bd.Compound(children=[bar, left, right])


    if __name__ == "__main__":
        arm()
"""

REFS = """
    import json, sys
    from cadgen import read_scene

    scene = read_scene(sys.argv[1])
    found = {}
    for leaf in scene.leaves():
        faces = list(leaf.entities("face"))
        found[leaf.label] = {
            "occurrence": leaf.ref,
            "planes": [f.ref for f in faces if f.shape().geom_type.name == "PLANE"],
            "cylinders": [f.ref for f in faces if f.shape().geom_type.name == "CYLINDER"],
            "edge": list(leaf.entities("edge"))[0].ref,
            "vertex": list(leaf.entities("vertex"))[0].ref,
        }
    print(json.dumps(found))
"""

FEATURE_ID = re.compile(r"^fe:[A-Za-z_][\w.]*@[\w./]+\.py:\d+#\d+$")


def _run(*argv: str, cwd: Path, cache: Path, warm: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        p for p in [str(REPO_ROOT / "packages" / "cadgen" / "src"), env.get("PYTHONPATH", "")] if p
    )
    env["CADGEN_CACHE_DIR"] = str(cache)
    env.pop("CADGEN_DAEMON_CHILD", None)
    if warm:
        env.update(warm_entries())
    else:
        env["CADGEN_DAEMON"] = "0"
    return subprocess.run(
        [sys.executable, *argv], cwd=str(cwd), env=env, capture_output=True, text=True, timeout=600
    )


class ProvenanceCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = generated_cad_directory(prefix="provenance-")
        cls.root = Path(cls._tmp.name) / "proj"
        (cls.root / "src" / "lib").mkdir(parents=True)
        cls.cache = Path(cls._tmp.name) / "store"
        for name, text in (("pin.py", PIN), ("arm.py", ARM), ("lib/geo.py", GEO), ("refs.py", REFS)):
            (cls.root / "src" / name).write_text(textwrap.dedent(text).lstrip(), encoding="utf-8")
        (cls.root / "src" / "lib" / "__init__.py").write_text("", encoding="utf-8")
        for script in ("src/pin.py", "src/arm.py"):
            result = _run(script, cwd=cls.root, cache=cls.cache)
            assert result.returncode == 0, result.stdout + result.stderr
        refs = _run("src/refs.py", "STEP/arm.step", cwd=cls.root, cache=cls.cache)
        assert refs.returncode == 0, refs.stdout + refs.stderr
        cls.refs = json.loads(refs.stdout.strip())

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def _store(self, *argv: str) -> subprocess.CompletedProcess:
        return _run("-m", "cadgen.cli", "store", *argv, cwd=self.root, cache=self.cache)

    def _where(self, ref: str, document: str = "STEP/arm.step") -> tuple[int, dict]:
        result = self._store("where", document, ref, "--json")
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return result.returncode, json.loads(result.stdout.strip())

    def _entries(self) -> dict[str, dict]:
        folder = self.cache / "index" / "provenance"
        return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(folder.iterdir())}

    def _record(self, script: str) -> dict:
        why = self._store("why", script, "--json")
        self.assertEqual(0, why.returncode, why.stdout + why.stderr)
        return json.loads(why.stdout.strip())["record"]


def _posix(model: str) -> str:
    """A store model reference (``<path>::<function>``, the path native) with its path as POSIX."""
    path, _, function = model.rpartition("::")
    return f"{Path(path).as_posix()}::{function}"


class TheEntry(ProvenanceCase):
    def test_a_build_writes_one_beside_its_record_for_its_tree_and_its_document(self) -> None:
        from cadgen._internal.step_hash import step_file_hash

        entries = self._entries()
        self.assertEqual(2, len(entries), "one entry per model")
        by_model = {entry["model"].rsplit("::", 1)[1]: entry for entry in entries.values()}
        self.assertEqual({"pin", "arm"}, set(by_model))
        for name, entry in by_model.items():
            record = self._record(f"src/{name}.py")
            self.assertEqual(("provenance", 1), (entry["kind"], entry["schemaVersion"]))
            self.assertTrue(_posix(entry["model"]).endswith(f"src/{name}.py::{name}"))
            self.assertEqual(record["tree"], entry["tree"])
            self.assertEqual(record["stepHash"], entry["stepHash"])
            self.assertEqual(step_file_hash(self.root / "STEP" / f"{name}.step"), entry["stepHash"])
        pin = by_model["pin"]
        ops = [feature["op"] for feature in pin["features"]]
        cylinder = ops.index("Solid.make_cylinder")
        feature = pin["features"][cylinder]
        self.assertRegex(feature["id"], FEATURE_ID)
        self.assertEqual(("pin.py", "pin", 1, []), (feature["file"], feature["function"], feature["n"], feature["inputs"]))
        for position, row in enumerate(pin["features"]):
            self.assertTrue(all(index is None or 0 <= index < position for index in row["inputs"]),
                            "a feature is given what earlier features made")
        [component] = pin["components"].values()
        self.assertIsNotNone(component["made"])
        self.assertEqual(3, len(component["faces"]))
        self.assertTrue(all(re.fullmatch(r"f:[0-9a-f]{12}", token) for token in component["faces"]))
        self.assertTrue(all(lineage == [cylinder, None] for lineage in component["faces"].values()))
        self.assertTrue(all(lineage == [cylinder, None] for lineage in component["edges"].values()))

    def test_features_name_the_calling_frame_in_the_model_and_its_helpers(self) -> None:
        arm = next(entry for entry in self._entries().values() if entry["model"].endswith("::arm"))
        frames = {(feature["file"], feature["function"], feature["op"]) for feature in arm["features"]}
        self.assertIn(("arm.py", "arm", "Solid.make_box"), frames)
        self.assertIn(("lib/geo.py", "bore", "cut"), frames)
        self.assertIn(("arm.py", "arm", "fillet"), frames)
        # The child's constructions are the child's build's, not this one's.
        self.assertFalse({feature for feature in frames if feature[0] == "pin.py"})
        for position, feature in enumerate(arm["features"]):
            self.assertRegex(feature["id"], FEATURE_ID)
            self.assertTrue(all(index is None or 0 <= index < position for index in feature["inputs"]))


class StoreWhere(ProvenanceCase):
    def test_a_face_an_edge_an_occurrence_and_a_label(self) -> None:
        bar = self.refs["bar"]
        # The bar's cylindrical faces: the bore's wall, and the four fillets.
        answers = [self._where(ref) for ref in bar["cylinders"]]
        self.assertEqual(5, len(answers))
        self.assertTrue(all(code == 0 and answer["known"] and answer["kind"] == "face" for code, answer in answers))
        self.assertTrue(all(_posix(answer["model"]).endswith("src/arm.py::arm") for _code, answer in answers))
        lineages = {
            (answer["made"]["file"], answer["made"]["function"], answer["made"]["op"],
             answer["modified"]["op"] if answer["modified"] else None)
            for _code, answer in answers
        }
        self.assertEqual({("lib/geo.py", "bore", "Solid.make_cylinder", "cut"), ("arm.py", "arm", "fillet", None)},
                         lineages)
        bore = next(answer for _code, answer in answers if answer["made"]["function"] == "bore")
        self.assertEqual(("lib/geo.py", "bore", "cut"),
                         (bore["modified"]["file"], bore["modified"]["function"], bore["modified"]["op"]))
        self.assertEqual([], bore["inputs"], "a primitive was given no shape")

        code, plane = self._where(bar["planes"][0])
        self.assertEqual(0, code, plane)
        self.assertEqual(("arm.py", "arm", "Solid.make_box"),
                         (plane["made"]["file"], plane["made"]["function"], plane["made"]["op"]))

        code, edge = self._where(bar["edge"])
        self.assertEqual(0, code, edge)
        self.assertEqual("edge", edge["kind"])
        self.assertIn(edge["made"]["op"], ("Solid.make_box", "Solid.make_cylinder", "cut", "fillet"))

        code, occurrence = self._where("#bar")
        self.assertEqual(0, code, occurrence)
        self.assertEqual("occurrence", occurrence["kind"])
        self.assertEqual("arm.py", occurrence["made"]["file"])
        self.assertIsNone(occurrence["modified"])
        self.assertEqual(occurrence["ref"], bar["occurrence"])

        text = self._store("where", "STEP/arm.step", bore["ref"])
        self.assertEqual(0, text.returncode, text.stderr)
        lines = text.stdout.splitlines()
        self.assertTrue(lines[0].startswith("document  "))
        self.assertTrue(any(line.startswith("face      made by lib/geo.py:") and "in bore (Solid.make_cylinder)" in line
                            for line in lines), lines)
        self.assertTrue(any("last modified by lib/geo.py:" in line and "(cut)" in line for line in lines), lines)

    def test_a_part_placed_from_a_child_model_is_answered_from_the_childs_build(self) -> None:
        left = self.refs["pin_left"]
        code, answer = self._where(left["cylinders"][0])
        self.assertEqual(0, code, answer)
        self.assertTrue(_posix(answer["model"]).endswith("src/pin.py::pin"), answer["model"])
        self.assertEqual(("pin.py", "pin", "Solid.make_cylinder"),
                         (answer["made"]["file"], answer["made"]["function"], answer["made"]["op"]))
        self.assertIsNone(answer["modified"])
        code, by_label = self._where("#pin_right/" + left["planes"][0].rsplit("/", 1)[1])
        self.assertEqual(0, code, by_label)
        self.assertEqual("Solid.make_cylinder", by_label["made"]["op"])

    def test_what_it_does_not_know_it_says(self) -> None:
        code, vertex = self._where(self.refs["bar"]["vertex"])
        self.assertEqual(1, code)
        self.assertFalse(vertex["known"])
        self.assertIn("vertex", vertex["reason"])
        bad = self._store("where", "STEP/arm.step", "#no_such_part/f:000000000000")
        self.assertEqual(2, bad.returncode)
        self.assertIn("cadgen store where:", bad.stderr)
        missing = self._store("where", "STEP/never.step", "#bar")
        self.assertEqual(1, missing.returncode)
        self.assertIn("unknown", missing.stdout)
        never = self._store("where", "src/arm.py", "#bar")
        self.assertEqual(1, never.returncode, never.stdout + never.stderr)


class TheEntryAndTheStore(unittest.TestCase):
    """Edits, forget, gc and the warm path, each on its own pin."""

    def setUp(self) -> None:
        self._tmp = generated_cad_directory(prefix="provenance-store-")
        self.root = Path(self._tmp.name) / "proj"
        (self.root / "src").mkdir(parents=True)
        self.cache = Path(self._tmp.name) / "store"
        (self.root / "src" / "pin.py").write_text(textwrap.dedent(PIN).lstrip(), encoding="utf-8")
        (self.root / "src" / "refs.py").write_text(textwrap.dedent(REFS).lstrip(), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build(self, *argv: str, warm: bool = False) -> subprocess.CompletedProcess:
        # The absolute script: the daemon's completed-job line names it (served()).
        result = _run(str(self.root / "src" / "pin.py"), *argv, cwd=self.root, cache=self.cache, warm=warm)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return result

    def _store(self, *argv: str) -> subprocess.CompletedProcess:
        return _run("-m", "cadgen.cli", "store", *argv, cwd=self.root, cache=self.cache)

    def _entry(self) -> dict | None:
        folder = self.cache / "index" / "provenance"
        found = sorted(folder.iterdir()) if folder.is_dir() else []
        self.assertLessEqual(len(found), 1)
        return json.loads(found[0].read_text(encoding="utf-8")) if found else None

    def _face(self) -> str:
        refs = _run("src/refs.py", "STEP/pin.step", cwd=self.root, cache=self.cache)
        self.assertEqual(0, refs.returncode, refs.stdout + refs.stderr)
        [leaf] = json.loads(refs.stdout.strip()).values()
        return leaf["cylinders"][0]

    def test_other_bytes_and_a_forgotten_model_are_unknown_and_a_rerun_answers_again(self) -> None:
        self._build()
        face = self._face()
        self.assertEqual(0, self._store("where", "STEP/pin.step", face).returncode)
        document = self.root / "STEP" / "pin.step"
        with document.open("a", encoding="utf-8") as handle:
            handle.write("\n")
        edited = self._store("where", "STEP/pin.step", face)
        self.assertEqual(1, edited.returncode)
        self.assertIn("not the ones this entry describes", edited.stdout)
        self.assertIsNotNone(self._entry(), "other bytes do not lose the entry")

        forgotten = self._store("forget", "src/pin.py")
        self.assertEqual(0, forgotten.returncode, forgotten.stderr)
        self.assertIsNone(self._entry(), "the entry goes with the record")
        after = self._store("where", "STEP/pin.step", face)
        self.assertEqual(1, after.returncode)
        self.assertIn("no record", after.stdout)

        self._build()
        self.assertIsNotNone(self._entry())
        self.assertEqual(0, self._store("where", "STEP/pin.step", face).returncode)

    def test_the_sweeper_never_evicts_the_entry_and_retires_one_it_does_not_own(self) -> None:
        self._build()
        from cadgen.store import gc

        folder = self.cache / "index" / "provenance"
        [owned] = sorted(folder.iterdir())
        stale = folder / (owned.name.rsplit("-v", 1)[0] + "-v0")
        stale.write_text(owned.read_text(encoding="utf-8"), encoding="utf-8")
        old = time.time() - gc.OBSOLETE_RETIRE_AFTER_SECONDS - 24 * 3600
        os.utime(stale, (old, old))
        with mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.cache)}):
            gc.collect(grace_seconds=0, max_bytes=1)
        self.assertTrue(owned.is_file(), "a pass under the smallest cap keeps the entry")
        self.assertFalse(stale.is_file(), "an entry at another cadgen's key, a week old, is retired")
        self.assertEqual(0, self._store("where", "STEP/pin.step", self._face()).returncode)

    def test_a_warm_rerun_writes_the_entry_a_cold_run_wrote(self) -> None:
        self._build()
        cold = self._entry()
        self.assertIsNotNone(cold)
        self._build("--force", warm=True)
        self.assertTrue(served(self.root.name), "the warm daemon did not build the pin")
        self.assertEqual(cold, self._entry())
        self._build("--force", warm=True)
        self.assertEqual(cold, self._entry(), "a hot run in the same worker writes the same entry")



if __name__ == "__main__":
    unittest.main()
