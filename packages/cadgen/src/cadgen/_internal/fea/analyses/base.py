"""The protocol every analysis implements, and the dataclasses it trades in.

Stdlib only at import: numeric work lives inside each analysis's ``solve``,
``estimate`` and ``apply``, so study parsing and ``cadgen fea --help`` stay
instant. Units are the engine's: mm, N, MPa, tonne/mm^3, s.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

if TYPE_CHECKING:
    import numpy as np
    from cadgen._internal.fea.femspace import FemSpace
    from cadgen._internal.fea.fit import Budget, Estimate, FitPlan, Step
    from cadgen._internal.fea.materials import Material
    from cadgen._internal.fea.mesh import VolumeMesh

__all__ = [
    "Analysis", "AnalysisBase", "AnalysisResult", "CheckSpec", "FieldSpec", "Inputs", "Rung", "Scaling", "Series",
    "SeriesFrame", "SeriesKind", "SolveContext", "Tier", "by_severity", "finding", "frame_attributes", "plan_value",
    "max_frames", "mirrored", "unfold_result", "xyz",
]

Tier = Literal[1, 2, 3]
SeriesKind = Literal["mode", "time", "frequency"]
#: How a check's value moves with the load multiple k the viewer's load control sets.
Scaling = Literal["linear", "inverse", "none"]
Rung = Literal["iterative", "local_refine", "defeature", "linear_elements", "idealise",
               "reduce_modes", "symmetry", "mass_scaling", "subcycling", "window",
               "adaptive_steps", "frequency_grid", "fluid_coarsen", "far_field", "continuation",
               # topology: a coarser fixed design mesh (it cannot refine locally; it says the member size it resolves).
               "coarse_design"]


@dataclass(frozen=True)
class FieldSpec:
    name: str                 # the view's name: "von_mises", "temperature", "mode_shape"
    attribute: str            # GLB attribute: "_VON_MISES", "_TEMPERATURE"
    title: str                # extras.fields[].name: "von Mises stress"
    units: str                # "MPa", "mm", "°C", "W/m²", "", "log10 cycles"
    components: Literal[1, 3] = 1
    attribute_scale: float = 1.0   # stored value * scale = units (a vector in glTF metres: 1000)
    signed: bool = False      # min is the field's own min, not 0 (temperature)
    per_frame: bool = False   # written once per series frame


@dataclass(frozen=True)
class CheckSpec:
    kind: str                                  # "frequency"
    keys: frozenset[str]                       # accepted keys, "kind" and "label" included
    parse: Callable[[dict, str], dict]         # (entry, where) -> normalised check; ValueError names the field
    default_label: str                         # "Vibration"
    scaling: Scaling = "linear"
    unique: bool = False                       # at most one per study (stress)


@dataclass(frozen=True)
class SeriesFrame:
    value: float               # 85.2 (Hz), 0.012 (s), 1 (mode index when kind == "mode")
    label: str                 # "Mode 1 · 85 Hz", "12 ms", "118 Hz"
    attributes: dict[str, str] # field name -> attribute holding this frame ("mode_shape": "_MODE_SHAPE_F2")


@dataclass
class Series:
    kind: SeriesKind
    unit: str                  # "Hz", "s", "%"
    frames: list[SeriesFrame]
    default: int = 0           # the frame the viewer opens on (the peak, mode 1)


@dataclass(frozen=True)
class Inputs:
    """Base of every analysis's parsed inputs (each analysis subclasses it, frozen)."""
    face_refs: tuple[str, ...]        # every face it names; resolved before meshing
    anchor_refs: tuple[str, ...]      # faces that make the problem well posed (fixtures, fixed T, convection)
    requires_anchor: bool             # static: True; modal: False (free-free allowed); thermal: True


@dataclass
class SolveContext:
    volume: "VolumeMesh"
    materials: "tuple[Material, ...]"      # one per domain; a single part is (material,)
    ordinal_of: dict[str, int]
    space: "FemSpace"                      # built once per mesh, shared by upstream and downstream solves
    log: Callable[[str], None] | None
    automatic: bool                        # the finer re-solve cadgen chose
    upstream: dict[str, "AnalysisResult"]  # by analysis name, solved first on the same mesh
    budget: "Budget"                       # targets the ladder fits to (section 9); never a refusal
    plan: "FitPlan"                        # the ladder's choices so far, read by solve()
    # Additive to the spec's fields: what run.py knows that an analysis reads.
    study: Any = None                      # the parsed study.Study (material, margin, loads as written)
    assembly: Any = None                   # run's assembly plan (parts, names, materials); None for one part
    part_name: str = ""                    # one part's name in findings (the document's stem)
    geometry: Any = None                   # fit.Geometry: what the ladder estimates from before meshing
    meshed_plan: Any = None                # the plan key ``volume`` was meshed with (fit counts it, not the geometry)


