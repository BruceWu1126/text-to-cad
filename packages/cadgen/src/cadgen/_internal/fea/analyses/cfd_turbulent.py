"""Turbulent flow (lite): steady RANS (k-omega SST) through or around the part, and its push on the part.

The study is the laminar flow's (:mod:`.cfd`, spec 5.14), and each inlet (and an
external flow) may add its ``turbulence_intensity`` (a share, 0.05 = 5 %). The
fluid region and its mesh are the laminar flow's (:mod:`..fluid_domain`); the
flow is :mod:`..turbulence`: the Taylor-Hood mean flow with Menter's k-omega SST
eddy viscosity, SUPG-stabilised k and omega, wall functions (Spalding's law, the
first element kept in the log layer), and pseudo-transient continuation from a
Stokes start.

An inlet's ``"developed"`` profile is the settled turbulent flow of a long
straight duct of the opening's shape, solved on the opening by the same model
(axial speed, k and omega), so a pipe fed that way is fully developed from its
inlet; ``"uniform"`` is one speed with k and omega from the intensity.

What comes out, the checks and ``map_to_structure`` are the laminar flow's, the
``wall_shear`` being the wall function's rho u_tau^2. The summary adds the
turbulence: the eddy-to-fluid viscosity ratio (mean and largest), the wall
function's y+ and the march's steps.

Its limits are written into every result: "Steady RANS (k-omega SST), wall
functions, incompressible". Below Re 2000 (internal, the inlet's hydraulic
diameter) or Re 1000 (external, the part's length) the flow is likely laminar:
it still solves, and says the laminar ``cfd`` is the better model there.

Stdlib only at import.
"""
from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import AnalysisResult, SolveContext, finding
from cadgen._internal.fea.analyses.cfd import (
    ASSEMBLE_S, DENSITY, FLOW_WORDS, MESH_S, PASCAL, RE_LIMIT, SPEED, VISCOSITY, CfdAnalysis, CfdInputs, balance_sentence,
    flow_lines, flow_numbers, flow_summary, flow_tracers, fluid_mesh_record, flux_of, limit_findings, mesh_flow, openings_of,
    part_result, reynolds_record, structure_line, structure_summary, wall_fields, wall_triangles,
)

__all__ = ["CfdTurbulentAnalysis", "CfdTurbulentInputs", "LIMITS", "laminar_sentence"]

LIMITS = ("Steady RANS (k-omega SST), wall functions, incompressible",)
#: Turbulence intensity by default: an internal inlet's, and an external free stream's.
INTENSITY = {"internal": 0.05, "external": 0.01}
#: A uniform internal inlet's turbulence length scale, as a share of its hydraulic diameter.
LENGTH_SHARE = 0.07
#: An external free stream's eddy-to-fluid viscosity ratio.
FREE_STREAM_RATIO = 10.0
#: Internal flow: elements across the narrowest opening's hydraulic diameter by default (the wall function
#: puts its distance at half the first element's depth, so the pipe needs a few more than laminar flow).
ACROSS = 10.0
#: The march's CFL (start, growth per step): by default, and with the ladder's continuation rung.
MARCH, MARCH_FAST = (5.0, 2.0), (50.0, 4.0)
#: The cost model, measured on the pipe benchmark: pseudo-time steps expected (an internal flow fed developed
#: starts near its answer; an external one starts from the free stream), fewer with the continuation rung; the
#: matrices factored afresh (a kept factor preconditions the other steps' solves, each about this share of a
#: factorisation); and assembly per step against the laminar solver's.
STEPS = {"internal": 15.0, "external": 40.0}
FAST_SHARE = 0.85
FACTORS, REUSE_SHARE, ASSEMBLIES = 3.0, 0.2, 2.0
#: A Krylov solve here (the rotated walls, the eddy viscosity's spread) costs this many laminar ones.
ITERATIVE_FACTOR = 4.0


