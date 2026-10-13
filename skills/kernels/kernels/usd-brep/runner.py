"""Run a cadgen model on NVIDIA's usd-brep kernel through keel and write STEP.

Runs under keel's Python (``keel/bin/keel``), which has no cadgen: keel ships
keel123d, build123d's API over usd-brep, so the model program runs unchanged
with it standing in for build123d. keel writes
the STEP itself, in the face and edge order OpenCASCADE reads back, so refs
picked in the CAD Viewer name the same faces keel numbers.

    keel runner.py <model.py> <function> <out.step>

Prints one JSON line last: {"ok", "volume", "valid"} or {"ok": false, "error"}.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "_shared"))

import cadgen_stub  # noqa: E402


def _install_shims(kb):
    """Fill the gaps between keel123d and build123d's builder API."""
    if not hasattr(kb.BuildPart, "edges"):
        kb.BuildPart.edges = lambda self: self.part.edges()
        kb.BuildPart.faces = lambda self: self.part.faces()
        kb.BuildPart.vertices = lambda self: self.part.vertices()

    if not hasattr(kb, "Hole"):
        def Hole(radius, depth=None, mode=kb.Mode.SUBTRACT):  # noqa: N802 - build123d name
            if depth is None:
                box = kb._innermost().part.bounding_box()
                depth = sum((hi - lo) ** 2 for lo, hi in zip(box.min, box.max)) ** 0.5
            # build123d centers the hole on its location: depth both ways.
            return kb.Cylinder(radius, 2 * depth, mode=mode)

        kb.Hole = Hole
        kb.__all__ = [*kb.__all__, "Hole"]


def run(model_path: str, function: str, step_path: str) -> dict:
    import keel123d as kb
    from keel.step import write_step

    _install_shims(kb)
    cadgen_stub.install(kb)
    model = cadgen_stub.load(model_path, function)
    result = cadgen_stub.shape_of(model.call())
    if isinstance(result, kb.BuildPart):
        result = result.part
    parts = list(getattr(result, "_parts", []))
    if len(parts) != 1:
        return {"ok": False, "error": f"keel writes one solid per STEP; this model made {len(parts)}"}
    Path(step_path).parent.mkdir(parents=True, exist_ok=True)
    write_step(parts, step_path)
    return {"ok": True, "volume": float(result.volume), "valid": bool(result.is_valid)}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 3:
        print(json.dumps({"ok": False, "error": "usage: runner.py <model.py> <function> <out.step>"}))
        return 2
    try:
        payload = run(*argv)
    except cadgen_stub.Unsupported as exc:
        payload = {"ok": False, "error": str(exc)}
    except Exception as exc:  # report kernel failures as data
        payload = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=4)}
    sys.stdout.write("\n" + json.dumps(payload) + "\n")
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
