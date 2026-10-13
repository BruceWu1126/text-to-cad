"""Template Python kernel: copy this folder to kernels/<your-id>/, set "id" in
kernel.json to the folder name, and replace build().

kernels.py runs kernel.json's "command" from the working directory. With
"input": "program" it passes the model recorded as a CSG program in JSON
(references/program.md); with "input": "model" it passes the model script and
its function name, for a kernel that runs build123d code itself.

Write the file the command names ({stl} or {step}), then print one JSON line
last: {"ok": true, "volume": mm3, "warnings": [...]} or
{"ok": false, "error": "..."}. A STEP can be picked in the CAD Viewer like any
STEP; an STL is a mesh. This placeholder writes the part's bounding box.
"""
import json
import struct
import sys


def bounds(node):
    """Bounding box of box/cylinder/sphere leaves (enough for a placeholder)."""
    kind = node["type"]
    if kind in ("union", "intersect"):
        boxes = [b for b in (bounds(c) for c in node["children"]) if b]
        if not boxes:
            return None
        return ([min(b[0][i] for b in boxes) for i in range(3)], [max(b[1][i] for b in boxes) for i in range(3)])
    if kind == "subtract":
        return bounds(node["base"])
    if kind in ("fillet", "chamfer"):
        return bounds(node["child"])
    m = node.get("matrix")
    if kind == "box":
        h = [s / 2 for s in node["size"]]
    elif kind in ("cylinder", "sphere"):
        r = node["radius"]
        h = [r, r, node.get("height", 2 * r) / 2]
    else:
        return None
    c = (m[3], m[7], m[11])
    return ([c[i] - h[i] for i in range(3)], [c[i] + h[i] for i in range(3)])


def build(program, stl_path):
    box = bounds(program["root"])
    if box is None:
        return {"ok": False, "error": "the template only bounds boxes, cylinders and spheres"}
    lo, hi = box
    corner = lambda i: (hi[0] if i & 1 else lo[0], hi[1] if i & 2 else lo[1], hi[2] if i & 4 else lo[2])
    faces = [(0, 2, 1), (1, 2, 3), (4, 5, 6), (5, 7, 6), (0, 1, 4), (1, 5, 4),
             (2, 6, 3), (3, 6, 7), (0, 4, 2), (2, 4, 6), (1, 3, 5), (3, 7, 5)]
    with open(stl_path, "wb") as fh:
        fh.write(b"\0" * 80 + struct.pack("<I", len(faces)))
        for face in faces:
            fh.write(struct.pack("<3f", 0, 0, 0))
            for i in face:
                fh.write(struct.pack("<3f", *corner(i)))
            fh.write(b"\0\0")
    volume = (hi[0] - lo[0]) * (hi[1] - lo[1]) * (hi[2] - lo[2])
    return {"ok": True, "volume": volume, "warnings": ["template kernel: shows the bounding box"]}


if __name__ == "__main__":
    program_path, stl_path = sys.argv[1:3]
    with open(program_path) as fh:
        program = json.load(fh)
    try:
        result = build(program, stl_path)
    except Exception as exc:  # report failures as data, not a traceback
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(result))
