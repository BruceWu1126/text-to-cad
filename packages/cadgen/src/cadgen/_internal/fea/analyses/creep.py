"""Creep (lite): the slow, permanent stretch of a part under a load held for a long time, usually hot.

The study (spec 2.1) takes static's ``fixtures`` and ``loads``, held constant
for ``duration_h`` hours, ``steps`` (the time steps it starts with, 20) and an
optional ``temperature_C`` (a note: the creep data must be for the temperature
the part runs at; a material whose creep block says another one is warned
about). The material gives its creep law:

    "creep": {"A": 1e-20, "n": 5, "m": 0, "units": "MPa, hours"}

the Norton power law (with optional time hardening), creep strain rate
A σ^n t^m, σ the von Mises stress in MPa and t in hours (or seconds). A study
whose materials have no creep block is refused with a sentence asking for one;
in an assembly a part without one stays elastic.

The load goes on at once (the elastic answer at 0 h), then the creep strain
grows at every quadrature point by backward Euler with the consistent tangent
(:mod:`..creep_law`), Newton's method on the whole part each time step
(:mod:`..nonlinear_driver`). Each step's local error (backward against forward
Euler, as a share of the stress) is kept under :data:`TOLERANCE` by cutting the
step; with the ladder's ``adaptive_steps`` the steps also grow past the
starting size where the creep is steady. Where the stress is held by a fixed
stretch rather than a load, it relaxes, and the results show it: the stress
frames fall, the summary gives the peak at the start and at the end, and the
stress curve against time is written.

What it writes: a series of time frames (``kind: "time"``, unit ``h``, at most
``Budget.max_frames``, spread evenly in time from 0 h, the last the default),
each with the von Mises stress, the displacement and the equivalent creep
strain in percent. Checks: ``creep_strain`` (``limit_percent``; by default 1 %),
``stress`` (its largest peak over the time, against yield) and ``displacement``
(at the end). Stdlib only at import.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import (
    AnalysisResult, FieldSpec, SolveContext, by_severity, finding, max_frames, plan_value, xyz,
)
from cadgen._internal.fea.analyses.nonlinear import (
    NonlinearAnalysis, SolidProblem, external_load, field_peak, figure, material_specs, nodal, parse_steps, peak_check,
    refuse_stress, supported, symmetry_planes, totals, unfold, view_checks,
)
from cadgen._internal.fea.analyses.static import StaticAnalysis, StaticInputs
from cadgen._internal.fea.timestep import stopped_check, stopped_finding, stopped_sentence, time_limit

if TYPE_CHECKING:
    import numpy as np

__all__ = ["CreepAnalysis", "CreepInputs", "DEFAULT_CREEP_LIMIT", "DEFAULT_STEPS", "LIMITS", "TOLERANCE", "hours_label"]

#: Time steps a study starts with when it names none.
DEFAULT_STEPS = 20
#: The most starting steps a study may ask for.
MAX_STEPS = 1000
#: A time step whose local error passes this share of the stress is cut in half and taken again.
TOLERANCE = 2e-3
#: Points under this share of the peak stress do not drive the step size (their relative error means little).
ERROR_FLOOR = 0.05
#: A step is halved at most this many times below the starting step.
MAX_HALVINGS = 16
#: With adaptive steps, a step grows to at most this many times the starting one.
MAX_GROWTH = 64
#: The creep strain a creep_strain check allows when the study names no check, in percent.
DEFAULT_CREEP_LIMIT = 1.0
#: Newton iterations a time step takes on average, and steps taken per starting step (cuts included).
NEWTON_PER_STEP, STEPS_PER_STEP = 3.0, 1.5
#: Quadrature points per quadratic tet, for the state's memory.
POINTS_PER_TET = 11

#: What this lite solver leaves out, written into its output, its verdict's Details and the skill.
LIMITS = (
    "Creep is Norton's power law (steady, secondary creep, with optional time hardening): no tertiary creep, "
    "no rupture and no damage, so a part near the end of its creep life reads safer than it is",
    "The creep data hold at one temperature: the part is taken to be at that temperature throughout",
    "Isotropic, small strain: creep keeps the volume and follows the von Mises stress; rotations stay small",
    "Loads and fixtures are held constant for the whole time; nothing is unloaded or cycled",
)


def hours_label(hours: float) -> str:
    """A time as the scrubber says it: "0 h", "2.5 h", "250 h", "10,000 h"."""
    if hours >= 1000.0:
        return f"{hours:,.0f} h"
    if hours >= 10.0:
        return f"{hours:.0f} h"
    return f"{figure(hours)} h"


@dataclass(frozen=True)
class CreepInputs(StaticInputs):
    duration_h: float = 1.0
    steps: int = DEFAULT_STEPS
    temperature_C: float | None = None


def _creep_materials(document: dict) -> list[tuple[str, Any]]:
    from cadgen._internal.fea.materials import material_from_spec

    return [(where, material_from_spec(spec)) for where, spec in material_specs(document)]


def _require_creep(document: dict) -> list:
    materials = _creep_materials(document)
    if not any(material.creep for _, material in materials):
        name = materials[0][1].name if materials else "steel"
        raise ValueError(
            "material.creep: a creep study needs the material's creep law, the Norton constants A and n from the "
            f'grade\'s creep data at the temperature it runs at, like {{"name": "{name}", "creep": {{"A": 1e-20, "n": 5, '
            '"m": 0, "units": "MPa, hours"}}}} (creep strain rate = A σ^n, σ in MPa); cadgen has no creep table of its own'
        )
    return materials


def _duration(document: dict) -> float:
    if "duration_h" not in document:
        raise ValueError("duration_h: how long the load is held, in hours, like 10000")
    return kinds.number(document["duration_h"], where="duration_h", positive=True)


# -- the problem the driver solves -----------------------------------------------------------------------


class _CreepProblem(SolidProblem):
    """The part over one time step: the free DOF, the constant load, the committed creep state."""

    def __init__(self, space, free, external, params):
        from cadgen._internal.fea.creep_law import CreepState

        super().__init__(space, free, external)
        self.params = params
        self.state = CreepState.zeros(tuple(self.ops.dx.shape))
        self.t0 = self.t1 = 0.0

    def step(self, t0: float, t1: float) -> None:
        self.t0, self.t1 = t0, t1

    def update(self, u, tangent):
        from cadgen._internal.fea.creep_law import norton_update
        from cadgen._internal.fea.plasticity import mean_dilatation_strain

        return norton_update(mean_dilatation_strain(self.ops.gradient(u), self.ops), self.state, self.params, self.t0, self.t1,
                             tangent=tangent)

    def evaluate(self, u, *, tangent):
        return self.mean_dilatation(self.update(u, tangent), tangent)  # as J2

    def error(self, update) -> float:
        """The step's local error: the largest over the points that carry at least ERROR_FLOOR of the peak stress."""
        import numpy as np

        stress = np.maximum(update.state.von_mises, self.state.von_mises)
        peak = float(stress.max())
        if peak <= 0:
            return 0.0
        return float(update.error[stress >= ERROR_FLOOR * peak].max())

    def commit(self, update) -> None:
        self.state = update.state


