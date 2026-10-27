"""Actual browser frames encoded to MP4, then decoded: authored native and glTF motion.

The two tiny models, cache and outputs belong to a fresh temporary directory. These
tests use the explicit raster animation path so CI's software GL exercises the real
browser and shipping codec without pretending to validate WebGPU image quality.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from cadgen.engine import build as engine_build
from cadgen.engine.snapshot import SnapshotRefused, snapshot


MODEL = """
import build123d as bd
from cadgen import glb, xbf
from cadgen.ap242 import configuration_sequence
from cadgen.gltf import animation


@xbf("XBF/indicator.xbf")
@glb("indicator.glb")
def indicator():
    base = bd.Pos(-25, 0, 0) * bd.Box(14, 20, 8)
    link = bd.Pos(20, 0, 0) * bd.Box(40, 8, 8)
    base.label, link.label = "blue_base", "red_link"
    base.color, link.color = bd.Color("#164cdd"), bd.Color("#ed281d")
    pivot = bd.Axis((0, 0, 0), (0, 0, 1))
    bd.RevoluteJoint("hinge", base, axis=pivot, angular_range=(0, 90))
    bd.RigidJoint("mount", link, bd.Plane(origin=pivot.position, z_dir=pivot.direction).location)
    base.joints["hinge"].connect_to(link.joints["mount"], angle=0)
    indicator = bd.Compound(children=[base, link], label="indicator")
    configuration_sequence(indicator, "swing", sweep={"hinge": (0, 90)}, duration=1.25, ease="linear")
    animation(indicator, "slide", duration=1.25).translation("red_link", by=(0, 40, 0), interpolation="LINEAR")
    return indicator
