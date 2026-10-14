"""Linear static elasticity on CalculiX (``ccx``) when it is installed: the same study, solved by an established code.

A static solve (:func:`cadgen._internal.fea.solve.solve_linear_static`) runs on CalculiX when a ``ccx`` program is
at hand and the study is one CalculiX solves as the built-in solver does (:func:`declines` says which); otherwise, and
whenever CalculiX fails or cannot be downloaded, it runs on the built-in solver. Either way it is the same
answer on the same mesh, and the result's ``solver`` names what ran ("calculix 2.23 (C3D10)").

The two share everything but the stiffness and the linear solve. cadgen meshes, builds the element space, assembles
the load vector (forces, pressures, per-facet pressures and body loads become nodal forces exactly as the built-in
solve makes them) and finds the held DOF. CalculiX gets the quadratic tetrahedra as C3D10 elements (corners turned to
a positive volume, each mid-edge node placed by the edge it is on, read from the mesh's own edge table), one isotropic
material per part, the held DOF as boundary conditions and the loads as nodal forces. It assembles its own stiffness,
factors it (SPOOLES), and prints the displacements, the reactions and the stresses at each element's four integration
points.

Stress is reported at the built-in solve's quadrature points, so von Mises, its Gauss-point peak and its L2 projection
mean the same thing whichever solver ran. On a straight-edged element the stress is linear, so CalculiX's four values
give it at any point exactly; on a curved one (a mid-edge node bowed onto a hole or a fillet) it is not, and cadgen
recovers it from CalculiX's displacement instead, as the built-in solve does. CalculiX prints seven significant
digits: plenty for a stress or a displacement, but a strain taken from printed displacements loses digits where the
part moves far for its strain (a long thin bar), which is why the straight elements read CalculiX's own stress. A
sample of elements is checked both ways: cadgen's recovery from the displacement must match CalculiX's stress, or the
two did not read the same mesh and the built-in solver takes over.

On a straight-edged mesh the answers agree to CalculiX's seven digits. On a curved one they differ by what the
quadrature does to a curved element (CalculiX integrates C3D10 at 4 points, the built-in solver at 11): on a plate
with a hole meshed at 3 mm, displacement by 3.5e-5 of its largest and the peak at the hole by 0.17 %, far less than
a finer mesh moves it.

``ccx`` is ``CADGEN_CCX`` (the program's path; ``0`` turns CalculiX off), else the pinned build cadgen downloads into
its cache the first time a study suits it (:mod:`cadgen._internal.fea.calculix_install`, 9 to 21 MB, once), else one
on PATH. Stdlib only at import; numerics inside the functions.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    import numpy as np

    from cadgen._internal.fea.femspace import FemSpace
    from cadgen._internal.fea.materials import Material
    from cadgen._internal.fea.supports import Supports

__all__ = [
    "C3D10_EDGES", "C3D10_POINTS", "CCX_ENV", "DOF_LIMIT", "NAMES", "Ccx", "Solved", "c3d10_connectivity", "choose",
    "cross_check", "declines", "environment", "find_ccx", "read_dat", "solve_static", "straight_elements", "threads",
    "write_inp",
]

#: The environment variable naming the ``ccx`` program; ``0`` turns CalculiX off.
CCX_ENV = "CADGEN_CCX"
#: What ``ccx`` is called on PATH: the plain name, then the versioned names some packages install.
NAMES = ("ccx", "ccx_2.23", "ccx_2.22", "ccx_2.21")
#: Above this many DOF a static solve stays on the built-in solver. SPOOLES, CalculiX's direct solver, fills in as a
#: direct solver does: a 50 mm cube of 207k DOF took 74 s and 4.3 GB where multigrid took 36 s, and the fit ladder
#: (fit.py) budgets multigrid's memory. At 73k DOF it took 5 s and 1.2 GB, and a bar of 140k DOF 10 s and 1.8 GB.
DOF_LIMIT = 150_000
#: C3D10's mid-edge nodes 5-10 sit on these corner pairs (CalculiX's order, Abaqus's too).
C3D10_EDGES = ((0, 1), (1, 2), (2, 0), (0, 3), (1, 3), (2, 3))
_A, _B = 0.138196601125011, 0.585410196624968
#: C3D10's four integration points in its natural coordinates, in CalculiX's order: the k-th sits nearest corner k.
#: skfem's reference tetrahedron has the same corners, so these are its reference coordinates too.
C3D10_POINTS = ((_A, _A, _A), (_B, _A, _A), (_A, _B, _A), (_A, _A, _B))
#: Elements whose stress is checked against cadgen's recovery from CalculiX's displacement.
SAMPLE_ELEMENTS = 64
#: The check fails when the sample's median element differs by more than this share of its own largest stress
#: component: a faithful run sits near 1e-5 (seven printed digits), a mesh read differently near 1.
CROSS_CHECK = 1e-2
#: A mid-edge node within this share of its edge's length of the midpoint is on a straight edge.
STRAIGHT = 1e-9
#: CalculiX's .inp reads each number from its first 20 characters: 13 significant digits fit with sign and exponent.
_REAL = "{:.12e}"
_JOB = "static"


@dataclass(frozen=True)
class Ccx:
    """A ``ccx`` program that runs: its path and the version it reports ("2.23"; "" when it says none)."""

    path: str
    version: str

    @property
    def label(self) -> str:
        """The result's ``solver``: "calculix 2.23 (C3D10)"."""
        return f"calculix {self.version} (C3D10)" if self.version else "calculix (C3D10)"


