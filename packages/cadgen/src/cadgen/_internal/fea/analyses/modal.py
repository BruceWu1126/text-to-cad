"""Vibration: the part's natural frequencies and the shape it rings in at each.

The generalized eigenproblem ``K φ = ω² M φ`` on the study's mesh, through
:mod:`cadgen._internal.fea.eigen`: shift-invert Lanczos while a factorisation
fits, LOBPCG with a multigrid preconditioner when the ladder says it does not.
Fixtures are optional. With none (or a part that nothing holds) the part is
free in space: its six rigid-body motions per free body come out at zero
frequency, are dropped, and are counted (``rigid_body_modes``).

What it writes: one series frame per mode (``Mode 2 · 118 Hz``), each mode's
shape scaled so its largest motion is 1 mm (mode 1's also as the displacement
a viewer deforms by), each mode's effective mass fraction along X, Y and Z, the
total mass, and the ``frequency`` checks: a mode that must stay above
``min_Hz``, or a band (``avoid_Hz``) no mode may sit in. Stdlib only at import;
numeric imports live inside the methods.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import (
    AnalysisBase, AnalysisResult, FieldSpec, Inputs, Series, SeriesFrame, SolveContext, assembly_parts, by_severity,
    finding, max_frames, node_domain, xyz, yield_at,
)
from cadgen._internal.fea.study import Fixture, parse_fixtures

if TYPE_CHECKING:
    import numpy as np

__all__ = ["MAX_MODES", "ModalAnalysis", "ModalInputs"]
# ``finding`` and ``max_frames`` are base's, still imported from here by the analyses that share these words.

#: The most modes a study may ask for (spec 5.3).
MAX_MODES = 30
#: How far a band check may widen the search to be sure no mode hides in its band.
_SEARCH_CAP = 60
#: A mode under this frequency (or under 1 % of the first elastic one) is a rigid-body motion.
RIGID_HZ = 0.5
RIGID_FRACTION = 0.01
#: A frequency check is close within 10 % of its limit.
_CLOSE_AT = 0.9
#: Without a ladder plan, a model past this many free DOF is solved without a factorisation.
EIGEN_DIRECT_BELOW = 250_000
_TWO_PI = 2.0 * math.pi


# -- words and numbers both analyses share -----------------------------------------------------------

def hz_text(f: float) -> str:
    """A frequency as a label says it: "85 Hz", "3.4 Hz", "0.42 Hz"."""
    if f >= 10:
        return f"{f:.0f} Hz"
    return f"{f:.1f} Hz" if f >= 1 else f"{f:.2f} Hz"


def factor_text(factor: float) -> str:
    """A load factor as a label says it: "13.3×", "2.41×", "118×"."""
    if factor >= 100:
        return f"{factor:.0f}×"
    return f"{factor:.1f}×" if factor >= 10 else f"{factor:.2f}×"


def parse_modes(document: dict, default: int, where: str = "modes") -> int:
    modes = document.get("modes", default)
    if isinstance(modes, bool) or not isinstance(modes, int) or not 1 <= modes <= MAX_MODES:
        raise ValueError(f"{where}: how many modes to find, a whole number from 1 to {MAX_MODES}, got {kinds.json_text(modes)}")
    return modes


def raw_checks(document: dict, *specs) -> tuple[dict, ...]:
    """The study's checks of ``specs``' kinds, parsed; a malformed one is left for the study's own parse to name."""
    view = document.get("view")
    entries = view.get("checks") if isinstance(view, dict) else None
    by_kind = {spec.kind: spec for spec in specs}
    found = []
    for index, entry in enumerate(entries if isinstance(entries, list) else ()):
        if isinstance(entry, dict) and entry.get("kind") in by_kind:
            try:
                found.append(by_kind[entry["kind"]].parse(entry, f"view.checks[{index}]"))
            except ValueError:
                continue
    return tuple(found)


def held_supports(space, fixtures: tuple[Fixture, ...], ordinal_of: dict[str, int], *, require: bool = False):
    """The fixtures as :class:`~cadgen._internal.fea.supports.Supports` (fixed faces and rollers); ``require``
    refuses, in a sentence, rollers that leave the part free to move."""
    from cadgen._internal.fea.supports import check_held, supports_of

    supports = supports_of(space, fixtures, ordinal_of)
    if require:
        check_held(space, supports)
    return supports


def floating_bodies(space, fixtures: tuple[Fixture, ...], ordinal_of: dict[str, int]) -> "np.ndarray":
    """The separate bodies of the mesh no fixture holds, as a (vector DOF,) label: -1 on a held body,
    else the free body's number (0, 1, ...). Each free body has six rigid-body modes."""
    import numpy as np
    import scipy.sparse as sparse
    from scipy.sparse.csgraph import connected_components

    corners = space.mesh.t[:4]
    n = space.vertices
    rows = np.concatenate([corners[a] for a in (0, 0, 0, 1, 1, 2)])
    cols = np.concatenate([corners[b] for b in (1, 2, 3, 2, 3, 3)])
    count, label = connected_components(sparse.coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n)), directed=False)
    held: set[int] = set()
    for fixture in fixtures:
        facets = space.facets_of(fixture.faces, ordinal_of)
        held |= set(np.unique(label[space.mesh.facets[:, facets]]).tolist())
    number = np.full(count, -1)
    floating = [body for body in range(count) if body not in held]
    number[floating] = np.arange(len(floating))
    # Every scalar DOF takes its element's body (an element's corners are all of one body).
    scalar = np.full(space.scalar_count, -1)
    element_dofs = space.scalar.element_dofs
    scalar[element_dofs] = number[label[corners[0]]][None, :]
    body = np.full(space.dofs, -1)
    for c in range(3):
        body[space.basis.nodal_dofs[c]] = scalar[space.scalar.nodal_dofs[0]]
        if space.order == 2:
            body[space.basis.edge_dofs[c]] = scalar[space.scalar.edge_dofs[0]]
    return body


def rigid_modes(space, body: "np.ndarray") -> "np.ndarray":
    """(vector DOF, 6 per free body): each free body's three translations and three rotations."""
    import numpy as np

    from cadgen._internal.fea.operators import rigid_body_modes

    bodies = int(body.max()) + 1 if len(body) else 0
    every = rigid_body_modes(space.locations, space.component)
    out = np.zeros((space.dofs, 6 * bodies))
    for b in range(bodies):
        rows = body == b
        out[rows, 6 * b:6 * b + 6] = every[rows]
    return out


