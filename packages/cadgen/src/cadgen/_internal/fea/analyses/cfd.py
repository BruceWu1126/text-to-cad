"""Flow (lite): steady laminar incompressible flow through or around the part, and its push on the part.

The study (spec 5.14) names the ``flow``: ``internal`` (through the part:
inlets at a mean velocity, outlets at a pressure, each on a side of the
part's bounding box, ``x_min`` ... ``z_max``) or ``external`` (around it: a
free-stream velocity vector), and the fluid (``air``, ``water`` or its
density and viscosity). The fluid region is an OCP boolean
(:mod:`..fluid_domain`), meshed by netgen with quadratic tetrahedra, and the
flow is Taylor-Hood Navier-Stokes (:mod:`..navier_stokes`): Stokes, Picard,
Newton, and Reynolds continuation when Newton needs it.

What comes out is on the part's own surface (the GLB is the part, as for every
analysis): the wall ``pressure`` (Pa, gauge) and ``wall_shear`` (Pa), carried
from the fluid's walls to the part's faces they came from, face by face; and
the ``speed`` of the flow near the wall (m/s): the wall itself is still (no
slip), so at each wetted point it is the fastest the fluid moves within one
passage width (internal: the inlet's hydraulic diameter; external: two wall
elements), reaching at least as far as the fastest point of the flow, so the
speed a velocity check judges is on the model. A face the fluid does not wet
reads 0. The volume numbers go to the summary:
the fastest velocity, the flow rate, the pressure drop from the inlets to the
outlets and the force of the flow on the part. Checks ``pressure_drop`` and
``velocity``. With ``map_to_structure`` the wall pressure is applied face by
face to a static solve of the part (one-way fluid-structure coupling), and
the static fields, the stress and displacement checks and their findings join.

Its limits are written into every result: "Laminar, steady, incompressible;
no turbulence model." Past Re 2000 (internal, the inlet's hydraulic diameter)
or Re 1000 (external, the part's largest extent) it still solves, reaching
the Reynolds number by continuation, and warns that the real flow is likely
turbulent: in the warnings, the summary's ``reynolds`` (structured: value,
limit, kind), a finding and the CLI.

The flow analyses (``cfd_turbulent``, ``cfd_compressible``, ``conjugate_heat``,
``fsi``) and ``multiphase`` share the parsing, meshing, mapping and wording
helpers here. Stdlib only at import.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import AnalysisBase, AnalysisResult, FieldSpec, Inputs, SolveContext, finding, xyz

__all__ = ["CfdAnalysis", "CfdInputs", "FLUIDS", "Fluid", "Inlet", "LIMITS", "Outlet", "RE_LIMIT", "SPEED_WALLS", "fastest_near",
           "flow_solve_budget", "flow_tracers", "reynolds_sentence", "speed_reach", "speed_to_part", "unsettled_check"]

#: Fluids by name at 20 °C: density kg/m³, dynamic viscosity Pa·s.
FLUIDS = {"air": (1.204, 1.81e-5), "water": (998.2, 1.002e-3)}
LIMITS = ("Laminar, steady, incompressible; no turbulence model.",)
#: Above these the flow is likely turbulent: internal by the inlet's hydraulic diameter, external by the part's length.
RE_LIMIT = {"internal": 2000, "external": 1000}
OPENINGS = ("x_min", "x_max", "y_min", "y_max", "z_min", "z_max")
PROFILES = ("developed", "uniform")
#: A check is close once its value passes this share of its limit.
CLOSE_AT = 0.9
#: The nonlinear flow solve stops at its best state after the time target, or this many times its estimate when the
#: run went ahead past the target (a flap in water sat over 100 minutes on its first flow solve, silent); and never
#: before this many times the linear solves it is expected to need (:func:`flow_solve_budget`), so the work, not the
#: machine's load, decides whether a study settles (the gallery's orifice, Re 299, settled in 90 linear solves in
#: 454 s, and stopped unsettled at the 600 s default after 67 of them on a machine at load 40 to 200).
DEADLINE_OVER = 3.0
#: The ladder: fluid_coarsen grows the free-stream size by this; continuation is offered above this Re.
COARSEN, CONTINUE_FROM, FAIL_FROM = 2.5, 100.0, 500.0
#: External flow: the walls are meshed at L / this by default, the free stream this many times coarser.
EXTERNAL_WALL, EXTERNAL_FAR = 8.0, 4.0
#: Internal flow: elements across the narrowest opening's hydraulic diameter by default.
ACROSS = 4.0
#: Engine units: kg/m³ -> t/mm³, Pa·s -> MPa·s, m/s -> mm/s, MPa -> Pa.
DENSITY, VISCOSITY, SPEED, PASCAL = 1e-12, 1e-6, 1000.0, 1e6
#: The cost model (measured on this engine's pipe benchmark; a factor of three is what choosing a rung needs).
TETS_VOLUME, TETS_SURFACE, NODES_PER_TET = 4.5, 2.0, 1.6
DIRECT_S, ITERATIVE_S, ASSEMBLE_S, MESH_S = 6e-7, 1.2e-4, 1e-4, 2.5e-4
BYTES_PER_TET, NNZ_PER_ROW, FILL = 80_000.0, 90.0, 1.0
#: The speed field (:func:`speed_to_part`) looks this many wall elements into an external flow from the wall.
SPEED_WALLS = 2.0
#: What a pressure-drop and a velocity check's findings call what they judge.
FLOW_WORDS = {"pressure_drop": "pressure drop", "velocity": "fastest flow"}
ONE_PART = ("a flow study solves one part: name it with --occurrence (#o1.2), or flow through the assembly's parts "
            "fused into one")


@dataclass(frozen=True)
class Fluid:
    name: str
    density_kg_m3: float
    viscosity_Pa_s: float


@dataclass(frozen=True)
class Inlet:
    opening: str
    velocity_m_s: float
    profile: str = "developed"


@dataclass(frozen=True)
class Outlet:
    opening: str
    pressure_Pa: float = 0.0


@dataclass(frozen=True)
class CfdInputs(Inputs):
    kind: str = "internal"
    fluid: Fluid = Fluid("water", *FLUIDS["water"])
    inlets: tuple[Inlet, ...] = ()
    outlets: tuple[Outlet, ...] = ()
    #: External flow: the free stream, m/s.
    velocity_m_s: tuple[float, float, float] | None = None
    #: map_to_structure: its fixtures and its material (None when the flow is not mapped).
    fixtures: tuple = ()
    structure_material: Any = None

    @property
    def mapped(self) -> bool:
        return self.structure_material is not None


def reynolds_sentence(value: float, limit: int) -> str:
    """The warning past the laminar range, the same words everywhere."""
    return (f"Re {value:.0f} is past the laminar range (laminar above Re {limit} is unreliable): real flow is likely "
            "turbulent, so this pressure drop is a lower bound and the flow pattern may be wrong; "
            "run the same study as cfd_turbulent for the turbulent answer")


def balance_sentence(imbalance: float, what: str = "flow") -> str:
    return (f"the {what} in and out differ by {imbalance:.1%}: the solve did not settle; "
            "check the result with a finer mesh")


def unsettled_check(judged: dict, settled: bool) -> dict:
    """A check on a flow that did not settle (its solve stopped short of its tolerance) is not a pass: it is marked
    ``settled: false``, its label says so, and a pass becomes ``close``. A failure stays a failure."""
    if settled:
        return judged
    judged["settled"] = False
    judged["label"] = f"{judged['label']} (flow not settled)"
    if judged["status"] == "passes":
        judged["status"] = "close"
    return judged


def limit_findings(check_results: list[dict], words: dict[str, str], *, held: bool = True) -> list[dict]:
    """A finding for each check of ``words``' kinds that is close to or over its limit. ``held``: a check held back
    from passing only because the flow did not settle has the unsettled finding say so, not its own."""
    found = []
    for check in check_results:
        if check["kind"] not in words or check["status"] == "passes":
            continue
        if held and check.get("settled") is False and check["ratio"] <= check["close_at"]:
            continue
        what = words[check["kind"]]
        unit = f" {check['unit']}" if check["unit"] else ""
        value = f"{check['value']:.4g}{unit}"
        fails = check["status"] == "fails"
        found.append(finding(
            "error" if fails else "warning", f"{check['kind']}_{'over_limit' if fails else 'close_to_limit'}",
            f"The {what} is {value}, {'over' if fails else 'close to'} the {check['limit']:g}{unit} allowed",
            f"{what} {value} against a {check['limit']:g}{unit} limit ({check['ratio']:.2f} of it)",
            [{"text": f"the {what}", **check["where"]}]))
    return found


# -- parse --------------------------------------------------------------------------------------------


def _fluid(raw: Any) -> Fluid:
    if raw is None:
        raise ValueError('flow.fluid: name the fluid: "air", "water", or {"density_kg_m3": 1000, "viscosity_Pa_s": 0.001}')
    if isinstance(raw, str):
        if raw not in FLUIDS:
            raise ValueError(f"flow.fluid: {kinds.json_text(raw)} is not a fluid cadgen knows; use \"air\", \"water\" "
                             "or {\"density_kg_m3\", \"viscosity_Pa_s\"}")
        return Fluid(raw, *FLUIDS[raw])
    if not isinstance(raw, dict):
        raise ValueError('flow.fluid: "air", "water", or {"density_kg_m3": 1000, "viscosity_Pa_s": 0.001}')
    return fluid_object(raw, "flow.fluid")


def fluid_object(raw: dict, where: str) -> Fluid:
    """A fluid given by its numbers: {"density_kg_m3", "viscosity_Pa_s", "name"}."""
    kinds.known(raw, {"name", "density_kg_m3", "viscosity_Pa_s"}, where, "a fluid takes density_kg_m3, viscosity_Pa_s and a name")
    for key in ("density_kg_m3", "viscosity_Pa_s"):
        if key not in raw:
            raise ValueError(f"{where}.{key}: a fluid object needs density_kg_m3 and viscosity_Pa_s")
    name = raw.get("name", "fluid")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{where}.name: expected a short piece of text")
    return Fluid(name.strip(), kinds.number(raw["density_kg_m3"], where=f"{where}.density_kg_m3", positive=True),
                 kinds.number(raw["viscosity_Pa_s"], where=f"{where}.viscosity_Pa_s", positive=True))


def parse_opening(entry: dict, where: str) -> str:
    opening = entry.get("opening")
    if opening not in OPENINGS:
        raise ValueError(f"{where}.opening: {kinds.json_text(opening)} is not a side of the part's bounding box; "
                         f"use one of {list(OPENINGS)}")
    return opening


def distinct_openings(named: list[str]) -> None:
    for opening in named:
        if named.count(opening) > 1:
            raise ValueError(f"flow: {opening} is named twice; each side of the bounding box is one opening, an inlet or an outlet")


def flow_entries(flow: dict, key: str, example: str) -> list[dict]:
    raw = flow.get(key)
    if not isinstance(raw, list) or not raw or not all(isinstance(entry, dict) for entry in raw):
        raise ValueError(f"flow.{key}: expected a list like [{example}]")
    return raw


def vector3(raw: Any, where: str, example: str = "[5, 0, 0]") -> tuple[float, float, float]:
    """The free stream, m/s: three numbers, not all zero."""
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError(f"{where}: the free stream as [x, y, z] in m/s, like {example}")
    vector = tuple(kinds.number(c, where=where) for c in raw)
    if not any(vector):
        raise ValueError(f"{where}: the free stream is zero; give its speed and direction, like {example}")
    return vector  # type: ignore[return-value]


def parse_flow(document: dict) -> tuple[str, Fluid, tuple[Inlet, ...], tuple[Outlet, ...], tuple[float, float, float] | None]:
    flow = document.get("flow")
    if not isinstance(flow, dict):
        raise ValueError('study.flow: a flow study needs its flow, like {"kind": "internal", "fluid": "water", '
                         '"inlets": [{"opening": "x_min", "velocity_m_s": 0.5}], "outlets": [{"opening": "x_max", "pressure_Pa": 0}]}')
    kind = flow.get("kind", "internal")
    if kind not in ("internal", "external"):
        raise ValueError(f"flow.kind: {kinds.json_text(kind)} is not \"internal\" (through the part) or \"external\" (around it)")
    fluid = _fluid(flow.get("fluid"))
    if kind == "external":
        kinds.known(flow, {"kind", "fluid", "velocity_m_s"}, "flow",
                    "an external flow takes kind, fluid and velocity_m_s (the free stream, [x, y, z] in m/s)")
        if "velocity_m_s" not in flow:
            raise ValueError("flow.velocity_m_s: an external flow needs the free stream as [x, y, z] in m/s, like [5, 0, 0]")
        return kind, fluid, (), (), vector3(flow["velocity_m_s"], "flow.velocity_m_s")
    kinds.known(flow, {"kind", "fluid", "inlets", "outlets"}, "flow", "an internal flow takes kind, fluid, inlets and outlets")
    inlets = []
    for index, entry in enumerate(flow_entries(flow, "inlets", '{"opening": "x_min", "velocity_m_s": 0.5}')):
        where = f"flow.inlets[{index}]"
        kinds.known(entry, {"opening", "velocity_m_s", "profile"}, where, "an inlet takes opening, velocity_m_s and profile")
        if "velocity_m_s" not in entry:
            raise ValueError(f"{where}.velocity_m_s: the mean speed the fluid comes in at, in m/s")
        speed = kinds.number(entry["velocity_m_s"], where=f"{where}.velocity_m_s", positive=True)
        profile = entry.get("profile", "developed")
        if profile not in PROFILES:
            raise ValueError(f"{where}.profile: {kinds.json_text(profile)} is not one of {list(PROFILES)} "
                             "(developed: the settled laminar profile of the opening's shape; uniform: the same speed everywhere)")
        inlets.append(Inlet(parse_opening(entry, where), speed, profile))
    outlets = []
    for index, entry in enumerate(flow_entries(flow, "outlets", '{"opening": "x_max", "pressure_Pa": 0}')):
        where = f"flow.outlets[{index}]"
        kinds.known(entry, {"opening", "pressure_Pa"}, where, "an outlet takes opening and pressure_Pa")
        pressure = kinds.number(entry.get("pressure_Pa", 0.0), where=f"{where}.pressure_Pa")
        outlets.append(Outlet(parse_opening(entry, where), pressure))
    distinct_openings([entry.opening for entry in (*inlets, *outlets)])
    return kind, fluid, tuple(inlets), tuple(outlets), None


def parse_mapping(document: dict, pushes: str, pressure: str) -> tuple[tuple, Any]:
    """``map_to_structure``'s fixtures and material (``()``, None when the study does not map), and no stress or
    displacement check without it. ``pushes``, ``pressure``: "the flow pushes", "the flow's pressure"."""
    from cadgen._internal.fea.materials import material_from_spec
    from cadgen._internal.fea.study import parse_fixtures

    mapping = document.get("map_to_structure")
    fixtures: tuple = ()
    material = None
    if mapping is not None:
        if not isinstance(mapping, dict):
            raise ValueError('map_to_structure: expected an object like {"material": "aluminum-6061-t6", '
                             '"fixtures": [{"faces": ["#o1.f1"]}]}')
        kinds.known(mapping, {"material", "fixtures"}, "map_to_structure",
                    f"it takes the part's material and its fixtures (where it is held while {pushes} on it)")
        spec = mapping.get("material", document.get("material"))
        if spec is None:
            raise ValueError(f"map_to_structure.material: the part's material, to solve its stress under {pressure}")
        material = material_from_spec(spec)
        try:
            fixtures = parse_fixtures(mapping)
        except ValueError as exc:
            raise ValueError(f"map_to_structure.{exc}") from None
    if material is None:
        for index, check in kinds.view_checks(document):
            if check.get("kind") in ("stress", "displacement"):
                raise ValueError(f"view.checks[{index}]: a {check['kind']} check needs {pressure} on the part: "
                                 "add map_to_structure with the part's material and fixtures")
    return fixtures, material


# -- map_to_structure: the part under the fluid's pressure ---------------------------------------------


def map_structure(ctx: SolveContext, inputs, result: AnalysisResult, per_row) -> None:
    """One-way coupling: ``per_row`` (MPa on each boundary row of the part) as a static load on the part; its
    fields and checks join."""
    import types

    from cadgen._internal.fea import solve
    from cadgen._internal.fea.analyses.static import solved_record

    volume = ctx.volume
    extra: dict[str, Any] = {"space": ctx.space, "facet_pressures": per_row}
    if ctx.plan is not None and ctx.plan.solver != "direct":
        extra["solver"] = "iterative"
    outcome = solve.solve_linear_static(volume, inputs.structure_material, inputs.fixtures, (), ctx.ordinal_of,
                                        log=ctx.log, automatic=ctx.automatic, **extra)
    study = types.SimpleNamespace(material=inputs.structure_material,
                                  margin=getattr(ctx.study, "margin", 2.0) if ctx.study is not None else 2.0)
    result.solved = [solved_record(volume, outcome, study, ctx.ordinal_of, ctx.part_name, inputs.fixtures)]
    result.fields["von_mises"] = outcome.von_mises
    result.fields["displacement"] = outcome.displacement
    result.deformation = outcome.displacement
    result.reactions = list(outcome.reactions)
    result.applied = tuple(outcome.applied)
    result.warnings += [w for w in outcome.warnings if w not in result.warnings]
    result.scalars["outcome"] = outcome
    result.scalars["yield_MPa"] = inputs.structure_material.yield_strength


def structure_summary(result: AnalysisResult) -> dict:
    """The mapped part's peak stress, displacement and safety factor; empty when nothing was mapped."""
    if not result.solved:
        return {}
    import numpy as np

    from cadgen._internal.fea import checks
    from cadgen._internal.fea.analyses.static import floored

    outcome, solved = result.scalars["outcome"], result.solved[0]
    return {
        "max_von_mises_MPa": round(float(outcome.von_mises.max()), 4),
        "max_von_mises_at_mm": [round(c, 3) for c in solved.peak_at],
        "yield_MPa": solved.yield_MPa,
        "max_displacement_mm": round(float(np.linalg.norm(outcome.displacement, axis=1).max()), 6),
        "safety_factor": floored(checks.safety_factor(solved)),
    }


