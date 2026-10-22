"""A build's preview revision (STORE.md §9b): the result tree the moment the result exists,
its annotations as one object, and its parts as the build pool's one job derives them, all
before the STEP is published; and the saved file's parts are the preview's."""
from __future__ import annotations

import contextlib
import io
import os
import unittest
from pathlib import Path
from unittest import mock

from tests.python.support.tmp_root import generated_cad_directory

MODEL = '''
import cadgen
from cadgen import revolute, step
from cadgen import build123d as bd

MATERIALS = {"definitions": {"paint": {"baseColor": "#336699"}},
             "assignments": [{"targets": ["#arm"], "material": "paint"}]}


def swing_open(t, m):
    m.get("#arm").rotate((0, 0, 1), 45 * t, (0, 0, 6))


@step(kinematics={"mates": [revolute("swing", parent="#base", child="#arm", origin=(0, 0, 6),
                                      direction=(0, 0, 1), limits=(0, 90))]},
      materials=MATERIALS, animation={"open": cadgen.clip(swing_open, duration=2, label="Open")})
def part():
    base = bd.Box(30, 20, 4)
    base.label = "base"
    arm = bd.Pos(10, 0, 6) * bd.Box(16, 4, 4)
    arm.label = "arm"
    return bd.Compound(children=[base, arm])


if __name__ == "__main__":
    part()
'''

UNRESOLVABLE = MODEL.replace('m.get("#arm")', 'm.get("#nothing")')


class PreviewRevision(unittest.TestCase):
    def setUp(self) -> None:
        temporary = generated_cad_directory(prefix="preview-revision-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.model = self.root / "part.py"
        self.step = self.root / "part.step"
        self.store = self.root / "store"
        env = mock.patch.dict(os.environ, {"CADGEN_CACHE_DIR": str(self.store), "CADGEN_JOBS": "1"})
        env.start()
        self.addCleanup(env.stop)

    def build(self, source: str = MODEL) -> tuple[int, list[dict]]:
        """Run the model in this process, with an event sink that notes at each event
        whether the STEP exists yet: ``(exit code, events)``."""
        from cadgen.cli._run_model import run_model_argv
        from cadgen.daemon import executors

        self.model.write_text(source, encoding="utf-8")
        events: list[dict] = []
        executors.set_event_sink(lambda event: events.append({**event, "stepOnDisk": self.step.is_file()}))
        self.addCleanup(executors.set_event_sink, None)
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = run_model_argv([str(self.model)])
        self.output = output.getvalue()
        return code, events

    @staticmethod
    def _of(events: list[dict], field: str) -> list[dict]:
        return [event for event in events if field in event]

    def test_the_preview_names_its_parts_before_the_step_is_written_and_the_saved_file_shares_them(self):
        from cadgen.daemon import artifacts
        from cadgen.store import parts
        from cadgen.store.records import document_entry_for_hash
        from cadgen.store.view import descriptor_for_view

        with mock.patch.object(artifacts, "submit_artifact", wraps=artifacts.submit_artifact) as submitted:
            code, events = self.build()
        self.assertEqual(code, 0, self.output)
        previews, annotations, saved = (self._of(events, field) for field in ("preview", "annotations", "saved"))
        self.assertTrue(previews and annotations and saved, events)
        # The tree first; its annotations, and its parts once the pool's job is done, whichever
        # is first; then the save. The tree and its annotations go out before the result is
        # recorded, so before its export job exists: no STEP on disk then. The parts' event is
        # this process hearing the display job end, which the export waits for in the pool, not
        # for that event: where the file is by then is the pool's timing, not the contract.
        self.assertLess(events.index(previews[0]), events.index(annotations[0]))
        self.assertLess(max(events.index(previews[-1]), events.index(annotations[0])), events.index(saved[0]))
        self.assertFalse(any(event["stepOnDisk"] for event in [previews[0], *annotations]))
        self.assertTrue(saved[0]["stepOnDisk"])
        tree = previews[0]["preview"]["tree"]
        self.assertTrue(all(event["preview"]["tree"] == tree for event in previews), "one result tree")
        keys, producer = parts.part_keys(tree)
        final = previews[-1]["preview"]
        self.assertEqual((final["parts"], final["complete"], final["producer"]), (keys, True, producer))
        # Stored parts first, then the job's: the map only grows.
        sizes = [len(event["preview"]["parts"]) for event in previews]
        self.assertEqual(sizes, sorted(sizes))
        jobs = [call.args[0] for call in submitted.call_args_list]
        [job] = [job for job in jobs if job["kind"] == "display"]
        self.assertEqual((job["tree"], sorted(job["cids"])), (tree, sorted(keys)), "one job derives the build's parts")
        # The file is an export of the same tree.
        self.assertEqual([(job["kind"], job["tree"]) for job in jobs if job["kind"] != "display"],
                         [("export", tree)])
        # The saved document names the same parts (law 20): nothing is derived or fetched twice.
        digest = saved[0]["saved"]["documentHash"]
        document = descriptor_for_view(document_entry_for_hash(digest)["tree"], document_hash=digest)
        self.assertEqual({entry["part"] for entry in document["components"].values()}, set(keys.values()))

    def test_a_build_bakes_its_clips_once_for_the_preview_and_the_sidecar(self):
        """The file is written from the result tree the preview resolves against, so its
        sidecar carries the very keyframes the annotations do: baked once, not twice."""
        from cadgen._internal import animation_bake

        with mock.patch.object(animation_bake, "bake_document_animation",
                               wraps=animation_bake.bake_document_animation) as bake:
            code, events = self.build()
        self.assertEqual(code, 0, self.output)
        self.assertTrue(self._of(events, "annotations") and self._of(events, "saved"))
        self.assertEqual(1, bake.call_count)

    def test_a_declaration_that_fails_to_resolve_fails_the_build_after_its_preview(self):
        code, events = self.build(UNRESOLVABLE)
        self.assertNotEqual(code, 0)
        self.assertIn("#nothing", self.output)
        previews = self._of(events, "preview")
        self.assertTrue(previews and previews[-1]["preview"]["complete"], "the preview stands, under the failure")
        self.assertFalse(self._of(events, "annotations") or self._of(events, "saved"))
        self.assertFalse(self.step.exists())

    def test_without_a_display_producer_the_preview_names_its_tree_and_no_parts(self):
        with mock.patch("cadgen.store.parts.producer_identity", side_effect=ValueError("unknown kernel")):
            code, events = self.build()
        self.assertEqual(code, 0, self.output)
        previews = [event["preview"] for event in self._of(events, "preview")]
        self.assertTrue(previews)
        self.assertTrue(all(preview["producer"] is None and preview["parts"] == {} and preview["complete"] is False
                            for preview in previews), previews)


if __name__ == "__main__":
    unittest.main()