@dataclass
class AnalysisResult:
    dof_locations: "np.ndarray"            # (M, 3) mm
    vertices: int
    tets: "np.ndarray"
    boundary_quadratic: "np.ndarray"       # (B, 6), or (B, 3) for linear elements
    element_dofs: "np.ndarray"
    # By FieldSpec.name: (M,) or (M, 3). For a series result, frame 0 (whatever ``series.default`` the
    # viewer opens on): run.py writes it under the field's own attribute, the other frames after it.
    fields: dict[str, "np.ndarray"]
    fields_by_part: dict[str, "np.ndarray"] = field(default_factory=dict)  # assembly per-part scalar
    deformation: "np.ndarray | None" = None   # (M, 3) baked into POSITION and written as _DISPLACEMENT
    series: Series | None = None
    frame_fields: dict[str, list["np.ndarray"]] = field(default_factory=dict)  # by field name, one per frame
    scalars: dict[str, Any] = field(default_factory=dict)   # unrounded numbers its summary reads
    curves: dict[str, dict] = field(default_factory=dict)   # {"x": [...], "x_unit": "s", "y": [...], "y_unit": "°C"}
    reactions: list[tuple[float, float, float]] = field(default_factory=list)
    applied: tuple[float, float, float] = (0.0, 0.0, 0.0)
    dofs: int = 0
    solver: str = ""
    timings: dict[str, float] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    solved: list[Any] | None = None        # static family: checks.Solved per part (findings read them)
    steps: list["Step"] = field(default_factory=list)   # the ladder steps taken (section 9), in order