_FOUND: dict[str, "tuple[Ccx | None, str]"] = {}


def find_ccx() -> "tuple[Ccx | None, str]":
    """The ``ccx`` to run and, when there is none, why, if anyone asked for one.

    ``CADGEN_CCX`` set to a path names the program; ``0`` turns CalculiX off (``(None, "")``). Unset, the pinned
    build in cadgen's cache, else the first of :data:`NAMES` on PATH that runs, else the pinned build downloaded now;
    ``(None, "")`` where there is none: nothing was asked, so nothing is said in the result. A ``CADGEN_CCX`` that
    names no working ccx says so. Each setting is looked up once, except a failed download, which the next solve tries
    again (a warm daemon outlives a dropped network).
    """
    setting = os.environ.get(CCX_ENV, "").strip()
    if setting in _FOUND:
        return _FOUND[setting]
    found, retry = _look(setting)
    if not retry:
        _FOUND[setting] = found
    return found


def _look(setting: str) -> "tuple[tuple[Ccx | None, str], bool]":
    """The lookup, and whether to try it again next time."""
    if setting == "0":
        return (None, ""), False
    if setting:
        path = shutil.which(setting) or (setting if os.path.isfile(setting) else None)
        ccx = _probe(path) if path else None
        return ((ccx, "") if ccx else (None, f"{CCX_ENV}={setting} is not a CalculiX program that runs")), False
    # The pinned build in cadgen's cache, else one on PATH, else the pinned build downloaded now (once per machine).
    from cadgen._internal.fea.calculix_install import ensure_calculix

    found = ensure_calculix(say=_say)
    if found.path is not None and (ccx := _probe(str(found.path))) is not None:
        return (ccx, ""), False
    if found.source.startswith("CalculiX could not be downloaded"):
        # Said with the progress lines, not in the result: the same study writes the same result offline or online,
        # and its ``solver`` already names the built-in one.
        _say(f"cadgen: {found.source}; solving with the built-in solver")
        return (None, ""), True
    return (None, ""), False


def _say(line: str) -> None:
    """The one line a download prints, on stderr: stdout is the result's."""
    print(line, file=sys.stderr, flush=True)