def _newton(problem: _CreepProblem, linear, u0: "np.ndarray", max_iterations: int, tolerance: float):
    """Newton's method on f_int(u) = f_ext over the free DOF from ``u0`` (its held DOF kept): (u or None, iterations).

    Converged when the free residual is ``tolerance`` of the force scale: the larger of the load and the internal
    force over every DOF (reactions included, so a part held at a fixed stretch with no load has a scale too). A
    correction that raises the residual is halved, up to six times."""
    import numpy as np

    free = problem.free
    u = u0.copy()
    linear.new_step()
    applied = float(np.linalg.norm(problem.external))
    for iteration in range(max_iterations + 1):
        internal, K = problem.evaluate(u, tangent=True)
        if not np.all(np.isfinite(internal)):
            return None, iteration
        r = problem.external[free] - internal[free]
        norm = float(np.linalg.norm(r))
        if norm <= tolerance * max(applied, float(np.linalg.norm(internal)), 1e-300):
            return u, iteration
        if iteration == max_iterations:
            break
        du = linear.solve(K[free][:, free].tocsr(), r)
        if du is None or not np.all(np.isfinite(du)):
            return None, iteration
        alpha = 1.0
        for _ in range(7):
            trial = u.copy()
            trial[free] += alpha * du
            internal_trial, _ = problem.evaluate(trial, tangent=False)
            if np.all(np.isfinite(internal_trial)) and np.linalg.norm(problem.external[free] - internal_trial[free]) < norm:
                break
            alpha *= 0.5
        u = trial
    return None, max_iterations


