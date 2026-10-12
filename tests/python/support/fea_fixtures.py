"""Shared fixtures for the FEA suites (``tests/python/packages/cadgen/test_fea*.py``).

Importing this puts cadgen's source on ``sys.path`` and says whether the optional
``cadgen[fea]`` stack is installed; the heavy imports (build123d, numpy, cadgen.fea)
stay inside the helpers, so a suite's parse tests run without the extra.
"""

from __future__ import annotations

import io
import json
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr
from pathlib import Path
from unittest import mock

from tests.python.support.paths import REPO_ROOT, add_repo_path

add_repo_path("packages/cadgen/src")

from cadgen._internal.fea.mesh import require_fea_stack  # noqa: E402

try:
    require_fea_stack()
    HAVE_FEA = True
except RuntimeError:
    HAVE_FEA = False

needs_fea = unittest.skipUnless(HAVE_FEA, "the fea extra (netgen-mesher, scikit-fem, pyamg) is not installed")


@contextmanager
def quiet():
    """The solver's progress lines go to stderr; a test that is not about them keeps them out of the run's output."""
    with redirect_stderr(io.StringIO()):
        yield


def solve(step: Path, out: Path, study: dict, **options):
    """``cadgen.fea.solve`` with its progress lines silenced."""
    from cadgen import fea

    with quiet():
        return fea.solve(step, out, study=study, **options)


def sidecar(result) -> dict:
    return json.loads(result.sidecar.read_text(encoding="utf-8"))


def class_directory(cls) -> Path:
    """A temporary directory for a TestCase class's fixtures, removed after its last test."""
    tmp = tempfile.TemporaryDirectory()
    cls.addClassCleanup(tmp.cleanup)
    return Path(tmp.name)


def solve_keeping(analysis: str, step: Path, out: Path, study: dict):
    """Run the study; also hand back the analysis's own result (its fields on the nodes it solved)."""
    from cadgen._internal.fea.analyses import get_analysis

    kind, kept = type(get_analysis(analysis)), {}
    original = kind.solve

    def keep(self, ctx, inputs):
        kept["result"] = original(self, ctx, inputs)
        return kept["result"]

    with mock.patch.object(kind, "solve", keep):
        return solve(step, out, study), kept.get("result")


def heavy_at_import(*modules: str) -> str:
    """The heavy modules importing ``modules`` pulls into a fresh interpreter: "" when they are stdlib-only at import."""
    code = (f"import sys; sys.path.insert(0, 'packages/cadgen/src'); import {', '.join(modules)};"
            "print(','.join(m for m in ('numpy', 'scipy', 'skfem', 'netgen', 'OCP') if m in sys.modules))")
    return subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True, check=True).stdout.strip()


def value_error(case: unittest.TestCase, call, *args, **kwargs) -> str:
    """The message of the ValueError ``call(*args, **kwargs)`` must raise."""
    with case.assertRaises(ValueError) as caught:
        call(*args, **kwargs)
    return str(caught.exception)


# --- STEP fixtures --------------------------------------------------------------------------------------

def _align(align: str | None):
    from build123d import Align

    return {None: None, "x": (Align.MIN, Align.CENTER, Align.CENTER), "corner": (Align.MIN, Align.MIN, Align.MIN),
            "base": (Align.CENTER, Align.CENTER, Align.MIN), "base_x": (Align.MIN, Align.CENTER, Align.MIN)}[align]


def box_step(directory: Path, name: str, size: tuple[float, float, float], align: str | None = None) -> Path:
    """A build123d box written to ``directory/name.step``: centred on the origin, or with ``align`` "x" (length
    along +x from x = 0, centred in y and z), "corner" (in the +x +y +z octant), "base" (standing on z = 0) or
    "base_x" (standing on z = 0, along +x from x = 0)."""
    from build123d import Box, export_step

    step = directory / f"{name}.step"
    aligned = _align(align)
    export_step(Box(*size) if aligned is None else Box(*size, align=aligned), str(step))
    return step


def box_duct_step(directory: Path, side: float, length: float, wall: float = 1.0) -> Path:
    """A square passage through a block along x: sharp corners where the inlet meets the walls."""
    from build123d import Box, export_step

    step = directory / "duct.step"
    along = _align("x")
    export_step(Box(length, side + 2 * wall, side + 2 * wall, align=along) - Box(length, side, side, align=along), str(step))
    return step


def tube_step(directory: Path, bore: float, wall: float, length: float, name: str = "tube") -> Path:
    """A round tube along x from x = 0: bore radius ``bore``, wall ``wall`` thick."""
    from build123d import Align, Cylinder, Rotation, export_step

    step = directory / f"{name}.step"
    along = (Align.CENTER, Align.CENTER, Align.MIN)
    export_step(Rotation(0, 90, 0) * (Cylinder(bore + wall, length, align=along) - Cylinder(bore, length, align=along)), str(step))
    return step


# netgen's edge order of a 10-node tet, and of a 6-node triangle's mid-edge nodes.
_TET_EDGES = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))
_TRIG_EDGES = ((1, 2), (0, 2), (0, 1))