def align_degenerate(vectors: "np.ndarray", M, frequencies: "np.ndarray", component: "np.ndarray", *,
                     within: float = 2e-3) -> "np.ndarray":
    """Mass-normalised modes with each cluster of (nearly) equal frequencies turned to line up with X, Y and Z.

    A square beam bends at one frequency in Y and in Z, and any mix of the two
    is as much a mode: the solver hands over an arbitrary mix. Within each
    cluster the modes are recombined (an orthogonal change of basis, which keeps
    them mass-orthonormal) so the first moves the most along the axis the
    cluster moves most along, the next along the next axis, and so on.
    """
    import numpy as np

    out = vectors.copy()
    directions = np.zeros((vectors.shape[0], 3))
    start = 0
    while start < len(frequencies):
        end = start + 1
        while end < len(frequencies) and frequencies[end] - frequencies[start] <= within * max(frequencies[start], 1e-30):
            end += 1
        if end - start > 1:
            if not directions.any():
                directions = np.stack([M @ (component == c).astype(float) for c in range(3)], axis=1)
            cluster = out[:, start:end]
            gamma = cluster.T @ directions                        # (m, 3): each mode's share along X, Y, Z
            columns: list = []
            for axis in np.argsort(-np.linalg.norm(gamma, axis=0)):
                u = gamma[:, axis].copy()
                for q in columns:
                    u -= (q @ u) * q
                if np.linalg.norm(u) > 1e-8 * max(np.linalg.norm(gamma), 1e-300) and len(columns) < end - start:
                    columns.append(u / np.linalg.norm(u))
            if columns:
                basis = np.column_stack(columns)
                rest = np.linalg.qr(np.column_stack([basis, np.eye(end - start)]))[0][:, len(columns):end - start]
                out[:, start:end] = cluster @ np.column_stack([basis, rest])
        start = end
    return out


def materials_of(ctx: SolveContext):
    """What the operators take: one material, or one per domain for an assembly."""
    return ctx.materials if ctx.space.domain is not None else ctx.materials[0]


def solver_method(ctx: SolveContext, free: int) -> str:
    """LOBPCG when the ladder chose the iterative rung, or with no plan for a model too big to factorise."""
    plan = getattr(ctx, "plan", None)
    if plan is not None and getattr(plan, "solver", "direct") in ("iterative", "matrix_free"):
        return "lobpcg"
    return "lobpcg" if plan is None and free > EIGEN_DIRECT_BELOW else "arpack"


def mode_shapes(space, vectors: "np.ndarray") -> tuple[list["np.ndarray"], list[int]]:
    """Each mode (a full vector DOF column) per node, scaled so its largest motion is 1 mm, and the node
    where that is. The sign is fixed (the largest component positive) so a run repeats exactly."""
    import numpy as np

    shapes, peaks = [], []
    for column in vectors.T:
        shape = space.nodal(column)
        magnitude = np.linalg.norm(shape, axis=1)
        peak = int(magnitude.argmax())
        size = float(magnitude[peak])
        if size > 0:
            flat = shape.ravel()
            sign = 1.0 if flat[int(np.abs(flat).argmax())] >= 0 else -1.0
            shape = shape * (sign / size)
        shapes.append(shape)
        peaks.append(peak)
    return shapes, peaks


def mode_series(values: list[float], text: Callable[[float], str], unit: str, frames: int) -> Series:
    """One frame per mode, 1-based mode numbers as values: mode 1's shape is the displacement, the rest their own."""
    return Series(
        kind="mode",
        unit=unit,
        frames=[
            SeriesFrame(value=float(i + 1), label=f"Mode {i + 1} · {text(v)}",
                        attributes={"mode_shape": "_DISPLACEMENT" if i == 0 else f"_MODE_SHAPE_F{i}"})
            for i, v in enumerate(values[:frames])
        ],
        default=0,
    )


def field_peak(ctx: SolveContext, result: AnalysisResult, values: "np.ndarray", faces, index: int):
    """The largest of a nodal field over ``faces`` (all of it when none), for check ``index``: a kinds.FieldMax."""
    return kinds.field_max_over(
        values, tuple(faces), boundary=result.boundary_quadratic, boundary_ordinal=ctx.volume.boundary_ordinal,
        locations=result.dof_locations, face_ref=ctx.volume.faces, ordinal_of=ctx.ordinal_of, where=f"view.checks[{index}]",
    )


def where_moves(ctx: SolveContext, result: AnalysisResult, magnitude: "np.ndarray", index: int) -> dict:
    """``where`` of a check about a mode: the face and point that move most in it."""
    peak = field_peak(ctx, result, magnitude, (), index)
    return {"ref": peak.ref, "at": [round(c, 3) for c in peak.at]}


def scaled_deformation(largest: float, bbox_diagonal: float, requested: float | None) -> float:
    """The deformation scale: the one asked for, else the automatic one for a largest motion of ``largest`` mm."""
    from cadgen._internal.fea.outputs import auto_deformation_scale

    return requested or auto_deformation_scale(largest, bbox_diagonal)


def fixtures_echo(fixtures, bare) -> list[dict]:
    return [{"type": fixture.type, "faces": bare(fixture.faces)} for fixture in fixtures]


def eigen_estimate(ctx: SolveContext, modes: int, *, static_solves: int = 0):
    """What finding ``modes`` modes on the plan's mesh costs (a :class:`fit.Estimate`).

    One linear solve on the plan's mesh as :func:`fit.solid_estimate` counts
    it (the space, the stiffness, and a factorisation below
    ``fit.DIRECT_BELOW`` DOF or a multigrid hierarchy above it or on an
    iterative plan), ``static_solves`` more such solves' time (buckling's
    prestress), the mass matrix, and the eigen solver's own: a direct plan
    factorises the stiffness whatever its size (fill ~ n^1.5, time ~ n^2) and
    keeps a Lanczos basis of 2k + 20 vectors, solving with the factor about
    three times per vector; an iterative one keeps LOBPCG's three blocks of
    k + 4 vectors and applies the preconditioner about as often as half a CG
    solve per vector.
    """
    from cadgen._internal.fea import fit

    plan = ctx.plan
    base = fit.solid_estimate(ctx, passes=1.0 + static_solves)
    n = max(int(base.dofs), 1)
    matrix = 12.0 * fit.NNZ_PER_ROW.get(plan.order, fit.NNZ_PER_ROW[2]) * n
    if getattr(plan, "solver", "direct") == "direct":
        vectors = 2 * modes + 20
        factor = 16.0 * fit.FILL * n ** 1.5
        counted = n < fit.DIRECT_BELOW  # the base solve factorised already
        memory = base.memory_bytes + (0.0 if counted else factor) + matrix + 8.0 * vectors * n
        seconds = (base.seconds + (0.0 if counted else fit.DIRECT_SECONDS * n ** 2)
                   + 3 * vectors * 4.0 * fit.FILL * n ** 1.5 * 1e-9)
    else:
        block = modes + 4
        memory = base.memory_bytes + matrix + 8.0 * 3 * block * n
        seconds = base.seconds + 0.5 * fit.AMG_SECONDS_PER_DOF * n * block
    return fit.Estimate(dofs=n, memory_bytes=int(memory), seconds=float(seconds))