def _probe(path: str) -> "Ccx | None":
    """``ccx -v`` says "This is Version 2.23"; a program that does not is not CalculiX."""
    try:
        out = subprocess.run([path, "-v"], capture_output=True, text=True, errors="replace", timeout=30,
                             env=environment(1)).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    if "Version" not in out:
        return None
    version = re.search(r"Version\s+([0-9][0-9.]*[0-9])", out)
    return Ccx(path=path, version=version.group(1) if version else "")


def threads() -> int:
    """The threads one CalculiX run gets: one, always.

    On several threads CalculiX sums in a different order each run, so the same study wrote different bytes each
    time (cadgen writes the same bytes for the same study), and on four it returned spurious vibration modes with no
    error. One thread was no slower on a 61k-DOF static solve; studies solving side by side use the other cores.
    """
    return 1


def environment(count: int) -> dict[str, str]:
    """The process environment with every CalculiX thread count (OpenMP's and its own) set to ``count``."""
    env = dict(os.environ)
    for name in ("OMP_NUM_THREADS", "CCX_NPROC_EQUATION_SOLVER", "CCX_NPROC_STIFFNESS", "CCX_NPROC_RESULTS"):
        env[name] = str(count)
    return env


# -- when -------------------------------------------------------------------------------------------

def declines(space: "FemSpace", materials: "Material | Sequence[Material]", *, solver: str | None = None,
             initial_strain=None, supports: "Supports | None" = None) -> str | None:
    """Why a static solve stays on the built-in solver, or ``None`` when CalculiX solves it as that would.

    CalculiX takes: quadratic (10-node) tetrahedra; isotropic linear elastic materials, one part or an assembly of
    them (one material per part); fixed faces, rollers on faces square to an axis, and symmetry planes (each holds
    whole DOF); forces, pressures, per-facet pressures, gravity and accelerations (cadgen makes them nodal forces);
    up to :data:`DOF_LIMIT` DOF.

    The built-in solver keeps: a solver asked for by name (``direct``, ``iterative``, ``matrix_free``: the fit
    ladder's choices, and a test's); linear (4-node) elements; an orthotropic material or a laminate; an initial
    (thermal) strain; a roller on a sloped or curved face (its nodes are held in axes of their own); and anything
    larger than :data:`DOF_LIMIT`, where SPOOLES's memory is not what the fit ladder budgeted.
    """
    from cadgen._internal.fea.operators import anisotropic

    if solver not in (None, "calculix"):
        return f"the {solver} solver was asked for"
    if space.order != 2:
        return "the mesh has linear (4-node) elements"
    if anisotropic(materials):
        return "a material is orthotropic or layered"
    if initial_strain is not None:
        return "the study has an initial (thermal) strain"
    if supports is not None and supports.turned:
        return "a roller holds a sloped or curved face"
    if space.dofs > DOF_LIMIT:
        return f"{space.dofs} DOF is past the {DOF_LIMIT} CalculiX's direct solver takes"
    return None


def choose(space: "FemSpace", materials, *, solver: str | None, initial_strain=None,
           supports: "Supports | None" = None) -> "tuple[Ccx | None, str]":
    """The ``ccx`` to solve this static study with (``None``: the built-in solver), and a warning to give, or "".

    The warning is only for a CalculiX someone asked for and did not get: ``solver="calculix"``, or a
    ``CADGEN_CCX`` that names no working program. With nothing asked, the built-in solver is simply the solver.
    """
    if solver not in (None, "calculix"):
        return None, ""
    # Whether CalculiX suits the study comes first: finding it may download it, which a study it declines never needs.
    reason = declines(space, materials, solver=solver, initial_strain=initial_strain, supports=supports)
    ccx, missing = find_ccx() if reason is None else (None, "")
    if ccx is not None:
        return ccx, ""
    if solver == "calculix":
        off = os.environ.get(CCX_ENV, "").strip() == "0"
        why = reason or missing or (f"{CCX_ENV}=0 turns it off" if off else
                                    f"no ccx program was found (install conda-forge's calculix, or set {CCX_ENV})")
        return None, f"CalculiX was asked for, but {why}: solved with the built-in solver"
    return None, (f"{missing}: solved with the built-in solver" if missing else "")