@dataclass
class _Path:
    """What the time march did."""

    times: list[float]
    seconds: float
    steps: int
    rejected: int
    cuts: int
    iterations: int
    smallest_step: float
    largest_step: float
    stopped: bool
    how: str
    #: It ran past its time limit and stopped where it had got to (``timestep.time_limit``).
    timed_out: bool = False


def _march(problem: _CreepProblem, duration: float, steps: int, *, solver: str, adaptive: bool, keep: int,
           log: Callable[[str], None] | None, u0: "np.ndarray | None" = None, limit_s: float | None = None):
    """The elastic answer at 0 h, then backward Euler time steps to ``duration``. Returns the path, the frames kept
    (t, u, von Mises at the points, creep strain at the points), and each accepted step's curve point.

    ``u0``: a starting displacement whose held DOF keep their values (a stretch held fixed); zeros by default.
    ``limit_s``: seconds of wall time after which it stops after the step it is on (``timed_out``), its last state the
    last frame."""
    import time

    import numpy as np

    from cadgen._internal.fea import nonlinear_driver
    from cadgen._internal.fea.timestep import Clock

    started = time.perf_counter()
    linear = nonlinear_driver.LinearSolver(problem, "iterative" if solver in ("iterative", "matrix_free") else "direct")
    max_iterations, tolerance = nonlinear_driver.MAX_ITERATIONS, nonlinear_driver.TOLERANCE

    def solve(u, t0, t1):
        problem.step(t0, t1)
        return _newton(problem, linear, u, max_iterations, tolerance)

    u = np.zeros(problem.size) if u0 is None else np.array(u0, dtype=float)
    u, iterations = solve(u, 0.0, 0.0)
    if u is None:  # an elastic part Newton cannot hold: a singular tangent
        raise ValueError("the part is not held: the fixtures leave it free to move; fix at least one face")
    update = problem.update(u, False)
    problem.commit(update)
    targets = [duration * k / max(keep - 1, 1) for k in range(1, max(keep, 2))]
    frames = [(0.0, u.copy(), update.state.von_mises.copy(), update.state.equivalent.copy())]
    curve = [(0.0, None, float(update.state.von_mises.max()), 0.0)]
    base = duration / steps
    smallest_allowed = base / 2 ** MAX_HALVINGS
    dt, t = base, 0.0
    accepted = rejected = cuts = 0
    smallest, largest = math.inf, 0.0
    stopped = timed_out = False
    clock = Clock(None, limit_s)
    next_target = 0
    while duration - t > 1e-9 * duration:
        dt = min(dt, duration - t)
        if duration - (t + dt) < 1e-6 * dt:  # do not leave a sliver at the end
            dt = duration - t
        found, taken = solve(u, t, t + dt)
        iterations += taken
        if found is None:
            if dt <= smallest_allowed * (1.0 + 1e-9):
                stopped = True
                break
            dt = max(dt / 2.0, smallest_allowed)
            cuts += 1
            if log:
                log(f"time step to {t + dt * 2:.4g} h did not converge; cutting it to {dt:.4g} h")
            continue
        update = problem.update(found, False)
        error = problem.error(update)
        if error > TOLERANCE and dt > smallest_allowed * (1.0 + 1e-9):
            dt = max(dt / 2.0, smallest_allowed)
            rejected += 1
            continue
        problem.commit(update)
        u = found
        t = t + dt
        accepted += 1
        smallest, largest = min(smallest, dt), max(largest, dt)
        curve.append((t, None, float(update.state.von_mises.max()), float(update.state.equivalent.max())))
        if next_target < len(targets) and t >= targets[next_target] * (1.0 - 1e-9):
            frames.append((t, u.copy(), update.state.von_mises.copy(), update.state.equivalent.copy()))
            while next_target < len(targets) and t >= targets[next_target] * (1.0 - 1e-9):
                next_target += 1
        if log:
            log(f"{hours_label(t)} in {taken} Newton iterations (step {dt:.4g} h, error {error:.2g})")
        grow = 2.0 if error <= 0 else min(2.0, max(1.0, 0.9 * math.sqrt(TOLERANCE / error)))
        dt = dt * grow
        dt = min(dt, MAX_GROWTH * base) if adaptive else min(dt, base)
        if clock.over() and duration - t > 1e-9 * duration:
            timed_out = True
            break
    problem.step(t, t)  # what is evaluated after the march is the committed state, with no more time to creep
    if frames[-1][0] < t:  # a march that stopped early: its last converged state is the last frame
        frames.append((t, u.copy(), problem.state.von_mises.copy(), problem.state.equivalent.copy()))
    path = _Path(times=[c[0] for c in curve], seconds=time.perf_counter() - started, steps=accepted, rejected=rejected,
                 cuts=cuts, iterations=iterations, smallest_step=0.0 if smallest is math.inf else smallest,
                 largest_step=largest, stopped=stopped, how=linear.how, timed_out=timed_out)
    return path, frames, curve


