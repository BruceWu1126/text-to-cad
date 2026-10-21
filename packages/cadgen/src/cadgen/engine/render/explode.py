"""Explode offsets from the viewer's own layout code (``@text-to-cad/core``
``lib/viewer/explodedView.js``, the math ``sceneExplode.js`` runs for an xbf scene), evaluated by
node over the same records the xbf scene hands it: each instance a part, ``partId`` its place in
the document's tree (``o1.2.3``), ``partBounds`` its prototype's box placed by its matrix, the
model's box at rest the bloom's centre. PROTOTYPE: needs node and the repository's source."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import numpy as np

_SCRIPT = r"""
import { computeExplodedViewLayout, applyExplodedViewProgress } from %s;
const input = JSON.parse(process.argv[1]);
class Matrix4 { makeTranslation(x, y, z) { this.t = [x, y, z]; return this; } }
const layout = computeExplodedViewLayout(input.records, input.rest);
applyExplodedViewProgress({ Matrix4 }, layout, input.amount);
const out = {};
for (const record of input.records) out[record.partId] = record.explodedViewMatrix ? record.explodedViewMatrix.t : [0, 0, 0];
process.stdout.write(JSON.stringify(out));
"""


def tree_paths(tree: dict[str, Any]) -> dict[int, str]:
    found: dict[int, str] = {}

    def walk(node: dict[str, Any], path: str) -> None:
        if isinstance(node.get("instance"), int):
            found[node["instance"]] = path
        for at, child in enumerate(node.get("children") or []):
            walk(child, f"{path}.{at + 1}")

    walk(tree, "o1")
    return found


def placed_box(box: tuple[np.ndarray, np.ndarray], matrix: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """three's ``Box3.applyMatrix4``: the box's eight corners placed, and their box."""
    low, high = box
    corners = np.array([[x, y, z] for x in (low[0], high[0]) for y in (low[1], high[1]) for z in (low[2], high[2])])
    placed = corners @ matrix[:3, :3].T + matrix[:3, 3]
    return placed.min(0), placed.max(0)


def offsets(structure: dict[str, Any], boxes: dict[int, tuple[np.ndarray, np.ndarray]], matrices: list[np.ndarray],
            amount: float, source: Path) -> dict[int, np.ndarray]:
    """Each instance's explode offset (world millimetres) at ``amount`` (0..1)."""
    paths = tree_paths(structure.get("tree") or {})
    records, every = [], []
    for index, instance in enumerate(structure["instances"]):
        box = boxes.get(instance["prototype"])
        if box is None or index not in paths:
            continue
        low, high = placed_box(box, matrices[index])
        every.append((low, high))
        records.append({"partId": paths[index], "mesh": True, "partBounds": {"min": low.tolist(), "max": high.tolist()},
                        "index": index})
    records.sort(key=lambda record: [int(part) if part.isdigit() else part for part in record["partId"][1:].split(".")])
    rest = {"min": np.min([b[0] for b in every], 0).tolist(), "max": np.max([b[1] for b in every], 0).tolist()}
    script = _SCRIPT % json.dumps(str(source / "packages/core/src/lib/viewer/explodedView.js"))
    answer = subprocess.run(["node", "--input-type=module", "-e", script,
                             json.dumps({"records": records, "rest": rest, "amount": amount})],
                            capture_output=True, text=True, check=True)
    found = json.loads(answer.stdout)
    return {record["index"]: np.array(found[record["partId"]], float) for record in records}