def laminar_sentence(value: float, limit: int) -> str:
    """The warning below the turbulent range, the same words everywhere."""
    return (f"Re {value:.0f} is in the laminar range (below Re {limit}): real flow there is likely laminar, so a "
            "turbulence model overstates the pressure drop; the laminar cfd analysis fits it better")


@dataclass(frozen=True)
class CfdTurbulentInputs(CfdInputs):
    #: Each inlet's turbulence intensity (a share), in the inlets' order; an external free stream's alone.
    intensities: tuple[float, ...] = ()
    intensity: float = INTENSITY["external"]


def _intensity(raw: Any, where: str) -> float:
    value = kinds.number(raw, where=where, positive=True)
    if value > 1.0:
        raise ValueError(f"{where}: a share of the mean speed, like 0.05 for 5 %; got {value:g}")
    return value


def _strip(document: dict) -> tuple[dict, tuple[float, ...], float]:
    """The study without its turbulence keys (what the laminar parser reads), and the intensities."""
    flow = document.get("flow")
    if not isinstance(flow, dict):
        return document, (), INTENSITY["external"]
    flow = dict(flow)
    intensity = INTENSITY["external"]
    if "turbulence_intensity" in flow:
        if flow.get("kind", "internal") != "external":
            raise ValueError("flow.turbulence_intensity: an internal flow gives it per inlet "
                             '(flow.inlets[i].turbulence_intensity, like 0.05)')
        intensity = _intensity(flow.pop("turbulence_intensity"), "flow.turbulence_intensity")
    intensities: list[float] = []
    if isinstance(flow.get("inlets"), list):
        inlets = []
        for index, entry in enumerate(flow["inlets"]):
            if isinstance(entry, dict):
                entry = dict(entry)
                intensities.append(_intensity(entry.pop("turbulence_intensity"), f"flow.inlets[{index}].turbulence_intensity")
                                   if "turbulence_intensity" in entry else INTENSITY["internal"])
            inlets.append(entry)
        flow["inlets"] = inlets
    return {**document, "flow": flow}, tuple(intensities), intensity


def wall_traction(space, flow, rho: float):
    """At every node: the wall function's shear stress rho u_tau^2 (MPa), and its traction along the slip."""
    import numpy as np

    shear = np.zeros(space.scalar_count)
    traction = np.zeros((space.scalar_count, 3))
    if len(flow.wall_nodes):
        tau = rho * flow.wall_u_tau ** 2
        slip = flow.wall_slip
        length = np.linalg.norm(slip, axis=1)
        shear[flow.wall_nodes] = tau
        traction[flow.wall_nodes] = tau[:, None] * slip / np.where(length > 0, length, 1.0)[:, None]
    return shear, traction


