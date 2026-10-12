"""Bolted joint (lite): bolts with a preload clamping parts that touch through frictional contact.

The study (spec 2.1, Y5) is a contact study (static's ``fixtures`` and
``loads``, ``steps``, ``rigid_planes``, ``contact`` connections) whose
``connections`` name bolts::

    {"between": ["plate", "bracket"], "type": "bolt", "size": "M6",
     "preload_N": 5000, "holes": ["#o1.1.f7", "#o1.2.f7"]}

- ``between``: the part under the head, then the part under the nut.
- ``size``: a metric size, ``"M3"`` to ``"M30"``, or ``{"diameter_mm": 6}``.
- ``preload_N``, or ``torque_Nm`` with a nut factor ``nut_factor`` (0.2 by
  default): ``F = T / (K d)``.
- ``grade``: the property class, ``"8.8"`` by default; its proof load is the
  bolt_load check's limit.
- ``friction``: Coulomb's coefficient between the clamped parts (0.2 by default).
- ``holes``: the hole walls (the bearing areas are found around them, out to
  the head's bearing diameter), or the flat faces the head and nut sit on.

Several bolts may clamp the same two parts. The clamped parts are meshed apart
and pressed together through contact_ops' augmented-Lagrange contact, with the
friction the bolts give. Each bolt is a pretensioned shank
(:mod:`..bolt_ops`): step 1 pulls the head and nut together with the preload,
then locks the shank's length there; step 2 applies the study's loads, in
``steps`` load steps (5 by default), the shank now a spring of the bolt's
VDI 2230 stiffness.

What it writes: a series that opens on the preload ("Preload", 0 %) and
follows the load steps ("60 % load"), each with the von Mises stress, the
displacement and the contact pressure (between the clamped parts, and under
each head and nut). Checks: ``bolt_load`` (the bolt's force after loading
against ``limit_N`` or its proof load), ``joint_separation`` (the clamp the
preload gave, lost: the joint opens when all of it is), ``joint_slip`` (the
sideways force the joint carries against what friction holds: μ times the
clamp); by default all three, plus ``stress``, ``displacement`` and
``contact_pressure`` when asked for. Stdlib only at import.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import AnalysisResult, SolveContext, by_severity, finding, max_frames, plan_value, xyz
from cadgen._internal.fea.analyses.contact import (
    DEFAULT_STEPS, LIMITS as CONTACT_LIMITS, ContactAnalysis, ContactInputs, face_refs, mark_stopped, mark_unsettled,
    parse_between, parse_connections, parse_friction, parse_rigid_planes, path_deadline, require_held, stop_words,
    unsettled_scalars, unsettled_status, unsettled_steps,
)
from cadgen._internal.fea.analyses.nonlinear import (
    load_label, material_specs, nodal, parse_steps, part_materials, pick_frames, refuse_stress, totals, view_checks,
)
from cadgen._internal.fea.bolt_ops import DEFAULT_GRADE, DEFAULT_NUT_FACTOR, PROOF_MPa, SIZES, BoltSize, bolt_size
from cadgen._internal.fea.study import parse_fixtures, parse_loads

if TYPE_CHECKING:
    import numpy as np

__all__ = ["Bolt", "BoltAnalysis", "BoltInputs", "DEFAULT_FRICTION", "LIMITS", "bolt_words", "force_words"]

#: Coulomb's coefficient between the clamped parts when the bolt names none: dry steel on steel.
DEFAULT_FRICTION = 0.2
#: Load steps step 1 (the preload) takes.
PRELOAD_STEPS = 2
#: A joint whose clamp falls under this share of what the preload gave is open.
OPEN_SHARE = 1e-3
#: A part held by contacts and bolts alone is kept from drifting by springs this share of its mean stiffness.
STABILISE = 1e-8
#: A sideways force under this share of the clamp is the solve's round-off, not a load.
SHEAR_NOISE = 1e-4
#: The keys a bolt connection takes.
BOLT_KEYS = frozenset({"between", "type", "size", "preload_N", "torque_Nm", "nut_factor", "grade", "friction", "holes"})

#: What this lite solver leaves out: the bolt's own limits, then contact's (the clamped parts touch through it).
LIMITS = (
    "Each bolt is a pretensioned spring along its axis, not meshed: it carries its axial force only, spread evenly "
    "under its head and nut, so once the joint slips the shank bearing on the hole wall is not modelled",
    "The bolt is linear elastic with no thread modelled: its stiffness is VDI 2230's for a plain shank, the head and "
    "the nut, and the check stops it at its proof load",
    "No fatigue of the bolt yet, and no tightening torsion: the preload is the axial force alone, from a torque by "
    "F = T/(K d)",
    *CONTACT_LIMITS,
)


def force_words(newtons: float) -> str:
    """A force as people say it: "5 kN", "1.25 kN", "800 N"."""
    value = float(newtons)
    if abs(value) >= 1000.0:
        return f"{value / 1000.0:.3g} kN"
    return f"{value:.3g} N"


@dataclass(frozen=True)
class Bolt:
    """A ``bolt`` connection, parsed: the parts under its head and nut, its size, preload, grade, friction, faces."""

    between: tuple[str, str]
    size: BoltSize
    preload_N: float
    holes: tuple[str, ...]
    torque_Nm: float | None = None
    nut_factor: float | None = None
    grade: str = DEFAULT_GRADE
    friction: float = DEFAULT_FRICTION

    @property
    def proof_N(self) -> float:
        from cadgen._internal.fea.bolt_ops import proof_load

        return proof_load(self.size, self.grade)


@dataclass(frozen=True)
class BoltInputs(ContactInputs):
    bolts: tuple[Bolt, ...] = ()


def bolt_words(bolt: Bolt) -> str:
    """A bolt as Study and the findings name it: "M6 bolt, 5 kN preload, clamps plate and bracket"."""
    return f"{bolt.size.name} bolt, {force_words(bolt.preload_N)} preload, clamps {bolt.between[0]} and {bolt.between[1]}"


def _size(entry: dict, where: str) -> BoltSize:
    raw = entry.get("size")
    words = f'{where}.size: a metric size like "M6" (M3 to M30), or {{"diameter_mm": 6}}'
    if isinstance(raw, str):
        key = raw.strip().upper()
        if key not in SIZES:
            raise ValueError(f"{words}; got {kinds.json_text(raw)}")
        return SIZES[key]
    if isinstance(raw, dict):
        if set(raw) != {"diameter_mm"}:
            raise ValueError(words)
        return bolt_size(kinds.number(raw["diameter_mm"], where=f"{where}.size.diameter_mm", positive=True))
    raise ValueError(words)


def _bolt(entry: dict, where: str) -> Bolt:
    unknown = set(entry) - BOLT_KEYS
    if unknown:
        raise ValueError(f"{where}: unknown keys {sorted(unknown)}; a bolt takes {sorted(BOLT_KEYS - {'type'})}")
    between = parse_between(entry, where, '["plate", "bracket"] (the part under the head, then the part under the nut)')
    size = _size(entry, where)
    given = [key for key in ("preload_N", "torque_Nm") if key in entry]
    if len(given) != 1:
        raise ValueError(f"{where}: give the bolt's preload_N (its clamp force, N), or the torque_Nm it is tightened "
                         "to (with nut_factor, 0.2 by default), exactly one")
    torque = factor = None
    if given == ["preload_N"]:
        if "nut_factor" in entry:
            raise ValueError(f"{where}.nut_factor: a nut factor turns a torque into a preload; it goes with torque_Nm")
        preload = kinds.number(entry["preload_N"], where=f"{where}.preload_N", positive=True)
    else:
        from cadgen._internal.fea.bolt_ops import preload_from_torque

        torque = kinds.number(entry["torque_Nm"], where=f"{where}.torque_Nm", positive=True)
        factor = kinds.number(entry.get("nut_factor", DEFAULT_NUT_FACTOR), where=f"{where}.nut_factor", positive=True)
        if not 0.05 <= factor <= 1.0:
            raise ValueError(f"{where}.nut_factor: K in F = T/(K d), about 0.2 dry and 0.15 oiled; from 0.05 to 1, got {factor:g}")
        preload = preload_from_torque(torque, factor, size.d)
    grade = entry.get("grade", DEFAULT_GRADE)
    if not isinstance(grade, str) or grade.strip().upper() not in PROOF_MPa:
        raise ValueError(f"{where}.grade: the property class, one of {list(PROOF_MPa)}; got {kinds.json_text(grade)}")
    holes = entry.get("holes")
    if isinstance(holes, str):
        holes = [holes]
    if not isinstance(holes, list) or not holes or not all(isinstance(ref, str) and ref.strip() for ref in holes):
        raise ValueError(f'{where}.holes: the hole walls the bolt passes through, like ["#o1.1.f7", "#o1.2.f7"] (or the flat '
                         "faces its head and nut sit on)")
    return Bolt(between, size, preload, tuple(dict.fromkeys(ref.strip() for ref in holes)), torque, factor,
                grade.strip().upper(), parse_friction(entry, where, DEFAULT_FRICTION))


def _opens_at(points: list[tuple[float, float]], preload: float) -> float | None:
    """The load factor the clamp reaches 0 at, from (load factor, clamp) points starting at (0, preload): on the line
    through the last two clamped points before it opens, else extrapolated from the last two; None when the clamp is
    not falling."""
    if preload <= 0:
        return 0.0
    gone = OPEN_SHARE * preload
    for m, (factor, clamp) in enumerate(points):
        if clamp <= gone:
            if m == 0:
                return 0.0
            (f0, c0), (f1, c1) = points[max(m - 2, 0)], points[m - 1]
            if m == 1 or not c0 > c1:
                f0, c0 = points[m - 1]
                return points[m - 1][0] + (factor - points[m - 1][0]) * c0 / max(c0 - clamp, 1e-300)
            return min(max(f1 + c1 * (f1 - f0) / (c0 - c1), f1), factor)
    if len(points) < 2:
        return None
    (f0, c0), (f1, c1) = points[-2], points[-1]
    if not c0 > c1 or not f1 > f0:
        return None
    return f1 + c1 * (f1 - f0) / (c0 - c1)


def _slips_at(points: list[tuple[float, float, float]]) -> float | None:
    """The load factor the sideways force reaches what friction holds, from (load factor, force, friction holds)
    points starting at (0, 0, ...): between the last point that holds and the first that does not, else extrapolated
    from the last (the force grows with the load); None with no sideways force."""
    for m in range(1, len(points)):
        (f0, s0, c0), (f1, s1, c1) = points[m - 1], points[m]
        if s1 > c1:
            spare0, spare1 = c0 - s0, c1 - s1
            return f0 + (f1 - f0) * spare0 / max(spare0 - spare1, 1e-300)
    factor, shear, holds = points[-1]
    if not shear > 0 or not factor > 0:
        return None
    return factor * holds / shear


class BoltAnalysis(ContactAnalysis):
    name: ClassVar[str] = "bolt"
    word: ClassVar[str] = "Bolted joint"
    limits: ClassVar[tuple[str, ...]] = LIMITS
    # The one analysis that accepts bolts (study._connections reads this); contact pairs come along.
    connection_types: ClassVar[tuple[str, ...]] = ("bonded", "free", "contact", "bolt")
    checks: ClassVar[tuple] = (kinds.BOLT_LOAD, kinds.JOINT_SEPARATION, kinds.JOINT_SLIP, kinds.STRESS, kinds.DISPLACEMENT,
                               kinds.CONTACT_PRESSURE)
    default_checks: ClassVar[tuple[dict, ...]] = ({"kind": "bolt_load"}, {"kind": "joint_separation"}, {"kind": "joint_slip"})
    extras_word: ClassVar[str] = "bolt"

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> BoltInputs:
        from cadgen._internal.fea.materials import material_from_spec

        fixtures = parse_fixtures(document, required=False)
        loads = parse_loads(document, required=False)
        steps = parse_steps(document, DEFAULT_STEPS)
        pairs, bolts = parse_connections(document, _bolt)
        planes = parse_rigid_planes(document)
        if not bolts:
            raise ValueError(
                'study: a bolt study needs a bolt: connections like [{"between": ["plate", "bracket"], "type": "bolt", '
                '"size": "M6", "preload_N": 5000, "holes": ["#o1.1.f7", "#o1.2.f7"]}]'
            )
        require_held(fixtures, planes)
        bolted = {frozenset(bolt.between) for bolt in bolts}
        for index, pair in enumerate(pairs):
            if frozenset(pair.between) in bolted:
                raise ValueError(f"connections: {pair.between[0]} and {pair.between[1]} are bolted, and the bolt already "
                                 "presses them together with its friction; leave out their contact connection")
        friction: dict[frozenset, float] = {}
        for bolt in bolts:
            key = frozenset(bolt.between)
            if friction.setdefault(key, bolt.friction) != bolt.friction:
                raise ValueError(f"connections: the bolts clamping {bolt.between[0]} and {bolt.between[1]} give different "
                                 "friction; the faces between them have one coefficient")
        checks = view_checks(document)
        if isinstance(checks, list) and any(material_from_spec(spec).yield_strength is None
                                            for _, spec in material_specs(document)):
            refuse_stress(checks, ", and a material here has none; give its yield_MPa, or check the bolt_load instead")
        refs, anchors, needs = face_refs(fixtures, loads, *(ref for bolt in bolts for ref in bolt.holes))
        return BoltInputs(refs, anchors, False, fixtures=fixtures, loads=loads, material_needs=needs, steps=steps,
                          model="elastic", pairs=pairs, planes=planes, bolts=bolts)

    # -- the ladder ------------------------------------------------------------------------------------

    def symmetric_about(self, plane, inputs: BoltInputs, ctx: SolveContext) -> bool:
        """Never: a bolt's spider would be cut in two (the rung is skipped, as for any part that is not symmetric)."""
        return False

    # -- solve ---------------------------------------------------------------------------------------

    def solve(self, ctx: SolveContext, inputs: BoltInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea import bolt_ops, contact_ops, nonlinear_driver, operators
        from cadgen._internal.fea.analyses.nonlinear import external_load, supported
        from cadgen._internal.fea.checks import quoted
        from cadgen._internal.fea.materials import lookup_material
        from cadgen._internal.fea.supports import driven, path_to_global

        if ctx.assembly is None:
            raise ValueError("connections: a bolt clamps two parts, and this document has one; put the parts it clamps "
                             "in one STEP")
        space = ctx.space
        basis = space.basis
        materials = list(ctx.materials)
        timings: dict[str, float] = {}
        warnings: list[str] = []
        started = time.perf_counter()
        external = external_load(space, materials, inputs.surface_loads, inputs.body_accelerations, ctx.ordinal_of, 1.0)
        supports, free = supported(ctx, inputs.fixtures, [])  # never symmetric: no planes
        K = operators.stiffness(space, materials).tocsr()

        names = self._part_names(ctx)
        refs = [part.ref for part in ctx.assembly.parts]
        nodes = self._nodes(ctx, materials, names)
        node_part, node_body = nodes.part, nodes.body

        # The bolts: where each bears, its shank's stiffness, and the spring it becomes once locked.
        bolts: list[dict] = []
        springs: list = []
        for n, bolt in enumerate(inputs.bolts):
            where = f"connections (bolt {n + 1})"
            head, nut = (self._part_index(ctx, name, f"{where}.between") for name in bolt.between)
            if head == nut:
                raise ValueError(f"{where}.between: both names are {quoted(names[head])}")
            geometry = bolt_ops.locate(space, ctx.volume.boundary_ordinal, node_part,
                                       [ctx.ordinal_of[ref] for ref in bolt.holes], head, nut, bolt.size, where)
            E = lookup_material("stainless-304" if bolt.grade.startswith("A") else "steel").E
            compliance = bolt_ops.bolt_compliance(bolt.size, geometry.clamp_mm, E)
            joint = bolt_ops.joint_compliance(bolt.size.head, 2.0 * geometry.hole_radius if geometry.hole_radius else bolt.size.hole,
                                              geometry.clamp_mm, geometry.outside_mm,
                                              min(nodes.E_of_part[head], nodes.E_of_part[nut]))
            index, value = bolt_ops.shank_gradient(space, geometry)
            springs.append(bolt_ops.BoltSpring(index, value, 1.0 / compliance["total"], bolt.preload_N))
            bolts.append({"bolt": bolt, "head": head, "nut": nut, "geometry": geometry, "compliance": compliance,
                          "joint": joint, "E": E})

        # The contacts: the study's contact pairs first (Study lists them by index), then one per bolted pair of parts.
        pair_list: list[tuple[tuple[str, str], float, int, int]] = []
        for n, pair in enumerate(inputs.pairs):
            i, j = (self._part_index(ctx, name, f"connections (contact {n + 1}).between") for name in pair.between)
            pair_list.append((pair.between, pair.friction, i, j))
        joint_of: dict[frozenset, int] = {}
        for entry in bolts:
            key = frozenset((entry["head"], entry["nut"]))
            if key not in joint_of:
                joint_of[key] = len(pair_list)
                pair_list.append((entry["bolt"].between, entry["bolt"].friction, entry["head"], entry["nut"]))
            entry["joint_index"] = joint_of[key]
        rows: list = []
        contacts: list[dict] = []
        for n, (between, friction, i, j) in enumerate(pair_list):
            if i == j:
                raise ValueError(f"connections (contact {n + 1}).between: both names are {quoted(names[i])}")
            found = self._pair_rows(ctx, nodes, i, j, friction, n)
            if n >= len(inputs.pairs) and not found.count:
                raise ValueError(f"connections: {quoted(names[i])} and {quoted(names[j])} are bolted, but no faces of them "
                                 "face each other near enough to touch; the bolt clamps two parts that touch")
            rows.append(found)
            contacts.append(self._pair_entry(f"{names[i]} on {names[j]}", names, refs, i, j, friction))
        self._plane_rows(ctx, inputs, nodes, names, refs, rows, contacts)
        constraints = contact_ops.Constraints.concatenate(rows, space.scalar_count)
        owner = np.where(constraints.owner >= 0, constraints.owner, len(pair_list) + (-1 - constraints.owner))
        # What holds each body: a fixture, a rigid plane, a contact with a held body, or a bolt to one.
        bolt_links = [(set(np.unique(node_body[entry["geometry"].head.nodes]).tolist()),
                       set(np.unique(node_body[entry["geometry"].nut.nodes]).tolist())) for entry in bolts]
        vdofs, loose, floating = self._loose_bodies(
            ctx, supports, constraints, nodes, names, len(pair_list), bolt_links,
            "fix a face of it, bolt it or put it in contact with a held part, or give it a rigid plane to rest on")
        # A part resting on contacts and bolts alone is kept from drifting by weak springs, across its bolts' axis only.
        axis_of: dict[int, np.ndarray] = {}
        for entry in bolts:
            for end in (entry["geometry"].head, entry["geometry"].nut):
                for b in np.unique(node_body[end.nodes]).tolist():
                    axis_of[int(b)] = entry["geometry"].axis
        drift = bolt_ops.drift_springs(K, vdofs, [(np.flatnonzero(node_body == b), axis_of.get(b)) for b in loose], STABILISE)
        stabilise = np.zeros(basis.N)
        preload = np.zeros(basis.N)
        for spring in springs:
            preload[spring.index] -= spring.preload * spring.value
        shift = contact_ops.close_gaps(constraints, node_body, vdofs, preload, loose)
        timings["assemble_s"] = time.perf_counter() - started

        history: list[tuple] = []
        problem_box: list = []

        def record(u, response):
            full = problem_box[0].full(u)
            history.append((operators.von_mises(operators.stress(space, materials, full)), response, full.copy()))

        solver = plan_value(ctx, "solver", "direct")
        method = "iterative" if solver in ("iterative", "matrix_free") else "direct"
        adaptive = bool(plan_value(ctx, "adaptive_steps", False))
        # Step 1 tightens with the bolted faces sliding freely; their friction holds from the locked state on.
        bolted_rows = np.isin(owner, list(joint_of.values()))
        friction = constraints.friction.copy()
        constraints.friction = np.where(bolted_rows, 0.0, friction)
        # Friction's stick spring is contact_ops.TANGENTIAL_SHARE of the normal penalty everywhere (it chatters near
        # slip when stiffer): the bolted faces need no softening of their own.
        normal_scale = np.ones(constraints.count)
        after = bolt_ops.drift_springs(K, vdofs, [(np.flatnonzero(node_body == b), None) for b in loose], STABILISE)
        problem = bolt_ops.BoltedProblem(space, K + drift, free, preload, constraints, stabilise, method=method, on_commit=record,
                                         springs=springs, normal_scale=normal_scale, drift=after)
        problem_box.append(problem)
        if ctx.log:
            ctx.log(f"{len(springs)} bolts, {constraints.count} contact nodes over {len(contacts)} contacts")
        # Step 1: the preload, the shanks pulling head and nut together; then each shank is locked there.
        tightened = path_to_global(nonlinear_driver.solve_path(driven(problem, supports, free), steps=PRELOAD_STEPS,
                                                               solver=solver, adaptive=False, runaway=None, log=ctx.log,
                                                               deadline_s=path_deadline(ctx)), supports)
        warnings += tightened.warnings
        preload_frame = len(history) - 1
        clamped = bool(tightened.records) and not tightened.collapsed
        path = tightened
        loaded = None
        if clamped:
            problem.lock(tightened.u, external, friction)
            if np.any(external):
                # Step 2: the study's loads, the shanks now springs of the bolts' stiffness. Its deadline is its own,
                # not what the preload left of one: it bounds time without progress, and a slow preload is progress.
                loaded = path_to_global(nonlinear_driver.solve_path(driven(problem, supports, free), steps=inputs.steps,
                                                                    solver=solver, adaptive=adaptive, runaway=None,
                                                                    log=ctx.log, deadline_s=path_deadline(ctx)),
                                 supports)
                warnings += loaded.warnings
                path = loaded
        timings["solve_s"] = tightened.seconds + (loaded.seconds if loaded is not None else 0.0)
        # Steps kept although their contact did not settle: the preload's, then the load's.
        unsettled = unsettled_steps(tightened, "the preload") + (unsettled_steps(loaded) if loaded is not None else [])
        self._warn_unsettled(problem, warnings)
        collapsed = not clamped or bool(loaded is not None and loaded.collapsed)
        factor = 0.0 if not clamped else (loaded.factor if loaded is not None else 1.0)

        started = time.perf_counter()
        if not history:  # not even the first preload step: the unloaded parts are the one frame
            zeros = np.zeros(problem.size)
            history = [(np.zeros(basis.dx.shape), problem.response(zeros - problem.offset), zeros)]
            preload_frame = 0
        # Frames: the preload (the last step-1 state), then step 2's load steps.
        load_states = list(range(preload_frame + 1, len(history)))
        picked = [preload_frame] + [load_states[i] for i in pick_frames(len(load_states), max(max_frames(ctx) - 1, 1))] \
            if load_states else [preload_frame]
        load_factors = [0.0] + [record.factor for record in loaded.records] if loaded is not None else [0.0]
        factor_of = {preload_frame + i: f for i, f in enumerate(load_factors)}

        def bolt_forces(state: int) -> list[float]:
            if state <= preload_frame:
                return [spring.preload for spring in springs]
            return [spring.force(history[state][2]) for spring in springs]

        def pressures(state: int):
            vm, response, full = history[state]
            field = contact_ops.pressure_field(space, constraints, response.normal_force)
            bearing = np.zeros(space.scalar_count)
            for entry, force in zip(bolts, bolt_forces(state)):
                for end in (entry["geometry"].head, entry["geometry"].nut):
                    np.maximum(bearing, end.share * force / end.area, out=bearing)
            return field, np.maximum(field, bearing)

        stress_frames = [nodal(space, history[state][0]) for state in picked]
        displacement_frames = [space.nodal(history[state][2] + shift) for state in picked]
        pressure_frames = [pressures(state)[1] for state in picked]
        final_state = picked[-1]

        def unheld_forces(full) -> dict[int, np.ndarray]:
            """Each part's share of what the drift springs held of step 2's load at the displacement ``full`` (N, xyz)."""
            held = (drift + after) @ (full - problem.offset)
            out: dict[int, np.ndarray] = {}
            for b, dofs in zip(loose, floating):
                spring = np.array(totals(space, held, dofs))
                for p in np.unique(node_part[node_body == b]):
                    out[int(p)] = out.get(int(p), np.zeros(3)) + spring
            return out

        def sideways(state: int, rows, parts: tuple[int, int], axis) -> float:
            """The sideways force a joint (its contact ``rows``, between ``parts``) carries in a settled frame: what
            friction carries across its faces, plus what the drift springs had to hold across the bolt ``axis`` (the
            rest of a sideways load friction could not hold). Never the forces of a step's first solve with every point
            held stuck: those grow with the step taken (a pure pull read 8.5 kN sideways after one 20 % step, 3.4 kN
            after a 2.5 % one, against the 2.3 kN friction held)."""
            _, response, full = history[state]
            forces = unheld_forces(full)
            unheld = sum((forces.get(p, np.zeros(3)) for p in parts), np.zeros(3))
            lateral = unheld - (unheld @ axis) * axis
            return float(np.linalg.norm(response.tangential[rows].sum(axis=0))) + float(np.linalg.norm(lateral))

        final_vm, final_response, final_full = history[final_state]
        contact_pressure = pressures(final_state)[0]

        # Each bolted joint: its clamp along the load (from the preload on), the sideways force it carries, its friction.
        joints: list[dict] = []
        moved = float(np.linalg.norm(space.nodal(final_full + shift), axis=1).max())
        stats = self._contact_stats(contacts, constraints, owner, final_response, contact_pressure, nodes.points, 1.0, moved)
        loose_parts: list[dict] = []
        total = float(np.linalg.norm([factor * external[space.component == k].sum() for k in range(3)]))
        pressing_bodies = self._pressing_bodies(constraints, node_body, final_response)
        held = (drift + after) @ (final_full - problem.offset)  # what the drift springs took of step 2's load
        for b, dofs in zip(loose, floating):
            spring = np.array(totals(space, held, dofs))
            load = [float(factor * c) for c in totals(space, external, dofs)]
            if b in pressing_bodies and b in axis_of:
                # Clamped by a bolt: along its axis the bolt holds it, and what the drift springs take there is their
                # share of how far the clamped parts bend (a strap pulled off lifts the link a millimetre or two), not
                # a load nothing holds (the reactions' mismatch says it). It slides away only across the axis.
                spring = spring - (spring @ axis_of[b]) * axis_of[b]
            size = float(np.linalg.norm(spring))
            if size > 0.01 * max(math.sqrt(sum(c * c for c in load)), total, max(bolt.preload_N for bolt in inputs.bolts)):
                loose_parts.append({"parts": sorted({names[int(p)] for p in np.unique(node_part[node_body == b])}),
                                    "pressing": b in pressing_bodies, "unheld_N": size})
        for key, index in joint_of.items():
            mine = owner == index
            members = [entry for entry in bolts if entry["joint_index"] == index]
            between = pair_list[index][0]
            friction = pair_list[index][1]
            i, j = pair_list[index][2], pair_list[index][3]
            preload_clamp = float(history[preload_frame][1].normal_force[mine].sum())
            curve = [(0.0, preload_clamp)] + [
                (record.factor, float(history[preload_frame + 1 + n][1].normal_force[mine].sum()))
                for n, record in enumerate(loaded.records if loaded is not None else [])
            ]
            axis = np.mean([entry["geometry"].axis for entry in members], axis=0)
            axis /= np.linalg.norm(axis)
            # Along the load: the sideways force the faces carry against what friction holds there.
            grip = [(0.0, 0.0, friction * preload_clamp)] + [
                (record.factor, sideways(preload_frame + 1 + n, mine, (i, j), axis),
                 friction * max(float(history[preload_frame + 1 + n][1].normal_force[mine].sum()), 0.0))
                for n, record in enumerate(loaded.records if loaded is not None else [])
            ]
            clamp = float(final_response.normal_force[mine].sum())
            pressing = final_response.normal_force[mine] > 0
            touching_area = float(constraints.area[mine][pressing].sum())
            preload_area = float(constraints.area[mine][history[preload_frame][1].normal_force[mine] > 0].sum())
            shear = sideways(final_state, mine, (i, j), axis)
            if shear <= SHEAR_NOISE * max(preload_clamp, 1e-300):
                shear = 0.0
            opens = _opens_at(curve, preload_clamp)
            open_now = clamp <= OPEN_SHARE * max(preload_clamp, 1e-300)
            slipping = float(final_response.slipping[mine][pressing].mean()) if pressing.any() and friction else 0.0
            centre = np.mean([0.5 * (entry["geometry"].head.centre + entry["geometry"].nut.centre) for entry in members], axis=0)
            joints.append({
                "name": f"{between[0]} and {between[1]}", "between": list(between), "refs": [refs[i], refs[j]],
                "bolts": [bolts.index(entry) + 1 for entry in members], "friction": friction,
                "preload_clamp_N": preload_clamp, "clamp_N": clamp, "clamp_lost_N": preload_clamp - clamp,
                "open": bool(open_now), "opens_at_percent": None if opens is None else opens * 100.0,
                "slips_at_percent": None if (slips := _slips_at(grip)) is None else slips * 100.0,
                "touching_share": touching_area / preload_area if preload_area > 0 else 0.0,
                "shear_N": shear, "friction_holds_N": friction * max(clamp, 0.0), "slipping_share": slipping,
                "slips": bool(shear > friction * max(clamp, 0.0) * (1 + 1e-6)),
                "at": [float(c) for c in centre], "curve": curve,
            })
        bolts_out = []
        preload_forces, final_forces = bolt_forces(preload_frame), bolt_forces(final_state)
        frame_forces = [bolt_forces(state) for state in picked]
        for n, (entry, spring) in enumerate(zip(bolts, springs)):
            bolt, geometry, compliance, joint = entry["bolt"], entry["geometry"], entry["compliance"], entry["joint"]
            force = final_forces[n]
            bolts_out.append({
                "name": f"bolt {n + 1}", "words": bolt_words(bolt), "size": bolt.size.name, "diameter_mm": bolt.size.d,
                "between": list(bolt.between), "refs": [refs[entry["head"]], refs[entry["nut"]]], "grade": bolt.grade,
                "preload_N": bolt.preload_N, "torque_Nm": bolt.torque_Nm, "nut_factor": bolt.nut_factor,
                "proof_N": bolt.proof_N, "force_N": force, "increase_N": force - preload_forces[n],
                "max_force_N": max(forces[n] for forces in frame_forces), "slack": bool(force <= 0.0),
                "stiffness_N_mm": spring.k, "clamp_length_mm": geometry.clamp_mm,
                "shortened_mm": spring.preload / spring.k - spring.stretch0,
                "hole_diameter_mm": None if geometry.hole_radius is None else 2.0 * geometry.hole_radius,
                "head_bearing_mm2": geometry.head.area, "nut_bearing_mm2": geometry.nut.area,
                "head_bearing_MPa": force / geometry.head.area, "nut_bearing_MPa": force / geometry.nut.area,
                "head_at": [float(c) for c in geometry.head.centre], "nut_at": [float(c) for c in geometry.nut.centre],
                "axis": [float(c) for c in geometry.axis], "joint": entry["joint_index"] - len(inputs.pairs),
                "hand": {"bolt_stiffness_N_mm": 1.0 / compliance["total"], "joint_stiffness_N_mm": 1.0 / joint["total"],
                         "load_factor": bolt_ops.load_factor(compliance["total"], joint["total"]), "joint_body": joint["body"],
                         "outside_diameter_mm": None if math.isinf(geometry.outside_mm) else geometry.outside_mm},
            })
        internal, _ = problem.evaluate(final_full - problem.offset, tangent=False)
        residual = internal - (factor if clamped else 0.0) * problem.external
        if not clamped:
            residual = internal - (tightened.factor * preload)
        reactions = [totals(space, residual, dofs) for dofs in supports.per_fixture]
        requested = totals(space, external)
        applied = tuple(float(factor * c) for c in requested)
        timings["stress_s"] = time.perf_counter() - started

        frame_values = [round(factor_of.get(state, 0.0) * 100.0, 6) for state in picked]
        frame_fields = {"von_mises": stress_frames, "displacement": displacement_frames, "contact_pressure": pressure_frames}
        series = self._series("%", frame_fields, [(value, "Preload" if i == 0 else load_label(value / 100.0))
                                                  for i, value in enumerate(frame_values)])
        final_fields = {name: values[-1] for name, values in frame_fields.items()}
        curve_x = frame_values
        curves = {"bolt_force_N": {"x": curve_x, "x_unit": "%", "y_unit": "N",
                                   "y": [round(max(forces), 6) for forces in frame_forces]}}
        if joints:
            curves["clamp_force_N"] = {"x": curve_x, "x_unit": "%", "y_unit": "N", "y": [
                round(float(history[state][1].normal_force[owner == joint_of[next(iter(joint_of))]].sum()), 6) for state in picked]}
        how = (f"{path.how}, preload in {len(tightened.records)} steps"
               + (f", {len(loaded.records)} {'adaptive ' if adaptive else ''}load steps" if loaded is not None else "")
               + f", {problem.augmentations} contact updates")
        peak_node = int(final_fields["contact_pressure"].argmax())
        if not clamped:
            warnings.append("the preload could not be applied: the parts did not settle under it")
        mesh = (space.dof_locations, space.boundary_quadratic, space.tets, space.element_dofs, space.vertices)
        return self._result(mesh, frame_fields, series, curves, reactions, applied, basis, how, timings, warnings, {
            "path": path, "final": final_fields, "factor": factor, "collapsed": collapsed, "clamped": clamped,
            "requested": requested, "model": "elastic", "adaptive": adaptive, "von_mises_gauss_max": float(final_vm.max()),
            "frame_factors": [v / 100.0 for v in frame_values], "contacts": stats, "augmentations": problem.augmentations,
            "contact_nodes": constraints.count, "floating_bodies": len(floating), "loose_parts": loose_parts,
            "peak_pressure_at": xyz(space.dof_locations[peak_node]),
            **unsettled_scalars([] if not collapsed else ["The preload could not be applied" if not clamped else stop_words(factor)],
                                unsettled),
            "part_refs": refs, "part_names": names, "part_materials": part_materials(ctx), "bolts": bolts_out, "joints": joints,
            "preload_steps": len(tightened.records), "load_steps": len(loaded.records) if loaded is not None else 0,
        })

    # -- judging -------------------------------------------------------------------------------------

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: BoltInputs) -> dict:
        from cadgen._internal.fea import checks

        kind = check["kind"]
        if kind not in ("bolt_load", "joint_separation", "joint_slip"):
            return super().judge(check, index, ctx, result, inputs)
        scalars = result.scalars
        label = check.get("label") or kinds.CHECK_SPECS[kind].default_label
        rows = []
        if kind == "bolt_load":
            for bolt in scalars["bolts"]:
                limit = float(check.get("limit_N", bolt["proof_N"]))
                rows.append((bolt["force_N"] / limit, bolt["force_N"], limit, bolt["force_N"] > limit, bolt["name"], bolt["head_at"]))
        elif kind == "joint_separation":
            for joint in scalars["joints"]:
                spare = joint["preload_clamp_N"] - float(check.get("min_clamp_N", 0.0))
                lost = max(joint["clamp_lost_N"], 0.0)
                opened = joint["open"] or joint["clamp_N"] <= float(check.get("min_clamp_N", 0.0))
                limit = max(spare, 1e-9)
                rows.append((max(lost / limit, 1.0 if opened else 0.0), lost, limit, opened, joint["name"], joint["at"]))
        else:
            for joint in scalars["joints"]:
                limit = joint["friction_holds_N"]
                ratio = joint["shear_N"] / limit if limit > 0 else (0.0 if joint["shear_N"] <= 1e-9 else 1e6)
                if joint["slips"]:
                    ratio = max(ratio, 1.0)
                rows.append((ratio, joint["shear_N"], max(limit, 1e-9), joint["slips"], joint["name"], joint["at"]))
        ratio, value, limit, failed, name, at = max(rows, key=lambda row: row[0])
        judged = {
            "kind": kind, "label": label, "value": round(value, 4), "limit": round(limit, 4), "unit": "N",
            "ratio": round(min(ratio, 1e6), 6), "close_at": 0.9,
            "status": "fails" if failed else checks.check_status(ratio, 0.9),
            "where": {"ref": None, "at": xyz(at)}, "of": name,
        }
        if kind == "joint_separation":
            joint = next(j for j in scalars["joints"] if j["name"] == name)
            if joint["opens_at_percent"] is not None:
                judged["opens_at_percent"] = round(joint["opens_at_percent"], 4)
        judged["at"] = self._at(result)
        judged["scaling"] = "none"  # preload, contact and friction: not linear in the load
        return mark_unsettled(mark_stopped(judged, scalars), scalars)

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: BoltInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        scalars = result.scalars
        found = list(super().findings(ctx, result, inputs, check_results, assembly=assembly))
        if not scalars["clamped"]:
            found.append(finding("error", "preload_fails", "The bolts could not be tightened: the parts did not settle under "
                                 "the preload", "step 1 (the preload) found no equilibrium; check the bolts' holes and that "
                                 "the clamped parts touch"))
        for bolt in scalars["bolts"]:
            at = xyz(bolt["head_at"])
            item = [{"text": f"{bolt['name']} head", "ref": None, "at": at}]
            if bolt["torque_Nm"] is not None:
                found.append(finding("info", "bolt_preload",
                                     f"{bolt['name'].capitalize()}: {bolt['torque_Nm']:g} N·m with nut factor {bolt['nut_factor']:g} "
                                     f"gives {force_words(bolt['preload_N'])} preload",
                                     f"F = T/(K d) = {bolt['torque_Nm']:g} N·m / ({bolt['nut_factor']:g} × {bolt['diameter_mm']:g} mm)",
                                     item))
            change = bolt["increase_N"]
            found.append(finding(
                "error" if bolt["force_N"] > bolt["proof_N"] else "info",
                "bolt_overloaded" if bolt["force_N"] > bolt["proof_N"] else "bolt_load",
                f"{bolt['name'].capitalize()} ({bolt['size']}, {bolt['grade']}): {force_words(bolt['preload_N'])} preload, "
                f"{force_words(bolt['force_N'])} after loading"
                + (f" ({'+' if change >= 0 else '−'}{force_words(abs(change))})" if abs(change) > 1e-6 * bolt["preload_N"] else "")
                + (f", over its {force_words(bolt['proof_N'])} proof load" if bolt["force_N"] > bolt["proof_N"] else ""),
                f"the shank was shortened {bolt['shortened_mm']:.3g} mm to carry its preload, then locked: a "
                f"{bolt['stiffness_N_mm']:.4g} N/mm spring over the {bolt['clamp_length_mm']:.3g} mm clamp; it bears "
                f"{bolt['head_bearing_MPa']:.3g} MPa under its head over {bolt['head_bearing_mm2']:.3g} mm²", item))
            if bolt["slack"]:
                found.append(finding("warning", "bolt_slack", f"{bolt['name'].capitalize()} goes slack: it carries no load",
                                     "the parts pushed its head and nut together past its preload", item))
        for joint in scalars["joints"]:
            at = xyz(joint["at"])
            item = [{"text": joint["name"], "ref": None, "at": at}]
            opens = joint["opens_at_percent"]
            if joint["open"]:
                found.append(finding(
                    "error", "joint_opens", f"The joint opens: {joint['name']} separate"
                    + (f" at about {opens:.0f} % of this load" if opens is not None else ""),
                    f"the {force_words(joint['preload_clamp_N'])} clamp the bolts gave is all used up; past it the bolts "
                    "carry the whole load", item))
            else:
                found.append(finding(
                    "info", "joint_clamped",
                    f"{joint['name'][0].upper()}{joint['name'][1:]} stay clamped with {force_words(joint['clamp_N'])} of the "
                    f"{force_words(joint['preload_clamp_N'])} the bolts gave"
                    + (f"; they would open at about {opens:.0f} % of this load" if opens is not None else ""),
                    f"the clamped faces touch over {joint['touching_share'] * 100:.0f} % of the area they touched at preload",
                    item))
            if joint["slips"]:
                found.append(finding(
                    "error", "joint_slips", f"The joint slips: {joint['name']} slide over each other",
                    f"they must carry {force_words(joint['shear_N'])} sideways, and friction {joint['friction']:g} holds "
                    f"{force_words(joint['friction_holds_N'])}; the bolts' shanks bearing in the holes are not modelled", item))
        return by_severity(found)

    # -- what is written -----------------------------------------------------------------------------

    def summary(self, result: AnalysisResult, inputs: BoltInputs, check_results: list[dict]) -> dict:
        summary = super().summary(result, inputs, check_results)
        scalars = result.scalars

        def rounded(value):
            if isinstance(value, float):
                return round(value, 6)
            if isinstance(value, dict):
                return {key: rounded(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [rounded(item) for item in value]
            return value

        joints = [{key: value for key, value in joint.items() if key != "curve"} for joint in scalars["joints"]]
        if not scalars["clamped"]:
            status = "The preload could not be applied"
        elif scalars["collapsed"]:
            status = stop_words(scalars["factor"])
        elif any(joint["open"] for joint in joints):
            status = "Joint opens"
        elif any(joint["slips"] for joint in joints):
            status = "Joint slips"
        else:
            status = "Joint holds"
        if scalars.get("unsettled"):
            status = unsettled_status(scalars["unsettled"])
        checks_out = summary.pop("checks")
        summary.update({"status": status, "preload_steps": scalars["preload_steps"], "steps": scalars["load_steps"],
                        "bolts": rounded(scalars["bolts"]),
                        "joints": rounded(joints), "checks": checks_out})
        return summary

    def extras_head(self, summary: dict) -> dict:
        return {**super().extras_head(summary), "bolts": summary["bolts"], "joints": summary["joints"]}

    def study_echo(self, inputs: BoltInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        return {
            **super().study_echo(inputs, bare),
            "bolts": [{"between": list(bolt.between), "size": bolt.size.name, "diameter_mm": bolt.size.d,
                       "preload_N": round(bolt.preload_N, 6), "torque_Nm": bolt.torque_Nm, "nut_factor": bolt.nut_factor,
                       "grade": bolt.grade, "friction": bolt.friction, "holes": bare(bolt.holes), "words": bolt_words(bolt)}
                      for bolt in inputs.bolts],
        }

    def human_lines(self, summary: dict) -> list[str]:
        lines = [f"{summary['status'].lower()}: max von Mises {summary['max_von_mises_MPa']:g} MPa, max displacement "
                 f"{summary['max_displacement_mm']:g} mm, peak contact pressure {summary['max_contact_pressure_MPa']:g} MPa"]
        for bolt in summary["bolts"]:
            lines.append(f"{bolt['name']} ({bolt['size']}, {bolt['grade']}): preload {bolt['preload_N']:g} N, after loading "
                         f"{bolt['force_N']:g} N, proof load {bolt['proof_N']:g} N")
        for joint in summary["joints"]:
            opens = joint["opens_at_percent"]
            lines.append(f"{joint['name']}: {'open' if joint['open'] else 'clamped'} with {joint['clamp_N']:g} N of "
                         f"{joint['preload_clamp_N']:g} N" + (f", opens at about {opens:.3g} % of this load" if opens is not None else "")
                         + f"; sideways {joint['shear_N']:g} N, "
                         + (f"reaching the {joint['friction_holds_N']:g} N friction holds, so it slips" if joint["slips"]
                            else f"under the {joint['friction_holds_N']:g} N friction holds"))
        lines.append(f"preload in {summary['preload_steps']} steps, then {summary['steps']} "
                     f"{'adaptive ' if summary['adaptive'] else ''}load steps ({summary['newton_iterations']} Newton iterations, "
                     f"{summary['contact_updates']} contact updates)")
        return lines