def structure_ranges(summary: dict) -> dict[str, tuple[float, float]]:
    if "max_von_mises_MPa" not in summary:
        return {}
    return {"von_mises": (0.0, summary["max_von_mises_MPa"]), "displacement": (0.0, summary["max_displacement_mm"])}


def structure_echo(inputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
    if not inputs.mapped:
        return {}
    material = inputs.structure_material
    return {"material": {"name": material.name, "yield_MPa": material.yield_strength,
                         "youngs_GPa": round(material.E / 1000.0, 6), "poisson": material.nu},
            "fixtures": [{"type": fixture.type, "faces": bare(fixture.faces)} for fixture in inputs.fixtures],
            "loads": []}


def structure_line(summary: dict, under: str) -> str:
    """"under the flow's pressure: peak von Mises ...", for a summary with the mapped part's numbers."""
    return (f"{under}: peak von Mises {summary['max_von_mises_MPa']:.4g} MPa, "
            f"largest displacement {summary['max_displacement_mm']:.4g} mm"
            + (f", safety factor {summary['safety_factor']:g}" if summary.get("safety_factor") is not None else ""))


def mapped_deformation_scale(result: AnalysisResult, bbox_diagonal: float, requested: float | None) -> float | None:
    if "displacement" not in result.fields:
        return None
    import numpy as np

    from cadgen._internal.fea.outputs import auto_deformation_scale

    magnitude = np.linalg.norm(result.fields["displacement"], axis=1)
    return requested or auto_deformation_scale(float(magnitude.max()), bbox_diagonal)


# -- shared by the flow analyses ---------------------------------------------------------------------


def cached_setup(cache: dict, ctx: SolveContext, inputs, refusal: str, build: Callable[[Any, dict], Any]):
    """``build(shape, refs)`` for this part and these inputs, once (a handful kept); ``refusal`` for an assembly."""
    shape = getattr(ctx.geometry, "shape", None)
    if ctx.assembly is not None or shape is None:
        raise ValueError(refusal)
    key = (id(shape), id(inputs))
    if key not in cache:
        setup = build(shape, {face.ordinal: face.ref for face in ctx.geometry.faces})
        if len(cache) > 4:
            cache.clear()
        cache[key] = setup
    return cache[key]


def iterative_rung(analysis, ctx: SolveContext, inputs, words: str):
    """The iterative rung: taken when it saves 5 % of what misses (memory if that misses, else time)."""
    from cadgen._internal.fea import fit

    plan, budget = ctx.plan, ctx.budget
    if plan.solver != "direct":
        return None
    before = analysis.estimate(ctx, inputs)
    plan.solver = "iterative"
    after = analysis.estimate(ctx, inputs)
    memory = before.memory_bytes > budget.memory_bytes
    saves = after.memory_bytes < 0.95 * before.memory_bytes if memory else after.seconds < 0.95 * before.seconds
    if not saves:
        plan.solver = "direct"
        return None
    why = "fit in memory" if memory else "finish sooner"
    return fit.Step("iterative", f"{words} to {why}", None, None,
                    detail={"solver": "iterative", "from_bytes": int(before.memory_bytes), "to_bytes": int(after.memory_bytes)})


def mesh_flow(ctx: SolveContext, domain, wall: float, far: float, timings: dict, what: str = "flow"):
    """The fluid region meshed (quadratic), its space, its faces by index and each boundary row's kind."""
    import time

    import numpy as np

    from cadgen._internal.fea import fluid_domain
    from cadgen._internal.fea.femspace import FemSpace

    timings["fluid_domain_s"] = domain.seconds
    if ctx.log:
        ctx.log(f"meshing the {domain.kind} {what} region at {wall:.3g} mm" + (f" (free stream {far:.3g} mm)" if far > wall else ""))
    started = time.perf_counter()
    fluid = fluid_domain.mesh_fluid(domain, wall, far)
    space = FemSpace.build(fluid, 2)
    timings["fluid_mesh_s"] = time.perf_counter() - started
    face_of = {face.index: face for face in domain.faces}
    row_kind = np.array([face_of[int(o)].kind if int(o) in face_of else "wall" for o in fluid.boundary_ordinal])
    return fluid, space, face_of, row_kind


def openings_of(fluid, face_of):
    """Each fluid boundary row's opening (``x_min`` ...), None off the openings."""
    import numpy as np

    return np.array([face_of[int(o)].opening if int(o) in face_of else None for o in fluid.boundary_ordinal], dtype=object)


def ordinals_of(fluid, face_of):
    """Each fluid boundary row's part face ordinal, 0 off the part."""
    import numpy as np

    return np.array([face_of[int(o)].ordinal if int(o) in face_of else 0 for o in fluid.boundary_ordinal])


def fluid_mesh_record(fluid, wall: float, far: float) -> dict:
    return {"elements": int(len(fluid.tets)), "nodes": int(len(fluid.nodes)), "wall_size_mm": round(wall, 4),
            "far_size_mm": round(far, 4)}


def reynolds_record(setup, kind: str) -> dict:
    return {"value": round(setup.reynolds, 2), "limit": RE_LIMIT[kind], "kind": kind, "length_mm": round(setup.length_mm, 4)}


def part_result(ctx: SolveContext, fields: dict, **more) -> AnalysisResult:
    """A result on the part's own space."""
    space = ctx.space
    return AnalysisResult(dof_locations=space.dof_locations, vertices=space.vertices, tets=space.tets,
                          boundary_quadratic=space.boundary_quadratic, element_dofs=space.element_dofs, fields=fields, **more)


def wall_fields(ctx: SolveContext, space, fluid, face_of, row_kind, post: dict, kind: str, length_mm: float, wall: float):
    """The wall pressure, wall shear and near-wall speed on the part, and the speed's reach (mm)."""
    import numpy as np

    pressure, shear = wall_to_part(ctx, space, fluid, face_of, row_kind, post)
    speed, reach = speed_to_part(ctx, space, fluid, face_of, row_kind, np.linalg.norm(post["nodal_velocity"], axis=1) / SPEED,
                                 speed_reach(kind, length_mm, wall))
    return pressure, shear, speed, reach


# -- the analysis -------------------------------------------------------------------------------------


@dataclass
class FlowSetup:
    """What the ladder estimates from and the solve starts from: the fluid region and its numbers."""

    domain: Any
    reynolds: float
    length_mm: float          # the Reynolds number's length: the inlet's hydraulic diameter, or the part's length
    wall_mm: float            # the default wall size


class CfdAnalysis(AnalysisBase):
    name: ClassVar[str] = "cfd"
    word: ClassVar[str] = "Flow"
    limits: ClassVar[tuple[str, ...]] = LIMITS
    study_keys: ClassVar[frozenset[str]] = frozenset({"flow", "map_to_structure"})
    #: The material is the structure's, and only a mapped flow has one (study.parse_study reads this).
    material_required: ClassVar[bool] = False
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("pressure", "_PRESSURE", "wall pressure", "Pa", signed=True),
        FieldSpec("wall_shear", "_WALL_SHEAR", "wall shear stress", "Pa"),
        FieldSpec("speed", "_SPEED", "flow speed near the wall", "m/s"),
        FieldSpec("von_mises", "_VON_MISES", "von Mises stress", "MPa"),
        FieldSpec("displacement", "_DISPLACEMENT", "displacement", "mm", 3, 1000.0),
    )
    checks: ClassVar[tuple] = (kinds.PRESSURE_DROP, kinds.VELOCITY, kinds.STRESS, kinds.DISPLACEMENT)
    default_checks: ClassVar[tuple[dict, ...]] = ()
    drives: ClassVar[tuple[str, ...]] = ("field", "deformation", "threshold")
    default_controls: ClassVar[dict[str, dict]] = {
        "field": {"drives": "field", "type": "enum", "options": ["pressure", "wall_shear", "speed"]},
    }
    ladder: ClassVar[tuple[str, ...]] = ("iterative", "fluid_coarsen", "continuation")
    noun: ClassVar[str] = "this flow"
    extras_word: ClassVar[str] = "flow"

    def __init__(self):
        self._setups: dict[tuple, Any] = {}

    def parse(self, document: dict) -> CfdInputs:
        kind, fluid, inlets, outlets, velocity = parse_flow(document)
        fixtures, material = parse_mapping(document, "the flow pushes", "the flow's pressure")
        refs = tuple(dict.fromkeys(ref for fixture in fixtures for ref in fixture.faces))
        return CfdInputs(refs, refs, material is not None, kind=kind, fluid=fluid, inlets=inlets, outlets=outlets,
                         velocity_m_s=velocity, fixtures=fixtures, structure_material=material)

    # -- the fluid region and its numbers ------------------------------------------------------------

    def setup_of(self, ctx: SolveContext, inputs: CfdInputs) -> FlowSetup:
        """The fluid region and its numbers (Reynolds number, length, default wall size), as this flow solves it."""
        return cached_setup(self._setups, ctx, inputs, ONE_PART, lambda shape, refs: self._build_setup(shape, refs, inputs))

    @staticmethod
    def _build_setup(shape, refs: dict, inputs: CfdInputs) -> FlowSetup:
        from cadgen._internal.fea import fluid_domain

        if inputs.kind == "internal":
            roles = {**{inlet.opening: "inlet" for inlet in inputs.inlets}, **{outlet.opening: "outlet" for outlet in inputs.outlets}}
            domain = fluid_domain.build_domain(shape, "internal", openings=roles, refs=refs)
            best = 0.0, 0.0, math.inf
            for inlet in inputs.inlets:
                for face in domain.faces:
                    if face.kind == "inlet" and face.opening == inlet.opening and face.perimeter > 0:
                        diameter = 4.0 * face.area / face.perimeter
                        if inlet.velocity_m_s * diameter > best[0] * best[1]:
                            best = inlet.velocity_m_s, diameter, best[2]
            diameters = [4.0 * face.area / face.perimeter for face in domain.faces if face.kind in ("inlet", "outlet") and face.perimeter > 0]
            speed, length = best[0], best[1]
            wall = min(diameters) / ACROSS if diameters else domain.length_mm / 10.0
        else:
            domain = fluid_domain.build_domain(shape, "external", velocity=inputs.velocity_m_s, refs=refs)
            speed = math.sqrt(sum(c * c for c in inputs.velocity_m_s))
            length = domain.length_mm
            wall = length / EXTERNAL_WALL
        reynolds = inputs.fluid.density_kg_m3 * speed * (length / 1000.0) / inputs.fluid.viscosity_Pa_s
        return FlowSetup(domain, reynolds, length, wall)

    def sizes(self, ctx: SolveContext, setup: FlowSetup) -> tuple[float, float]:
        """(wall, free-stream) element sizes, mm: the study's size (or the default) at walls and openings."""
        plan = ctx.plan
        wall = float(plan.size_mm) if plan is not None and plan.size_mm else setup.wall_mm
        default_far = wall * (EXTERNAL_FAR if setup.domain.kind == "external" else 1.0)
        far = plan.fluid_far_size_mm if plan is not None and plan.fluid_far_size_mm else default_far
        return wall, max(far, wall)

    # -- the ladder ----------------------------------------------------------------------------------

    def _tets(self, setup: FlowSetup, wall: float, far: float) -> float:
        domain = setup.domain
        from cadgen._internal.fea.fluid_domain import fine_faces

        area = sum(face.area for face in fine_faces(domain))
        volume = domain.volume_mm3
        if far > wall:
            near = min(volume, area * wall)
            return TETS_VOLUME * (near / wall ** 3 + (volume - near) / far ** 3) + TETS_SURFACE * area / wall ** 2
        return TETS_VOLUME * volume / wall ** 3 + TETS_SURFACE * area / wall ** 2

    def _cost(self, ctx: SolveContext, setup, iterative_factor: float = 1.0):
        """(tets, unknowns, seconds a linear solve, bytes) of the flow mesh the plan meshes. ``iterative_factor``: a
        Krylov solve's cost in laminar ones."""
        from cadgen._internal.fea import fit

        wall, far = self.sizes(ctx, setup)
        tets = self._tets(setup, wall, far)
        n = 3.0 * NODES_PER_TET * tets + 0.25 * tets
        build = BYTES_PER_TET * tets
        matrix = 12.0 * NNZ_PER_ROW * n
        if ctx.plan.solver == "iterative":
            return tets, n, iterative_factor * ITERATIVE_S * n, build + matrix * (1.0 + fit.AMG_COPIES)
        return tets, n, DIRECT_S * n ** 1.5, build + matrix * 2.0 + 16.0 * FILL * n ** 1.5

    @staticmethod
    def _estimate(ctx: SolveContext, inputs, n: float, memory: float, seconds: float):
        """The flow's estimate, the mapped part's solve added."""
        from cadgen._internal.fea import fit

        if inputs.mapped:
            structure = fit.solid_estimate(ctx)
            memory = max(memory, structure.memory_bytes - fit.BASE_BYTES)
            seconds += structure.seconds
        return fit.Estimate(dofs=int(n), memory_bytes=int(fit.BASE_BYTES + memory), seconds=float(seconds))

    def estimate(self, ctx: SolveContext, inputs: CfdInputs):
        setup = self.setup_of(ctx, inputs)
        tets, n, per, memory = self._cost(ctx, setup)
        solves = _expected_solves(setup.reynolds, ctx.plan.re_schedule, FAIL_FROM)
        return self._estimate(ctx, inputs, n, memory, MESH_S * tets + solves * (per + ASSEMBLE_S * tets))

    def apply(self, rung, ctx: SolveContext, inputs: CfdInputs):
        from cadgen._internal.fea import fit

        plan = ctx.plan
        setup = self.setup_of(ctx, inputs)
        if rung == "iterative":
            return iterative_rung(self, ctx, inputs, "Used an iterative flow solver (Krylov with a multigrid preconditioner)")
        if rung == "fluid_coarsen":
            wall, far = self.sizes(ctx, setup)
            low, high = setup.domain.box
            span = max(h - l for l, h in zip(low, high))
            coarser = min(COARSEN * far, span / 2.0)
            if coarser <= 1.05 * far:
                return None
            before = self.estimate(ctx, inputs)
            saved = plan.fluid_far_size_mm
            plan.fluid_far_size_mm = coarser
            after = self.estimate(ctx, inputs)
            if after.seconds > 0.95 * before.seconds and after.memory_bytes > 0.95 * before.memory_bytes:
                plan.fluid_far_size_mm = saved  # every element is near a wall: nothing to coarsen
                return None
            faces = tuple(face.ref for face in setup.domain.faces if face.kind == "wall" and face.ref)
            return fit.Step(
                "fluid_coarsen",
                f"Coarsened the flow mesh away from the walls to fit: {'walls and openings are' if setup.domain.kind == 'internal' else 'the part is'} still meshed at {wall:.3g} mm, "
                f"the free stream at {coarser:.3g} mm",
                "the flow's core is resolved more coarsely; the pressure drop and wall shear are set at the walls, which keep their size",
                None, faces=tuple(dict.fromkeys(faces)),
                detail={"wall_mm": round(wall, 4), "from_far_mm": round(far, 4), "to_far_mm": round(coarser, 4)},
            )
        if rung == "continuation":
            if plan.re_schedule or setup.reynolds <= CONTINUE_FROM:
                return None
            from cadgen._internal.fea.navier_stokes import CONTINUATION

            plan.re_schedule = tuple(setup.reynolds * share for share in CONTINUATION)
            stages = ", ".join(f"{re:.0f}" for re in plan.re_schedule)
            return fit.Step("continuation",
                            f"Reached Re {setup.reynolds:.0f} in steps (Re {stages}) from a slow flow, so the nonlinear "
                            "solve starts each stage near its answer", None, None,
                            detail={"reynolds": round(setup.reynolds, 2), "schedule": [round(re, 2) for re in plan.re_schedule]})
        return None

    # -- solve ---------------------------------------------------------------------------------------

    def deadline(self, ctx: SolveContext, inputs: CfdInputs) -> float:
        """Seconds the nonlinear flow solve may take before it stops at its best state, saying so: the study's
        time target, or DEADLINE_OVER times the estimate when the run went ahead past the target."""
        from cadgen._internal.fea import fit

        budget = getattr(ctx, "budget", None)
        target = float(getattr(budget, "seconds", 0.0) or fit.DEFAULT_SECONDS)
        try:
            expected = float(self.estimate(ctx, inputs).seconds)
        except Exception:  # noqa: BLE001 - no estimate: the target alone
            expected = 0.0
        return max(target, DEADLINE_OVER * expected)

    def solve(self, ctx: SolveContext, inputs: CfdInputs) -> AnalysisResult:
        import time

        from cadgen._internal.fea import navier_stokes

        timings: dict[str, float] = {}
        setup = self.setup_of(ctx, inputs)
        domain = setup.domain
        wall, far = self.sizes(ctx, setup)
        fluid, space, face_of, row_kind = mesh_flow(ctx, domain, wall, far, timings)

        started = time.perf_counter()
        problem = self.problem_of(space, fluid, face_of, row_kind, inputs)
        schedule = tuple(re / setup.reynolds for re in ctx.plan.re_schedule) if ctx.plan.re_schedule and setup.reynolds > 0 else ()
        solver = "iterative" if ctx.plan.solver == "iterative" else "direct"
        flow = navier_stokes.solve_flow(problem, schedule=schedule, solver=solver, log=ctx.log, deadline_s=self.deadline(ctx, inputs),
                                        min_solves=flow_solve_budget(setup.reynolds, tuple(getattr(ctx.plan, "re_schedule", ()) or ())))
        timings["flow_s"] = time.perf_counter() - started
        if ctx.log:
            ctx.log(f"flow: {flow.picard} Picard and {flow.newton} Newton steps, residual {flow.residual:.1e}")

        started = time.perf_counter()
        post = flow_numbers(space, fluid, face_of, row_kind, flow, inputs)
        pressure, shear, speed, reach = wall_fields(ctx, space, fluid, face_of, row_kind, post, inputs.kind, setup.length_mm, wall)
        timings["post_s"] = time.perf_counter() - started
        started = time.perf_counter()
        tracers = flow_tracers(space, row_kind, post, domain)
        timings["tracers_s"] = time.perf_counter() - started

        warnings = list(flow.warnings)
        analysis_warnings = []
        limit = RE_LIMIT[inputs.kind]
        reynolds = {**reynolds_record(setup, inputs.kind), "laminar": setup.reynolds <= limit}
        if setup.reynolds > limit:
            analysis_warnings.append(reynolds_sentence(setup.reynolds, limit))
        if post["imbalance"] > 0.01:
            analysis_warnings.append(balance_sentence(post["imbalance"]))
        if not flow.converged:
            analysis_warnings.append(navier_stokes.unsettled_sentence(flow))
        # The Reynolds and balance sentences are findings (and extras.analysis.warnings); an unsettled solve is a warning too.
        warnings += [w for w in analysis_warnings if w.startswith("the flow solve stopped") and w not in warnings]

        result = part_result(
            ctx, {"pressure": pressure, "wall_shear": shear, "speed": speed},
            dofs=int(problem.space.basis.N + flow.pressure_basis.N),
            solver=f"Taylor-Hood P2/P1 Navier-Stokes, {'GMRES + multigrid' if solver == 'iterative' else 'direct'}",
            timings={**timings, **{f"flow_{k}": v for k, v in flow.timings.items()}}, warnings=warnings,
            scalars={
                "post": post, "reynolds": reynolds, "flow": flow, "wall_mm": wall, "far_mm": far, "speed_reach_mm": reach,
                "fluid_mesh": fluid_mesh_record(fluid, wall, far), "analysis_warnings": analysis_warnings,
                # Structured, beside the sentence: what extras.analysis carries for the viewer.
                "analysis_extras": {"reynolds": reynolds, **({"motion": tracers} if tracers else {})},
            },
        )
        return self._mapped(ctx, inputs, result, pressure)

    def problem_of(self, space, fluid, face_of, row_kind, inputs: CfdInputs, setup: FlowSetup | None = None):
        """The flow problem on the fluid's space (what :func:`..navier_stokes.solve_flow` takes): walls held, inlets
        at their profile, outlets at their pressure."""
        import numpy as np

        from cadgen._internal.fea import navier_stokes

        table = navier_stokes.vector_dofs(space)
        locations = space.dof_locations
        boundary = space.boundary_quadratic
        values: dict[int, float] = {}
        rho = inputs.fluid.density_kg_m3 * DENSITY
        mu = inputs.fluid.viscosity_Pa_s * VISCOSITY
        outlets = []
        walls = np.unique(boundary[row_kind == "wall"])
        opening_of = openings_of(fluid, face_of)
        if inputs.kind == "external":
            stream = np.array(inputs.velocity_m_s, dtype=float) * SPEED
            for side in np.unique(opening_of[row_kind == "side"]):
                axis = "xyz".index(side[0])
                for node in np.unique(boundary[(row_kind == "side") & (opening_of == side)]):
                    values[int(table[node, axis])] = 0.0
            for node in np.unique(boundary[row_kind == "inlet"]):
                for c in range(3):
                    values[int(table[node, c])] = float(stream[c])
            outlets.append((space.facets_of_rows(np.flatnonzero(row_kind == "outlet")), 0.0))
        else:
            for inlet in inputs.inlets:
                rows = np.flatnonzero((row_kind == "inlet") & (opening_of == inlet.opening))
                axis, high = "xyz".index(inlet.opening[0]), inlet.opening.endswith("max")
                inward = -1.0 if high else 1.0
                if inlet.profile == "developed":
                    shape = navier_stokes.developed_profile(locations, boundary[rows], axis)
                else:
                    shape = {int(n): 1.0 for n in np.setdiff1d(np.unique(boundary[rows]), walls)}
                # The mean speed over the opening's true area: scale the profile so its flux is V A.
                area = sum(face.area for face in face_of.values() if face.kind == "inlet" and face.opening == inlet.opening)
                flux = flux_of(space, rows, shape, axis)
                scale = inlet.velocity_m_s * SPEED * area / flux if flux > 0 else inlet.velocity_m_s * SPEED
                for node, w in shape.items():
                    for c in range(3):
                        values[int(table[node, c])] = inward * scale * w if c == axis else 0.0
            for outlet in inputs.outlets:
                rows = np.flatnonzero((row_kind == "outlet") & (opening_of == outlet.opening))
                outlets.append((space.facets_of_rows(rows), outlet.pressure_Pa / PASCAL))
        # Walls last: no slip wins at the rim of every opening.
        for node in walls:
            for c in range(3):
                values[int(table[node, c])] = 0.0
        dofs = np.fromiter(values.keys(), dtype=np.int64, count=len(values))
        given = np.fromiter(values.values(), dtype=float, count=len(values))
        return navier_stokes.FlowProblem(space, rho, mu, dofs, given, outlets)

    @staticmethod
    def _mapped(ctx: SolveContext, inputs, result: AnalysisResult, pressure) -> AnalysisResult:
        """With map_to_structure: the wall pressure on the wetted faces, face by face, as a static load on the part."""
        if inputs.mapped:
            import time

            import numpy as np

            started = time.perf_counter()
            rows = np.isin(ctx.volume.boundary_ordinal, sorted(result.scalars["post"]["wetted"]))
            map_structure(ctx, inputs, result, np.where(rows, pressure[ctx.space.boundary_quadratic].mean(axis=1), 0.0) / PASCAL)
            result.timings["structure_s"] = time.perf_counter() - started
        return result

    # -- judging -------------------------------------------------------------------------------------

    def _measured(self, check: dict, post: dict) -> tuple:
        """(value, limit, unit, default label, where) of a flow check."""
        if check["kind"] == "pressure_drop":
            return post["pressure_drop_Pa"], check["limit_Pa"], "Pa", "Flow resistance", post["inlet_at"]
        return post["max_velocity_m_s"], check["limit_m_s"], "m/s", "Flow speed", post["max_velocity_at"]

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: CfdInputs) -> dict:
        from cadgen._internal.fea.checks import check_status

        settled = result.scalars["flow"].converged
        if check["kind"] in ("stress", "displacement"):
            from cadgen._internal.fea.analyses import get_analysis

            return unsettled_check(get_analysis("static").judge(check, index, ctx, result, inputs), settled)
        value, limit, unit, label, at = self._measured(check, result.scalars["post"])
        ratio = value / limit
        return unsettled_check({
            "kind": check["kind"], "label": check.get("label") or label, "value": round(value, 6), "limit": limit,
            "unit": unit, "ratio": round(ratio, 6), "close_at": CLOSE_AT, "status": check_status(ratio, CLOSE_AT),
            "where": {"ref": None, "at": xyz(at)},
        }, settled)

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: CfdInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        from cadgen._internal.fea import checks

        found: list[dict] = list(checks.findings(result.solved[0])) if result.solved else []
        reynolds = result.scalars["reynolds"]
        if not reynolds["laminar"]:
            found.append(finding(
                "warning", "reynolds_past_laminar", reynolds_sentence(reynolds["value"], reynolds["limit"]),
                f"Re {reynolds['value']:.0f} from the {'inlet hydraulic diameter' if inputs.kind == 'internal' else 'part length'} "
                f"of {reynolds['length_mm']:.4g} mm; this solver has no turbulence model"))
        flow = result.scalars["flow"]
        if flow.continued and not ctx.plan.re_schedule:
            stages = ", ".join(f"{share * reynolds['value']:.0f}" for share in flow.stages)
            found.append(finding(
                "info", "continuation", f"Newton did not converge at Re {reynolds['value']:.0f} from a slow flow, so the "
                f"Reynolds number was stepped up ({stages})",
                "the steady flow was reached by continuation; the answer is the same steady flow"))
        if not flow.converged:
            from cadgen._internal.fea import navier_stokes

            sentence = navier_stokes.unsettled_sentence(flow)
            found.append(finding(
                "warning", "flow_unsettled", sentence[:1].upper() + sentence[1:],
                "every check on this flow is marked not settled and none passes outright; a finer mesh, "
                "a slower flow or a longer time target may settle it"))
        found += limit_findings(check_results, FLOW_WORDS)
        post = result.scalars["post"]
        if post["imbalance"] > 0.01:
            found.append(finding(
                "warning", "flow_balance", f"The flow in and out differ by {post['imbalance']:.1%}: the flow may not have settled",
                "the solve's residual leaves fluid unaccounted for; a finer mesh should close it"))
        return found

    # -- what is written -----------------------------------------------------------------------------

    def deformation_scale(self, result: AnalysisResult, bbox_diagonal: float, requested: float | None) -> float | None:
        return mapped_deformation_scale(result, bbox_diagonal, requested)

    def summary(self, result: AnalysisResult, inputs: CfdInputs, check_results: list[dict]) -> dict:
        flow, reynolds = result.scalars["flow"], result.scalars["reynolds"]
        return {
            **flow_summary(result, inputs),
            "solve": {"picard": flow.picard, "newton": flow.newton, "linear_solves": flow.linear_solves,
                      "residual": float(f"{flow.residual:.3g}"), "converged": flow.converged, "continuation": flow.continued,
                      "stages_Re": [round(s * reynolds["value"], 2) for s in flow.stages], "solver": flow.solver},
            "fluid_mesh": dict(result.scalars["fluid_mesh"]),
            "deformation_scale": result.scalars.get("deformation_scale"),
            **structure_summary(result),
            "checks": check_results,
        }

    def field_ranges(self, summary: dict, result: AnalysisResult) -> dict[str, tuple[float, float]]:
        return {"pressure": (summary["min_wall_pressure_Pa"], summary["max_wall_pressure_Pa"]),
                "wall_shear": (0.0, summary["max_wall_shear_Pa"]), "speed": (0.0, summary["max_speed_near_wall_m_s"]),
                **structure_ranges(summary)}

    def extras_head(self, summary: dict) -> dict:
        return {"safety_factor": summary["safety_factor"]} if "safety_factor" in summary else {}

    def extras_assembly(self, summary: dict) -> dict:
        return {}

    def study_echo(self, inputs: CfdInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        fluid = {"name": inputs.fluid.name, "density_kg_m3": inputs.fluid.density_kg_m3, "viscosity_Pa_s": inputs.fluid.viscosity_Pa_s}
        if inputs.kind == "external":
            flow: dict[str, Any] = {"kind": "external", "fluid": fluid, "velocity_m_s": [float(c) for c in inputs.velocity_m_s]}
        else:
            flow = {"kind": "internal", "fluid": fluid,
                    "inlets": [{"opening": i.opening, "velocity_m_s": i.velocity_m_s, "profile": i.profile} for i in inputs.inlets],
                    "outlets": [{"opening": o.opening, "pressure_Pa": o.pressure_Pa} for o in inputs.outlets]}
        return {"flow": flow, **structure_echo(inputs, bare)}

    def human_lines(self, summary: dict) -> list[str]:
        reynolds = summary["reynolds"]
        solve = summary["solve"]
        lines = [
            f"{summary['flow_kind']} flow of {summary['fluid']['name']}: Re {reynolds['value']:.4g} "
            f"({'laminar' if reynolds['laminar'] else 'past the laminar range, above Re ' + str(reynolds['limit'])})",
            *flow_lines(summary),
            f"solved by {solve['picard']} Picard and {solve['newton']} Newton steps"
            + (f" through Re {', '.join(f'{re:g}' for re in solve['stages_Re'])}" if solve["continuation"] else "")
            + f", residual {solve['residual']:.1e}" + ("" if solve["converged"] else " (not settled)"),
        ]
        if "max_von_mises_MPa" in summary:
            lines.append(structure_line(summary, "under the flow's pressure"))
        return lines


def flow_summary(result: AnalysisResult, inputs: CfdInputs) -> dict:
    """The summary's flow numbers, as an incompressible flow (laminar or turbulent) writes them."""
    post = result.scalars["post"]
    wet = post["wetted_nodes"]
    pressure, shear = result.fields["pressure"], result.fields["wall_shear"]
    return {
        "flow_kind": inputs.kind,
        "fluid": {"name": inputs.fluid.name, "density_kg_m3": inputs.fluid.density_kg_m3,
                  "viscosity_Pa_s": inputs.fluid.viscosity_Pa_s},
        "reynolds": dict(result.scalars["reynolds"]),
        "max_velocity_m_s": round(post["max_velocity_m_s"], 6),
        "max_velocity_at_mm": xyz(post["max_velocity_at"]),
        "flow_rate_m3_s": float(f"{post['flow_rate_m3_s']:.6g}"),
        "flow_rate_L_min": round(post["flow_rate_m3_s"] * 60000.0, 6),
        "outflow_rate_m3_s": float(f"{post['outflow_rate_m3_s']:.6g}"),
        "flow_balance": round(post["imbalance"], 6),
        "pressure_drop_Pa": round(post["pressure_drop_Pa"], 6),
        "inlet_pressure_Pa": round(post["inlet_pressure_Pa"], 6),
        "outlet_pressure_Pa": round(post["outlet_pressure_Pa"], 6),
        "max_wall_pressure_Pa": round(float(pressure[wet].max()), 6) if len(wet) else 0.0,
        "min_wall_pressure_Pa": round(float(pressure[wet].min()), 6) if len(wet) else 0.0,
        "max_wall_shear_Pa": round(float(shear[wet].max()), 6) if len(wet) else 0.0,
        "max_speed_near_wall_m_s": round(float(result.fields["speed"][wet].max()), 6) if len(wet) else 0.0,
        "speed_reach_mm": round(result.scalars["speed_reach_mm"], 4),
        "force_N": [round(float(c), 9) for c in post["force_N"]],
    }


def flow_lines(summary: dict) -> list[str]:
    return [f"pressure drop {summary['pressure_drop_Pa']:.4g} Pa, flow {summary['flow_rate_L_min']:.4g} L/min, "
            f"fastest {summary['max_velocity_m_s']:.4g} m/s",
            f"wall pressure {summary['min_wall_pressure_Pa']:.4g} to {summary['max_wall_pressure_Pa']:.4g} Pa, "
            f"wall shear up to {summary['max_wall_shear_Pa']:.4g} Pa, force on the part {summary['force_N']} N"]


# -- the flow solve's budget ---------------------------------------------------------------------------


def flow_tracers(space, row_kind, post: dict, domain, label: str = "Flow") -> dict | None:
    """What the viewer's Flow routine plays (``extras.analysis.motion``, kind ``tracers``): particles let go at the
    inlets and carried by the solved velocity (:func:`motion.tracer_record`); an external flow's across the part's
    own width. None where the flow has no inlet to let them go from."""
    from cadgen._internal.fea import motion

    near = domain.part_box if getattr(domain, "kind", "internal") == "external" else None
    return motion.tracer_record(space, row_kind, post["nodal_velocity"], label=label, near_box=near)


def _expected_solves(reynolds: float, schedule: tuple[float, ...], fallback_above: float) -> int:
    """Linear solves the nonlinear iteration is expected to take: Picard slows as Re grows, and above
    ``fallback_above`` a Newton start from Stokes is expected to fail and fall back to continuation; a planned
    continuation (``schedule``) takes a few steps a stage."""
    from cadgen._internal.fea.navier_stokes import CONTINUATION, MAX_PICARD

    stages = 1 + 4 * len(CONTINUATION) + 6
    if schedule:
        return stages
    return 1 + int(min(MAX_PICARD, 3 + reynolds / 50.0)) + 6 + (stages if reynolds > fallback_above else 0)


def flow_solve_budget(reynolds: float, schedule: tuple[float, ...] = ()) -> int:
    """The linear solves a flow solve at ``reynolds`` may make before its time limit can stop it: DEADLINE_OVER times
    what it is expected to need. Above CONTINUE_FROM that counts the continuation Newton falls back to when it fails
    from the Stokes flow (a sharp orifice fails there at Re 299, well under FAIL_FROM, and settles in 90 solves), as
    well as the direct attempt. Wall-clock free: the seconds those solves take are the machine's, and its load only
    makes them take longer."""
    return int(math.ceil(DEADLINE_OVER * _expected_solves(reynolds, schedule, CONTINUE_FROM)))


# -- numbers from the flow ----------------------------------------------------------------------------


def wall_triangles(space, rows):
    """Boundary triangles ``rows``: each one's area (flat, through its corners) and unit normal pointing out of
    the fluid (away from the element it closes). Integrals over them use the quadratic triangle's rule, exact
    for a quadratic on a flat triangle: the area times the mean of the three mid-edge values."""
    import numpy as np

    triangles = space.boundary_quadratic[rows]
    corners = space.dof_locations[triangles[:, :3]]
    cross = np.cross(corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0])
    area = 0.5 * np.linalg.norm(cross, axis=1)
    normal = cross / np.where(area > 0, 2.0 * area, 1.0)[:, None]
    element = space.mesh.f2t[0, space.facets_of_rows(rows)]
    inside = space.mesh.p[:, space.mesh.t[:, element]].mean(axis=1).T     # each closing element's centroid
    flip = np.einsum("ij,ij->i", normal, inside - corners[:, 0]) > 0
    normal[flip] *= -1.0
    return triangles, area, normal