def box_volume(lengths=(4.0, 1.0, 1.0), cells=(4, 1, 1)):
    """A box of 10-node tets as the mesher hands one over: faces 1-6 are x-, x+, y-, y+, z-, z+."""
    import numpy as np
    from skfem import MeshTet

    from cadgen._internal.fea.mesh import FaceFingerprint, VolumeMesh

    mesh = MeshTet.init_tensor(*(np.linspace(0.0, size, count + 1) for size, count in zip(lengths, cells)))
    points = list(mesh.p.T)
    middle: dict[tuple[int, int], int] = {}

    def mid(a: int, b: int) -> int:
        key = (min(a, b), max(a, b))
        if key not in middle:
            middle[key] = len(points)
            points.append(0.5 * (mesh.p[:, a] + mesh.p[:, b]))
        return middle[key]

    tets = [list(t) + [mid(t[a], t[b]) for a, b in _TET_EDGES] for t in mesh.t.T]
    facets = mesh.facets[:, mesh.boundary_facets()].T
    boundary = [list(f) + [mid(f[a], f[b]) for a, b in _TRIG_EDGES] for f in facets]
    nodes = np.array(points)
    centres = nodes[facets].mean(axis=1)
    ordinal = np.zeros(len(facets), dtype=np.int64)
    for axis in range(3):
        ordinal[np.isclose(centres[:, axis], 0.0)] = 2 * axis + 1
        ordinal[np.isclose(centres[:, axis], lengths[axis])] = 2 * axis + 2
    faces = {}
    for o in range(1, 7):
        axis, high = divmod(o - 1, 2)
        centre = [size / 2 for size in lengths]
        centre[axis] = lengths[axis] if high else 0.0
        area = float(np.prod([size for i, size in enumerate(lengths) if i != axis]))
        faces[o] = FaceFingerprint(f"#o1.f{o}", o, area, tuple(centre))
    diagonal = float(np.linalg.norm(lengths))
    return VolumeMesh(nodes=nodes, tets=np.array(tets, dtype=np.int64), boundary=np.array(boundary, dtype=np.int64),
                      boundary_ordinal=ordinal, faces=faces, max_h=max(lengths) / max(cells), bbox_diagonal=diagonal, seconds=0.0)


def _listing(step_or_listing):
    if isinstance(step_or_listing, (str, Path)):
        from cadgen import fea

        return fea.faces(step_or_listing)
    return step_or_listing


def planes_along(step_or_listing, axis: int = 0) -> dict[float, str]:
    """The flat faces normal to ``axis``, by their centre's coordinate along it (a STEP path or a face listing)."""
    return {face.center_mm[axis]: face.ref for face in _listing(step_or_listing).faces
            if face.surface == "plane" and face.normal is not None and abs(abs(face.normal[axis]) - 1) < 1e-6}


def ends(step_or_listing, axis: int = 0) -> tuple[str, str]:
    """The flat faces at the low and the high end of ``axis``."""
    planes = planes_along(step_or_listing, axis)
    return planes[min(planes)], planes[max(planes)]


def side_faces(step: Path) -> list[str]:
    """The faces whose normal is square to x: the sides of a bar along x."""
    return [face.ref for face in _listing(step).faces if face.normal and abs(face.normal[0]) < 1e-6]


# --- Result GLBs ----------------------------------------------------------------------------------------

def read_glb(path: Path) -> tuple[dict, bytes]:
    """A GLB's JSON chunk and its binary chunk's payload."""
    raw = Path(path).read_bytes()
    length, _ = struct.unpack_from("<II", raw, 12)
    return json.loads(raw[20:20 + length]), raw[20 + length + 8:]


def glb_extras(path: Path) -> dict:
    """The result mesh's extras: the analysis, its study, fields, checks and parts."""
    return read_glb(path)[0]["meshes"][0]["extras"]


def glb_attribute_names(path: Path) -> list[str]:
    return list(read_glb(path)[0]["meshes"][0]["primitives"][0]["attributes"])


def assert_tracers_cross(case: unittest.TestCase, glb: Path, length: float, label: str | None = None):
    """The viewer's Flow routine: particles let go at the inlet (x = 0), carried along the duct to its outlet."""
    motion = glb_extras(glb)["analysis"]["motion"]
    case.assertEqual(motion["kind"], "tracers")
    if label is not None:
        case.assertEqual(motion["label"], label)
    case.assertLess(max(path[0] for path in motion["paths"]), 0.05 * length)
    across = [path for path in motion["paths"] if path[-3] > 0.8 * length]
    case.assertGreater(len(across), len(motion["paths"]) / 2)


def glb_rows(path: Path, *names: str) -> dict[str, list[tuple[float, ...]]]:
    """The named float32 vertex attributes (SCALAR or VEC3), each as a list of rows."""
    gltf, binary = read_glb(path)
    attributes = gltf["meshes"][0]["primitives"][0]["attributes"]
    out = {}
    for name in names:
        accessor = gltf["accessors"][attributes[name]]
        view = gltf["bufferViews"][accessor["bufferView"]]
        width = {"SCALAR": 1, "VEC3": 3}[accessor["type"]]
        values = struct.unpack_from(f"<{accessor['count'] * width}f", binary, view["byteOffset"])
        out[name] = [values[i:i + width] for i in range(0, len(values), width)]
    return out


def glb_array(path: Path, name: str, width: int = 1):
    """One float32 vertex attribute as a (count, width) float64 array."""
    import numpy as np

    gltf, binary = read_glb(path)
    accessor = gltf["accessors"][gltf["meshes"][0]["primitives"][0]["attributes"][name]]
    view = gltf["bufferViews"][accessor["bufferView"]]
    data = np.frombuffer(binary, np.float32, accessor["count"] * width, view["byteOffset"])
    return data.reshape(-1, width).astype(float)