# -- the input ----------------------------------------------------------------------------------------

def c3d10_connectivity(space: "FemSpace") -> "tuple[np.ndarray, np.ndarray]":
    """``(nodes, turned)``: each element's ten scalar DOF in C3D10's order, and which elements were turned.

    The corners come in skfem's order, turned (corners 2 and 3 swapped) where that gives a negative volume, since
    CalculiX needs ``(x2 - x1) × (x3 - x1) · (x4 - x1) > 0``. Each mid-edge node is then the one on the edge between
    its corner pair in :data:`C3D10_EDGES`: found by the end vertices the element mesh lists for that edge, not by
    a fixed permutation, so it is exact on a curved element too.
    """
    import numpy as np

    dofs = np.asarray(space.scalar.element_dofs.T)                 # (E, 10): corners, then edges
    if dofs.shape[1] != 10:
        raise ValueError("CalculiX's C3D10 needs quadratic (10-node) tetrahedra")
    corners = dofs[:, :4].copy()                                   # vertex ids: skfem numbers vertices first
    x = space.dof_locations
    a, b, c, d = (x[corners[:, k]] for k in range(4))
    turned = np.einsum("ij,ij->i", np.cross(b - a, c - a), d - a) < 0.0
    corners[turned] = corners[turned][:, [0, 2, 1, 3]]
    edge_dofs = dofs[:, 4:]
    ends = np.sort(space.mesh.edges[:, edge_dofs - space.vertices], axis=0)  # (2, E, 6) each edge's end vertices
    nodes = np.empty_like(dofs)
    nodes[:, :4] = corners
    for slot, (i, j) in enumerate(C3D10_EDGES, start=4):
        pair = np.sort(corners[:, [i, j]], axis=1)
        match = (ends[0] == pair[:, :1]) & (ends[1] == pair[:, 1:])
        if not (match.sum(axis=1) == 1).all():
            raise RuntimeError("an element's mid-edge node is not on the edge between two of its corners")
        nodes[:, slot] = edge_dofs[match]
    return nodes, turned


def dof_nodes(space: "FemSpace") -> "np.ndarray":
    """(N,) the scalar DOF (CalculiX's node, less one) each vector DOF sits on; ``space.component`` is its direction."""
    import numpy as np

    node = np.empty(space.dofs, dtype=np.int64)
    for c in range(3):
        node[space.basis.nodal_dofs[c]] = space.scalar.nodal_dofs[0]
        node[space.basis.edge_dofs[c]] = space.scalar.edge_dofs[0]
    return node


def straight_elements(space: "FemSpace") -> "np.ndarray":
    """(E,) whether each element's mid-edge nodes sit at their edges' midpoints: an affine element, whose stress
    is linear in it. The mesher bows a mid-edge node onto a curved face; inside the part every edge is straight."""
    import numpy as np

    x = space.dof_locations
    dofs = np.asarray(space.scalar.element_dofs.T)
    ends = space.mesh.edges[:, dofs[:, 4:] - space.vertices]       # (2, E, 6)
    chord = x[ends[1]] - x[ends[0]]
    sag = np.linalg.norm(x[dofs[:, 4:]] - 0.5 * (x[ends[0]] + x[ends[1]]), axis=-1)
    return (sag <= STRAIGHT * np.linalg.norm(chord, axis=-1)).all(axis=1)


def _part_of(space: "FemSpace", parts: list) -> "np.ndarray":
    """(E,) the part (an index into ``parts``) of each element: all 0 for one part, or one material for all."""
    import numpy as np

    if space.domain is None or len(parts) == 1:
        return np.zeros(len(space.tets), dtype=np.int64)
    return np.asarray(space.domain, dtype=np.int64)


def _lines(values, per_line: int = 10):
    """Comma-separated rows of at most ``per_line`` numbers: CalculiX reads 132 characters a line."""
    values = [str(v) for v in values]
    for start in range(0, len(values), per_line):
        yield ", ".join(values[start:start + per_line])