def surface_integral(space, rows, nodal) -> float:
    """∫ f dA over boundary triangles ``rows`` of a field given at the quadratic nodes (scalar per node)."""
    import numpy as np

    if len(rows) == 0:
        return 0.0
    triangles, area, _ = wall_triangles(space, rows)
    return float((area * np.asarray(nodal)[triangles[:, 3:]].mean(axis=1)).sum())


def flux_of(space, rows, values: dict[int, float], axis: int) -> float:
    """∫ w dA of a profile given by node over the opening's triangles ``rows``."""
    import numpy as np

    field = np.zeros(space.scalar_count)
    for node, w in values.items():
        field[node] = w
    return surface_integral(space, rows, field)


def _node_gradients(space, u) -> "np.ndarray":
    """(scalar_count, 3, 3) the velocity gradient at every quadratic node, averaged over the elements sharing it.

    The gradient is evaluated at the element's own nodes (a quadrature placed on them), so no point is mapped
    back into a curved element."""
    import numpy as np
    from skfem import CellBasis, ElementTetP2, ElementVector

    element = ElementTetP2()
    points = np.ascontiguousarray(element.doflocs.T)
    basis = CellBasis(space.mesh, ElementVector(element), quadrature=(points, np.full(points.shape[1], 1.0 / points.shape[1])))
    grad = basis.interpolate(u).grad                     # (3, 3, E, 10)
    nodes = space.scalar.element_dofs                    # (10, E): the same local order as the element's nodes
    total = np.zeros((space.scalar_count, 3, 3))
    count = np.zeros(space.scalar_count)
    flat = nodes.T.ravel()
    np.add.at(total, flat, np.moveaxis(grad, (0, 1), (2, 3)).reshape(-1, 3, 3))
    np.add.at(count, flat, 1.0)
    return total / np.maximum(count, 1.0)[:, None, None]