"""

SIZE = (240, 180)
FPS = 6
DURATION = 1.25
FRAME_COUNT = math.ceil(DURATION * FPS)
CAMERA = {"position": [5, 20, 180], "target": [5, 20, 0], "up": [0, 1, 0],
          "zoom": 1, "orthographicHalfHeight": 65}


def decoded(path: Path) -> tuple[dict, list[np.ndarray]]:
    """Decode with the same bundled executable shipped for encoding, not a host ffprobe."""
    import imageio_ffmpeg

    answer = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-hide_banner", "-i", str(path),
                             "-map", "0:v:0", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                            capture_output=True, check=True, timeout=30)
    info = answer.stderr.decode("utf-8", "replace")
    stream = re.search(r"Video: ([^ ,]+).*?, (\d+)x(\d+)[ ,].*?(\d+(?:\.\d+)?) fps", info)
    duration = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", info)
    if stream is None or duration is None:
        raise AssertionError(f"decoder returned no video metadata: {info}")
    codec, width, height, fps = stream.groups()
    width, height = int(width), int(height)
    metadata = {"size": (width, height), "codec": codec, "fps": float(fps),
                "duration": sum(float(part) * scale for part, scale in zip(duration.groups(), (3600, 60, 1)))}
    # subprocess.run closes all pipe handles, including after a decoder failure.
    # imageio-ffmpeg 0.6's read_frames leaves stdin/stdout open on normal EOF.
    pixels = list(np.frombuffer(answer.stdout, dtype=np.uint8).reshape(-1, height, width, 3))
    return metadata, pixels


def colored_region(image: np.ndarray, color: str) -> tuple[np.ndarray, int]:
    values = image.astype(np.int16)
    r, g, b = values[:, :, 0], values[:, :, 1], values[:, :, 2]
    mask = ((r > g + 35) & (r > b + 35)) if color == "red" else ((b > r + 35) & (b > g + 20))
    y, x = np.nonzero(mask)
    if len(x) < 20:
        raise AssertionError(f"decoded frame lost the {color} part ({len(x)} pixels)")
    return np.array([x.mean(), y.mean()]), len(x)


class SnapshotAnimationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.folder = Path(tempfile.mkdtemp(prefix="snapshot-animation-")).resolve()
        cls.environment = patch.dict(os.environ, {"CADGEN_ENGINE_MESH": "inline",
                                                  "CADGEN_CACHE_DIR": str(cls.folder / "cache")})
        cls.environment.start()
        script = cls.folder / "indicator.py"
        script.write_text(textwrap.dedent(MODEL), encoding="utf-8")
        reports = engine_build.build(script, ["indicator"], cwd=cls.folder)
        if not reports or not all(report["ok"] for report in reports):
            cls.environment.stop()
            shutil.rmtree(cls.folder, ignore_errors=True)
            raise AssertionError(reports)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.environment.stop()
        shutil.rmtree(cls.folder, ignore_errors=True)

    def render(self, source: Path, name: str | bool, filename: str) -> tuple[dict, list[np.ndarray]]:
        out = self.folder / filename
        report = snapshot(source, out, animation=name, fps=FPS, camera=CAMERA,
                          display={"mode": "solid", "grid": False,
                                   "camera": {"projection": "orthographic"}},
                          size=SIZE, daemon=False)
        self.assertEqual(report["renderer"], "raster")
        self.assertEqual([output["mimeType"] for output in report["outputs"]], ["video/mp4"])
        animation = report["animation"]
        self.assertAlmostEqual(animation["duration"], DURATION)
        self.assertEqual((animation["frames"], animation["fps"]), (FRAME_COUNT, FPS))
        np.testing.assert_allclose(animation["times"], np.arange(FRAME_COUNT) / FPS, atol=1e-12)
        metadata, frames = decoded(out)
        self.assertEqual(metadata["size"], SIZE)
        self.assertEqual(len(frames), FRAME_COUNT)
        self.assertAlmostEqual(metadata["fps"], FPS, places=3)
        self.assertAlmostEqual(metadata["duration"], FRAME_COUNT / FPS, delta=0.05)
        self.assertIn("h264", metadata["codec"])
        # The stationary blue fiducial proves that fitting, camera and pose sampling
        # do not follow the moving red part. Its camera stays fixed for the entire clip.
        blue = [colored_region(frame, "blue")[0] for frame in frames]
        self.assertLess(float(np.max(np.linalg.norm(np.array(blue) - blue[0], axis=1))), 1.5)
        return report, frames

    def test_native_ap242_sequence_is_selected_automatically_and_moves_in_the_encoded_video(self) -> None:
        report, frames = self.render(self.folder / "XBF/indicator.xbf", True, "native.mp4")
        self.assertEqual(report["animation"]["name"], "swing")
        red = [colored_region(frame, "red") for frame in frames]
        self.assertGreater(float(np.linalg.norm(red[-1][0] - red[0][0])), 15)
        # An in-plane hinge preserves the part's projected area. A renderer that
        # accumulates successive poses would leave an increasingly large red trail.
        areas = [area for _center, area in red]
        self.assertLess(max(areas) / min(areas), 1.25)

    def test_named_gltf_clip_has_the_authored_translation_and_a_fixed_camera(self) -> None:
        report, frames = self.render(self.folder / "indicator.glb", "slide", "gltf.mp4")
        self.assertEqual(report["animation"]["name"], "slide")
        centers = np.array([colored_region(frame, "red")[0] for frame in frames])
        steps = np.diff(centers, axis=0)
        self.assertGreater(float(np.linalg.norm(centers[-1] - centers[0])), 25)
        # LINEAR motion advances at the same speed at every deterministic frame,
        # including the final frame before the authored duration (no extra endpoint).
        np.testing.assert_allclose(steps, np.repeat(steps.mean(axis=0)[None, :], len(steps), axis=0), atol=1.5)

    def test_unknown_routine_does_not_replace_an_existing_video_or_leave_staging_files(self) -> None:
        out = self.folder / "existing.mp4"
        out.write_bytes(b"previous video")
        before = set(self.folder.iterdir())
        with self.assertRaisesRegex(SnapshotRefused, "Available routines"):
            snapshot(self.folder / "indicator.glb", out, animation="missing", fps=FPS, size=SIZE, daemon=False)
        self.assertEqual(out.read_bytes(), b"previous video")
        self.assertEqual(set(self.folder.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