#: The iterative rung's words for finding modes.
ITERATIVE_WORDS = "Found the modes with an iterative solver (LOBPCG with multigrid) instead of factorising the stiffness"


def apply_eigen_rung(analysis, rung, ctx: SolveContext, inputs, *, words: str = ITERATIVE_WORDS):
    """The rungs an eigen analysis takes its own way (``iterative``: LOBPCG; ``idealise`` and ``symmetry``
    are not taken for eigenproblems yet), the rest the shared way (:func:`fit.apply_generic`)."""
    from cadgen._internal.fea import fit

    plan, budget = ctx.plan, ctx.budget
    if rung == "iterative":
        if plan.solver != "direct":
            return None
        before = analysis.estimate(ctx, inputs)
        plan.solver = "iterative"
        after = analysis.estimate(ctx, inputs)
        if before.memory_bytes > budget.memory_bytes:
            # No factorisation: on a large model the factor is most of the memory, and it grows as n^1.5.
            if after.memory_bytes < before.memory_bytes:
                return fit.Step("iterative", f"{words}, to fit in memory", None, None,
                                detail={"solver": "lobpcg", "from_bytes": before.memory_bytes, "to_bytes": after.memory_bytes})
        elif after.seconds < 0.95 * before.seconds:
            return fit.Step("iterative", f"{words}, to finish sooner", None, None,
                            detail={"solver": "lobpcg", "from_seconds": round(before.seconds, 1), "to_seconds": round(after.seconds, 1)})
        plan.solver = "direct"
        return None
    if rung in ("idealise", "symmetry"):
        # A shell or beam model has no mass and geometric stiffness here yet, and a symmetric half needs the
        # antisymmetric modes solved too: neither is taken for an eigenproblem until both are.
        return None
    return fit.apply_generic(rung, analysis, ctx, inputs)


def governing_field(space, materials, mode: "np.ndarray") -> "np.ndarray":
    """Where a mode strains the part most: von Mises of its (max-normalised) shape, per scalar DOF.

    A frequency or a load factor is a ratio of strain energy to mass or to
    prestress work: its accuracy is set where the mode bends the part, so the
    ladder's second pass refines there.
    """
    import numpy as np

    from cadgen._internal.fea import operators

    return np.maximum(space.scalar.project(operators.von_mises(operators.stress(space, materials, mode))), 0.0)


# -- modal -------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ModalInputs(Inputs):
    fixtures: tuple[Fixture, ...] = ()
    #: How many modes the study asked for (``modes``, default 6).
    modes: int = 6
    #: The window searched, [low, high] Hz, or None for the lowest modes.
    range_Hz: tuple[float, float] | None = None
    #: The study's frequency checks, parsed (the study's own parse names a bad one).
    checks: tuple[dict, ...] = ()

    @property
    def check_modes(self) -> int:
        """The highest mode number a check names: it is found whatever ``modes`` says."""
        return max((check.get("mode", 1) for check in self.checks if "min_Hz" in check), default=0)

    @property
    def band_top_Hz(self) -> float | None:
        """The top of the highest band a check keeps clear: the search reaches past it."""
        return max((check["avoid_Hz"][1] for check in self.checks if "avoid_Hz" in check), default=None)


def _frequency_check(check: dict, frequencies: list[float]) -> dict:
    """A frequency check judged on the elastic modes, without its ``where``."""
    label = check.get("label") or kinds.FREQUENCY.default_label
    if "min_Hz" in check:
        mode = int(check.get("mode", 1))
        f = frequencies[mode - 1]
        ratio = check["min_Hz"] / f if f > 0 else math.inf
        status = "fails" if ratio > 1 else "close" if ratio > _CLOSE_AT else "passes"
        return {"kind": "frequency", "label": label, "value": round(f, 4), "limit": check["min_Hz"], "unit": "Hz",
                "ratio": round(min(ratio, 1e6), 6), "close_at": _CLOSE_AT, "status": status, "mode": mode,
                "at": {"frame": mode - 1, "value": round(f, 4), "unit": "Hz"}}
    low, high = check["avoid_Hz"]
    inside = [i for i, f in enumerate(frequencies) if low <= f <= high]
    if inside:
        index = inside[0]
    else:
        index = min(range(len(frequencies)), key=lambda i: min(abs(frequencies[i] - low), abs(frequencies[i] - high)))
    f = frequencies[index]
    edge = low if abs(f - low) <= abs(f - high) else high
    gap = abs(f - edge)
    if inside:
        ratio, status = max(1.0 + gap / edge, 1.000001), "fails"
    else:
        ratio = max(0.0, 1.0 - gap / edge)
        status = "close" if ratio > _CLOSE_AT else "passes"
    return {"kind": "frequency", "label": label, "value": round(f, 4), "limit": edge, "unit": "Hz",
            "ratio": round(ratio, 6), "close_at": _CLOSE_AT, "status": status, "mode": index + 1, "avoid_Hz": [low, high],
            "at": {"frame": index, "value": round(f, 4), "unit": "Hz"}}