def pressure_nodes(space, p):
    """The linear pressure ``p`` at every quadratic node: the corners' own, each edge's mean of its two corners."""
    import numpy as np

    nodes = np.zeros(space.scalar_count)
    nodes[: space.vertices] = p[: space.vertices]
    edges = space.mesh.edges
    nodes[space.scalar.edge_dofs[0]] = 0.5 * (p[edges[0]] + p[edges[1]])
    return nodes


def flow_numbers(space, fluid, face_of, row_kind, flow, inputs: CfdInputs) -> dict:
    """The flow's numbers: fastest speed, flow rates, pressure drop, force on the part, and per-node wall values."""
    import numpy as np

    mu = inputs.fluid.viscosity_Pa_s * VISCOSITY
    boundary = space.boundary_quadratic
    nodal = space.nodal(flow.u)
    p_nodes = pressure_nodes(space, flow.p)

    def mean_pressure(rows) -> float:
        area = surface_integral(space, rows, np.ones(space.scalar_count))
        return surface_integral(space, rows, p_nodes) / area if area > 0 else 0.0

    def flux(rows) -> float:
        if len(rows) == 0:
            return 0.0
        triangles, area, normal = wall_triangles(space, rows)
        along = np.einsum("tki,ti->tk", nodal[triangles[:, 3:]], normal)
        return float((area * along.mean(axis=1)).sum())

    inlets, outlets = np.flatnonzero(row_kind == "inlet"), np.flatnonzero(row_kind == "outlet")
    inflow, outflow = -flux(inlets), flux(outlets)
    p_in, p_out = mean_pressure(inlets), mean_pressure(outlets)
    speed = np.linalg.norm(nodal, axis=1)
    fastest = int(speed.argmax())

    # Wall shear and the force on the part, at the wall's nodes, from the velocity gradient there.
    walls = np.flatnonzero(row_kind == "wall")
    shear_nodes = np.zeros(space.scalar_count)
    force = np.zeros(3)
    if len(walls):
        triangles, area, normal = wall_triangles(space, walls)
        node_normal = np.zeros((space.scalar_count, 3))
        np.add.at(node_normal, triangles.ravel(), np.repeat(normal * area[:, None], triangles.shape[1], axis=0))
        length = np.linalg.norm(node_normal, axis=1)
        node_normal /= np.where(length > 0, length, 1.0)[:, None]
        gradient = _node_gradients(space, flow.u)
        on = np.unique(triangles)
        strain = gradient[on] + np.swapaxes(gradient[on], 1, 2)
        n = node_normal[on]
        traction = mu * np.einsum("kij,kj->ki", strain, n)
        tangential = traction - np.einsum("ki,ki->k", traction, n)[:, None] * n
        shear_nodes[on] = np.linalg.norm(tangential, axis=1)
        # Force of the fluid on the part: (p n - mu (grad u + grad u^T) n) over the wall, n out of the fluid.
        node_traction = np.zeros((space.scalar_count, 3))
        node_traction[on] = traction
        mids = triangles[:, 3:]
        per = p_nodes[mids].mean(axis=1)[:, None] * normal - node_traction[mids].mean(axis=1)
        force = (area[:, None] * per).sum(axis=0)

    inlet_points = boundary[inlets].ravel()
    inlet_at = space.dof_locations[inlet_points].mean(axis=0) if len(inlet_points) else np.zeros(3)
    return {
        "max_velocity_m_s": float(speed[fastest]) / SPEED,
        "max_velocity_at": space.dof_locations[fastest],
        "flow_rate_m3_s": inflow * 1e-9,
        "outflow_rate_m3_s": outflow * 1e-9,
        "imbalance": abs(inflow - outflow) / abs(inflow) if inflow else 0.0,
        "inlet_pressure_Pa": p_in * PASCAL,
        "outlet_pressure_Pa": p_out * PASCAL,
        "pressure_drop_Pa": (p_in - p_out) * PASCAL,
        "inlet_at": inlet_at,
        "force_N": force,
        "pressure_nodes_Pa": p_nodes * PASCAL,
        "shear_nodes_Pa": shear_nodes * PASCAL,
        "nodal_velocity": nodal,                 # (fluid nodes, 3) mm/s
        "velocity_locations": space.dof_locations,  # where, mm
    }


