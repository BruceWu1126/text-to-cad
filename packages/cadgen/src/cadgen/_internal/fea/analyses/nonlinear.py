"""Permanent bend / Stretch (lite): metal past yield, or rubber stretched far, with the load applied in steps.

The study (spec 5.16) takes static's ``fixtures`` and ``loads`` and ``steps``,
the starting number of load steps (10). The material says which model:

- ``plasticity: {"tangent_MPa": 2000}`` (or ``tangent_MPa``): J2 plasticity,
  small strain, von Mises yield at ``yield_MPa`` and a bilinear isotropic
  hardening of that slope past it (0 is perfectly plastic); :mod:`..plasticity`.
- ``hyperelastic: {"model": "neo_hookean", "mu_MPa": 0.6, "bulk_MPa": 300}``:
  compressible Neo-Hookean, total Lagrangian, large deformation;
  :mod:`..hyperelastic`.

In an assembly a part with neither stays elastic (in a rubber study, a
Neo-Hookean of its own E and ν). :mod:`..nonlinear_driver` takes the load
from 0 to 100 % in steps, Newton's method in each, cutting a step that does
not converge up to six times. A load the part cannot carry is a result: the
path ends at the last load it carried, "Collapses at about 70 % of the load",
with every converged step written. There is no load control (the response is
not proportional to the load).

What it writes: a series of load steps as pseudo-time (``kind: "time"``, unit
``%``, frames labelled "60 % load", at most ``Budget.max_frames`` of them,
the last carried one the default), each with the von Mises stress (the true
stress for rubber), the displacement and (J2) the equivalent plastic strain
in percent. Checks: ``plastic_strain`` (``limit_percent``; by default 0.2 %,
the offset that defines yield), ``stress`` and ``displacement``, each judged
at the last load carried; a collapse fails every check. Stdlib only at import.

The solve's plumbing (symmetry planes, supports, unfolding a half, the frame series) lives here as module helpers
that contact, bolt, creep and topology share.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import (
    AnalysisBase, AnalysisResult, FieldSpec, Series, SeriesFrame, SolveContext, by_severity, finding, frame_attributes,
    max_frames, plan_value, xyz,
)
from cadgen._internal.fea.analyses.static import StaticAnalysis, StaticInputs

if TYPE_CHECKING:
    import numpy as np

__all__ = ["DEFAULT_STEPS", "LIMITS", "NonlinearAnalysis", "NonlinearInputs", "collapse_words", "load_label", "part_materials",
           "with_materials"]

#: Load steps a study starts with when it names none.
DEFAULT_STEPS = 10
#: The most starting steps a study may ask for.
MAX_STEPS = 200
#: Newton iterations a load step takes on average, for the cost estimate.
NEWTON_PER_STEP = 4.0
#: A Newton-Krylov solve against an AMG + CG one of the same size: GMRES's longer recurrences and a nonsymmetric start.
GMRES_PER_AMG = 1.5
#: Quadrature points per quadratic tet (the vector basis's), for the state's memory.
POINTS_PER_TET = 11
#: The permanent strain a plastic_strain check allows when the study names no check: the 0.2 % offset that defines yield.
DEFAULT_PLASTIC_LIMIT = 0.2

#: What this lite solver leaves out, written into its output, its verdict's Details and the skill.
LIMITS = (
    "Metal plasticity is small strain J2 (von Mises) with a bilinear curve and isotropic hardening only: "
    "no kinematic hardening, so reversed and cyclic loads are not modelled",
    "No rate or temperature effects: the material answers the same however fast it is loaded",
    "Rubber is Neo-Hookean: fair to stretches of about 100 %, with no softening on reloading and no damping",
    "Loads keep their size and direction as the part deforms; a pressure does not turn with the surface",
    "The load is stepped up to the full load; past a collapse the path is not followed",
)


def figure(value: float) -> str:
    """A number in three figures, never in exponent form: "2.5", "250", "1000"."""
    text = f"{value:.3g}"
    return text if "e" not in text else f"{value:.0f}"


def load_label(factor: float) -> str:
    """A load factor as the scrubber says it: "60 % load"."""
    return f"{figure(factor * 100.0)} % load"


def collapse_words(factor: float) -> str:
    """"Collapses at about 70 % of the load"."""
    percent = factor * 100.0
    if percent < 1.0:
        return "Collapses before 1 % of the load"
    return f"Collapses at about {percent:.0f} % of the load"


@dataclass(frozen=True)
class NonlinearInputs(StaticInputs):
    steps: int = DEFAULT_STEPS
    #: "plasticity" or "hyperelastic": the model the study's materials ask for.
    model: str = "plasticity"


def part_materials(ctx) -> "list[dict] | None":
    """Each part's material, in the assembly's part order (``ctx.materials`` is indexed as the mesh's domains are):
    its name and yield, for the summary's and the GLB's ``parts``. ``None`` for one part."""
    if ctx.assembly is None:
        return None
    return [{"material": m.name, "yield_MPa": m.yield_strength} for m in ctx.materials]


def with_materials(parts: list[dict], scalars: dict) -> list[dict]:
    """The summary's ``parts``, each with the material it was solved with (where the solve recorded them)."""
    materials = scalars.get("part_materials") or []
    return [{**part, **(materials[i] if i < len(materials) else {})} for i, part in enumerate(parts)]


def material_specs(document: dict) -> list[tuple[str, Any]]:
    specs = [("material", document["material"])] if "material" in document else []
    parts = document.get("parts")
    if isinstance(parts, dict):
        specs += [(f"parts[{name!r}].material", entry["material"]) for name, entry in parts.items()
                  if isinstance(entry, dict) and "material" in entry]
    return specs


def view_checks(document: dict) -> Any:
    view = document.get("view")
    return view.get("checks") if isinstance(view, dict) else None


def refuse_stress(checks: list, words: str) -> None:
    """A material with no yield strength: the first stress check in ``checks`` is a study error ending in ``words``."""
    for index, check in enumerate(checks):
        if isinstance(check, dict) and check.get("kind") == "stress":
            raise ValueError(f"view.checks[{index}]: a stress check compares the peak with a yield strength{words}")


def _model(document: dict) -> str:
    from cadgen._internal.fea.materials import material_from_spec

    materials = [(where, material_from_spec(spec)) for where, spec in material_specs(document)]
    if any(m.hyperelastic for _, m in materials):
        return "hyperelastic"
    if any(m.tangent is not None for _, m in materials):
        if any(m.yield_strength is None for _, m in materials if m.tangent is not None):
            raise ValueError("material.yield_MPa: plasticity needs the yield strength the metal starts to bend for good at")
        return "plasticity"
    raise ValueError(
        'material: a nonlinear study needs how the material behaves past the linear range: for a metal that bends for '
        'good, its slope past yield, like {"name": "steel", "plasticity": {"tangent_MPa": 2000}} (0 for perfectly '
        'plastic); for rubber, {"name": "rubber", "hyperelastic": {"model": "neo_hookean", "mu_MPa": 0.6, "bulk_MPa": 300}}'
    )


def parse_steps(document: dict, default: int, most: int = MAX_STEPS, what: str = "load") -> int:
    raw = document.get("steps", default)
    if isinstance(raw, bool) or not isinstance(raw, int) or not 1 <= raw <= most:
        raise ValueError(f"steps: the number of {what} steps to start with, a whole number from 1 to {most}, "
                         f"got {kinds.json_text(raw)}")
    return raw


# -- the problems the driver solves --------------------------------------------------------------------


class SolidProblem:
    """A solid on the space: the free DOF, the full load, and each converged step's quadrature-point fields."""

    def __init__(self, space, free, external):
        from cadgen._internal.fea.plasticity import ElementOps

        self.space = space
        self.ops = ElementOps(space.basis)
        self.size = int(space.basis.N)
        self.free = free
        self.external = external
        self.locations = space.locations
        self.component = space.component
        #: Per converged step: (von Mises at the quadrature points, equivalent plastic strain there or None).
        self.history: list[tuple["np.ndarray", "np.ndarray | None"]] = []

    def mean_dilatation(self, update, tangent):
        """(internal force, tangent or None) of a small-strain update: the deviatoric tangent at the points, the bulk
        modulus on each element's mean volume change."""
        import numpy as np

        if not tangent:
            return self.ops.force(update.stress), None
        C = update.tangent
        bulk = np.broadcast_to(np.asarray(self.params.bulk, dtype=float), self.ops.dx.shape)
        for i in range(3):
            for k in range(3):
                C[i, i, k, k] -= bulk
        K = self.ops.matrix(C) + self.ops.volumetric_matrix(bulk[:, 0])
        return self.ops.force(update.stress), K


class _J2Problem(SolidProblem):
    def __init__(self, space, free, external, params):
        from cadgen._internal.fea.plasticity import J2State

        super().__init__(space, free, external)
        self.params = params
        self.state = J2State.zeros(tuple(self.ops.dx.shape))

    def _update(self, u, tangent):
        from cadgen._internal.fea.plasticity import mean_dilatation_strain, radial_return

        return radial_return(mean_dilatation_strain(self.ops.gradient(u), self.ops), self.state, self.params, tangent=tangent)

    def evaluate(self, u, *, tangent):
        return self.mean_dilatation(self._update(u, tangent), tangent)

    def commit(self, u, factor):
        from cadgen._internal.fea.operators import von_mises

        update = self._update(u, False)
        self.state = update.state
        self.history.append((von_mises(update.stress), update.state.equivalent.copy()))


class _RubberProblem(SolidProblem):
    def __init__(self, space, free, external, params):
        super().__init__(space, free, external)
        self.params = params

    def _stress(self, u, tangent):
        import numpy as np

        from cadgen._internal.fea.hyperelastic import neo_hookean

        F = self.ops.gradient(u) + np.eye(3)[:, :, None, None]
        P, A, ok = neo_hookean(F, self.params, tangent=tangent)
        return F, P, A, ok

    def evaluate(self, u, *, tangent):
        import numpy as np

        F, P, A, ok = self._stress(u, tangent)
        if not ok:
            return np.full(self.size, np.nan), None
        return self.ops.force(P), (self.ops.matrix(A) if tangent else None)

    def commit(self, u, factor):
        from cadgen._internal.fea.hyperelastic import cauchy
        from cadgen._internal.fea.operators import von_mises

        F, P, _, _ = self._stress(u, False)
        self.history.append((von_mises(cauchy(P, F)), None))


def external_load(space, materials, loads, body, ordinal_of, share: float) -> "np.ndarray":
    """The full load on every DOF: each force as a uniform traction on its faces (``share`` of it on a symmetric
    half), each pressure along the faces' inward normal on the undeformed part, each body acceleration as ∫ ρ b·v."""
    import numpy as np
    from skfem import LinearForm, asm

    from cadgen._internal.fea import operators

    basis = space.basis
    f = np.zeros(basis.N)
    for load in loads:
        facet_basis = basis.boundary(space.facets_of(load.faces, ordinal_of))
        if load.type == "force":
            traction = share * np.asarray(load.vector, dtype=float) / float(facet_basis.dx.sum())

            @LinearForm
            def form(v, w, traction=traction):
                return traction[0] * v[0] + traction[1] * v[1] + traction[2] * v[2]

        else:
            pressure = float(load.pressure)

            @LinearForm
            def form(v, w, pressure=pressure):
                return -pressure * (w.n[0] * v[0] + w.n[1] * v[1] + w.n[2] * v[2])

        f += asm(form, facet_basis)
    for acceleration in body:
        f += operators.body_force(space, materials, acceleration)
    return f


def pick_frames(count: int, keep: int) -> list[int]:
    """At most ``keep`` of ``count`` steps, evenly spread, the last always among them."""
    if count <= keep:
        return list(range(count))
    if keep <= 1:
        return [count - 1]
    picked = {round(i * (count - 1) / (keep - 1)) for i in range(keep)}
    return sorted(picked)


# -- the solve's plumbing, shared with contact, bolt, creep and topology ----------------------------------


def symmetry_planes(ctx: SolveContext) -> list:
    """The planes fit's ``symmetry`` rung cut the part along (the solve is of one half per plane)."""
    plan = ctx.plan
    return list(plan.prepared.planes) if plan is not None and plan.prepared is not None else []


def supported(ctx: SolveContext, fixtures, planes: list):
    """(supports, free DOF): fixed faces, rollers (a sloped or curved one's nodes turned into their own axes:
    supports.py) and each symmetry plane's normal component."""
    import numpy as np

    from cadgen._internal.fea.supports import check_held, supports_of

    space = ctx.space
    plane_dofs = []
    for plane in planes:
        dofs = space.basis.get_dofs(space.facets_of_ordinals([plane.ordinal], "a symmetry plane")).all()
        plane_dofs.append(dofs[space.component[dofs] == plane.component])
    supports = supports_of(space, fixtures, ctx.ordinal_of, held=plane_dofs)
    check_held(space, supports)
    return supports, np.setdiff1d(np.arange(space.basis.N), supports.fixed)


def totals(space, values: "np.ndarray", dofs: "np.ndarray | None" = None) -> tuple[float, float, float]:
    """A DOF vector's x, y and z sums (over ``dofs`` alone, when given): the force it is."""
    if dofs is None:
        return tuple(float(values[space.component == c].sum()) for c in range(3))
    return tuple(float(values[dofs][space.component[dofs] == c].sum()) for c in range(3))


def nodal(space, values: "np.ndarray") -> "np.ndarray":
    """Quadrature-point values at the nodes: the L2 projection, never past the points' own range (it overshoots where
    a field turns sharply)."""
    import numpy as np

    return np.clip(space.scalar.project(values), 0.0, float(values.max()))


def unfold(ctx: SolveContext, planes: list, scalars: dict[str, list], vectors: dict[str, list], reactions: list, *forces):
    """A symmetric half's frames mirrored back into the whole part, plane by plane (``ctx.volume`` becomes the whole):
    (mesh, scalars, vectors, reactions, forces), mesh = (dof_locations, boundary, tets, element_dofs, vertices), each
    of ``scalars`` and ``vectors`` a list of frames by name, ``forces`` each one force."""
    space = ctx.space
    locations, boundary, tets, element_dofs, vertices = (space.dof_locations, space.boundary_quadratic, space.tets,
                                                         space.element_dofs, space.vertices)
    if planes:
        from cadgen._internal.fea import symmetry

        volume = ctx.volume
        for plane in reversed(planes):
            whole, keep = symmetry.unfold_volume(volume, plane)
            locations, flat_scalars, flat_vectors, boundary, tets, element_dofs, vertices = symmetry.unfold_fields(
                plane, vertices, locations, keep, boundary=boundary, tets=tets, element_dofs=element_dofs,
                scalars={(name, i): values for name, frames in scalars.items() for i, values in enumerate(frames)},
                vectors={(name, i): values for name, frames in vectors.items() for i, values in enumerate(frames)},
            )
            scalars = {name: [flat_scalars[name, i] for i in range(len(frames))] for name, frames in scalars.items()}
            vectors = {name: [flat_vectors[name, i] for i in range(len(frames))] for name, frames in vectors.items()}
            reactions = [symmetry.unfold_force(plane, r) for r in reactions]
            forces = tuple(symmetry.unfold_force(plane, force) for force in forces)
            volume = whole
        ctx.volume = volume
    return (locations, boundary, tets, element_dofs, vertices), scalars, vectors, reactions, forces


def field_peak(ctx: SolveContext, result: AnalysisResult, values: "np.ndarray", faces: tuple, index: int):
    """``kinds.field_max_over`` of a check: the peak of ``values`` over ``faces`` (the whole part when none)."""
    return kinds.field_max_over(values, faces, boundary=result.boundary_quadratic, boundary_ordinal=ctx.volume.boundary_ordinal,
                                locations=result.dof_locations, face_ref=ctx.volume.faces, ordinal_of=ctx.ordinal_of,
                                where=f"view.checks[{index}]")


def peak_check(kind: str, label: str, peak, limit: float, unit: str) -> dict:
    """A check of a field's peak against a limit (plastic strain, creep strain, contact pressure), close at 90 %."""
    from cadgen._internal.fea import checks

    ratio = peak.value / limit
    return {
        "kind": kind, "label": label, "value": round(peak.value, 6), "limit": limit, "unit": unit, "ratio": round(ratio, 6),
        "close_at": 0.9, "status": checks.check_status(ratio, 0.9), "where": {"ref": peak.ref, "at": xyz(peak.at)},
    }


class NonlinearAnalysis(AnalysisBase):
    name: ClassVar[str] = "nonlinear"
    word: ClassVar[str] = "Permanent bend / Stretch"
    #: Its solids are the same in every direction: study.parse_study refuses an orthotropic material (contact, bolt
    #: and creep inherit this).
    isotropic_only: ClassVar[bool] = True
    #: A load path that stops short is the part collapsing: every check fails. Contact (and bolt) stop where the
    #: contact cannot be followed, which says nothing of the part, so their checks judge their own numbers.
    collapse_fails: ClassVar[bool] = True
    limits: ClassVar[tuple[str, ...]] = LIMITS
    study_keys: ClassVar[frozenset[str]] = frozenset({"fixtures", "loads", "steps"})
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("von_mises", "_VON_MISES", "von Mises stress", "MPa", per_frame=True),
        FieldSpec("displacement", "_DISPLACEMENT", "displacement", "mm", 3, 1000.0, per_frame=True),
        FieldSpec("plastic_strain", "_PLASTIC_STRAIN", "plastic strain", "%", per_frame=True),
    )
    checks: ClassVar[tuple] = (kinds.PLASTIC_STRAIN, kinds.STRESS, kinds.DISPLACEMENT)
    default_checks: ClassVar[tuple[dict, ...]] = ({"kind": "plastic_strain", "limit_percent": DEFAULT_PLASTIC_LIMIT},)
    # No load_scale: the response is not proportional to the load, so a load multiple says nothing.
    drives: ClassVar[tuple[str, ...]] = ("field", "deformation", "threshold", "frame")
    default_controls: ClassVar[dict[str, dict]] = {
        "frame": {"drives": "frame", "type": "number", "min": 0.0, "max": 100.0},
        "field": {"drives": "field", "type": "enum", "options": ["von_mises", "displacement", "plastic_strain"]},
        "deformation": {"drives": "deformation", "type": "number", "min": 0.0, "max": None},
    }
    ladder: ClassVar[tuple[str, ...]] = ("iterative", "adaptive_steps", "local_refine", "defeature", "symmetry")
    noun: ClassVar[str] = "this load"
    governing_word: ClassVar[str] = "peak stress"
    extras_word: ClassVar[str] = "nonlinear"
    # needs_finer stays AnalysisBase's False: a whole load path is too dear to run twice unasked.

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> NonlinearInputs:
        static = StaticAnalysis().parse(document)
        model = _model(document)
        steps = parse_steps(document, DEFAULT_STEPS)
        checks = view_checks(document)
        if isinstance(checks, list):
            from cadgen._internal.fea.materials import material_from_spec

            if any(material_from_spec(spec).yield_strength is None for _, spec in material_specs(document)):
                refuse_stress(checks, ", and the rubber has none; give the material's yield_MPa, or check its displacement")
        return NonlinearInputs(
            static.face_refs, static.anchor_refs, True, fixtures=static.fixtures, loads=static.loads,
            material_needs=static.material_needs, steps=steps, model=model,
        )

    # -- the ladder (fit.py drives these) -------------------------------------------------------------

    def _load_steps(self, ctx: SolveContext, inputs: NonlinearInputs) -> float:
        """Load steps the plan expects: the starting count, fewer where adaptive steps let them grow."""
        steps = float(inputs.steps)
        if getattr(ctx.plan, "adaptive_steps", False):
            steps = max(2.0, steps / 2.0)
        return steps

    def estimate(self, ctx: SolveContext, inputs: NonlinearInputs):
        """Each Newton iteration of each load step: assembling the tangent and solving with it, by SuperLU (its
        factor, n^1.5 entries and n^2 time, at any size: the driver never switches by itself) or by Newton-Krylov
        (an AMG hierarchy and GMRES); plus the quadrature-point state (the tangent tensor, each step's stress and
        plastic strain). fit.py's per-method constants; a two-pass plan pays for both passes."""
        from cadgen._internal.fea import fit

        elements, nodes, two = fit.plan_counts(ctx)
        order = ctx.plan.order
        n = max(int(3 * nodes), 1)
        matrix = 12.0 * fit.NNZ_PER_ROW[order] * n
        build = fit.BYTES_PER_ELEMENT[order] * elements
        assemble = fit.SECONDS_PER_ELEMENT[order] * elements
        if ctx.plan.solver == "direct":
            memory = build + 2.0 * matrix + 16.0 * fit.FILL * n ** 1.5
            seconds = assemble + fit.DIRECT_SECONDS * n ** 2
        else:
            memory = build + matrix * (1.0 + fit.AMG_COPIES)
            seconds = assemble + GMRES_PER_AMG * fit.AMG_SECONDS_PER_DOF * n
        steps = self._load_steps(ctx, inputs)
        state = elements * POINTS_PER_TET * 8.0 * (81.0 + 3 * 9.0 + 2.0 * (steps + 1))
        passes = 2.0 if two else 1.0
        return fit.Estimate(dofs=n, memory_bytes=int(fit.BASE_BYTES + memory + state),
                            seconds=float(passes * seconds * steps * NEWTON_PER_STEP))

    #: What the adaptive_steps rung says (``{growth}``, ``{steps}``), and its accuracy note (None when nothing is lost).
    _adaptive_words = ("Let the load steps grow (up to {growth}× the {steps} starting ones) while Newton converges in a "
                       "few iterations, cutting them where it does not, to fit the time target")

    def _adaptive_accuracy(self, inputs) -> str | None:
        return None if inputs.model == "hyperelastic" else (
            "plastic strain is followed along the load in larger steps where the part answers smoothly; the load it "
            "collapses at is still found to within the smallest step")

    def apply(self, rung, ctx: SolveContext, inputs: NonlinearInputs):
        from cadgen._internal.fea import fit

        if rung == "iterative":
            return self._apply_iterative(ctx, inputs)
        if rung == "adaptive_steps":
            if ctx.plan.adaptive_steps:
                return None
            ctx.plan.adaptive_steps = True
            from cadgen._internal.fea.nonlinear_driver import MAX_GROWTH

            return fit.Step(
                "adaptive_steps", self._adaptive_words.format(growth=MAX_GROWTH, steps=inputs.steps),
                self._adaptive_accuracy(inputs), None, detail={"starting_steps": inputs.steps, "max_growth": MAX_GROWTH},
            )
        return fit.apply_generic(rung, self, ctx, inputs)

    def _apply_iterative(self, ctx: SolveContext, inputs: NonlinearInputs):
        """Newton-Krylov: GMRES with AMG for every Newton step, when it saves what misses (memory, else time)."""
        from cadgen._internal.fea import fit

        plan, budget = ctx.plan, ctx.budget
        if plan.solver != "direct":
            return None
        before = self.estimate(ctx, inputs)
        plan.solver = "iterative"
        after = self.estimate(ctx, inputs)
        if before.memory_bytes > budget.memory_bytes:
            saves, why = after.memory_bytes < 0.95 * before.memory_bytes, "fit in memory"
        else:
            saves, why = after.seconds < 0.95 * before.seconds, "finish sooner"
        if not saves:
            plan.solver = "direct"
            return None
        return fit.Step("iterative", f"Solved each Newton step iteratively (Newton-Krylov: GMRES with multigrid) to {why}",
                        None, None, detail={"solver": "iterative", "from_bytes": int(before.memory_bytes),
                                            "to_bytes": int(after.memory_bytes)})

    def symmetric_about(self, plane, inputs: NonlinearInputs, ctx: SolveContext) -> bool:
        return StaticAnalysis().symmetric_about(plane, inputs, ctx)

    def governing(self, result: AnalysisResult):
        """local_refine follows the stress at the last load carried; two passes compare its peak."""
        stress = result.scalars["final"]["von_mises"]
        return stress, float(stress.max())

    # -- solve ---------------------------------------------------------------------------------------

    def _series(self, unit: str, frame_fields: dict[str, list], frames: list[tuple[float, str]]) -> Series:
        """The time series of ``frames`` ((value, label) each), every field in ``frame_fields`` per frame; the last opens."""
        attributes = {spec.name: spec.attribute for spec in self.fields if spec.name in frame_fields}
        return Series(kind="time", unit=unit, default=len(frames) - 1, frames=[
            SeriesFrame(value=value, label=label, attributes=frame_attributes(attributes, i))
            for i, (value, label) in enumerate(frames)
        ])

    def solve(self, ctx: SolveContext, inputs: NonlinearInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea import nonlinear_driver
        from cadgen._internal.fea.supports import driven, path_to_global

        space = ctx.space
        basis = space.basis
        materials = list(ctx.materials)
        timings: dict[str, float] = {}
        warnings: list[str] = []
        started = time.perf_counter()
        planes = symmetry_planes(ctx)
        share = 0.5 ** len(planes)
        external = external_load(space, materials, inputs.surface_loads, inputs.body_accelerations, ctx.ordinal_of, share)
        supports, free = supported(ctx, inputs.fixtures, planes)
        if inputs.model == "hyperelastic":
            from cadgen._internal.fea.hyperelastic import neo_hookean_params

            if any(m.tangent is not None and not m.hyperelastic for m in materials):
                warnings.append("a rubber study treats its metal parts as elastic: their plasticity is left out")
            problem: SolidProblem = _RubberProblem(space, free, external, neo_hookean_params(space, materials))
        else:
            from cadgen._internal.fea.plasticity import j2_params

            problem = _J2Problem(space, free, external, j2_params(space, materials))
        timings["assemble_s"] = time.perf_counter() - started

        solver = plan_value(ctx, "solver", "direct")
        adaptive = bool(plan_value(ctx, "adaptive_steps", False))
        path = path_to_global(nonlinear_driver.solve_path(driven(problem, supports, free), steps=inputs.steps, solver=solver,
                                                          adaptive=adaptive, log=ctx.log), supports)
        timings["solve_s"] = path.seconds
        warnings += path.warnings
        if ctx.log:
            ctx.log(f"followed the load to {path.factor * 100:.4g}% in {len(path.records)} steps, {path.iterations} "
                    f"Newton iterations, in {path.seconds:.1f}s")

        started = time.perf_counter()
        records = path.records
        if not records:  # not even the smallest first step: the unloaded part is the one frame
            zeros = np.zeros(problem.size)
            records = [nonlinear_driver.StepRecord(0.0, 0, zeros, 0.0)]
            problem.history = [(np.zeros(basis.dx.shape), None if inputs.model == "hyperelastic" else np.zeros(basis.dx.shape))]
        picked = pick_frames(len(records), max(max_frames(ctx), 1))
        plastic = inputs.model == "plasticity"
        stress_frames = [nodal(space, problem.history[index][0]) for index in picked]
        displacement_frames = [space.nodal(records[index].u) for index in picked]
        plastic_frames = [nodal(space, 100.0 * problem.history[index][1]) for index in picked] if plastic else []
        final = records[-1]
        internal, _ = problem.evaluate(final.u, tangent=False) if path.records else (np.zeros(problem.size), None)
        residual = internal - final.factor * external
        reactions = [totals(space, residual, dofs) for dofs in supports.per_fixture]
        requested = totals(space, external)
        applied = tuple(float(final.factor * c) for c in requested)
        vm_gauss = float(problem.history[-1][0].max())
        alpha_gauss = float(problem.history[-1][1].max()) * 100.0 if plastic else 0.0
        mesh, scalars, vectors, reactions, (applied, requested) = unfold(
            ctx, planes, {"von_mises": stress_frames, "plastic_strain": plastic_frames},
            {"displacement": displacement_frames}, reactions, applied, requested)
        timings["stress_s"] = time.perf_counter() - started

        factors = [records[i].factor for i in picked]
        frame_fields = {"von_mises": scalars["von_mises"], "displacement": vectors["displacement"]}
        if plastic:
            frame_fields["plastic_strain"] = scalars["plastic_strain"]
        series = self._series("%", frame_fields, [(round(f * 100.0, 6), load_label(f)) for f in factors])
        curve_x = [round(record.factor * 100.0, 6) for record in path.records]
        moved = [float(np.linalg.norm(space.nodal(record.u), axis=1).max()) for record in path.records]
        curves = {"max_displacement_mm": {"x": curve_x, "x_unit": "%", "y": [round(v, 6) for v in moved], "y_unit": "mm"}}
        if plastic:
            curves["max_plastic_strain_percent"] = {
                "x": curve_x, "x_unit": "%", "y_unit": "%",
                "y": [round(100.0 * float(history[1].max()), 6) for history in problem.history[:len(path.records)]],
            }
        how = f"{path.how}, {len(path.records)} {'adaptive ' if adaptive else ''}load steps"
        return self._result(mesh, frame_fields, series, curves, reactions, applied, basis, how, timings, warnings, {
            "path": path, "final": {name: values[-1] for name, values in frame_fields.items()},
            "factor": path.factor if path.records else 0.0, "collapsed": path.collapsed,
            "requested": requested, "model": inputs.model, "adaptive": adaptive, "von_mises_gauss_max": vm_gauss,
            "plastic_gauss_max": alpha_gauss, "frame_factors": factors,
            "analysis_warnings": [collapse_words(path.factor)] if path.collapsed else [],
            "part_refs": None if ctx.assembly is None else [part.ref for part in ctx.assembly.parts],
            "part_materials": part_materials(ctx),
        })

    @staticmethod
    def _result(mesh, frame_fields, series, curves, reactions, applied, basis, how, timings, warnings, scalars):
        """A load or time path's result: frame 0 the fields (and the deformation), the series and curves as given."""
        dof_locations, boundary, tets, element_dofs, vertices = mesh
        return AnalysisResult(
            dof_locations=dof_locations, vertices=vertices, tets=tets, boundary_quadratic=boundary, element_dofs=element_dofs,
            fields={name: values[0] for name, values in frame_fields.items()}, deformation=frame_fields["displacement"][0],
            series=series, frame_fields=frame_fields, curves=curves, reactions=reactions, applied=applied,
            dofs=int(basis.N), solver=how, timings=timings, warnings=warnings, scalars=scalars,
        )

    # -- judging -------------------------------------------------------------------------------------

    def _at(self, result: AnalysisResult) -> dict:
        frame = len(result.series.frames) - 1
        return {"frame": frame, "value": result.series.frames[frame].value, "unit": "%"}

    def _solved(self, ctx: SolveContext, result: AnalysisResult, inputs, stress, displacement, peak_gauss: float):
        """The stress check's numbers for one frame's ``stress`` and ``displacement``: the peak, and the yield of the
        part it is in."""
        import numpy as np

        from cadgen._internal.fea import checks
        from cadgen._internal.fea.analyses.static import peak_face

        moved = np.linalg.norm(displacement, axis=1)
        peak = int(stress.argmax())
        node_moved = int(moved.argmax())
        materials = list(ctx.materials)
        material = materials[0]
        if len(materials) > 1 and ctx.volume.domain is not None:
            element = int(np.flatnonzero((result.element_dofs == peak).any(axis=1))[0])
            material = materials[int(ctx.volume.domain[element])]
        volume = ctx.volume
        fixed = {ctx.ordinal_of[ref] for fixture in inputs.fixtures for ref in fixture.faces}
        extent = result.dof_locations.max(axis=0) - result.dof_locations.min(axis=0)
        return checks.Solved(
            material_name=material.name, yield_MPa=material.yield_strength, peak_MPa=float(stress[peak]),
            peak_gauss_MPa=float(peak_gauss),
            peak_at=tuple(float(c) for c in result.dof_locations[peak]),
            peak_face=peak_face(volume, result, peak, fixed),  # it reads the boundary and node locations
            fixed_faces=tuple(volume.faces[o].ref for o in sorted(fixed) if o in volume.faces),
            max_displacement_mm=float(moved[node_moved]),
            displacement_at=tuple(float(c) for c in result.dof_locations[node_moved]),
            bbox_diagonal_mm=float(np.linalg.norm(extent)), margin=ctx.study.margin if ctx.study is not None else 2.0,
            coarser_peak_MPa=None, part=ctx.part_name, dofs=result.dofs,
        )

    def _displacement_check(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult) -> dict:
        """A displacement check at the last frame."""
        import numpy as np

        from cadgen._internal.fea import checks

        peak = field_peak(ctx, result, np.linalg.norm(result.scalars["final"]["displacement"], axis=1),
                          tuple(check.get("faces", ())), index)
        return checks.displacement_check(peak.value, check["limit_mm"], at=peak.at, ref=peak.ref, faces=peak.faces,
                                         label=check.get("label"))

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: NonlinearInputs) -> dict:
        import numpy as np

        from cadgen._internal.fea import checks

        final = result.scalars["final"]
        if check["kind"] == "stress":
            judged = checks.stress_check(self._solved(ctx, result, inputs, final["von_mises"], final["displacement"],
                                                      result.scalars["von_mises_gauss_max"]), label=check.get("label"))
        elif check["kind"] == "displacement":
            judged = self._displacement_check(check, index, ctx, result)
        else:
            values = final.get("plastic_strain")
            if values is None:
                values = np.zeros(len(result.dof_locations))
            judged = peak_check("plastic_strain", check.get("label") or kinds.PLASTIC_STRAIN.default_label,
                                field_peak(ctx, result, values, (), index), float(check["limit_percent"]), "%")
        judged["at"] = self._at(result)
        # Not linear in the load: the check holds at this load only, so the verdict says its own sentence, no load multiple.
        judged["scaling"] = "none"
        if result.scalars["collapsed"]:
            # A part that collapses does not carry the load: whatever the numbers at the last load it carried, the
            # check fails. A contact path that could not be followed further (`collapse_fails` False) is no such
            # verdict on the part: each check says its own number there, and the stop leads the caption.
            if self.collapse_fails:
                judged["status"] = "fails"
            judged["collapsed_at_percent"] = round(result.scalars["factor"] * 100.0, 4)
        return judged

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: NonlinearInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        scalars = result.scalars
        path = scalars["path"]
        whole = "The assembly" if assembly else "The part"
        found: list[dict] = []
        final_at = xyz(result.dof_locations[int(scalars["final"]["von_mises"].argmax())])
        if scalars["collapsed"]:
            carried = math.sqrt(sum(c * c for c in result.applied))
            found.append(finding(
                "error", "collapses",
                f"{whole} collapses at about {scalars['factor'] * 100:.0f} % of the load: it carries about {carried:.4g} N, "
                "not the full load",
                f"past {scalars['factor'] * 100:.4g} % of the load it finds no equilibrium, or runs away (moves more than "
                f"500 times as far as when it was new for the same added load), even in steps of {path.smallest_step * 100:.3g} %; "
                "the last frame is the last load it carries",
                [{"text": "most stressed at the last load carried", "ref": None, "at": final_at}],
            ))
        for check in check_results:
            if check["kind"] == "plastic_strain" and check["status"] == "fails" and not scalars["collapsed"]:
                found.append(finding(
                    "error", "bends_for_good",
                    f"{whole} bends for good: {check['value']:.3g} % permanent strain, more than the {check['limit']:g} % allowed",
                    f"equivalent plastic strain {check['value']:.4g} % at the full load ({check['label']})",
                    [{"text": "most permanent strain", **check["where"]}],
                ))
        if scalars["model"] == "plasticity" and not scalars["collapsed"]:
            peak = float(scalars["final"]["plastic_strain"].max())
            if peak <= 0:
                found.append(finding("info", "stays_elastic", f"{whole} stays elastic: nothing yields under the full load",
                                     "no quadrature point passed the yield surface; a linear static study answers the same"))
        if path.cuts and not scalars["collapsed"]:
            found.append(finding(
                "info", "load_steps_cut",
                f"Cut the load steps down to {path.smallest_step * 100:.3g} % to get through where the part "
                f"{'yields' if scalars['model'] == 'plasticity' else 'stiffens or softens'}",
                f"{path.cuts} steps did not converge at first and were halved"))
        return by_severity(found)

    # -- what is written -----------------------------------------------------------------------------

    def deformation_scale(self, result: AnalysisResult, bbox_diagonal: float, requested: float | None) -> float | None:
        import numpy as np

        from cadgen._internal.fea.outputs import auto_deformation_scale

        largest = max(float(np.linalg.norm(frame, axis=1).max()) for frame in result.frame_fields["displacement"])
        return requested or auto_deformation_scale(largest, bbox_diagonal)

    def _peaks(self, result: AnalysisResult) -> dict:
        """The summary's peak stress and displacement at the last frame, and where."""
        import numpy as np

        final = result.scalars["final"]
        moved = np.linalg.norm(final["displacement"], axis=1)
        peak = int(final["von_mises"].argmax())
        node_moved = int(moved.argmax())
        return {
            "max_von_mises_MPa": round(float(final["von_mises"][peak]), 4),
            "max_von_mises_gauss_MPa": round(result.scalars["von_mises_gauss_max"], 4),
            "max_von_mises_at_mm": xyz(result.dof_locations[peak]),
            "max_displacement_mm": round(float(moved[node_moved]), 6),
            "max_displacement_at_mm": xyz(result.dof_locations[node_moved]),
        }

    def _path_record(self, result: AnalysisResult, inputs) -> dict:
        """The summary's forces and how the load path was followed."""
        scalars = result.scalars
        path = scalars["path"]
        return {
            "yield_MPa": scalars.get("yield_MPa"),
            "applied_force_N": [round(x, 4) for x in result.applied],
            "requested_force_N": [round(x, 4) for x in scalars["requested"]],
            "reaction_force_N": [round(sum(r[c] for r in result.reactions), 4) for c in range(3)],
            "steps_requested": inputs.steps, "steps": len(path.records), "step_cuts": path.cuts,
            "smallest_step_percent": round(path.smallest_step * 100.0, 6), "newton_iterations": path.iterations,
        }

    @staticmethod
    def _frames_record(result: AnalysisResult) -> dict:
        scalars = result.scalars
        return {"adaptive": scalars["adaptive"], "frames": len(result.series.frames),
                "deformation_scale": scalars.get("deformation_scale")}

    def summary(self, result: AnalysisResult, inputs: NonlinearInputs, check_results: list[dict]) -> dict:
        scalars = result.scalars
        factor = scalars["factor"]
        summary: dict[str, Any] = {
            "status": collapse_words(factor) if scalars["collapsed"] else "Carries the full load",
            "collapsed": scalars["collapsed"],
            "load_percent": round(factor * 100.0, 4),
            "collapse_percent": round(factor * 100.0, 4) if scalars["collapsed"] else None,
            "material_model": scalars["model"],
            **self._peaks(result),
        }
        if scalars["model"] == "plasticity":
            plastic = scalars["final"]["plastic_strain"]
            node = int(plastic.argmax())
            summary.update({
                "max_plastic_strain_percent": round(float(plastic[node]), 6),
                "max_plastic_strain_gauss_percent": round(scalars["plastic_gauss_max"], 6),
                "max_plastic_strain_at_mm": xyz(result.dof_locations[node]),
            })
        summary.update({**self._path_record(result, inputs), **self._frames_record(result)})
        if scalars.get("part_refs"):
            summary["parts"] = with_materials([{"ref": ref} for ref in scalars["part_refs"]], scalars)
        summary["checks"] = check_results
        return summary

    #: Digits a frame field's range keeps (6 when not named).
    _range_digits: ClassVar[dict[str, int]] = {"von_mises": 4, "contact_pressure": 4}

    def field_ranges(self, summary: dict, result: AnalysisResult) -> dict[str, tuple[float, float]]:
        import numpy as np

        ranges = {}
        for name, frames in result.frame_fields.items():
            peak = max(float((np.linalg.norm(f, axis=1) if name == "displacement" else f).max()) for f in frames)
            ranges[name] = (0.0, round(peak, self._range_digits.get(name, 6)))
        return ranges

    def extras_head(self, summary: dict) -> dict:
        return {"load_percent": summary["load_percent"], "collapsed": summary["collapsed"]}

    def extras_assembly(self, summary: dict) -> dict:
        return {"parts": summary.get("parts", [])}

    def study_echo(self, inputs: NonlinearInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        return {**StaticAnalysis().study_echo(inputs, bare), "steps": inputs.steps, "material_model": inputs.model}

    @staticmethod
    def _cut_words(summary: dict) -> str:
        return f", {summary['step_cuts']} cut, smallest {summary['smallest_step_percent']:g} %" if summary["step_cuts"] else ""

    def human_lines(self, summary: dict) -> list[str]:
        head = summary["status"].lower() if summary["collapsed"] else "carries the full load"
        lines = [f"{head}: max von Mises {summary['max_von_mises_MPa']:g} MPa, max displacement "
                 f"{summary['max_displacement_mm']:g} mm"
                 + (f", permanent strain {summary['max_plastic_strain_percent']:.3g} %" if "max_plastic_strain_percent" in summary else "")]
        if summary["collapsed"]:
            lines.append(f"carried {summary['applied_force_N']} N of {summary['requested_force_N']} N")
        lines.append(f"followed in {summary['steps']} {'adaptive ' if summary['adaptive'] else ''}load steps "
                     f"({summary['newton_iterations']} Newton iterations" + self._cut_words(summary)
                     + f"; {summary['material_model']})")
        return lines