def write_inp(path: Path, space: "FemSpace", materials: "Material | Sequence[Material]", f: "np.ndarray",
              fixed: "np.ndarray") -> None:
    """The CalculiX input of one linear static solve.

    Nodes are the scalar DOF (node ``n + 1`` is DOF ``n``) and elements keep cadgen's order (element ``e + 1``);
    each part is an element set with its own material. ``fixed`` vector DOF are held at zero; ``f``'s nonzero
    entries on the free DOF are nodal forces (one on a held DOF moves nothing, and the reaction takes it back out).
    The step prints U for every node, RF on the held nodes and S at every element's integration points.
    """
    import numpy as np

    from cadgen._internal.fea.operators import material_list

    parts = material_list(materials)
    nodes, _ = c3d10_connectivity(space)
    domain = _part_of(space, parts)
    node_of = dof_nodes(space)
    held = np.zeros(space.dofs, dtype=bool)
    held[fixed] = True
    real = _REAL.format
    out = ["** cadgen: linear static", "*NODE, NSET=NALL"]
    out += [f"{n + 1}, {real(x)}, {real(y)}, {real(z)}" for n, (x, y, z) in enumerate(space.dof_locations.tolist())]
    sets = []
    for index, material in enumerate(parts):
        rows = np.flatnonzero(domain == index)
        if not len(rows):
            continue
        sets.append(f"PART{index}")
        out.append(f"*ELEMENT, TYPE=C3D10, ELSET=PART{index}")
        out += [f"{e + 1}, " + ", ".join(str(n + 1) for n in nodes[e]) for e in rows.tolist()]
        out += [f"*MATERIAL, NAME=MATERIAL{index}", "*ELASTIC", f"{real(material.E)}, {real(material.nu)}",
                f"*SOLID SECTION, ELSET=PART{index}, MATERIAL=MATERIAL{index}"]
    out += ["*NSET, NSET=NHELD", *_lines(np.unique(node_of[fixed]) + 1)]
    out += ["*BOUNDARY"]
    out += [f"{node_of[d] + 1}, {space.component[d] + 1}, {space.component[d] + 1}" for d in np.asarray(fixed).tolist()]
    out += ["*STEP", "*STATIC"]
    loaded = np.flatnonzero((np.asarray(f) != 0.0) & ~held)
    if len(loaded):
        out.append("*CLOAD")
        out += [f"{node_of[d] + 1}, {space.component[d] + 1}, {real(f[d])}" for d in loaded.tolist()]
    out += ["*NODE PRINT, NSET=NALL", "U", "*NODE PRINT, NSET=NHELD, TOTALS=NO", "RF"]
    for name in sets:
        out += [f"*EL PRINT, ELSET={name}", "S"]
    out += ["*END STEP", ""]
    Path(path).write_text("\n".join(out), encoding="ascii")


# -- the output ---------------------------------------------------------------------------------------

_BLOCKS = {"displacements": "U", "forces": "RF", "stresses": "S"}
# Fortran's E13.6 drops the E of a three-digit exponent: 1.234567-100.
_BARE_EXPONENT = re.compile(r"(?<=\d)([+-]\d{3})\b")


def read_dat(text: str) -> "dict[str, np.ndarray]":
    """The printed blocks of a CalculiX .dat as arrays: ``U`` and ``RF`` rows ``(node, x, y, z)``, ``S`` rows
    ``(element, point, xx, yy, zz, xy, xz, yz)``. A block's rows follow its title line and one blank line; blocks
    of one kind (each part's stresses) are joined."""
    import io

    import numpy as np

    blocks: dict[str, list[str]] = {}
    current: list[str] | None = None
    titled = False                                                  # the last line was a block's title
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            current = current if titled else None
            titled = False
            continue
        title = stripped.split(" ", 1)[0]
        titled = title in _BLOCKS and " for set " in stripped
        if titled:
            current = blocks.setdefault(_BLOCKS[title], [])
        elif current is not None:
            current.append(stripped)
    out = {}
    for name, rows in blocks.items():
        body = "\n".join(rows)
        try:
            out[name] = np.loadtxt(io.StringIO(body), ndmin=2)
        except ValueError:
            out[name] = np.loadtxt(io.StringIO(_BARE_EXPONENT.sub(r"E\1", body)), ndmin=2)
    return out