class CreepAnalysis(NonlinearAnalysis):
    name: ClassVar[str] = "creep"
    word: ClassVar[str] = "Creep"
    limits: ClassVar[tuple[str, ...]] = LIMITS
    study_keys: ClassVar[frozenset[str]] = frozenset({"fixtures", "loads", "duration_h", "steps", "temperature_C"})
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("von_mises", "_VON_MISES", "von Mises stress", "MPa", per_frame=True),
        FieldSpec("displacement", "_DISPLACEMENT", "displacement", "mm", 3, 1000.0, per_frame=True),
        FieldSpec("creep_strain", "_CREEP_STRAIN", "creep strain", "%", per_frame=True),
    )
    checks: ClassVar[tuple] = (kinds.CREEP_STRAIN, kinds.STRESS, kinds.DISPLACEMENT)
    default_checks: ClassVar[tuple[dict, ...]] = ({"kind": "creep_strain", "limit_percent": DEFAULT_CREEP_LIMIT},)
    # No load_scale (nonlinear's drives): creep is not proportional to the load (σ^n), so a load multiple says nothing.
    default_controls: ClassVar[dict[str, dict]] = {
        "frame": {"drives": "frame", "type": "number", "min": 0.0, "max": None},
        "field": {"drives": "field", "type": "enum", "options": ["von_mises", "displacement", "creep_strain"]},
        "deformation": {"drives": "deformation", "type": "number", "min": 0.0, "max": None},
    }
    extras_word: ClassVar[str] = "creep"

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> CreepInputs:
        static = StaticAnalysis().parse(document)
        materials = _require_creep(document)
        duration = _duration(document)
        steps = parse_steps(document, DEFAULT_STEPS, MAX_STEPS, "time")
        temperature = None
        if "temperature_C" in document:
            temperature = kinds.number(document["temperature_C"], where="temperature_C")
        checks = view_checks(document)
        if isinstance(checks, list) and any(material.yield_strength is None for _, material in materials):
            refuse_stress(checks, "; give the material's yield_MPa, or check its creep strain")
        return CreepInputs(
            static.face_refs, static.anchor_refs, True, fixtures=static.fixtures, loads=static.loads,
            material_needs=static.material_needs, duration_h=duration, steps=steps, temperature_C=temperature,
        )

    # -- the ladder (fit.py drives these) -------------------------------------------------------------

    def _load_steps(self, ctx: SolveContext, inputs: CreepInputs) -> float:
        """Time steps the plan expects: the starting count with its cuts, fewer where adaptive steps let them grow."""
        steps = float(inputs.steps) * STEPS_PER_STEP
        if getattr(ctx.plan, "adaptive_steps", False):
            steps = max(4.0, steps / 2.0)
        return steps

    def estimate(self, ctx: SolveContext, inputs: CreepInputs):
        """As nonlinear's (a Newton solve per step on the tangent), with creep's steps and Newton iterations a step."""
        from cadgen._internal.fea import fit
        from cadgen._internal.fea.analyses import nonlinear

        cost = super().estimate(ctx, inputs)
        scale = NEWTON_PER_STEP / nonlinear.NEWTON_PER_STEP
        return fit.Estimate(dofs=cost.dofs, memory_bytes=cost.memory_bytes, seconds=float(cost.seconds * scale))

    def apply(self, rung, ctx: SolveContext, inputs: CreepInputs):
        from cadgen._internal.fea import fit

        if rung == "adaptive_steps":
            if ctx.plan.adaptive_steps:
                return None
            ctx.plan.adaptive_steps = True
            return fit.Step(
                "adaptive_steps",
                f"Let the time steps grow (up to {MAX_GROWTH}× the {inputs.steps} starting ones) where the creep is steady, "
                f"still cutting any step whose error passes {TOLERANCE * 100:g} % of the stress, to fit the time target",
                None, None, detail={"starting_steps": inputs.steps, "max_growth": MAX_GROWTH, "tolerance": TOLERANCE},
            )
        return super().apply(rung, ctx, inputs)

    def governing(self, result: AnalysisResult):
        """local_refine follows the largest stress over the time; two passes compare its peak."""
        stress = result.frame_fields["von_mises"][result.scalars["peak_frame"]]
        return stress, float(stress.max())

    # -- solve ---------------------------------------------------------------------------------------

    def solve(self, ctx: SolveContext, inputs: CreepInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea.creep_law import creep_params
        from cadgen._internal.fea.supports import Turned

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
        if not any(m.creep for m in materials):
            raise ValueError("material.creep: no part of this study has a creep law; add a creep block to its material")
        if len(materials) > 1 and not all(m.creep for m in materials):
            warnings.append("parts with no creep block in their material stay elastic: they do not creep")
        problem = _CreepProblem(space, free, external, creep_params(space, materials))
        timings["assemble_s"] = time.perf_counter() - started

        adaptive = bool(plan_value(ctx, "adaptive_steps", False))
        marched = Turned(problem, supports, free) if supports.turned else problem
        path, frames, curve = _march(marched, inputs.duration_h, inputs.steps, solver=plan_value(ctx, "solver", "direct"),
                                     adaptive=adaptive, keep=max(max_frames(ctx), 2), log=ctx.log, limit_s=time_limit(ctx))
        if supports.turned:  # a sloped or curved roller: the march's displacements back in global axes
            frames = [(t, supports.global_vector(u), vm, eq) for t, u, vm, eq in frames]
        timings["solve_s"] = path.seconds
        reached = path.times[-1]
        if ctx.log:
            ctx.log(f"followed the creep to {hours_label(reached)} in {path.steps} time steps ({path.rejected} cut for "
                    f"accuracy), {path.iterations} Newton iterations, in {path.seconds:.1f}s")
        data_temperatures = {m.creep.get("temperature_C") for m in materials if m.creep and "temperature_C" in m.creep}
        if inputs.temperature_C is not None:
            for value in sorted(data_temperatures):
                if abs(value - inputs.temperature_C) > 0.5:
                    warnings.append(f"the creep data are for {value:g} °C and the study runs at {inputs.temperature_C:g} °C; "
                                    "creep changes steeply with temperature, so use data for the temperature it runs at")

        started = time.perf_counter()
        gauss_peaks = [float(vm_q.max()) for _, _, vm_q, _ in frames]
        internal, _ = problem.evaluate(frames[-1][1], tangent=False)
        reactions = [totals(space, internal - external, dofs) for dofs in supports.per_fixture]
        creep_gauss = float(problem.state.equivalent.max()) * 100.0
        mesh, scalars, vectors, reactions, (applied,) = unfold(
            ctx, planes, {"von_mises": [nodal(space, vm_q) for _, _, vm_q, _ in frames],
                          "creep_strain": [nodal(space, 100.0 * p_q) for _, _, _, p_q in frames]},
            {"displacement": [space.nodal(u) for _, u, _, _ in frames]}, reactions, totals(space, external))
        timings["stress_s"] = time.perf_counter() - started

        times = [t for t, _, _, _ in frames]
        frame_fields = {"von_mises": scalars["von_mises"], "displacement": vectors["displacement"],
                        "creep_strain": scalars["creep_strain"]}
        series = self._series("h", frame_fields, [(round(t, 6), hours_label(t)) for t in times])
        peak_frame = int(np.argmax([float(values.max()) for values in frame_fields["von_mises"]]))
        curve_x = [round(t, 6) for t, _, _, _ in curve]
        curves = {
            "max_von_mises_MPa": {"x": curve_x, "x_unit": "h", "y": [round(c[2], 6) for c in curve], "y_unit": "MPa"},
            "max_creep_strain_percent": {"x": curve_x, "x_unit": "h", "y": [round(100.0 * c[3], 8) for c in curve], "y_unit": "%"},
            "max_displacement_mm": {"x": [round(t, 6) for t in times], "x_unit": "h", "y_unit": "mm",
                                    "y": [round(float(np.linalg.norm(d, axis=1).max()), 6) for d in frame_fields["displacement"]]},
        }
        analysis_warnings = []
        if path.stopped:
            analysis_warnings.append(f"Creep runs away after about {hours_label(reached)}")
        if path.timed_out:
            analysis_warnings.append(stopped_sentence(hours_label(reached), hours_label(inputs.duration_h), time_limit(ctx)))
        how = f"{path.how}, {path.steps} {'adaptive ' if adaptive else ''}backward Euler time steps"
        return self._result(mesh, frame_fields, series, curves, reactions, applied, basis, how, timings, warnings, {
            "path": path, "final": {name: values[-1] for name, values in frame_fields.items()}, "reached_h": reached,
            "stopped": path.stopped, "adaptive": adaptive, "timed_out": path.timed_out,
            "von_mises_gauss_peaks": gauss_peaks, "creep_gauss_max": creep_gauss, "peak_frame": peak_frame,
            "frame_times": times, "analysis_warnings": analysis_warnings, "data_temperatures": sorted(data_temperatures),
            "part_refs": None if ctx.assembly is None else [part.ref for part in ctx.assembly.parts],
        })

    # -- judging -------------------------------------------------------------------------------------

    def _at(self, result: AnalysisResult, frame: int | None = None) -> dict:
        frame = len(result.series.frames) - 1 if frame is None else frame
        return {"frame": frame, "value": result.series.frames[frame].value, "unit": "h"}

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: CreepInputs) -> dict:
        from cadgen._internal.fea import checks

        if check["kind"] == "stress":
            frame = result.scalars["peak_frame"]
            fields = result.frame_fields
            judged = checks.stress_check(self._solved(ctx, result, inputs, fields["von_mises"][frame], fields["displacement"][frame],
                                                      result.scalars["von_mises_gauss_peaks"][frame]), label=check.get("label"))
            judged["at"] = self._at(result, frame)
        elif check["kind"] == "displacement":
            judged = self._displacement_check(check, index, ctx, result)
            judged["at"] = self._at(result)
        else:
            judged = peak_check("creep_strain", check.get("label") or kinds.CREEP_STRAIN.default_label,
                                field_peak(ctx, result, result.scalars["final"]["creep_strain"], (), index),
                                float(check["limit_percent"]), "%")
            judged["duration_h"] = round(result.scalars["reached_h"], 6)
            judged["at"] = self._at(result)
        if result.scalars["stopped"]:
            # The creep ran away before the whole time: whatever the numbers when it stopped, the check fails.
            judged["status"] = "fails"
            judged["stopped_at_h"] = round(result.scalars["reached_h"], 6)
        elif result.scalars.get("timed_out"):
            # The march stopped at its time limit: a pass covers only the hours it reached.
            stopped_check(judged, hours_label(result.scalars["reached_h"]))
        # Creep grows with a power of the stress (and with time): no check of it is a multiple of the load.
        judged["scaling"] = "none"
        return judged

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: CreepInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        scalars = result.scalars
        path = scalars["path"]
        whole = "The assembly" if assembly else "The part"
        found: list[dict] = []
        if scalars.get("timed_out"):
            found += [stopped_finding(sentence) for sentence in scalars["analysis_warnings"]
                      if sentence.startswith("Stopped at its time limit")]
        if scalars["stopped"]:
            found.append(finding(
                "error", "creep_runs_away",
                f"{whole}'s creep runs away after about {hours_label(scalars['reached_h'])}, before the "
                f"{hours_label(inputs.duration_h)} asked",
                "past that time Newton's method finds no equilibrium even in the smallest time step; the last frame is the last "
                "state found", []))
        for check in check_results:
            if check["kind"] == "creep_strain" and check["status"] == "fails" and not scalars["stopped"]:
                found.append(finding(
                    "error", "creeps_too_far",
                    f"{whole} creeps too far: {check['value']:.3g} % creep strain after {hours_label(check['duration_h'])}, "
                    f"more than the {check['limit']:g} % allowed",
                    f"equivalent creep strain {check['value']:.4g} % at the end of the hold ({check['label']})",
                    [{"text": "most creep strain", **check["where"]}],
                ))
        start, end = result.frame_fields["von_mises"][0].max(), result.frame_fields["von_mises"][-1].max()
        if start > 0 and end < 0.98 * start:
            found.append(finding(
                "info", "stress_relaxes",
                f"The peak stress relaxes from {start:.4g} MPa to {end:.4g} MPa over the hold ({(1 - end / start) * 100:.0f} % less)",
                "creep lets the most stressed regions shed load to the rest; the strength check judges the largest peak over the "
                "hold, the instant the load goes on", []))
        if path.rejected:
            found.append(finding(
                "info", "time_steps_cut",
                f"Cut {path.rejected} time steps to keep each step's error under {TOLERANCE * 100:g} % of the stress",
                f"the smallest step taken was {path.smallest_step:.3g} h", []))
        return by_severity(found)

    # -- what is written -----------------------------------------------------------------------------

    def summary(self, result: AnalysisResult, inputs: CreepInputs, check_results: list[dict]) -> dict:
        import numpy as np

        scalars = result.scalars
        path = scalars["path"]
        final = scalars["final"]
        moved = np.linalg.norm(final["displacement"], axis=1)
        peak = int(final["von_mises"].argmax())
        node_moved = int(moved.argmax())
        creep = final["creep_strain"]
        node = int(creep.argmax())
        start = float(result.frame_fields["von_mises"][0].max())
        end = float(final["von_mises"][peak])
        reached = scalars["reached_h"]
        summary: dict[str, Any] = {
            "status": (f"Creep runs away after about {hours_label(reached)}" if scalars["stopped"]
                       else f"Stopped at its time limit after {hours_label(reached)}" if scalars.get("timed_out")
                       else f"Held for {hours_label(inputs.duration_h)}"),
            **({"timed_out": True} if scalars.get("timed_out") else {}),
            "stopped": scalars["stopped"], "duration_h": round(inputs.duration_h, 6), "reached_h": round(reached, 6),
            "temperature_C": inputs.temperature_C, "creep_data_temperature_C": scalars["data_temperatures"] or None,
            "max_creep_strain_percent": round(float(creep[node]), 6),
            "max_creep_strain_gauss_percent": round(scalars["creep_gauss_max"], 6),
            "max_creep_strain_at_mm": xyz(result.dof_locations[node]),
            "initial_max_von_mises_MPa": round(start, 4),
            "max_von_mises_MPa": round(end, 4),
            "max_von_mises_gauss_MPa": round(scalars["von_mises_gauss_peaks"][-1], 4),
            "max_von_mises_at_mm": xyz(result.dof_locations[peak]),
            "relaxation_percent": round((1.0 - end / start) * 100.0, 4) if start > 0 else 0.0,
            "initial_max_displacement_mm": round(float(np.linalg.norm(result.frame_fields["displacement"][0], axis=1).max()), 6),
            "max_displacement_mm": round(float(moved[node_moved]), 6),
            "max_displacement_at_mm": xyz(result.dof_locations[node_moved]),
            "applied_force_N": [round(x, 4) for x in result.applied],
            "reaction_force_N": [round(sum(r[c] for r in result.reactions), 4) for c in range(3)],
            "steps_requested": inputs.steps, "steps": path.steps, "steps_cut": path.rejected + path.cuts,
            "smallest_step_h": round(path.smallest_step, 9), "largest_step_h": round(path.largest_step, 6),
            "newton_iterations": path.iterations,
            **self._frames_record(result),
        }
        if scalars.get("part_refs"):
            summary["parts"] = [{"ref": ref} for ref in scalars["part_refs"]]
        summary["checks"] = check_results
        return summary

    def extras_head(self, summary: dict) -> dict:
        return {"duration_h": summary["duration_h"], "reached_h": summary["reached_h"], "stopped": summary["stopped"]}

    def study_echo(self, inputs: CreepInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        echo = {**StaticAnalysis().study_echo(inputs, bare), "duration_h": inputs.duration_h, "steps": inputs.steps}
        if inputs.temperature_C is not None:
            echo["temperature_C"] = inputs.temperature_C
        return echo

    def human_lines(self, summary: dict) -> list[str]:
        head = summary["status"].lower()
        lines = [f"{head}: creep strain {summary['max_creep_strain_percent']:.3g} %, max displacement "
                 f"{summary['max_displacement_mm']:g} mm (from {summary['initial_max_displacement_mm']:g} mm at 0 h)",
                 f"peak stress {summary['initial_max_von_mises_MPa']:g} MPa when loaded, {summary['max_von_mises_MPa']:g} MPa "
                 f"at the end ({summary['relaxation_percent']:.0f} % relaxed)"]
        lines.append(f"followed in {summary['steps']} {'adaptive ' if summary['adaptive'] else ''}time steps "
                     f"({summary['newton_iterations']} Newton iterations"
                     + (f", {summary['steps_cut']} cut, smallest {summary['smallest_step_h']:.3g} h" if summary["steps_cut"] else "")
                     + ")")
        return lines