def fastest_near(points, sources, speed, reach: float):
    """(len(points),) the fastest of ``speed`` at ``sources`` within ``reach`` mm of each of ``points`` (0 where none
    is): the sources from the fastest down, each settling the points in its reach not settled yet, so the work is
    one ball per source until every point is settled."""
    import numpy as np
    from scipy.spatial import cKDTree

    out = np.full(len(points), -1.0)
    if not len(points) or not len(sources):
        return np.zeros(len(points))
    tree = cKDTree(points)
    left = len(points)
    order = np.argsort(-np.asarray(speed), kind="stable")
    for start in range(0, len(order), 256):
        chunk = order[start:start + 256]
        for source, near in zip(chunk, tree.query_ball_point(sources[chunk], r=reach)):
            if not near:
                continue
            near = np.asarray(near, dtype=np.int64)
            new = near[out[near] < 0.0]
            if len(new):
                out[new] = float(speed[source])
                left -= len(new)
        if left == 0:
            break
    return np.maximum(out, 0.0)


def speed_to_part(ctx: SolveContext, space, fluid, face_of, row_kind, speed, reach: float):
    """The flow's speed on the part's wetted surface (m/s): at each wetted point, the fastest the fluid moves
    within ``reach`` mm of it (the wall itself is still: no slip). ``speed`` is at every fluid node, m/s. The reach
    grows, if it must, to take in the fastest point of the whole flow, so the field shows the speed a velocity
    check judges. Carried face by face as the wall pressure is; a face the fluid does not wet reads 0. Returns the
    field and the reach used, mm."""
    import numpy as np
    from scipy.spatial import cKDTree

    speed = np.asarray(speed, dtype=float)
    wall_rows = (row_kind == "wall") & (ordinals_of(fluid, face_of) > 0)
    if not wall_rows.any():
        return np.zeros(ctx.space.scalar_count), float(reach)
    locations = space.dof_locations
    wall_nodes = np.unique(space.boundary_quadratic[wall_rows])
    gap, _ = cKDTree(locations[wall_nodes]).query(locations[int(speed.argmax())])
    reach = max(float(reach), 1.05 * float(gap))
    at_wall = np.zeros(space.scalar_count)
    at_wall[wall_nodes] = fastest_near(locations[wall_nodes], locations, speed, reach)
    return to_part(ctx, space, fluid, face_of, row_kind, {"speed": at_wall})["speed"], reach