class CfdTurbulentAnalysis(CfdAnalysis):
    name: ClassVar[str] = "cfd_turbulent"
    word: ClassVar[str] = "Turbulent flow"
    limits: ClassVar[tuple[str, ...]] = LIMITS
    ladder: ClassVar[tuple[str, ...]] = ("fluid_coarsen", "continuation", "iterative")
    extras_word: ClassVar[str] = "turbulent flow"

    def parse(self, document: dict) -> CfdTurbulentInputs:
        stripped, intensities, intensity = _strip(document)
        try:
            base = super().parse(stripped)
        except ValueError as exc:
            words = str(exc)
            if "an inlet takes opening, velocity_m_s and profile" in words:
                words = words.replace("velocity_m_s and profile", "velocity_m_s, profile and turbulence_intensity")
            elif "an external flow takes kind, fluid and velocity_m_s" in words:
                words = words.replace("kind, fluid and velocity_m_s", "kind, fluid, velocity_m_s and turbulence_intensity")
            raise ValueError(words) from None
        values = {f.name: getattr(base, f.name) for f in dataclasses.fields(base)}
        return CfdTurbulentInputs(**values, intensities=intensities, intensity=intensity)

    # -- sizes and the ladder ------------------------------------------------------------------------

    def sizes(self, ctx: SolveContext, setup) -> tuple[float, float]:
        plan = ctx.plan
        if setup.domain.kind == "internal" and not (plan is not None and plan.size_mm):
            wall = setup.wall_mm * 4.0 / ACROSS
            far = plan.fluid_far_size_mm if plan is not None and plan.fluid_far_size_mm else wall
            return wall, max(far, wall)
        return super().sizes(ctx, setup)

    def estimate(self, ctx: SolveContext, inputs: CfdTurbulentInputs):
        plan = ctx.plan
        tets, n, per, memory = self._cost(ctx, self.setup_of(ctx, inputs), ITERATIVE_FACTOR)
        steps = STEPS[inputs.kind] * (FAST_SHARE if "continuation" in plan.taken else 1.0)
        solves = steps + 1.0 if plan.solver == "iterative" else FACTORS + REUSE_SHARE * steps
        return self._estimate(ctx, inputs, n, memory, MESH_S * tets + solves * per + steps * ASSEMBLIES * ASSEMBLE_S * tets)

    def apply(self, rung, ctx: SolveContext, inputs: CfdTurbulentInputs):
        from cadgen._internal.fea import fit

        if rung == "continuation":
            if "continuation" in ctx.plan.taken:
                return None
            return fit.Step(
                "continuation",
                f"Marched to the steady turbulent flow in longer pseudo-time steps (CFL from {MARCH_FAST[0]:g}, growing "
                f"{MARCH_FAST[1]:g}x a step) so it settles in fewer solves", None, None,
                detail={"cfl_start": MARCH_FAST[0], "cfl_growth": MARCH_FAST[1]})
        return super().apply(rung, ctx, inputs)

    # -- solve ---------------------------------------------------------------------------------------

    def solve(self, ctx: SolveContext, inputs: CfdTurbulentInputs) -> AnalysisResult:
        import time

        from cadgen._internal.fea import turbulence

        timings: dict[str, float] = {}
        setup = self.setup_of(ctx, inputs)
        domain = setup.domain
        wall, far = self.sizes(ctx, setup)
        fluid, space, face_of, row_kind = mesh_flow(ctx, domain, wall, far, timings)

        started = time.perf_counter()
        problem, developed = self._problem(space, fluid, face_of, row_kind, inputs, setup)
        timings["inlet_s"] = time.perf_counter() - started
        started = time.perf_counter()
        solver = "iterative" if ctx.plan.solver == "iterative" else "direct"
        march = MARCH_FAST if "continuation" in ctx.plan.taken else MARCH
        flow = turbulence.solve_rans(problem, solver=solver, cfl=march, log=ctx.log)
        timings["flow_s"] = time.perf_counter() - started
        if ctx.log:
            ctx.log(f"turbulent flow: {flow.steps} pseudo-time steps, last change {flow.residual:.1e}")

        started = time.perf_counter()
        post = flow_numbers(space, fluid, face_of, row_kind, flow, inputs)
        self.wall_function_shear(space, row_kind, flow, post, inputs.fluid.density_kg_m3 * DENSITY)
        pressure, shear, speed, reach = wall_fields(ctx, space, fluid, face_of, row_kind, post, inputs.kind, setup.length_mm, wall)
        timings["post_s"] = time.perf_counter() - started
        started = time.perf_counter()
        tracers = flow_tracers(space, row_kind, post, domain, label="Turbulent flow")
        timings["tracers_s"] = time.perf_counter() - started

        analysis_warnings = []
        limit = RE_LIMIT[inputs.kind]
        reynolds = {**reynolds_record(setup, inputs.kind), "turbulent": setup.reynolds >= limit}
        if setup.reynolds < limit:
            analysis_warnings.append(laminar_sentence(setup.reynolds, limit))
        if post["imbalance"] > 0.01:
            analysis_warnings.append(balance_sentence(post["imbalance"]))
        if not flow.converged:
            analysis_warnings += [w for w in flow.warnings if w.startswith("the turbulent flow stopped")]

        result = part_result(
            ctx, {"pressure": pressure, "wall_shear": shear, "speed": speed},
            dofs=int(space.basis.N + flow.pressure_basis.N + 2 * flow.pressure_basis.N),
            solver=f"Taylor-Hood P2/P1 RANS, k-omega SST (P1, SUPG), {'GMRES + multigrid' if solver == 'iterative' else 'direct'}",
            timings={**timings, **{f"flow_{k}": v for k, v in flow.timings.items()}}, warnings=list(flow.warnings),
            scalars={
                "post": post, "reynolds": reynolds, "flow": flow, "wall_mm": wall, "far_mm": far, "developed": developed,
                "speed_reach_mm": reach, "fluid_mesh": fluid_mesh_record(fluid, wall, far),
                "analysis_warnings": analysis_warnings,
                "analysis_extras": {"turbulence": {"model": "k-omega SST", "reynolds": reynolds["value"],
                                                   "kind": inputs.kind, "viscosity_ratio_max": round(flow.nut_ratio[1], 2)},
                                    **({"motion": tracers} if tracers else {})},
            },
        )
        return self._mapped(ctx, inputs, result, pressure)

    def _problem(self, space, fluid, face_of, row_kind, inputs: CfdTurbulentInputs, setup):
        """The RANS problem: inlets (velocity, k and omega), slip sides, outlets, and the walls the law acts on."""
        import numpy as np

        from cadgen._internal.fea import navier_stokes, turbulence

        table = navier_stokes.vector_dofs(space)
        locations = space.dof_locations
        boundary = space.boundary_quadratic
        rho = inputs.fluid.density_kg_m3 * DENSITY
        nu = inputs.fluid.viscosity_Pa_s * VISCOSITY / rho
        values: dict[int, float] = {}
        k_in: dict[int, float] = {}
        w_in: dict[int, float] = {}
        outlets = []
        developed: dict[str, dict] = {}
        opening_of = openings_of(fluid, face_of)
        inlet_rows = np.flatnonzero(row_kind == "inlet")
        outlet_rows = np.flatnonzero(row_kind == "outlet")
        if inputs.kind == "external":
            stream = np.array(inputs.velocity_m_s, dtype=float) * SPEED
            speed = float(np.linalg.norm(stream))
            k0, w0 = turbulence.inlet_turbulence(speed, inputs.intensity, setup.length_mm, nu, viscosity_ratio=FREE_STREAM_RATIO)
            for side in np.unique(opening_of[row_kind == "side"]):
                axis = "xyz".index(side[0])
                for node in np.unique(boundary[(row_kind == "side") & (opening_of == side)]):
                    values[int(table[node, axis])] = 0.0
            for node in np.unique(boundary[inlet_rows]):
                for c in range(3):
                    values[int(table[node, c])] = float(stream[c])
                if node < space.vertices:
                    k_in[int(node)], w_in[int(node)] = k0, w0
            outlets.append((space.facets_of_rows(outlet_rows), 0.0))
            length, free = setup.length_mm, (k0, w0)
        else:
            speed = 0.0
            for inlet, intensity in zip(inputs.inlets, inputs.intensities or (INTENSITY["internal"],) * len(inputs.inlets)):
                rows = np.flatnonzero((row_kind == "inlet") & (opening_of == inlet.opening))
                axis, high = "xyz".index(inlet.opening[0]), inlet.opening.endswith("max")
                inward = -1.0 if high else 1.0
                area = sum(face.area for face in face_of.values() if face.kind == "inlet" and face.opening == inlet.opening)
                perimeter = sum(face.perimeter for face in face_of.values() if face.kind == "inlet" and face.opening == inlet.opening)
                diameter = 4.0 * area / perimeter if perimeter > 0 else setup.length_mm
                mean = inlet.velocity_m_s * SPEED
                speed = max(speed, mean)
                if inlet.profile == "developed":
                    settled = turbulence.developed_turbulent(locations, boundary[rows], axis, mean, nu)
                    shape, ks, ws = settled.w, settled.k, settled.omega
                    developed[inlet.opening] = {"friction_factor": settled.friction_factor, "u_tau_mm_s": settled.u_tau,
                                                "converged": settled.converged}
                else:
                    k0, w0 = turbulence.inlet_turbulence(mean, intensity, LENGTH_SHARE * diameter, nu)
                    nodes = np.unique(boundary[rows])
                    shape = {int(node): mean for node in nodes}
                    ks = {int(node): k0 for node in nodes}
                    ws = {int(node): w0 for node in nodes}
                # The mean speed over the opening's true area: scale the profile so its flux is V A.
                flux = flux_of(space, rows, shape, axis)
                scale = mean * area / flux if flux > 0 else 1.0
                for node, w in shape.items():
                    for c in range(3):
                        values[int(table[node, c])] = inward * scale * w if c == axis else 0.0
                for node in ks:
                    if node < space.vertices:
                        k_in[node], w_in[node] = ks[node], ws[node]
            for outlet in inputs.outlets:
                rows = np.flatnonzero((row_kind == "outlet") & (opening_of == outlet.opening))
                outlets.append((space.facets_of_rows(rows), outlet.pressure_Pa / PASCAL / rho))
            length, free = setup.length_mm, (0.0, 0.0)
        dofs = np.fromiter(values.keys(), dtype=np.int64, count=len(values))
        given = np.fromiter(values.values(), dtype=float, count=len(values))
        inflow = np.fromiter(k_in.keys(), dtype=np.int64, count=len(k_in))
        problem = turbulence.RansProblem(
            space=space, rho=rho, nu=nu, dirichlet=dofs, values=given, wall_rows=np.flatnonzero(row_kind == "wall"),
            inflow_nodes=inflow, inflow_k=np.array([k_in[int(n)] for n in inflow]),
            inflow_omega=np.array([w_in[int(n)] for n in inflow]), outlets=outlets, speed=speed, length=length,
            kind=inputs.kind, free_k=free[0], free_omega=free[1],
            inlet_facets=space.facets_of_rows(inlet_rows) if len(inlet_rows) else None,
            outlet_facets=space.facets_of_rows(outlet_rows) if len(outlet_rows) else None,
        )
        return problem, developed

    def problem_of(self, space, fluid, face_of, row_kind, inputs: CfdTurbulentInputs, setup):
        """The RANS problem on the fluid's space: what :func:`..turbulence.solve_rans` takes."""
        return self._problem(space, fluid, face_of, row_kind, inputs, setup)[0]

    @staticmethod
    def wall_function_shear(space, row_kind, flow, post: dict, rho: float) -> None:
        """Into ``post``: the wall shear, the wall function's rho u_tau^2 along the slip, and the force on the part."""
        import numpy as np

        shear, traction = wall_traction(space, flow, rho)
        post["shear_nodes_Pa"] = shear * PASCAL
        walls = np.flatnonzero(row_kind == "wall")
        if len(walls):
            # Force of the fluid on the part: its pressure along the wall's normal (out of the fluid) and its drag.
            triangles, area, normal = wall_triangles(space, walls)
            mids = triangles[:, 3:]
            pressure = post["pressure_nodes_Pa"] / PASCAL
            per = pressure[mids].mean(axis=1)[:, None] * normal + traction[mids].mean(axis=1)
            post["force_N"] = (area[:, None] * per).sum(axis=0)

    # -- what is written -----------------------------------------------------------------------------

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: CfdTurbulentInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        from cadgen._internal.fea import checks

        found: list[dict] = list(checks.findings(result.solved[0])) if result.solved else []
        reynolds = result.scalars["reynolds"]
        if not reynolds["turbulent"]:
            found.append(finding(
                "warning", "reynolds_laminar_range", laminar_sentence(reynolds["value"], reynolds["limit"]),
                f"Re {reynolds['value']:.0f} from the {'inlet hydraulic diameter' if inputs.kind == 'internal' else 'part length'} "
                f"of {reynolds['length_mm']:.4g} mm"))
        flow = result.scalars["flow"]
        if not flow.converged:
            found.append(finding(
                "warning", "turbulent_unsettled",
                f"The turbulent flow was still changing by {flow.residual:.1e} of its speed a step when it stopped",
                "its numbers are approximate; a finer or a coarser mesh may settle"))
        found += limit_findings(check_results, FLOW_WORDS)
        post = result.scalars["post"]
        if post["imbalance"] > 0.01:
            found.append(finding(
                "warning", "flow_balance", f"The flow in and out differ by {post['imbalance']:.1%}: the flow may not have settled",
                "the solve leaves fluid unaccounted for; a finer mesh should close it"))
        return found

    def summary(self, result: AnalysisResult, inputs: CfdTurbulentInputs, check_results: list[dict]) -> dict:
        flow = result.scalars["flow"]
        developed = result.scalars.get("developed") or {}
        return {
            **flow_summary(result, inputs),
            "turbulence": {
                "model": "k-omega SST", "wall_treatment": "wall functions (Spalding, y+ >= 30)",
                "viscosity_ratio_mean": round(flow.nut_ratio[0], 3), "viscosity_ratio_max": round(flow.nut_ratio[1], 3),
                "wall_yplus": {"min": round(flow.yplus[0], 2), "mean": round(flow.yplus[1], 2), "max": round(flow.yplus[2], 2)},
            },
            "solve": {"steps": flow.steps, "linear_solves": flow.linear_solves, "factorizations": flow.factorizations,
                      "residual": float(f"{flow.residual:.3g}"), "converged": flow.converged, "solver": flow.solver},
            "fluid_mesh": dict(result.scalars["fluid_mesh"]),
            "deformation_scale": result.scalars.get("deformation_scale"),
            **({"developed_inlets": {opening: {"friction_factor": round(entry["friction_factor"], 6)}
                                     for opening, entry in developed.items()}} if developed else {}),
            **structure_summary(result),
            "checks": check_results,
        }

    def study_echo(self, inputs: CfdTurbulentInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        echo = super().study_echo(inputs, bare)
        flow = echo["flow"]
        if inputs.kind == "external":
            flow["turbulence_intensity"] = inputs.intensity
        else:
            for entry, intensity in zip(flow["inlets"], inputs.intensities):
                entry["turbulence_intensity"] = intensity
        return echo

    def human_lines(self, summary: dict) -> list[str]:
        reynolds, solve, turbulence = summary["reynolds"], summary["solve"], summary["turbulence"]
        lines = [
            f"{summary['flow_kind']} turbulent flow of {summary['fluid']['name']}: Re {reynolds['value']:.4g} "
            f"({'turbulent' if reynolds['turbulent'] else 'in the laminar range, below Re ' + str(reynolds['limit'])}), "
            "k-omega SST with wall functions",
            *flow_lines(summary),
            f"eddy viscosity up to {turbulence['viscosity_ratio_max']:.4g}x the fluid's (mean {turbulence['viscosity_ratio_mean']:.3g}x), "
            f"wall y+ {turbulence['wall_yplus']['min']:.3g} to {turbulence['wall_yplus']['max']:.3g}",
            f"solved in {solve['steps']} pseudo-time steps, last change {solve['residual']:.1e}"
            + ("" if solve["converged"] else " (not settled)"),
        ]
        if "max_von_mises_MPa" in summary:
            lines.append(structure_line(summary, "under the flow's pressure"))
        return lines
