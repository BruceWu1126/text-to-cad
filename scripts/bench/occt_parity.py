#!/usr/bin/env python3
"""How alike the CAD Viewer and the OCCT prototype draw the regular display modes.

A manual measurement, never run in CI; what it writes goes under an ignored folder
(``tmp/bench/occt-parity`` by default). Each document is drawn in every mode and camera
asked for, twice: by OCCT's raster (``python -m cadgen.engine.render.occt``'s ``Scene``, the
reference: ``engine/render/look.py`` is its look) and by the snapshot page the viewer's
code draws (``cadgen xbf snapshot``'s page, the xbf family of ``headlessScene.js``), both from
the engine daemon's meshes, at one size. Per picture pair it reports:

- ``iou``: the silhouettes' intersection over union (a pixel is the model's where it is not the
  backdrop);
- ``de_median``, ``de_p95``: CIE76 colour difference (sRGB to Lab, D65) over the pixels both
  pictures give the model;
- ``line_diff``: the share of the pixels either picture inks (not backdrop) where the two differ
  by more than a just-visible ΔE of 10 -- the line modes' measure.

It writes each picture, ``metrics.json`` and a contact sheet per document (Chromium, OCCT and
the ΔE map side by side, at most 1800 px wide), and prints a table.

    PYTHONPATH=packages/cadgen/src .venv/bin/python scripts/bench/occt_parity.py \\
        --doc fixture=path/to/bench.xbf --doc motorbike=models-xbf/motorbike/XBF/motorbike.xbf \\
        --out tmp/bench/occt-parity/after

``--only chromium`` keeps the OCCT pictures already in ``--out`` (the reference does not move
while the viewer is tuned); ``--modes`` and ``--cameras`` narrow the set. The daemon serves and
meshes each document (``CADGEN_ENGINE_DIR`` chooses which daemon); macOS only, as the
prototype's GL context is.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import numpy as np

MODES = ("solid", "shaded", "parts", "xray", "hidden-line", "wireframe")
CAMERAS = ("iso", "front")
LINE_MODES = ("hidden-line", "wireframe")
SILHOUETTE_TOLERANCE = 6  # a pixel is the model's when a channel is this far from the backdrop
VISIBLE_DE = 10.0


# --- measures (pure: numpy arrays in, numbers out) ---------------------------------------------


def srgb_to_lab(pixels: np.ndarray) -> np.ndarray:
    """8-bit sRGB (..., 3) to CIE Lab under D65."""
    c = np.asarray(pixels, float) / 255.0
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    xyz = linear @ np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750],
                             [0.0193339, 0.1191920, 0.9503041]]).T
    xyz = xyz / np.array([0.95047, 1.0, 1.08883])
    delta = 6 / 29
    f = np.where(xyz > delta ** 3, np.cbrt(xyz), xyz / (3 * delta ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], -1)


def silhouette(pixels: np.ndarray, backdrop: tuple[int, int, int]) -> np.ndarray:
    return np.abs(np.asarray(pixels, int) - np.array(backdrop)).max(-1) > SILHOUETTE_TOLERANCE


def compare(left: np.ndarray, right: np.ndarray, backdrop: tuple[int, int, int]) -> dict[str, float]:
    """The measures of one picture pair (two HxWx3 uint8 arrays of one size)."""
    a, b = silhouette(left, backdrop), silhouette(right, backdrop)
    union, both = np.logical_or(a, b), np.logical_and(a, b)
    de = np.linalg.norm(srgb_to_lab(left) - srgb_to_lab(right), axis=-1)
    found = {"iou": float(both.sum() / union.sum()) if union.any() else 1.0,
             "de_median": float(np.median(de[both])) if both.any() else float("nan"),
             "de_p95": float(np.percentile(de[both], 95)) if both.any() else float("nan"),
             "line_diff": float((de[union] > VISIBLE_DE).mean()) if union.any() else 0.0}
    return found


def delta_map(left: np.ndarray, right: np.ndarray, scale: float = 20.0) -> np.ndarray:
    """The ΔE of each pixel as a picture: white 0, through yellow to red at ``scale``."""
    de = np.clip(np.linalg.norm(srgb_to_lab(left) - srgb_to_lab(right), axis=-1) / scale, 0, 1)
    out = np.empty(de.shape + (3,), np.uint8)
    out[..., 0] = 255
    out[..., 1] = (255 * (1 - np.clip(de * 2 - 1, 0, 1)) * 1).astype(np.uint8)
    out[..., 2] = (255 * (1 - np.clip(de * 2, 0, 1))).astype(np.uint8)
    return out


# --- drawing ----------------------------------------------------------------------------------


def draw_occt(doc: Path, folder: Path, modes: list[str], cameras: list[str], size: tuple[int, int]) -> None:
    from cadgen.engine.render.meshes import from_daemon
    from cadgen.engine.render.occt import Scene

    structure, buffers, _ = from_daemon(doc)
    scene = Scene(doc, buffers, size=size, structure=structure)
    for mode in modes:
        scene.show({"mode": mode})
        for camera in cameras:
            scene.frame(camera, "orthographic")
            scene.save(folder / f"{mode}-{camera}-occt.png")


def draw_chromium(doc: Path, folder: Path, modes: list[str], cameras: list[str], size: tuple[int, int]) -> None:
    from cadgen.engine import snapshot as snap

    served = snap._Served(doc.resolve(), daemon=True)
    try:
        snap._get_json(f"{served.url}/mesh", method="POST")
        for mode in modes:
            outputs = [{"path": str(folder / f"{mode}-{camera}-chromium.png"), "width": size[0], "height": size[1],
                        "camera": camera} for camera in cameras]
            job = {"kind": "xbf", "mode": "view", "display": {"mode": mode}, "outputs": outputs,
                   "resolved": {"kind": "xbf", "url": served.url, "inputPath": str(doc)}}
            result, _timings = snap._render(job, snap._runtime(), size)
            for output, drawn in zip(outputs, result["outputs"]):
                Path(output["path"]).write_bytes(base64.b64decode(str(drawn["dataUrl"]).partition(",")[2]))
    finally:
        served.close()


def sheet(folder: Path, name: str, rows: list[tuple[str, dict[str, float]]], out: Path) -> None:
    """Chromium, OCCT and the ΔE map per row, the measures beside; 1800 px wide."""
    from PIL import Image, ImageDraw, ImageFont

    label, cell, head = 240, (520, 390), 44
    image = Image.new("RGB", (label + 3 * cell[0], head + len(rows) * cell[1]), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        small = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 15)
    except OSError:
        font = small = ImageFont.load_default()
    draw.text((10, 12), name, fill="black", font=font)
    for column, title in enumerate(("Viewer (three.js)", "OCCT (reference)", "ΔE (white 0, red 20+)")):
        draw.text((label + column * cell[0] + 12, 12), title, fill="black", font=font)
    for at, (row, found) in enumerate(rows):
        y = head + at * cell[1]
        draw.line([(0, y), (image.width, y)], fill="#999999")
        draw.text((10, y + 10), row, fill="black", font=font)
        lines = [f"IoU {found['iou']:.3f}", f"ΔE median {found['de_median']:.2f}", f"ΔE p95 {found['de_p95']:.2f}",
                 f"line diff {found['line_diff']:.3f}"]
        for n, text in enumerate(lines):
            draw.text((10, y + 44 + n * 22), text, fill="#333333", font=small)
        left = np.asarray(Image.open(folder / f"{row}-chromium.png").convert("RGB"))
        right = np.asarray(Image.open(folder / f"{row}-occt.png").convert("RGB"))
        for column, picture in enumerate((left, right, delta_map(left, right))):
            tile = Image.fromarray(picture)
            tile.thumbnail(cell)
            image.paste(tile, (label + column * cell[0], y))
    image.save(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--doc", action="append", required=True, help="NAME=PATH of an .xbf, repeated")
    parser.add_argument("--out", default="tmp/bench/occt-parity")
    parser.add_argument("--modes", default=",".join(MODES))
    parser.add_argument("--cameras", default=",".join(CAMERAS))
    parser.add_argument("--size", default="1200x900")
    parser.add_argument("--only", choices=("occt", "chromium"), help="draw one side; keep the other's pictures")
    args = parser.parse_args(argv)
    from cadgen.engine.render.look import LOOK

    backdrop = tuple(int(LOOK.backdrop[i:i + 2], 16) for i in (1, 3, 5))
    modes, cameras = args.modes.split(","), args.cameras.split(",")
    size = tuple(int(v) for v in args.size.split("x"))
    out = Path(args.out)
    table = {}
    for entry in args.doc:
        name, _sep, path = entry.partition("=")
        doc = Path(path).resolve()
        folder = out / name
        folder.mkdir(parents=True, exist_ok=True)
        started = time.perf_counter()
        if args.only != "chromium":
            draw_occt(doc, folder, modes, cameras, size)
        if args.only != "occt":
            draw_chromium(doc, folder, modes, cameras, size)
        from PIL import Image

        rows = []
        for mode in modes:
            for camera in cameras:
                row = f"{mode}-{camera}"
                left = np.asarray(Image.open(folder / f"{row}-chromium.png").convert("RGB"))
                right = np.asarray(Image.open(folder / f"{row}-occt.png").convert("RGB"))
                rows.append((row, compare(left, right, backdrop)))
        table[name] = dict(rows)
        (folder / "metrics.json").write_text(json.dumps(table[name], indent=1))
        sheet(folder, name, rows, out / f"sheet-{name}.png")
        print(f"{name}: {time.perf_counter() - started:.1f}s", file=sys.stderr)
    print("| document | row | IoU | ΔE median | ΔE p95 | line diff |")
    print("| --- | --- | --- | --- | --- | --- |")
    for name, rows in table.items():
        for row, found in rows.items():
            print(f"| {name} | {row} | {found['iou']:.3f} | {found['de_median']:.2f} | {found['de_p95']:.2f} | "
                  f"{found['line_diff']:.3f} |")
    (out / "table.json").write_text(json.dumps(table, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