def _points(printed: "np.ndarray", turned: "np.ndarray") -> "np.ndarray":
    """(E, 4, 6) the printed stress of every element at its four integration points, each point nearest skfem's
    corner of the same number (a turned element's points 2 and 3 are skfem's 3 and 2, as its corners are)."""
    import numpy as np

    count = len(turned)
    out = np.full((count, 4, 6), np.nan)
    out[printed[:, 0].astype(np.int64) - 1, printed[:, 1].astype(np.int64) - 1] = printed[:, 2:8]
    if np.isnan(out).any():
        raise RuntimeError("its stresses leave elements out")
    out[turned] = out[turned][:, [0, 2, 1, 3]]
    return out


def _to_quadrature(points: "np.ndarray", X: "np.ndarray") -> "np.ndarray":
    """(6, E, Q) a linear field given at C3D10's four integration points (``points``, (E, 4, 6)) at the reference
    points ``X`` (3, Q): each point's barycentric weights against the four integration points'."""
    import numpy as np

    at = np.full((4, 4), _A)
    np.fill_diagonal(at, _B)                                       # row k: integration point k's barycentric weights
    weights = np.vstack([1.0 - X.sum(axis=0), X])                  # (4, Q): each quadrature point's
    transfer = weights.T @ np.linalg.inv(at)                       # (Q, 4)
    return np.einsum("qk,ekc->ceq", transfer, points)


def _recovered_at_points(space: "FemSpace", materials, u: "np.ndarray", elements: "np.ndarray") -> "np.ndarray":
    """(elements, 4, 6) cadgen's stress from ``u`` at C3D10's integration points, in CalculiX's components
    (xx, yy, zz, xy, xz, yz), point k nearest skfem's corner k."""
    import numpy as np
    from skfem import Basis

    from cadgen._internal.fea.operators import material_list

    points = np.array(C3D10_POINTS).T
    own = Basis(space.mesh, space.basis.elem, elements=elements, quadrature=(points, np.full(4, 1.0 / 24.0)),
                dofs=space.basis.dofs)
    grad = own.interpolate(u).grad                                  # (3, 3, elements, 4)
    strain = 0.5 * (grad + np.transpose(grad, (1, 0, 2, 3)))
    parts = material_list(materials)
    domain = _part_of(space, parts)[elements]
    E = np.array([parts[p].E for p in domain])[:, None]
    nu = np.array([parts[p].nu for p in domain])[:, None]
    lam, mu = E * nu / ((1.0 + nu) * (1.0 - 2.0 * nu)), E / (2.0 * (1.0 + nu))
    trace = strain[0, 0] + strain[1, 1] + strain[2, 2]
    s = 2.0 * mu * strain
    for i in range(3):
        s[i, i] = s[i, i] + lam * trace
    return np.stack([s[0, 0], s[1, 1], s[2, 2], s[0, 1], s[0, 2], s[1, 2]], axis=-1)


def cross_check(space: "FemSpace", materials, u: "np.ndarray", points: "np.ndarray", sample: "np.ndarray") -> float:
    """How far cadgen's recovery from ``u`` is from CalculiX's stress (``points``, :func:`_points`'s) on the
    elements ``sample``: the median element's largest difference over its largest component (0 when unstressed)."""
    import numpy as np

    ours = _recovered_at_points(space, materials, u, sample)
    theirs = points[sample]
    scale = np.abs(theirs).max(axis=(1, 2))
    stressed = scale > 0.0
    if not stressed.any():
        return 0.0
    return float(np.median(np.abs(ours - theirs).max(axis=(1, 2))[stressed] / scale[stressed]))