def speed_reach(kind: str, length_mm: float, wall_mm: float) -> float:
    """How far into the fluid the speed field looks from each wetted point, mm: an internal flow's passage width
    (the inlet's hydraulic diameter), an external flow's SPEED_WALLS wall elements."""
    return float(length_mm) if kind == "internal" else SPEED_WALLS * float(wall_mm)


def to_part(ctx: SolveContext, space, fluid, face_of, row_kind, nodal: dict, *, corners: bool = False,
            default: dict | None = None) -> dict:
    """Fluid wall node values carried onto the part's surface nodes, face by face (the nearest fluid wall node on the
    same face; ``corners``: its corners alone, for values at the fluid's vertices). A part node the fluid does not
    wet reads ``default`` (else 0). Adds ``wetted`` (the part's wetted face ordinals) and ``wetted_nodes``."""
    import numpy as np
    from scipy.spatial import cKDTree

    part_space, volume = ctx.space, ctx.volume
    default = default or {}
    out = {name: np.full(part_space.scalar_count, float(default.get(name, 0.0))) for name in nodal}
    ordinal_of_row = ordinals_of(fluid, face_of)
    wetted: set[int] = set()
    wet_nodes: list = []
    for ordinal in sorted(set(int(o) for o in ordinal_of_row[row_kind == "wall"]) - {0}):
        source = space.boundary_quadratic[(row_kind == "wall") & (ordinal_of_row == ordinal)]
        source = np.unique(source[:, :3] if corners else source)
        target = np.unique(part_space.boundary_quadratic[volume.boundary_ordinal == ordinal])
        if len(source) == 0 or len(target) == 0:
            continue
        wetted.add(ordinal)
        _, nearest = cKDTree(space.dof_locations[source]).query(part_space.dof_locations[target])
        for name, values in nodal.items():
            out[name][target] = np.asarray(values)[source[nearest]]
        wet_nodes.append(target)
    out["wetted"] = wetted
    out["wetted_nodes"] = np.unique(np.concatenate(wet_nodes)) if wet_nodes else np.zeros(0, dtype=np.int64)
    return out


def wall_to_part(ctx: SolveContext, space, fluid, face_of, row_kind, post: dict):
    """The wall pressure and shear carried onto the part's surface nodes (:func:`to_part`); the wetted faces into
    ``post``."""
    out = to_part(ctx, space, fluid, face_of, row_kind, {"pressure": post["pressure_nodes_Pa"], "shear": post["shear_nodes_Pa"]})
    post["wetted"], post["wetted_nodes"] = out["wetted"], out["wetted_nodes"]
    return out["pressure"], out["shear"]
