"""Fast gas flow (lite): steady compressible flow of an ideal gas through or around the part, and its push on the part.

The study is a flow study (spec 5.14) for a gas fast enough that its density
changes with its speed (past about Mach 0.3, where the incompressible ``cfd``
stops being right):

- ``flow.kind`` ``internal`` (through the part): inlets on sides of the part's
  bounding box, each held by its total pressure (``total_pressure_Pa``,
  absolute) or its mass flow (``mass_flow_kg_s``), with a total temperature
  (``total_temperature_C``, 20 by default); outlets by their static pressure
  (``pressure_Pa``, absolute, 101325 by default).
- ``flow.kind`` ``external`` (around it): a free stream (``velocity_m_s`` as
  [x, y, z], at ``pressure_Pa`` and ``temperature_C``).
- ``flow.gas``: ``air`` (gamma 1.4, R 287 J/kg K, Sutherland viscosity) by
  default, ``nitrogen``, ``helium``, ``co2``, or an object.
- ``flow.walls``: ``slip`` (an inviscid core: frictionless walls), ``no_slip``
  (laminar walls, Sutherland's viscosity) or ``auto`` (the default: laminar
  walls in the laminar range of the Reynolds number, else the inviscid core).

The fluid region and its mesh are the laminar flow's (:mod:`..fluid_domain`);
the solve is :mod:`..compressible`: a pressure-based compressible solver on the
Taylor-Hood pair, consistent SUPG, shock capturing, pseudo-time marched, with
continuation in the pressure difference for a choked nozzle.

What comes out is on the part's own wetted surface: the wall ``pressure`` (Pa
above the first outlet's static pressure, the ambient a part's outside sees),
the ``mach`` number, the ``speed`` near the wall (m/s, the fastest flow within a
passage width: :func:`.cfd.speed_to_part`) and the gas ``temperature`` (°C). The summary carries the
mass flow, the fastest Mach number and speed, the pressure ratio (the inlets'
total pressure over the outlet's static), the pressure drop, whether the flow
choked and where a shock stands. Checks ``pressure_drop``, ``velocity`` and
``mach``; with ``map_to_structure`` the wall pressure is applied to a static
solve of the part as for ``cfd``.

Its limits are written into every result. Past the Mach number its benchmark
reached (a normal shock standing in a nozzle's diverging part, Mach 1.8 just
ahead of it as captured), or with a supersonic outlet, it still solves and warns, in a
sentence and as data (``analysis.mach``: value, limit, regime).

Stdlib only at import.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import AnalysisResult, FieldSpec, Inputs, SolveContext, finding, xyz
from cadgen._internal.fea.analyses.cfd import (
    ASSEMBLE_S, FLOW_WORDS, MESH_S, ONE_PART, RE_LIMIT, CfdAnalysis, FlowSetup, balance_sentence, cached_setup, distinct_openings,
    flow_entries, flow_tracers, fluid_mesh_record, limit_findings, mesh_flow, openings_of, parse_opening, part_result,
    speed_reach, speed_to_part, structure_echo, structure_line, structure_ranges, structure_summary, surface_integral,
    to_part, vector3, wall_triangles,
)

__all__ = ["CfdCompressibleAnalysis", "CfdCompressibleInputs", "GASES", "GasInletSpec", "LIMITS", "MACH_CHECKED", "mach_sentence"]

LIMITS = ("Steady, ideal gas, adiabatic (one total temperature); an inviscid core with frictionless walls past the "
          "laminar range, laminar walls in it; no turbulence model; shocks are captured over a few elements.",)
#: Gases by name: gamma, R (J/kg K), Sutherland's mu_ref (Pa s) at T_ref (K) and S (K) (White, Viscous Fluid Flow).
GASES = {
    "air": (1.4, 287.0, 1.716e-5, 273.15, 110.4),
    "nitrogen": (1.4, 296.8, 1.663e-5, 273.15, 106.7),
    "helium": (5.0 / 3.0, 2077.0, 1.870e-5, 273.15, 79.4),
    "co2": (1.289, 188.9, 1.370e-5, 273.15, 222.0),
}
#: The fastest flow the solver is checked to: a normal shock in a nozzle's diverging part (Mach 1.96 ahead of it by
#: the isentropic tables, captured as 1.8 on a coarse mesh, the shock placed within 2 % of the diverging length).
MACH_CHECKED = 1.8
#: Mach numbers past which the flow is transonic, and incompressible flow stops being right.
TRANSONIC, COMPRESSIBLE = 0.8, 0.3
#: A drive that could expand the gas past this Mach number may choke a converging-diverging passage.
MAY_CHOKE = 0.5
#: A shock: an element compressed by -div u h / c past this (a normal shock spread over a few elements reads ~0.3).
SHOCK_COMPRESSION = 0.15
STANDARD_PRESSURE, KELVIN = 101325.0, 273.15
WALLS = ("auto", "slip", "no_slip")
PROFILES = ("uniform", "developed")
#: The cost model: pseudo-time steps a subsonic march takes, and one through choking (with and without continuation).
STEPS_SUBSONIC, STEPS_CHOKED, STEPS_CHOKED_CONTINUED = 45.0, 300.0, 190.0
#: A step factors afresh and assembles about this many laminar assemblies' worth.
ASSEMBLIES = 3.0


def mach_sentence(value: float, limit: float, outlet_supersonic: bool) -> str:
    """The warning past the checked range, the same words everywhere."""
    if outlet_supersonic:
        return (f"Mach {value:.2f}, and the gas leaves the outlet faster than sound: the outlet's pressure is held "
                "there though a supersonic exit sets its own, so the flow near the outlet is not to be trusted; "
                "extend the passage past the exit, or lower the outlet pressure toward the exit's own")
    return (f"Mach {value:.2f} is past Mach {limit:g}, the fastest this solver is checked to: shocks are captured "
            "over a few elements and their strength and place are approximate")


@dataclass(frozen=True)
class Gas:
    name: str
    gamma: float
    R: float
    mu_ref: float
    T_ref: float
    S: float


@dataclass(frozen=True)
class GasInletSpec:
    opening: str
    #: "total" (total_pressure_Pa) or "mass" (mass_flow_kg_s).
    kind: str
    total_pressure_Pa: float = 0.0
    mass_flow_kg_s: float = 0.0
    total_temperature_C: float = 20.0
    profile: str | None = None


@dataclass(frozen=True)
class GasOutletSpec:
    opening: str
    pressure_Pa: float = STANDARD_PRESSURE


@dataclass(frozen=True)
class CfdCompressibleInputs(Inputs):
    kind: str = "internal"
    gas: Gas = Gas("air", *GASES["air"])
    inlets: tuple[GasInletSpec, ...] = ()
    outlets: tuple[GasOutletSpec, ...] = ()
    walls: str = "auto"
    #: External flow: the free stream (m/s), its static pressure (Pa) and temperature (°C).
    velocity_m_s: tuple[float, float, float] | None = None
    pressure_Pa: float = STANDARD_PRESSURE
    temperature_C: float = 20.0
    fixtures: tuple = ()
    structure_material: Any = None

    @property
    def mapped(self) -> bool:
        return self.structure_material is not None

    @property
    def outlet_pressure_Pa(self) -> float:
        return self.outlets[0].pressure_Pa if self.outlets else self.pressure_Pa


# -- parse --------------------------------------------------------------------------------------------


def _gas(raw: Any) -> Gas:
    if raw is None:
        return Gas("air", *GASES["air"])
    if isinstance(raw, str):
        if raw not in GASES:
            raise ValueError(f"flow.gas: {kinds.json_text(raw)} is not a gas cadgen knows; use one of {list(GASES)} "
                             'or {"gamma": 1.4, "gas_constant_J_kgK": 287, "viscosity_Pa_s": 1.8e-5}')
        return Gas(raw, *GASES[raw])
    if not isinstance(raw, dict):
        raise ValueError('flow.gas: "air", or {"name": "argon", "gamma": 1.67, "gas_constant_J_kgK": 208, "viscosity_Pa_s": 2.2e-5}')
    kinds.known(raw, {"name", "gamma", "gas_constant_J_kgK", "viscosity_Pa_s"}, "flow.gas",
           "a gas takes name, gamma, gas_constant_J_kgK and viscosity_Pa_s (at 20 °C)")
    for key in ("gamma", "gas_constant_J_kgK", "viscosity_Pa_s"):
        if key not in raw:
            raise ValueError(f"flow.gas.{key}: a gas object needs gamma, gas_constant_J_kgK and viscosity_Pa_s (at 20 °C)")
    gamma = kinds.number(raw["gamma"], where="flow.gas.gamma", positive=True)
    if not 1.0 < gamma <= 1.7:
        raise ValueError(f"flow.gas.gamma: the ratio of specific heats is between 1 and 5/3 (about 1.67) for a gas, got {gamma:g}")
    name = raw.get("name", "gas")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("flow.gas.name: expected a short piece of text")
    # The viscosity given at 20 °C, carried to other temperatures by Sutherland's law with air's constant.
    mu20 = kinds.number(raw["viscosity_Pa_s"], where="flow.gas.viscosity_Pa_s", positive=True)
    S, T_ref = GASES["air"][4], 293.15
    return Gas(name.strip(), gamma, kinds.number(raw["gas_constant_J_kgK"], where="flow.gas.gas_constant_J_kgK", positive=True),
               mu20, T_ref, S)


def _temperature(value: Any, where: str) -> float:
    celsius = kinds.number(value, where=where)
    if celsius <= -KELVIN:
        raise ValueError(f"{where}: in °C, above absolute zero (-273.15), got {celsius:g}")
    return celsius


def parse_gas_flow(document: dict) -> dict:
    flow = document.get("flow")
    if not isinstance(flow, dict):
        raise ValueError('study.flow: a gas flow study needs its flow, like {"kind": "internal", "gas": "air", "inlets": '
                         '[{"opening": "x_min", "total_pressure_Pa": 150000, "total_temperature_C": 20}], '
                         '"outlets": [{"opening": "x_max", "pressure_Pa": 101325}]}')
    kind = flow.get("kind", "internal")
    if kind not in ("internal", "external"):
        raise ValueError(f"flow.kind: {kinds.json_text(kind)} is not \"internal\" (through the part) or \"external\" (around it)")
    gas = _gas(flow.get("gas"))
    walls = flow.get("walls", "auto")
    if walls not in WALLS:
        raise ValueError(f"flow.walls: {kinds.json_text(walls)} is not one of {list(WALLS)} (slip: frictionless walls, an "
                         "inviscid core; no_slip: laminar walls; auto: laminar in the laminar range, else slip)")
    out: dict[str, Any] = {"kind": kind, "gas": gas, "walls": walls}
    if kind == "external":
        kinds.known(flow, {"kind", "gas", "walls", "velocity_m_s", "pressure_Pa", "temperature_C"}, "flow",
               "an external gas flow takes kind, gas, walls, velocity_m_s, pressure_Pa and temperature_C")
        out.update(velocity_m_s=vector3(flow.get("velocity_m_s"), "flow.velocity_m_s", "[150, 0, 0]"),
                   pressure_Pa=kinds.number(flow.get("pressure_Pa", STANDARD_PRESSURE), where="flow.pressure_Pa", positive=True),
                   temperature_C=_temperature(flow.get("temperature_C", 20.0), "flow.temperature_C"))
        return out
    kinds.known(flow, {"kind", "gas", "walls", "inlets", "outlets"}, "flow",
           "an internal gas flow takes kind, gas, walls, inlets and outlets")
    for key, example in (("inlets", '{"opening": "x_min", "total_pressure_Pa": 150000}'), ("outlets", '{"opening": "x_max", "pressure_Pa": 101325}')):
        flow_entries(flow, key, example)
    outlets = []
    for index, entry in enumerate(flow["outlets"]):
        where = f"flow.outlets[{index}]"
        kinds.known(entry, {"opening", "pressure_Pa"}, where, "an outlet takes opening and pressure_Pa (absolute static pressure)")
        outlets.append(GasOutletSpec(parse_opening(entry, where),
                                     kinds.number(entry.get("pressure_Pa", STANDARD_PRESSURE), where=f"{where}.pressure_Pa", positive=True)))
    lowest = min(outlet.pressure_Pa for outlet in outlets)
    inlets = []
    for index, entry in enumerate(flow["inlets"]):
        where = f"flow.inlets[{index}]"
        kinds.known(entry, {"opening", "total_pressure_Pa", "mass_flow_kg_s", "total_temperature_C", "profile"}, where,
               "an inlet takes opening, total_pressure_Pa or mass_flow_kg_s, total_temperature_C and profile")
        given = [key for key in ("total_pressure_Pa", "mass_flow_kg_s") if key in entry]
        if len(given) != 1:
            raise ValueError(f"{where}: an inlet is held by its total_pressure_Pa (absolute, Pa) or its mass_flow_kg_s, exactly one")
        temperature = _temperature(entry.get("total_temperature_C", 20.0), f"{where}.total_temperature_C")
        profile = entry.get("profile")
        if profile is not None:
            if "mass_flow_kg_s" not in entry:
                raise ValueError(f"{where}.profile: a profile goes with mass_flow_kg_s; a total-pressure inlet's speed is the flow's own")
            if profile not in PROFILES:
                raise ValueError(f"{where}.profile: {kinds.json_text(profile)} is not one of {list(PROFILES)}")
        if given == ["total_pressure_Pa"]:
            total = kinds.number(entry["total_pressure_Pa"], where=f"{where}.total_pressure_Pa", positive=True)
            if total <= lowest:
                raise ValueError(f"{where}.total_pressure_Pa: {total:g} Pa is not above the outlet's {lowest:g} Pa, so no gas "
                                 "flows in; both are absolute pressures (1 atm is 101325 Pa)")
            inlets.append(GasInletSpec(parse_opening(entry, where), "total", total_pressure_Pa=total, total_temperature_C=temperature))
        else:
            mass = kinds.number(entry["mass_flow_kg_s"], where=f"{where}.mass_flow_kg_s", positive=True)
            inlets.append(GasInletSpec(parse_opening(entry, where), "mass", mass_flow_kg_s=mass, total_temperature_C=temperature,
                                       profile=profile))
    distinct_openings([entry.opening for entry in (*inlets, *outlets)])
    out.update(inlets=tuple(inlets), outlets=tuple(outlets))
    return out


# -- the analysis -------------------------------------------------------------------------------------


@dataclass
class _Setup(FlowSetup):
    walls: str                 # "slip" or "no_slip", as chosen
    total_temperature_K: float


class CfdCompressibleAnalysis(CfdAnalysis):
    name: ClassVar[str] = "cfd_compressible"
    word: ClassVar[str] = "Fast gas flow"
    limits: ClassVar[tuple[str, ...]] = LIMITS
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("pressure", "_PRESSURE", "wall pressure (above the outlet's)", "Pa", signed=True),
        FieldSpec("mach", "_MACH", "Mach number", ""),
        FieldSpec("temperature", "_TEMPERATURE", "gas temperature", "°C", signed=True),
        FieldSpec("speed", "_SPEED", "flow speed near the wall", "m/s"),
        FieldSpec("von_mises", "_VON_MISES", "von Mises stress", "MPa"),
        FieldSpec("displacement", "_DISPLACEMENT", "displacement", "mm", 3, 1000.0),
    )
    checks: ClassVar[tuple] = (kinds.PRESSURE_DROP, kinds.VELOCITY, kinds.MACH, kinds.STRESS, kinds.DISPLACEMENT)
    default_controls: ClassVar[dict[str, dict]] = {
        "field": {"drives": "field", "type": "enum", "options": ["pressure", "mach", "temperature", "speed"]},
    }
    ladder: ClassVar[tuple[str, ...]] = ("fluid_coarsen", "continuation", "iterative")
    extras_word: ClassVar[str] = "gas flow"

    def parse(self, document: dict) -> CfdCompressibleInputs:
        flow = parse_gas_flow(document)
        # map_to_structure and the stress/displacement checks read exactly as the laminar flow's.
        probe = {"flow": {"kind": "internal", "fluid": "air", "inlets": [{"opening": "x_min", "velocity_m_s": 1}],
                          "outlets": [{"opening": "x_max"}]}}
        for key in ("map_to_structure", "material", "view"):
            if key in document:
                probe[key] = document[key]
        base = super().parse(probe)
        return CfdCompressibleInputs(base.face_refs, base.anchor_refs, base.requires_anchor, fixtures=base.fixtures,
                                     structure_material=base.structure_material, **flow)

    # -- the fluid region and its numbers ------------------------------------------------------------

    def setup_of(self, ctx: SolveContext, inputs: CfdCompressibleInputs) -> _Setup:
        return cached_setup(self._setups, ctx, inputs, ONE_PART, lambda shape, refs: self._build_setup(shape, refs, inputs))

    @staticmethod
    def _build_setup(shape, refs: dict, inputs: CfdCompressibleInputs) -> _Setup:
        from cadgen._internal.fea import fluid_domain
        from cadgen._internal.fea.compressible import sutherland

        gas = inputs.gas
        solver_gas = flow_gas(inputs)
        g = gas.gamma
        if inputs.kind == "internal":
            roles = {**{inlet.opening: "inlet" for inlet in inputs.inlets}, **{outlet.opening: "outlet" for outlet in inputs.outlets}}
            domain = fluid_domain.build_domain(shape, "internal", openings=roles, refs=refs)
            T0 = sum(inlet.total_temperature_C for inlet in inputs.inlets) / len(inputs.inlets) + KELVIN
            mu = sutherland(solver_gas, T0)
            best = (0.0, 0.0)
            for inlet in inputs.inlets:
                faces = [f for f in domain.faces if f.kind == "inlet" and f.opening == inlet.opening]
                area = sum(f.area for f in faces)
                perimeter = sum(f.perimeter for f in faces)
                diameter = 4.0 * area / perimeter if perimeter > 0 else domain.length_mm
                if inlet.kind == "mass":
                    flux = inlet.mass_flow_kg_s / (area * 1e-6)
                else:
                    # The mass flux of the total pressure expanded to the outlet's, capped at sonic (choked).
                    ratio = min(inputs.outlet_pressure_Pa / inlet.total_pressure_Pa, 1.0)
                    mach = math.sqrt(max(2.0 / (g - 1.0) * (ratio ** (-(g - 1.0) / g) - 1.0), 0.0))
                    mach = min(mach, 1.0)
                    t = 1.0 / (1.0 + 0.5 * (g - 1.0) * mach * mach)
                    flux = inlet.total_pressure_Pa * t ** (g / (g - 1.0)) / (gas.R * T0 * t) * mach * math.sqrt(g * gas.R * T0 * t)
                reynolds = flux * diameter / 1000.0 / mu
                if reynolds >= best[0]:
                    best = (reynolds, diameter)
            reynolds, length = best
            diameters = [4.0 * f.area / f.perimeter for f in domain.faces if f.kind in ("inlet", "outlet") and f.perimeter > 0]
            wall = min(diameters) / 4.0 if diameters else domain.length_mm / 10.0
        else:
            domain = fluid_domain.build_domain(shape, "external", velocity=inputs.velocity_m_s, refs=refs)
            speed = math.sqrt(sum(c * c for c in inputs.velocity_m_s))
            T = inputs.temperature_C + KELVIN
            T0 = T + speed * speed / (2.0 * solver_gas.cp)
            length = domain.length_mm
            reynolds = inputs.pressure_Pa / (gas.R * T) * speed * length / 1000.0 / sutherland(solver_gas, T)
            wall = length / 8.0
        limit = RE_LIMIT[inputs.kind]
        walls = inputs.walls if inputs.walls != "auto" else ("no_slip" if reynolds <= limit else "slip")
        return _Setup(domain, reynolds, length, wall, walls, T0)

    # -- the ladder ----------------------------------------------------------------------------------

    def _expected_mach(self, inputs: CfdCompressibleInputs) -> float:
        """The Mach number the drive could reach expanding isentropically to the outlet (an upper bound)."""
        g = inputs.gas.gamma
        if inputs.kind == "external":
            T = inputs.temperature_C + KELVIN
            return math.sqrt(sum(c * c for c in inputs.velocity_m_s)) / math.sqrt(g * inputs.gas.R * T)
        best = 0.0
        for inlet in inputs.inlets:
            if inlet.kind == "total":
                ratio = inputs.outlet_pressure_Pa / inlet.total_pressure_Pa
                best = max(best, math.sqrt(max(2.0 / (g - 1.0) * (ratio ** (-(g - 1.0) / g) - 1.0), 0.0)))
        return best

    def estimate(self, ctx: SolveContext, inputs: CfdCompressibleInputs):
        tets, n, per, memory = self._cost(ctx, self.setup_of(ctx, inputs), 4.0)
        if self._expected_mach(inputs) < MAY_CHOKE:
            steps = STEPS_SUBSONIC
        else:
            steps = STEPS_CHOKED_CONTINUED if "continuation" in ctx.plan.taken else STEPS_CHOKED
        return self._estimate(ctx, inputs, n, memory, MESH_S * tets + steps * (per + ASSEMBLIES * ASSEMBLE_S * tets))

    def apply(self, rung, ctx: SolveContext, inputs: CfdCompressibleInputs):
        from cadgen._internal.fea import fit
        from cadgen._internal.fea.compressible import CONTINUATION

        if rung == "continuation":
            if "continuation" in ctx.plan.taken or self._expected_mach(inputs) < MAY_CHOKE:
                return None
            shares = ", ".join(f"{share:.0%}" for share in CONTINUATION)
            return fit.Step(
                "continuation",
                f"Reached the full pressure ratio in steps ({shares} of the drive) from a gentler flow, so the march "
                "starts the choked flow near its answer", None, None, detail={"shares": list(CONTINUATION)})
        return super().apply(rung, ctx, inputs)

    # -- solve ---------------------------------------------------------------------------------------

    def solve(self, ctx: SolveContext, inputs: CfdCompressibleInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea import compressible
        from cadgen._internal.fea.compressible import CONTINUATION

        timings: dict[str, float] = {}
        setup = self.setup_of(ctx, inputs)
        domain = setup.domain
        wall, far = self.sizes(ctx, setup)
        fluid, space, face_of, row_kind = mesh_flow(ctx, domain, wall, far, timings, "gas flow")
        problem = self._problem(space, fluid, face_of, row_kind, inputs, setup)
        solver = "iterative" if ctx.plan.solver == "iterative" else "direct"
        schedule = tuple(CONTINUATION) if "continuation" in ctx.plan.taken else ()
        started = time.perf_counter()
        flow = compressible.solve_gas(problem, schedule=schedule, solver=solver, log=ctx.log)
        timings["flow_s"] = time.perf_counter() - started
        if ctx.log:
            ctx.log(f"gas flow: {flow.steps} pseudo-time steps, residual {flow.residual:.1e}")

        started = time.perf_counter()
        post = self._post(space, fluid, face_of, row_kind, flow, inputs, setup)
        mapped = to_part(ctx, space, fluid, face_of, row_kind,
                         {"pressure": post["pressure_nodes_Pa"], "mach": flow.mach, "temperature": flow.temperature_K - KELVIN})
        post["wetted"], post["wetted_nodes"] = mapped.pop("wetted"), mapped.pop("wetted_nodes")
        mapped["speed"], reach = speed_to_part(ctx, space, fluid, face_of, row_kind, np.linalg.norm(space.nodal(flow.u), axis=1) / 1000.0,
                                               speed_reach(inputs.kind, setup.length_mm, wall))
        timings["post_s"] = time.perf_counter() - started

        warnings = list(flow.warnings)
        analysis_warnings = []
        limit = RE_LIMIT[inputs.kind]
        reynolds = {"value": round(post["reynolds"], 2), "limit": limit, "kind": inputs.kind,
                    "length_mm": round(setup.length_mm, 4), "laminar": post["reynolds"] <= limit, "walls": setup.walls}
        peak = post["max_mach"]
        regime = "supersonic" if peak > 1.0 else "transonic" if peak > TRANSONIC else "subsonic"
        outlet_supersonic = post["outlet_mach"] > 1.0
        mach = {"value": round(peak, 4), "limit": MACH_CHECKED, "regime": regime, "checked": peak <= MACH_CHECKED and not outlet_supersonic}
        if not mach["checked"]:
            analysis_warnings.append(mach_sentence(peak, MACH_CHECKED, outlet_supersonic))
        if setup.walls == "slip" and inputs.walls == "auto":
            analysis_warnings.append(
                f"Re {post['reynolds']:.0f} is past the laminar range, so the walls are frictionless (an inviscid core): "
                "the pressure lost to wall friction is not counted")
        elif setup.walls == "no_slip" and post["reynolds"] > limit:
            analysis_warnings.append(
                f"Re {post['reynolds']:.0f} is past the laminar range (laminar above Re {limit} is unreliable) with laminar "
                "walls: real flow is likely turbulent, so the friction loss is a lower bound")
        if peak < COMPRESSIBLE:
            analysis_warnings.append(
                f"the fastest flow is Mach {peak:.2f}, under Mach {COMPRESSIBLE:g}: the gas is nearly incompressible here, so "
                "the cfd analysis gives the same answer")
        if post["imbalance"] > 0.01:
            analysis_warnings.append(balance_sentence(post["imbalance"], "mass"))
        if not flow.converged:
            analysis_warnings += [w for w in flow.warnings if w.startswith("the gas flow stopped")]

        fields = {name: mapped[name] for name in ("pressure", "mach", "temperature", "speed")}
        started = time.perf_counter()
        tracers = flow_tracers(space, row_kind, {"nodal_velocity": space.nodal(flow.u)}, domain, label="Gas flow")
        timings["tracers_s"] = time.perf_counter() - started
        result = part_result(
            ctx, fields, dofs=int(space.basis.N + flow.pressure_basis.N),
            solver=f"pressure-based compressible Taylor-Hood P2/P1, SUPG and shock capturing, "
                   f"{'GMRES + multigrid' if solver == 'iterative' else 'direct'}",
            timings={**timings, **{f"flow_{k}": v for k, v in flow.timings.items()}}, warnings=warnings,
            scalars={
                "post": post, "reynolds": reynolds, "mach": mach, "flow": flow, "wall_mm": wall, "far_mm": far, "speed_reach_mm": reach,
                "fluid_mesh": fluid_mesh_record(fluid, wall, far), "analysis_warnings": analysis_warnings,
                "analysis_extras": {"mach": mach, "walls": setup.walls, **({"motion": tracers} if tracers else {})},
            },
        )
        return self._mapped(ctx, inputs, result, fields["pressure"])

    def _problem(self, space, fluid, face_of, row_kind, inputs: CfdCompressibleInputs, setup: _Setup):
        import numpy as np

        from skfem import ElementTetP2, FacetBasis

        from cadgen._internal.fea import compressible, navier_stokes

        boundary = space.boundary_quadratic
        locations = space.dof_locations
        opening_of = openings_of(fluid, face_of)
        walls = np.unique(boundary[row_kind == "wall"])
        inlets = []
        outlets = []
        slip_rows = None
        if inputs.kind == "external":
            stream = np.array(inputs.velocity_m_s, dtype=float)
            speed = float(np.linalg.norm(stream))
            rows = np.flatnonzero(row_kind == "inlet")
            nodes = np.unique(boundary[rows])
            area = sum(face.area for face in face_of.values() if face.kind == "inlet")
            inlets.append(compressible.GasInlet(rows=rows, nodes=nodes, weights=np.ones(len(nodes)), direction=stream / speed,
                                                area_mm2=area, kind="velocity", velocity_m_s=speed))
            outlets.append((np.flatnonzero(row_kind == "outlet"), inputs.pressure_Pa))
            slip_rows = np.flatnonzero(row_kind == "side")
        else:
            for inlet in inputs.inlets:
                rows = np.flatnonzero((row_kind == "inlet") & (opening_of == inlet.opening))
                axis, high = "xyz".index(inlet.opening[0]), inlet.opening.endswith("max")
                direction = np.zeros(3)
                direction[axis] = -1.0 if high else 1.0
                area = sum(face.area for face in face_of.values() if face.kind == "inlet" and face.opening == inlet.opening)
                nodes = np.unique(boundary[rows])
                weights = np.ones(len(nodes))
                if inlet.kind == "mass":
                    profile = inlet.profile or ("developed" if setup.walls == "no_slip" else "uniform")
                    if setup.walls == "no_slip":
                        nodes = np.setdiff1d(nodes, walls)
                    if profile == "developed":
                        shape = navier_stokes.developed_profile(locations, boundary[rows], axis)
                        nodes = np.array(sorted(shape), dtype=np.int64)
                        weights = np.array([shape[int(node)] for node in nodes])
                    else:
                        shape = {int(node): 1.0 for node in nodes}
                    # The mean over the opening's true area: scale the profile so its flux, integrated as the solver
                    # measures the mass (on the curved quadratic facets), is the area.
                    profile_field = np.zeros(space.scalar_count)
                    profile_field[nodes] = weights
                    facets = FacetBasis(space.mesh, ElementTetP2(), facets=space.facets_of_rows(rows))
                    flux = float((facets.interpolate(profile_field).value * facets.dx).sum())
                    weights = weights * (area / flux if flux > 0 else 1.0)
                inlets.append(compressible.GasInlet(
                    rows=rows, nodes=nodes, weights=weights, direction=direction, area_mm2=area, kind=inlet.kind,
                    total_pressure_Pa=inlet.total_pressure_Pa, mass_flow_kg_s=inlet.mass_flow_kg_s))
            for outlet in inputs.outlets:
                outlets.append((np.flatnonzero((row_kind == "outlet") & (opening_of == outlet.opening)), outlet.pressure_Pa))
        return compressible.GasProblem(space=space, gas=flow_gas(inputs), total_temperature_K=setup.total_temperature_K, inlets=inlets,
                                       outlets=outlets, wall_rows=np.flatnonzero(row_kind == "wall"), walls=setup.walls,
                                       slip_rows=slip_rows)

    def _post(self, space, fluid, face_of, row_kind, flow, inputs: CfdCompressibleInputs, setup: _Setup) -> dict:
        """The flow's numbers: mass flow, Mach, pressures, the force on the part, choking and the shock."""
        import numpy as np

        from cadgen._internal.fea.compressible import sutherland

        p_abs = flow.pressure_nodes_Pa
        reference = inputs.outlet_pressure_Pa
        inlets, outlets = np.flatnonzero(row_kind == "inlet"), np.flatnonzero(row_kind == "outlet")

        def mean(rows, values) -> float:
            area = surface_integral(space, rows, np.ones(space.scalar_count))
            return surface_integral(space, rows, values) / area if area > 0 else 0.0

        inflow, outflow = float(sum(flow.inflow_kg_s)), float(sum(flow.outflow_kg_s))
        speed = np.linalg.norm(space.nodal(flow.u), axis=1) / 1000.0          # m/s
        fastest = int(flow.mach.argmax())
        outlet_nodes = np.unique(space.boundary_quadratic[outlets]) if len(outlets) else np.zeros(0, dtype=np.int64)
        inlet_static = mean(inlets, p_abs) if len(inlets) else reference
        outlet_static = mean(outlets, p_abs) if len(outlets) else reference
        totals = [inlet.total_pressure_Pa for inlet in inputs.inlets if inlet.kind == "total"]
        inlet_total = max(totals) if totals else (float(np.mean(flow.inlet_total_Pa)) if flow.inlet_total_Pa else reference)
        # The force of the gas on the part: its pressure above the outlet's along the wall's normal (out of the fluid).
        force = np.zeros(3)
        walls = np.flatnonzero(row_kind == "wall")
        if len(walls):
            triangles, area, normal = wall_triangles(space, walls)
            force = (area[:, None] * (p_abs - reference)[triangles[:, 3:]].mean(axis=1)[:, None] * normal).sum(axis=0) * 1e-6
        # Reynolds number from the solved mass flux through the inlet (internal), or the free stream (external).
        reynolds = setup.reynolds
        if inputs.kind == "internal" and len(inlets):
            area_in = surface_integral(space, inlets, np.ones(space.scalar_count))
            mu = sutherland(flow_gas(inputs), setup.total_temperature_K)
            reynolds = inflow / (area_in * 1e-6) * setup.length_mm / 1000.0 / mu if area_in > 0 else setup.reynolds
        compression = flow.compression if flow.compression is not None else np.zeros(0)
        shock = None
        if len(compression) and float(compression.max()) > SHOCK_COMPRESSION and float(flow.mach.max()) > 1.0:
            # Where the gas drops back through sonic while it is compressed: the elements whose corners straddle
            # Mach 1 and that compress (the throat's sonic line accelerates, and is not among them).
            corners = flow.mach[space.mesh.t[:4]]
            sharp = (corners.min(axis=0) < 1.0) & (corners.max(axis=0) > 1.0) & (compression > SHOCK_COMPRESSION / 3.0)
            if sharp.any():
                centres = space.mesh.p[:, space.mesh.t[:4]].mean(axis=1).T
                shock = {"at_mm": xyz(centres[sharp].mean(axis=0)),
                         "compression": round(float(compression.max()), 4), "elements": int(sharp.sum())}
        return {
            "mass_flow_kg_s": inflow, "outflow_kg_s": outflow,
            "imbalance": abs(inflow - outflow) / abs(inflow) if inflow else 0.0,
            "max_mach": float(flow.mach[fastest]), "max_mach_at": space.dof_locations[fastest],
            "max_velocity_m_s": float(speed.max()), "max_velocity_at": space.dof_locations[int(speed.argmax())],
            "outlet_mach": float(flow.mach[outlet_nodes].mean()) if len(outlet_nodes) else 0.0,
            "inlet_pressure_Pa": inlet_static, "outlet_pressure_Pa": outlet_static,
            "inlet_total_pressure_Pa": inlet_total, "pressure_drop_Pa": inlet_static - outlet_static,
            "pressure_ratio": inlet_total / reference,
            "inlet_at": space.dof_locations[np.unique(space.boundary_quadratic[inlets])].mean(axis=0) if len(inlets) else np.zeros(3),
            "force_N": force, "reynolds": reynolds, "shock": shock,
            "choked": bool(float(flow.mach.max()) >= 1.0 and inputs.kind == "internal"),
            "pressure_nodes_Pa": p_abs - reference,
            "min_temperature_C": float(flow.temperature_K.min() - KELVIN),
        }

    # -- judging -------------------------------------------------------------------------------------

    def _measured(self, check: dict, post: dict) -> tuple:
        if check["kind"] == "mach":
            return post["max_mach"], check["limit"], "", "Mach number", post["max_mach_at"]
        return super()._measured(check, post)

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: CfdCompressibleInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        from cadgen._internal.fea import checks

        found: list[dict] = list(checks.findings(result.solved[0])) if result.solved else []
        mach = result.scalars["mach"]
        post = result.scalars["post"]
        if not mach["checked"]:
            found.append(finding(
                "warning", "mach_past_checked", mach_sentence(mach["value"], mach["limit"], post["outlet_mach"] > 1.0),
                f"fastest Mach {mach['value']:.3g} ({mach['regime']}); the solver's benchmark is a normal shock in "
                f"a nozzle with Mach {MACH_CHECKED:g} ahead of it as captured"))
        if post["shock"]:
            shock = post["shock"]
            found.append(finding(
                "info", "shock", f"A shock stands in the flow near {shock['at_mm']} mm: the gas slows from supersonic to "
                "subsonic there and loses total pressure",
                f"captured over about {shock['elements']} elements; its place is good to about two elements",
                [{"text": "the shock", "ref": None, "at": shock["at_mm"]}]))
        flow = result.scalars["flow"]
        if not flow.converged:
            found.append(finding(
                "warning", "gas_flow_unsettled",
                f"The gas flow's residual was {flow.residual:.1e} of its largest when it stopped, short of 1e-7",
                "its numbers are approximate and every check on it is marked not settled (none passes "
                "outright); a finer or a coarser mesh, or a gentler pressure ratio, may settle"))
        found += limit_findings(check_results, {**FLOW_WORDS, "mach": "fastest Mach number"})
        if post["imbalance"] > 0.01:
            found.append(finding(
                "warning", "flow_balance", f"The mass in and out differ by {post['imbalance']:.1%}: the flow may not have settled",
                "the solve leaves gas unaccounted for; a finer mesh should close it"))
        return found

    # -- what is written -----------------------------------------------------------------------------

    def summary(self, result: AnalysisResult, inputs: CfdCompressibleInputs, check_results: list[dict]) -> dict:
        import numpy as np

        post, flow, reynolds, mach = result.scalars["post"], result.scalars["flow"], result.scalars["reynolds"], result.scalars["mach"]
        wet = post["wetted_nodes"]
        fields = result.fields

        def span(name):
            values = fields[name][wet] if len(wet) else np.zeros(1)
            return round(float(values.min()), 6), round(float(values.max()), 6)

        pressure, mach_wall, temperature = span("pressure"), span("mach"), span("temperature")
        gas = inputs.gas
        return {
            "flow_kind": inputs.kind,
            "gas": {"name": gas.name, "gamma": gas.gamma, "gas_constant_J_kgK": gas.R},
            "walls": result.scalars["analysis_extras"]["walls"],
            "reynolds": dict(reynolds),
            "mach": dict(mach),
            "mass_flow_kg_s": float(f"{post['mass_flow_kg_s']:.6g}"),
            "outflow_kg_s": float(f"{post['outflow_kg_s']:.6g}"),
            "flow_balance": round(post["imbalance"], 6),
            "max_mach": round(post["max_mach"], 4),
            "max_mach_at_mm": xyz(post["max_mach_at"]),
            "max_velocity_m_s": round(post["max_velocity_m_s"], 4),
            "max_velocity_at_mm": xyz(post["max_velocity_at"]),
            "outlet_mach": round(post["outlet_mach"], 4),
            "pressure_ratio": round(post["pressure_ratio"], 6),
            "inlet_total_pressure_Pa": round(post["inlet_total_pressure_Pa"], 3),
            "inlet_pressure_Pa": round(post["inlet_pressure_Pa"], 3),
            "outlet_pressure_Pa": round(post["outlet_pressure_Pa"], 3),
            "pressure_drop_Pa": round(post["pressure_drop_Pa"], 3),
            "total_temperature_C": round(self._t0(inputs) - KELVIN, 4),
            "choked": post["choked"],
            "shock": post["shock"],
            "min_wall_pressure_Pa": pressure[0], "max_wall_pressure_Pa": pressure[1],
            "min_wall_mach": mach_wall[0], "max_wall_mach": mach_wall[1],
            "max_speed_near_wall_m_s": span("speed")[1], "speed_reach_mm": round(result.scalars["speed_reach_mm"], 4),
            "min_wall_temperature_C": temperature[0], "max_wall_temperature_C": temperature[1],
            "force_N": [round(float(c), 9) for c in post["force_N"]],
            "solve": {"steps": flow.steps, "linear_solves": flow.linear_solves, "residual": float(f"{flow.residual:.3g}"),
                      "converged": flow.converged, "continuation": flow.continued, "stages": list(flow.stages),
                      "solver": flow.solver},
            "fluid_mesh": dict(result.scalars["fluid_mesh"]),
            "deformation_scale": result.scalars.get("deformation_scale"),
            **structure_summary(result),
            "checks": check_results,
        }

    @staticmethod
    def _t0(inputs: CfdCompressibleInputs) -> float:
        if inputs.kind == "external":
            from cadgen._internal.fea.compressible import Gas as SolverGas

            gas = inputs.gas
            T = inputs.temperature_C + KELVIN
            speed2 = sum(c * c for c in inputs.velocity_m_s)
            return T + speed2 / (2.0 * SolverGas(gas.name, gas.gamma, gas.R).cp)
        return sum(inlet.total_temperature_C for inlet in inputs.inlets) / len(inputs.inlets) + KELVIN

    def field_ranges(self, summary: dict, result: AnalysisResult) -> dict[str, tuple[float, float]]:
        return {"pressure": (summary["min_wall_pressure_Pa"], summary["max_wall_pressure_Pa"]),
                "mach": (0.0, summary["max_wall_mach"]), "speed": (0.0, summary["max_speed_near_wall_m_s"]),
                "temperature": (summary["min_wall_temperature_C"], summary["max_wall_temperature_C"]), **structure_ranges(summary)}

    def study_echo(self, inputs: CfdCompressibleInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        gas = inputs.gas
        flow: dict[str, Any] = {"kind": inputs.kind, "gas": {"name": gas.name, "gamma": gas.gamma, "gas_constant_J_kgK": gas.R},
                                "walls": inputs.walls}
        if inputs.kind == "external":
            flow.update(velocity_m_s=[float(c) for c in inputs.velocity_m_s], pressure_Pa=inputs.pressure_Pa,
                        temperature_C=inputs.temperature_C)
        else:
            entries = []
            for inlet in inputs.inlets:
                entry: dict[str, Any] = {"opening": inlet.opening}
                if inlet.kind == "total":
                    entry["total_pressure_Pa"] = inlet.total_pressure_Pa
                else:
                    entry["mass_flow_kg_s"] = inlet.mass_flow_kg_s
                    if inlet.profile:
                        entry["profile"] = inlet.profile
                entry["total_temperature_C"] = inlet.total_temperature_C
                entries.append(entry)
            flow["inlets"] = entries
            flow["outlets"] = [{"opening": o.opening, "pressure_Pa": o.pressure_Pa} for o in inputs.outlets]
        return {"flow": flow, **structure_echo(inputs, bare)}

    def human_lines(self, summary: dict) -> list[str]:
        mach, solve, reynolds = summary["mach"], summary["solve"], summary["reynolds"]
        walls = "frictionless walls (inviscid core)" if summary["walls"] == "slip" else "laminar walls"
        lines = [
            f"{summary['flow_kind']} flow of {summary['gas']['name']}: fastest Mach {summary['max_mach']:.3g} ({mach['regime']}"
            + (", choked" if summary["choked"] else "") + f"), Re {reynolds['value']:.4g}, {walls}",
            f"mass flow {summary['mass_flow_kg_s']:.4g} kg/s, pressure ratio {summary['pressure_ratio']:.4g} "
            f"(inlet total over outlet static), pressure drop {summary['pressure_drop_Pa']:.4g} Pa, fastest {summary['max_velocity_m_s']:.4g} m/s",
            f"wall pressure {summary['min_wall_pressure_Pa']:.4g} to {summary['max_wall_pressure_Pa']:.4g} Pa above the outlet's, "
            f"wall temperature {summary['min_wall_temperature_C']:.4g} to {summary['max_wall_temperature_C']:.4g} °C, "
            f"force on the part {summary['force_N']} N",
        ]
        if summary["shock"]:
            lines.append(f"a shock stands near {summary['shock']['at_mm']} mm")
        lines.append(f"solved in {solve['steps']} pseudo-time steps"
                     + (f" through {', '.join(f'{share:.0%}' for share in solve['stages'])} of the drive" if solve["continuation"] else "")
                     + f", residual {solve['residual']:.1e}" + ("" if solve["converged"] else " (not settled)"))
        if "max_von_mises_MPa" in summary:
            lines.append(structure_line(summary, "under the gas's pressure"))
        return lines


def flow_gas(inputs: CfdCompressibleInputs):
    from cadgen._internal.fea.compressible import Gas as SolverGas

    gas = inputs.gas
    return SolverGas(gas.name, gas.gamma, gas.R, gas.mu_ref, gas.T_ref, gas.S)