@dataclass
class Solved:
    """One CalculiX solve, read back."""

    #: (N,) the displacement on every vector DOF.
    u: "np.ndarray"
    #: (N,) the reaction K u - f on the held DOF, zero elsewhere.
    residual: "np.ndarray"
    #: The result's ``solver``.
    how: str
    #: (E, 4, 6) CalculiX's stress at each element's integration points (:func:`_points`).
    points: "np.ndarray"
    #: (E,) the straight-edged elements, where those four values are the element's whole (linear) stress.
    straight: "np.ndarray"

    def stress(self, space: "FemSpace", recovered: "np.ndarray") -> "np.ndarray":
        """The stress to report at ``space``'s quadrature points, (3, 3, E, Q): CalculiX's own on its straight
        elements, ``recovered`` (cadgen's, from ``u``) on the curved ones. ``recovered`` is written into."""
        import numpy as np

        rows = np.flatnonzero(self.straight)
        own = _to_quadrature(self.points[rows], np.asarray(space.basis.X))
        for c, (i, j) in enumerate(((0, 0), (1, 1), (2, 2), (0, 1), (0, 2), (1, 2))):
            recovered[i, j, rows] = own[c]
            recovered[j, i, rows] = own[c]
        return recovered


# -- the solve ----------------------------------------------------------------------------------------

def solve_static(ccx: Ccx, space: "FemSpace", materials, f: "np.ndarray", fixed: "np.ndarray",
                 warnings: list[str]) -> "Solved | None":
    """K u = f with the ``fixed`` DOF held, solved by CalculiX; ``None`` when that failed.

    A failed run (CalculiX's error, missing output, a cross-check that does not match) adds one warning naming why
    and returns ``None``: the caller solves it itself.
    """
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="cadgen-ccx-") as scratch:
        directory = Path(scratch)
        try:
            write_inp(directory / f"{_JOB}.inp", space, materials, f, fixed)
            run = subprocess.run([ccx.path, "-i", _JOB], cwd=directory, env=environment(threads()),
                                 capture_output=True, text=True, errors="replace")
            dat = directory / f"{_JOB}.dat"
            error = next((line.strip() for line in run.stdout.splitlines() if "*ERROR" in line), None)
            if run.returncode != 0 or error or not dat.is_file():
                raise RuntimeError(error or f"ccx exited with {run.returncode}")
            printed = read_dat(dat.read_text(encoding="ascii", errors="replace"))
            node_of = dof_nodes(space)
            nodal = np.full((space.scalar_count, 3), np.nan)
            nodal[printed["U"][:, 0].astype(np.int64) - 1] = printed["U"][:, 1:4]
            u = nodal[node_of, space.component]
            if not np.isfinite(u).all():
                raise RuntimeError("its displacements leave nodes out or are not numbers")
            forces = np.zeros((space.scalar_count, 3))
            forces[printed["RF"][:, 0].astype(np.int64) - 1] = printed["RF"][:, 1:4]
            residual = np.zeros(space.dofs)
            residual[fixed] = forces[node_of[fixed], space.component[fixed]] - f[fixed]
            _, turned = c3d10_connectivity(space)
            points = _points(printed["S"], turned)
            count = len(turned)
            sample = np.unique(np.linspace(0, count - 1, min(count, SAMPLE_ELEMENTS)).round().astype(np.int64))
            mismatch = cross_check(space, materials, u, points, sample)
            if not mismatch <= CROSS_CHECK:
                raise RuntimeError(f"its stresses differ from cadgen's recovery from its displacements by "
                                   f"{mismatch:.1%}")
        except (OSError, RuntimeError, ValueError, KeyError, IndexError) as exc:
            warnings.append(f"CalculiX failed ({exc}): solved with the built-in solver")
            return None
    return Solved(u=u, residual=residual, how=ccx.label, points=points, straight=straight_elements(space))