class Analysis(Protocol):
    name: ClassVar[str]
    tier: ClassVar[Tier]
    word: ClassVar[str]                                # "Vibration"
    estimate_only: ClassVar[bool]                      # tier 2
    limits: ClassVar[tuple[str, ...]]                  # plain sentences, written into output (tier 2, 3)
    study_keys: ClassVar[frozenset[str]]               # top-level keys it reads beyond the common ones
    material_needs: ClassVar[frozenset[str]]           # subset of MATERIAL_PROPERTIES (section 8)
    mesh_orders: ClassVar[tuple[int, ...]]             # (2,) by default; impact: (1,); the ladder may switch 2 -> 1
    connection_types: ClassVar[tuple[str, ...]]        # ("bonded", "free"); contact adds "contact"
    fields: ClassVar[tuple[FieldSpec, ...]]
    checks: ClassVar[tuple[CheckSpec, ...]]
    default_checks: ClassVar[tuple[dict, ...]]
    drives: ClassVar[tuple[str, ...]]                  # view drives it allows (section 6.4)
    default_controls: ClassVar[dict[str, dict]]        # what a preset may set when the view declares none
    upstream: ClassVar[tuple[str, ...]]                # solved first, on the same mesh, same study document
    ladder: ClassVar[tuple[Rung, ...]]                 # rungs it may take, in order (section 9)

    def parse(self, document: dict) -> Inputs: ...                     # stdlib; ValueError names the field
    def estimate(self, ctx: SolveContext, inputs: Inputs) -> "Estimate": ...   # cost of the current ctx.plan
    def apply(self, rung: Rung, ctx: SolveContext, inputs: Inputs) -> "Step | None": ...  # changes ctx.plan; None if it does not apply
    def solve(self, ctx: SolveContext, inputs: Inputs) -> AnalysisResult: ...
    def needs_finer(self, result: AnalysisResult, inputs: Inputs, check_results: list[dict]) -> bool: ...
    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: Inputs) -> dict: ...
    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: Inputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]: ...
    def study_echo(self, inputs: Inputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict: ...
    def summary(self, result: AnalysisResult, inputs: Inputs, check_results: list[dict]) -> dict: ...  # rounded
    def human_lines(self, summary: dict) -> list[str]: ...
    def deformation_scale(self, result: AnalysisResult, bbox_diagonal: float, requested: float | None) -> float | None: ...


class AnalysisBase:
    """The protocol's defaults: a subclass states only what is its own.

    Optional hooks that ``run.py`` and ``fit.py`` probe with ``hasattr`` (``governing``, ``refined_record``,
    ``upstream_for``, ``settle_steps``, ``write_files``, ``symmetric_about``) are never defined here."""

    tier: ClassVar[Tier] = 3
    estimate_only: ClassVar[bool] = False
    limits: ClassVar[tuple[str, ...]] = ()
    material_needs: ClassVar[frozenset[str]] = frozenset()
    mesh_orders: ClassVar[tuple[int, ...]] = (2,)
    connection_types: ClassVar[tuple[str, ...]] = ("bonded", "free")
    upstream: ClassVar[tuple[str, ...]] = ()
    #: What follows the document's stem in the GLB's extras name: "bracket creep".
    extras_word: ClassVar[str] = ""

    def needs_finer(self, result: AnalysisResult, inputs: Inputs, check_results: list[dict]) -> bool:
        return False

    def deformation_scale(self, result: AnalysisResult, bbox_diagonal: float, requested: float | None) -> float | None:
        return None

    def extras_name(self, stem: str) -> str:
        return f"{stem} {self.extras_word}"


def finding(severity: str, kind: str, summary: str, description: str, items=()) -> dict:
    """One entry of a study's findings."""
    return {"check": "fea", "severity": severity, "type": kind, "summary": summary, "description": description,
            "items": list(items)}


_SEVERITY = {"error": 0, "warning": 1, "info": 2}


def by_severity(found: list[dict]) -> list[dict]:
    """Findings errors first, then warnings, then notes; stable within each."""
    return sorted(found, key=lambda item: _SEVERITY[item["severity"]])


def xyz(point, digits: int = 3) -> list[float]:
    """A point as the summary writes it: ``[12.5, 0.0, 3.25]``."""
    return [round(float(c), digits) for c in point]


def frame_attributes(attributes: dict[str, str], index: int) -> dict[str, str]:
    """Series frame ``index``'s attributes: frame 0 under each field's own attribute, the others ``_F<index>`` after it."""
    return {name: attribute if index == 0 else f"{attribute}_F{index}" for name, attribute in attributes.items()}


def plan_value(ctx: SolveContext, name: str, default: Any) -> Any:
    """``ctx.plan.<name>``, or ``default`` with no plan or no such choice."""
    return default if ctx.plan is None else getattr(ctx.plan, name, default)


def max_frames(ctx: SolveContext, default: int = 24) -> int:
    """The most series frames the budget allows (``default`` with no budget or none set)."""
    return default if ctx.budget is None else int(getattr(ctx.budget, "max_frames", default) or default)


def mirrored(plane, ctx: SolveContext, groups) -> bool:
    """Each face group in ``groups``, and every check's faces, maps onto itself across the symmetry ``plane``."""
    checks = ctx.study.checks if ctx.study is not None else ()
    for refs in (*groups, *(tuple(check["faces"]) for check in checks if check.get("faces"))):
        ordinals = {ctx.ordinal_of[ref] for ref in refs}
        if {plane.mirror.get(o) for o in ordinals} != ordinals:
            return False
    return True


def assembly_parts(ctx: SolveContext) -> list[dict] | None:
    """An assembly's parts as a summary lists them (ref, name, material); None for one part."""
    if ctx.assembly is None:
        return None
    assembly = ctx.assembly
    return [{"ref": part.ref, "name": name, "material": material.name}
            for part, name, material in zip(assembly.parts, assembly.names, assembly.materials)]


def node_domain(space) -> "np.ndarray | None":
    """Each node's part (the domain of an element it belongs to), for an assembly; None for one part."""
    import numpy as np

    if space.domain is None:
        return None
    out = np.zeros(space.scalar_count, dtype=np.int64)
    out[space.scalar.element_dofs] = np.asarray(space.domain)[None, :]
    return out


def yield_at(scalars: dict, node: int) -> float:
    """The yield strength at ``node``: its own part's in an assembly (``scalars``' "yields" and "node_domain")."""
    domains, yields = scalars["node_domain"], scalars["yields"]
    return yields[0] if domains is None or len(yields) == 1 else yields[int(domains[node])]


def unfold_result(ctx: SolveContext, result: AnalysisResult, planes, *, vectors=frozenset(), scalars=()) -> None:
    """The solved half (or quarter) mirrored back into the whole part: every field and series frame, the deformation,
    the reactions and the ``result.scalars`` arrays named in ``scalars``. ``vectors``: the fields that are vectors."""
    import numpy as np

    from cadgen._internal.fea import symmetry

    volume = ctx.volume
    for plane in reversed(planes):
        whole, keep = symmetry.unfold_volume(volume, plane)
        split: tuple[dict, dict] = ({}, {})      # (scalars, vectors) by key
        for name, values in result.fields.items():
            split[name in vectors][f"f:{name}"] = np.asarray(values)
        for name, frames in result.frame_fields.items():
            for i, values in enumerate(frames):
                split[name in vectors][f"{name}:{i}"] = np.asarray(values)
        for name in scalars:
            split[0][f"s:{name}"] = np.asarray(result.scalars[name])
        if result.deformation is not None:
            split[1]["deformation"] = np.asarray(result.deformation)
        locations, s_out, v_out, boundary, tets, elements, vertices = symmetry.unfold_fields(
            plane, result.vertices, result.dof_locations, keep, scalars=split[0], vectors=split[1],
            boundary=result.boundary_quadratic, tets=result.tets, element_dofs=result.element_dofs,
        )
        merged = {**s_out, **v_out}
        result.fields = {name: merged[f"f:{name}"] for name in result.fields}
        result.frame_fields = {name: [merged[f"{name}:{i}"] for i in range(len(frames))]
                               for name, frames in result.frame_fields.items()}
        for name in scalars:
            result.scalars[name] = merged[f"s:{name}"]
        if result.deformation is not None:
            result.deformation = merged["deformation"]
        result.reactions = [symmetry.unfold_force(plane, r) for r in result.reactions]
        result.dof_locations, result.boundary_quadratic = locations, boundary
        result.tets, result.element_dofs, result.vertices = tets, elements, vertices
        volume = whole
    ctx.volume = volume