class ModalAnalysis(AnalysisBase):
    name: ClassVar[str] = "modal"
    tier: ClassVar[int] = 1
    word: ClassVar[str] = "Vibration"
    extras_word: ClassVar[str] = "vibration"
    study_keys: ClassVar[frozenset[str]] = frozenset({"fixtures", "modes", "range_Hz"})
    material_needs: ClassVar[frozenset[str]] = frozenset({"density"})
    # One field, the mode shape, a vector per frame: the viewer reads its magnitude for colour and its
    # vector to deform by. With no scalar field, run.py colours the GLB by the shape's magnitude.
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("mode_shape", "_DISPLACEMENT", "mode shape", "mm", 3, 1000.0, per_frame=True),
    )
    checks: ClassVar[tuple] = (kinds.FREQUENCY,)
    default_checks: ClassVar[tuple[dict, ...]] = ()
    drives: ClassVar[tuple[str, ...]] = ("field", "deformation", "threshold", "mode")
    default_controls: ClassVar[dict[str, dict]] = {
        "mode": {"drives": "mode", "type": "enum", "options": None},
        "deformation": {"drives": "deformation", "type": "number", "min": 0.0, "max": None},
    }
    ladder: ClassVar[tuple[str, ...]] = (
        "iterative", "local_refine", "defeature", "linear_elements", "idealise", "symmetry", "reduce_modes",
    )
    noun: ClassVar[str] = "this shake"
    #: What the ladder's two passes compare ("the first frequency moved 0.4% between ...").
    governing_word: ClassVar[str] = "the first frequency"

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> ModalInputs:
        fixtures = parse_fixtures(document, required=False)
        modes = parse_modes(document, 6)
        window = None
        if document.get("range_Hz") is not None:
            raw = document["range_Hz"]
            if not isinstance(raw, list) or len(raw) != 2:
                raise ValueError("range_Hz: the window to search as [low, high] in Hz, like [0, 2000]")
            low, high = (kinds.number(value, where="range_Hz") for value in raw)
            if low < 0 or not low < high:
                raise ValueError(f"range_Hz: low ({low:g}) must be 0 or more and below high ({high:g})")
            window = (low, high)
        checks = raw_checks(document, kinds.FREQUENCY)
        too_high = [check["mode"] for check in checks if check.get("mode", 1) > MAX_MODES]
        if too_high:
            raise ValueError(f"view.checks: a frequency check names mode {too_high[0]}; at most mode {MAX_MODES} is found")
        refs = tuple(dict.fromkeys(ref for fixture in fixtures for ref in fixture.faces))
        return ModalInputs(refs, refs, False, fixtures=fixtures, modes=modes, range_Hz=window, checks=checks)

    # -- the ladder (fit.py drives these) -------------------------------------------------------------

    def _wanted(self, ctx: SolveContext | None, inputs: ModalInputs) -> int:
        planned = getattr(getattr(ctx, "plan", None), "modes", None)
        return max(planned or inputs.modes, inputs.check_modes, 1)

    def estimate(self, ctx: SolveContext, inputs: ModalInputs):
        """Finding the modes on the plan's mesh (:func:`eigen_estimate`), the rigid-body ones included."""
        return eigen_estimate(ctx, self._wanted(ctx, inputs) + 6)

    def apply(self, rung, ctx: SolveContext, inputs: ModalInputs):
        """``iterative``: LOBPCG with multigrid, no factorisation. ``reduce_modes``: half the modes, never
        fewer than a check names. The mesh rungs the shared way."""
        from cadgen._internal.fea import fit

        if rung == "reduce_modes":
            current = self._wanted(ctx, inputs)
            floor = max(inputs.check_modes, 1)
            if current <= floor:
                return None
            fewer = max(floor, current // 2)
            ctx.plan.modes = fewer
            return fit.Step("reduce_modes", f"Found the first {fewer} of the {inputs.modes} modes asked for, to fit"
                            + ("; every mode a check names is kept" if inputs.check_modes else ""), None, None,
                            detail={"from_modes": inputs.modes, "to_modes": fewer})
        return apply_eigen_rung(self, rung, ctx, inputs)

    def governing(self, result: AnalysisResult) -> tuple["np.ndarray", float]:
        """What local_refine's two passes follow and compare: where mode 1 strains the part, and its frequency."""
        return result.scalars["governing_field"], result.scalars["frequencies_Hz"][0]

    # -- solve ---------------------------------------------------------------------------------------

    def solve(self, ctx: SolveContext, inputs: ModalInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea import operators

        space = ctx.space
        timings: dict[str, float] = {}
        warnings: list[str] = []
        started = time.perf_counter()
        materials = materials_of(ctx)
        K = operators.stiffness(space, materials)
        M = operators.mass(space, materials)
        held = held_supports(space, inputs.fixtures, ctx.ordinal_of)
        fixed = held.fixed
        free = np.setdiff1d(np.arange(space.dofs), fixed)
        Kl, Ml = held.local(K), held.local(M)   # a sloped or curved roller: in its nodes' own axes
        Kff, Mff = Kl[free][:, free].tocsr(), Ml[free][:, free].tocsr()
        body = floating_bodies(space, inputs.fixtures, ctx.ordinal_of)
        floating = int(body.max()) + 1 if len(body) else 0
        # Rollers hold a body only along their normals: the motions they leave free are rigid-body modes too.
        loose = 0
        if held.rollers and floating == 0:
            from cadgen._internal.fea.supports import unheld_motions

            loose = unheld_motions(space, held)
        timings["assemble_s"] = time.perf_counter() - started
        if ctx.log:
            ctx.log(f"modal: {space.dofs} DOF, {len(free)} free, {floating} bod{'y' if floating == 1 else 'ies'} held by nothing")

        started = time.perf_counter()
        method = solver_method(ctx, len(free))
        wanted = self._wanted(ctx, inputs)
        rigid_expected = 6 * floating + loose
        constraints = rigid_modes(space, body)[free] if method == "lobpcg" and floating and not held.rollers else None
        omega2, vectors, how = self._search(Kff, Mff, space, free, wanted, rigid_expected, inputs, method, constraints,
                                            warnings)
        timings["eigen_s"] = time.perf_counter() - started

        frequencies = np.sqrt(np.maximum(omega2, 0.0)) / _TWO_PI
        searched_from_zero = inputs.range_Hz is None or inputs.range_Hz[0] == 0
        if constraints is not None:
            # LOBPCG searched beside the rigid-body modes, which are known exactly: none is in the list.
            rigid, dropped = 0, rigid_expected
        elif searched_from_zero:
            rigid = dropped = self._rigid_count(frequencies, rigid_expected)
        else:
            # A window above 0 Hz never meets the rigid-body modes; the part has them all the same.
            rigid, dropped = 0, rigid_expected
        elastic = np.arange(rigid, len(frequencies))
        if inputs.range_Hz is not None:
            low, high = inputs.range_Hz
            within = elastic[(frequencies[elastic] >= low) & (frequencies[elastic] <= high)]
            if len(within) == 0:
                nearest = elastic[np.argmin(np.minimum(abs(frequencies[elastic] - low), abs(frequencies[elastic] - high)))]
                warnings.append(f"no mode lies between {low:g} and {high:g} Hz; the nearest is {hz_text(frequencies[nearest])}")
                within = np.array([nearest])
            elastic = within
        elastic = elastic[:max(wanted, 1)]
        if len(elastic) < inputs.check_modes:
            raise ValueError(
                f"view.checks: a check names mode {inputs.check_modes}, but only {len(elastic)} modes were found"
                + (f" between {inputs.range_Hz[0]:g} and {inputs.range_Hz[1]:g} Hz; widen range_Hz" if inputs.range_Hz else "")
            )

        full = np.zeros((space.dofs, len(elastic)))
        full[free] = vectors[:, elastic]
        full = held.global_vector(full)
        # Mass-normalised (φᵀMφ = 1), whatever the solver normalised by; equal frequencies lined up with the axes.
        full /= np.sqrt(np.einsum("ij,ij->j", full, M @ full))
        full = align_degenerate(full, M, frequencies[elastic], space.component)
        directions = [(space.component == c).astype(float) for c in range(3)]
        mass_dir = [float(r @ (M @ r)) for r in directions]
        gamma = np.stack([full.T @ (M @ r) for r in directions], axis=1)
        fractions = gamma ** 2 / np.array(mass_dir)

        frames = min(len(elastic), max_frames(ctx))
        if frames < len(elastic):
            warnings.append(f"wrote the shapes of the first {frames} of {len(elastic)} modes; every mode's frequency is in the summary")
        shapes, _ = mode_shapes(space, full[:, :frames])
        series = mode_series([float(f) for f in frequencies[elastic]], hz_text, "Hz", frames)
        governing = governing_field(space, materials, full[:, 0] / max(float(np.abs(full[:, 0]).max()), 1e-300))
        return AnalysisResult(
            dof_locations=space.dof_locations,
            vertices=space.vertices,
            tets=space.tets,
            boundary_quadratic=space.boundary_quadratic,
            element_dofs=space.element_dofs,
            fields={"mode_shape": shapes[0]},
            deformation=shapes[0],
            series=series,
            frame_fields={"mode_shape": shapes},
            scalars={
                "frequencies_Hz": [float(f) for f in frequencies[elastic]],
                "effective_mass_fractions": fractions.tolist(),
                "total_mass_kg": mass_dir[0] * 1000.0,
                "rigid_body_modes": int(dropped),
                "free_bodies": floating,
                "magnitudes": [np.linalg.norm(shape, axis=1) for shape in shapes],
                "governing_field": governing,
                "analysis_warnings": list(warnings),
                "parts": assembly_parts(ctx),
            },
            dofs=int(space.dofs),
            solver=how,
            timings=timings,
            warnings=warnings,
        )

    @staticmethod
    def _rigid_count(frequencies: "np.ndarray", expected: int) -> int:
        """The leading modes that are rigid-body motions: under 0.5 Hz, or under 1 % of the first elastic mode."""
        if len(frequencies) == 0:
            return 0
        first = frequencies[min(expected, len(frequencies) - 1)]
        if expected == 0:
            first = next((f for f in frequencies if f >= RIGID_HZ), frequencies[-1])
        threshold = max(RIGID_HZ, RIGID_FRACTION * float(first))
        return int((frequencies < threshold).sum())

    @staticmethod
    def _shift(Kff, Mff, rigid_expected: int) -> float:
        """s of K + sM: 0 when every body is held, else 1 Hz's ω², raised to keep K + sM's conditioning (its
        stiffness-to-mass scale over s) under 1e12, so its factorisation stays accurate."""
        if rigid_expected == 0:
            return 0.0
        scale = float(Kff.diagonal().sum() / Mff.diagonal().sum())
        return max(_TWO_PI ** 2, 1e-12 * scale)

    def _search(self, Kff, Mff, space, free, wanted: int, rigid_expected: int, inputs: ModalInputs, method: str,
                constraints, warnings: list[str]):
        """(ω² ascending, vectors on the free DOF, how): enough modes for ``wanted`` elastic ones, past every band.

        Direct: shift-invert Lanczos at σ = -s (the lowest modes, rigid ones
        included) or at the window's bottom. Iterative: LOBPCG on K + sM with a
        multigrid preconditioner, kept beside the rigid-body modes (``constraints``).
        """
        import numpy as np

        from cadgen._internal.fea import eigen

        n = Kff.shape[0]
        window = inputs.range_Hz
        band_top = inputs.band_top_Hz
        s = self._shift(Kff, Mff, rigid_expected)
        precond = None
        count = wanted
        while True:
            if method == "lobpcg":
                k = min(count + 2, n - 1 - (0 if constraints is None else constraints.shape[1]))
                A = (Kff + s * Mff).tocsr() if s else Kff
                if precond is None:
                    precond = eigen.amg_preconditioner(A, space.locations[free], space.component[free])
                found = eigen.solve_generalized(A, Mff, k, precond=precond, which="SA", method="lobpcg",
                                                constraints=constraints)
                omega2, vectors, how = found.values - s, found.vectors, found.how
                warnings.extend(w for w in found.warnings if w not in warnings)
                rigid_in_list = 0
            else:
                k = min(count + rigid_expected + 2, n - 1)
                sigma = (_TWO_PI * window[0]) ** 2 if window is not None and window[0] > 0 else -s
                found = eigen.shift_invert(Kff, Mff, k, sigma)
                omega2, vectors, how = found.values, found.vectors, found.how
                rigid_in_list = rigid_expected if sigma <= 0 else 0
            order = np.argsort(omega2)
            omega2, vectors = omega2[order], vectors[:, order]
            f = np.sqrt(np.maximum(omega2, 0.0)) / _TWO_PI
            top = float(f[-1]) if len(f) else 0.0
            reach = [band_top * 1.1] if band_top is not None else []
            if window is not None and ((f >= window[0]) & (f <= window[1])).sum() < wanted:
                reach.append(window[1])
            cap = min(_SEARCH_CAP, n - 1 - rigid_in_list)
            if not reach or top >= max(reach) or count >= cap:
                if reach and top < max(reach):
                    warnings.append(f"searched the lowest {len(f)} modes, up to {hz_text(top)}; none above that was looked for")
                return omega2, vectors, how
            count = min(2 * count, cap)

    # -- checks, findings ----------------------------------------------------------------------------

    def _checks_on(self, result: AnalysisResult, checks) -> list[dict]:
        return [_frequency_check(dict(check), result.scalars["frequencies_Hz"]) for check in checks]

    def needs_finer(self, result: AnalysisResult, inputs: ModalInputs, check_results: list[dict]) -> bool:
        """Solve once more on a finer mesh when a frequency check is close to its limit."""
        return any(check["status"] == "close" for check in check_results or self._checks_on(result, inputs.checks))

    def refined_record(self, result: AnalysisResult, size_mm: float, finer_mm: float, *, assembly: bool) -> dict:
        return {"from_size_mm": round(size_mm, 4), "from_first_frequency_Hz": round(result.scalars["frequencies_Hz"][0], 4),
                "size_mm": round(finer_mm, 4), "first_frequency_Hz": None}

    def merge_finer(self, coarse: AnalysisResult, finer: AnalysisResult, refined: dict) -> AnalysisResult:
        refined["first_frequency_Hz"] = round(finer.scalars["frequencies_Hz"][0], 4)
        finer.scalars["coarser_frequencies_Hz"] = coarse.scalars["frequencies_Hz"]
        return finer

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: ModalInputs) -> dict:
        judged = _frequency_check(dict(check), result.scalars["frequencies_Hz"])
        frame = judged["at"]["frame"]
        magnitudes = result.scalars["magnitudes"]
        if frame >= len(magnitudes):  # a mode past the frames written: no frame to jump to
            judged.pop("at")
            magnitude = magnitudes[0] * 0.0
        else:
            magnitude = magnitudes[frame]
        judged["where"] = where_moves(ctx, result, magnitude, index)
        return judged

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: ModalInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        scalars = result.scalars
        frequencies = scalars["frequencies_Hz"]
        whole = "The assembly" if assembly else "The part"
        found: list[dict] = []
        judged = check_results or [self.judge(check, i, ctx, result, inputs) for i, check in enumerate(ctx.study.checks)]
        for check in judged:
            if check["status"] not in ("fails", "close"):
                continue
            f, label, fails = check["value"], check["label"], check["status"] == "fails"
            if "avoid_Hz" in check:
                low, high = check["avoid_Hz"]
                kind = "resonates_in_band" if fails else "close_to_band"
                sentence = (f"Mode {check['mode']} rings at {hz_text(f)}, {'inside' if fails else 'close to'} the "
                            f"{low:g}–{high:g} Hz band to keep clear of ({label})")
                description = f"mode {check['mode']} at {f:.4g} Hz, {abs(f - check['limit']):.3g} Hz from the band's edge"
            else:
                which = "first mode" if check["mode"] == 1 else f"mode {check['mode']}"
                kind = "frequency_too_low" if fails else "frequency_close"
                sentence = (f"{whole}'s {which} is {hz_text(f)}, {'under' if fails else 'within 10 % of'} the "
                            f"{check['limit']:g} Hz it must stay above ({label})")
                description = f"{which} {f:.4g} Hz against a {check['limit']:g} Hz minimum"
            found.append(finding("error" if fails else "warning", kind, sentence, description,
                                 [{"text": label, **check["where"]}]))
        if scalars["rigid_body_modes"]:
            bodies = scalars["free_bodies"]
            held = "nothing holds it" if not inputs.fixtures else (
                f"{bodies} part{'s' if bodies > 1 else ''} of it {'are' if bodies > 1 else 'is'} held by nothing")
            found.append(finding(
                "info", "rigid_body_modes",
                f"{whole} is free in space ({held}): its {scalars['rigid_body_modes']} rigid-body modes, where it moves "
                "without bending, were left out",
                "a mode under 0.5 Hz, or under 1 % of the first elastic mode, is a rigid-body motion", [],
            ))
        if frequencies:
            share = scalars["effective_mass_fractions"][0]
            axis = max(range(3), key=lambda c: share[c])
            along = f", moving mostly along {'XYZ'[axis]} ({share[axis] * 100:.0f} % of its mass)" if share[axis] >= 0.05 else ""
            magnitude = scalars["magnitudes"][0]
            node = int(magnitude.argmax())
            found.append(finding(
                "info", "first_mode", f"First mode {hz_text(frequencies[0])}{along}",
                "the lowest natural frequency; a vibration near it (a motor, a fan, a road) makes the part resonate",
                [{"text": "moves most here", "ref": None, "at": xyz(result.dof_locations[node])}],
            ))
        coarser = scalars.get("coarser_frequencies_Hz")
        if coarser:
            moved = abs(frequencies[0] - coarser[0]) / frequencies[0] if frequencies[0] > 0 else 0.0
            found.append(finding(
                "warning" if moved > 0.05 else "info", "mesh_not_converged" if moved > 0.05 else "mesh_converged",
                f"The first mode moved {moved * 100:.1f} % on a finer mesh ({hz_text(coarser[0])} to {hz_text(frequencies[0])})",
                "a check was close, so the part was solved again finer; the finer answer is the one reported", [],
            ))
        return by_severity(found)

    # -- what is written -----------------------------------------------------------------------------

    def deformation_scale(self, result: AnalysisResult, bbox_diagonal: float, requested: float | None) -> float | None:
        return scaled_deformation(1.0, bbox_diagonal, requested)

    def summary(self, result: AnalysisResult, inputs: ModalInputs, check_results: list[dict]) -> dict:
        scalars = result.scalars
        modes = [row for _, row in mode_rows(scalars)]
        summary: dict[str, Any] = {
            "modes": modes,
            "first_frequency_Hz": modes[0]["frequency_Hz"] if modes else None,
            "total_mass_kg": round(scalars["total_mass_kg"], 6),
            "rigid_body_modes": scalars["rigid_body_modes"],
            "modes_requested": inputs.modes,
            "range_Hz": None if inputs.range_Hz is None else list(inputs.range_Hz),
            "deformation_scale": scalars["deformation_scale"],
        }
        return finish_summary(summary, scalars, check_results)

    def field_ranges(self, summary: dict, result: AnalysisResult) -> dict[str, tuple[float, float]]:
        # Every shape is scaled to a largest motion of 1 mm, so one range holds across the frames.
        return {"mode_shape": (0.0, 1.0)}

    def extras_head(self, summary: dict) -> dict:
        return {"first_frequency_Hz": summary["first_frequency_Hz"]}

    def extras_assembly(self, summary: dict) -> dict:
        return {"parts": summary.get("parts", [])}

    def study_echo(self, inputs: ModalInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        echo: dict[str, Any] = {"fixtures": fixtures_echo(inputs.fixtures, bare), "modes_requested": inputs.modes}
        if inputs.range_Hz is not None:
            echo["range_Hz"] = list(inputs.range_Hz)
        return echo

    def human_lines(self, summary: dict) -> list[str]:
        modes = summary.get("modes", [])
        lines = []
        if modes:
            lines.append(f"first mode {hz_text(modes[0]['frequency_Hz'])}; modes "
                         + ", ".join(hz_text(mode["frequency_Hz"]) for mode in modes))
        if summary.get("rigid_body_modes"):
            lines.append(f"free in space: {summary['rigid_body_modes']} rigid-body modes left out")
        lines.append(f"total mass {summary.get('total_mass_kg'):.4g} kg")
        return lines


def mode_rows(scalars: dict) -> list[tuple[int, dict]]:
    """Each mode's summary row (number, frequency, effective mass fractions), by its 0-based index."""
    return [(i, {"mode": i + 1, "frequency_Hz": round(f, 4), "effective_mass_fraction": [round(x, 4) for x in share]})
            for i, (f, share) in enumerate(zip(scalars["frequencies_Hz"], scalars["effective_mass_fractions"]))]


def finish_summary(summary: dict, scalars: dict, check_results: list[dict], *, kept_share: bool = False) -> dict:
    """A summary's tail: the share reduce_modes kept (``kept_share``), an assembly's parts, then the checks."""
    if kept_share and scalars["kept_share"] is not None:
        summary["kept_share"] = round(scalars["kept_share"], 4)
    if scalars.get("parts"):
        summary["parts"] = scalars["parts"]
    summary["checks"] = check_results
    return summary


# -- what the modal responses share (harmonic, random vibration, shock, transient) --------------------

G0_MM_S2 = 9806.65
#: reduce_modes keeps the fewest modes holding this share of the mass moving along the shake (or of the response).
KEEP_SHARE = 0.9
#: The modes the ladder assumes before the solve has found them, and after reduce_modes.
GUESS_MODES, REDUCED_GUESS = 20, 6
_AXES = "XYZ"


def axis_words(direction) -> str:
    """"Z", "-X" or "(0.71, 0, 0.71)": the line a shake moves along."""
    for c, name in enumerate(_AXES):
        if abs(abs(direction[c]) - 1) < 1e-9:
            return name
    return "(" + ", ".join(f"{v:.2g}" for v in direction) + ")"


def unit_vector(vector, where: str, named: str = "it is shaken along", axis: str = "it is shaken along"):
    """A direction as a unit vector; ``named`` and ``axis`` say it in the errors ("of the shock", "the shock comes along")."""
    if not isinstance(vector, list) or len(vector) != 3:
        raise ValueError(f"{where}: the direction {named} as [x, y, z], like [0, 0, 1] for Z")
    values = [kinds.number(v, where=where) for v in vector]
    size = math.sqrt(sum(v * v for v in values))
    if size == 0:
        raise ValueError(f"{where}: the direction is zero; give the axis {axis}, like [0, 0, 1]")
    return tuple(v / size for v in values)  # type: ignore[return-value]


def parse_damping(document: dict) -> float:
    zeta = kinds.number(document.get("damping_ratio", 0.02), where="damping_ratio", positive=True)
    if zeta >= 1:
        raise ValueError(f"damping_ratio: a share of critical damping, like 0.02 for 2 %; must be under 1, got {zeta:g}")
    return zeta


def modes_text(count: int) -> str:
    return f"{count} mode{'s' if count != 1 else ''}"


def modes_estimate(ctx: SolveContext):
    """(modes, their eigen estimate, nodes, its seconds plus each mode's stress): what a modal response's estimate
    adds its own work to."""
    from cadgen._internal.fea import fit

    plan = getattr(ctx, "plan", None)
    modes = int(getattr(plan, "modes", None) or GUESS_MODES)
    base = eigen_estimate(ctx, modes)
    nodes = base.dofs / 3.0
    seconds = base.seconds + modes * fit.SECONDS_PER_ELEMENT.get(getattr(plan, "order", 2), 2.5e-4) * nodes / 1.7
    return modes, base, nodes, seconds


def fewest_modes_rung(ctx: SolveContext, direction):
    """reduce_modes: keep only the fewest modes holding 90 % of the mass moving along the shake; the solve finds the
    modes, then says how many it kept and the share they hold (``settle_steps``)."""
    from cadgen._internal.fea import fit

    plan = ctx.plan
    if plan.modes is not None and plan.modes <= REDUCED_GUESS:
        return None
    plan.modes = REDUCED_GUESS
    along = "" if direction is None else f" along {axis_words(direction)}"
    return fit.Step("reduce_modes", f"Kept only the fewest modes holding {KEEP_SHARE * 100:.0f}% of the mass moving{along}, "
                    "to fit", None, None, detail={"keep_share": KEEP_SHARE})


def kept_step(found, kept, share: float | None, what: str) -> dict:
    """reduce_modes' words once the modes are found, for ``settle_steps``: ``what`` the kept ones hold."""
    return {"words": f"Kept {len(kept)} of the {len(found)} modes up to {hz_text(found.searched_Hz)}, the ones holding "
                     f"{(share or 0.0) * 100:.0f}% {what}, to fit",
            "detail": {"kept_modes": len(kept), "found_modes": len(found), "kept_share": round(share or 0.0, 4)}}


def response_operators(ctx: SolveContext, fixtures):
    """(materials, K, M, the supports): the fixtures must hold the part."""
    from cadgen._internal.fea import operators

    materials = materials_of(ctx)
    K = operators.stiffness(ctx.space, materials)
    M = operators.mass(ctx.space, materials)
    return materials, K, M, held_supports(ctx.space, fixtures, ctx.ordinal_of, require=True)


def mass_enough(Mr: "np.ndarray", total_along: float) -> Callable:
    """find_modes' ``enough``: the modes found hold KEEP_SHARE of the mass moving along the shake."""
    def enough(omega2, vectors, free):
        return float(((vectors.T @ Mr[free]) ** 2).sum()) / total_along >= KEEP_SHARE

    return enough


def response_modes(ctx: SolveContext, K, M, held, top_Hz: float, enough, timings: dict, warnings: list[str],
                   name: str | None = None):
    """The modes up to ``top_Hz`` (:func:`superposition.find_modes`), equal frequencies lined up with the axes."""
    import time

    from cadgen._internal.fea import superposition as sp

    space = ctx.space
    started = time.perf_counter()
    found = sp.find_modes(K, M, held, top_Hz, method=solver_method(ctx, space.dofs - len(held.fixed)),
                          locations=space.locations, component=space.component, enough=enough)
    warnings.extend(found.warnings)
    found.vectors = align_degenerate(found.vectors, M, found.frequencies_Hz, space.component)
    timings["eigen_s"] = time.perf_counter() - started
    if name and ctx.log:
        ctx.log(f"{name}: {len(found)} modes up to {found.frequencies_Hz[-1]:.4g} Hz ({found.how})")
    return found


def stress_scalars(ctx: SolveContext) -> dict:
    """What judging a stress against the yield where it peaks reads, in ``scalars``."""
    return {"study_checks": tuple(getattr(ctx.study, "checks", ()) or ()),
            "margin": float(getattr(ctx.study, "margin", 2.0) or 2.0), "parts": assembly_parts(ctx),
            "yields": [m.yield_strength for m in ctx.materials], "node_domain": node_domain(ctx.space)}


def yield_check(ctx: SolveContext, result: AnalysisResult, node: int, peak: float, label, fixtures=()) -> dict:
    """The stress check of ``peak`` MPa at ``node`` against the yield of the part it is in."""
    from cadgen._internal.fea import checks
    from cadgen._internal.fea.analyses.static import peak_face

    fixed = {ctx.ordinal_of[ref] for fixture in fixtures for ref in fixture.faces}
    at = tuple(float(c) for c in result.dof_locations[node])
    solved = checks.Solved(
        material_name="", yield_MPa=yield_at(result.scalars, node), peak_MPa=peak, peak_gauss_MPa=peak, peak_at=at,
        peak_face=peak_face(ctx.volume, result, node, fixed), fixed_faces=(), max_displacement_mm=0.0,
        displacement_at=at, bbox_diagonal_mm=0.0, margin=result.scalars["margin"], coarser_peak_MPa=None, part="",
    )
    return checks.stress_check(solved, label=label)


def displacement_judged(ctx: SolveContext, result: AnalysisResult, values, check: dict, index: int):
    """(the displacement check of the largest of ``values`` over the check's faces, that peak)."""
    from cadgen._internal.fea import checks

    peak = field_peak(ctx, result, values, check.get("faces", ()), index)
    return checks.displacement_check(peak.value, check["limit_mm"], at=peak.at, ref=peak.ref, faces=peak.faces,
                                     label=check.get("label")), peak


def response_findings(check_results: list[dict], kind: str, lead: Callable[[dict], str], whole: str,
                      described: Callable[[dict], str], *, after: str = "",
                      moved: str = "the displacement is relative to where it is held", other=None) -> list[dict]:
    """What the failing and close checks say: a stress past (or near) yield, a displacement close to its limit, and
    ``other(check, severity, item)``'s finding for any other kind. ``lead(check)`` starts the sentence ("At 2 ms,")."""
    found = []
    for check in check_results:
        status = check["status"]
        if status not in ("fails", "close"):
            continue
        fails = status == "fails"
        severity, item = "error" if fails else "warning", [{"text": check["label"], **check["where"]}]
        if check["kind"] == "stress":
            limit = (f"over its {check['limit']:g} MPa yield" if fails
                     else f"inside the {check['margin']:g}× margin under its {check['limit']:g} MPa yield")
            found.append(finding(severity, f"{kind}_yields" if fails else f"{kind}_close_to_yield",
                                 f"{lead(check)} {whole} reaches {check['value']:.3g} MPa{after}, {limit} ({check['label']})",
                                 described(check), item))
        elif check["kind"] == "displacement" and not fails:
            found.append(finding("warning", "displacement_close",
                                 f"{lead(check)} it moves {check['value']:.3g} mm{after}, close to the {check['limit']:g} mm "
                                 f"allowed ({check['label']})", moved, item))
        elif other is not None and (said := other(check, severity, item)) is not None:
            found.append(said)
    return found


def converged_finding(what: str, peak: float, coarser: float) -> dict:
    moved = abs(peak - coarser) / coarser if coarser > 0 else 0.0
    return finding("warning" if moved > 0.1 else "info", "mesh_not_converged" if moved > 0.1 else "mesh_converged",
                   f"{what} moved {moved * 100:.1f} % on a finer mesh ({coarser:.3g} to {peak:.3g} MPa)",
                   "a check was close, so the part was solved again finer; the finer answer is the one reported", [])


def missing_mass_finding(scalars: dict, share: float, top: str) -> dict:
    along = axis_words(scalars["direction"])
    return finding("warning", "modes_missing_mass",
                   f"The modes up to {hz_text(scalars['searched_Hz'])} move only {share * 100:.0f}% of the mass along "
                   f"{along}: the rest answers above them, so the response may read a little low",
                   f"effective mass of the modes found {share:.3f} of the total along {along}; "
                   f"a fixed face's own mass never moves, and modes above 1.5× the {top} top are left out", [])


def no_resonance_finding(kind: str, low: float, high: float, whole: str, frequencies, description: str) -> dict:
    return finding("info", kind, f"No natural frequency lies between {low:g} and {high:g} Hz: {whole} follows the shake "
                   "without resonating" + (f"; its first mode is {hz_text(frequencies[0])}" if frequencies else ""),
                   description, [])


def modes_line(summary: dict) -> str:
    line = f"{modes_text(summary['modes_used'])} up to {hz_text(summary['modes_up_to_Hz'])}"
    if summary.get("effective_mass_fraction") is not None:
        line += f", holding {summary['effective_mass_fraction'] * 100:.0f}% of the mass along {summary['effective_mass_axis']}"
    return line


def load_echo(load, bare) -> dict:
    return {"type": load.type, "faces": bare(load.faces),
            **({"vector_N": [float(c) for c in load.vector]} if load.type == "force" else {"pressure_MPa": load.pressure})}


class ModalResponse(AnalysisBase):
    """What harmonic, random vibration, shock and transient share: modes found up to a top frequency, a stress
    judged against the yield of the part where it peaks (``_peak(result, inputs)``: node, MPa), reduce_modes settled
    once the modes are found."""

    tier: ClassVar[int] = 1
    material_needs: ClassVar[frozenset[str]] = frozenset({"density"})
    #: The field local_refine follows and its two passes compare the peak of.
    envelope: ClassVar[str] = "von_mises"

    def apply(self, rung, ctx: SolveContext, inputs):
        """``reduce_modes``: the fewest modes holding 90 % of the mass along the shake. ``iterative``: LOBPCG instead of
        factorising. The mesh rungs the shared way; ``symmetry`` and ``idealise`` are not taken for a modal response yet."""
        if rung == "reduce_modes":
            return fewest_modes_rung(ctx, inputs.direction)
        return apply_eigen_rung(self, rung, ctx, inputs)

    def governing(self, result: AnalysisResult) -> tuple["np.ndarray", float]:
        field = result.fields[self.envelope]
        return field, float(field.max())

    def settle_steps(self, result: AnalysisResult, steps: list) -> list:
        """reduce_modes' step said again once the modes are found (run.py calls this after the solve; the solve
        leaves the words in ``reduce_step``)."""
        from cadgen._internal.fea import fit

        return fit.settle_step(steps, "reduce_modes", result.scalars.get("reduce_step"), marker="keep_share")

    def needs_finer(self, result: AnalysisResult, inputs, check_results: list[dict]) -> bool:
        """Solve once more finer when a check is close, or the stress peak lies between yield over the margin and yield."""
        if check_results:
            return any(check["status"] == "close" for check in check_results)
        if not any(check.get("kind") == "stress" for check in result.scalars["study_checks"]):
            return False
        node, peak = self._peak(result, inputs)
        ratio = peak / yield_at(result.scalars, node)
        return 1.0 / result.scalars["margin"] < ratio <= 1.0

    def refined_record(self, result: AnalysisResult, size_mm: float, finer_mm: float, *, assembly: bool) -> dict:
        return {"from_size_mm": round(size_mm, 4), "from_max_von_mises_MPa": round(self._peak(result)[1], 4),
                "size_mm": round(finer_mm, 4), "max_von_mises_MPa": None}

    def merge_finer(self, coarse: AnalysisResult, finer: AnalysisResult, refined: dict) -> AnalysisResult:
        refined["max_von_mises_MPa"] = round(self._peak(finer)[1], 4)
        finer.scalars["coarser_peak_MPa"] = self._peak(coarse)[1]
        return finer

    def _yield_check(self, ctx: SolveContext, result: AnalysisResult, inputs, label) -> dict:
        node, peak = self._peak(result, inputs)
        return yield_check(ctx, result, node, peak, label, inputs.fixtures)

    def extras_assembly(self, summary: dict) -> dict:
        return {"parts": summary.get("parts", [])}
