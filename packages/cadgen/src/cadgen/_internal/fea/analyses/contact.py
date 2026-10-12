"""Contact (lite): parts that press on each other or slide instead of being glued, and parts resting on a rigid plane.

The study (spec 5.17) takes static's ``fixtures`` and ``loads``, ``steps``
(the starting number of load steps, 5), and two kinds of contact:

- ``connections`` of ``"type": "contact"`` between two named parts, with an
  optional Coulomb ``friction`` (0, frictionless, by default). Only this
  analysis accepts them; every other touching pair stays bonded, as in every
  other analysis. The two parts are meshed apart, nothing glued between them.
- ``rigid_planes``: ``{"point_mm", "normal", "parts", "friction"}``, a rigid
  floor the named parts (every part, by default) cannot pass through; the
  normal points from the plane to the parts.

:mod:`..contact_ops` pairs each surface node of the smaller part with the
closest point of the other's surface before loading (small sliding) and
enforces no interpenetration by augmented Lagrange inside
:mod:`..nonlinear_driver`, with Coulomb friction by a return map. The parts
stay linear elastic. A part nothing fixes may rest on contacts alone (a very
weak spring keeps it from drifting where nothing else holds it); a part held
by nothing at all is a study error naming it.

What it writes: a series of load steps (``kind: "time"``, unit ``%``,
"60 % load"), each with the von Mises stress, the displacement and the contact
pressure (on contact faces, else 0). Checks: ``stress`` (by default),
``displacement`` and ``contact_pressure`` (``limit_MPa``, optional ``faces``),
judged at the last load carried. Findings say in plain words which pairs press
and how hard, which separate, which slide or stick, and where the contact
pressure peaks. Stdlib only at import.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import (
    AnalysisResult, FieldSpec, SolveContext, by_severity, finding, max_frames, plan_value, xyz,
)
from cadgen._internal.fea.analyses.nonlinear import (
    NonlinearAnalysis, NonlinearInputs, field_peak, load_label, material_specs, nodal, parse_steps, part_materials,
    peak_check, pick_frames, refuse_stress, symmetry_planes, totals, unfold, view_checks, with_materials,
)
from cadgen._internal.fea.study import parse_fixtures, parse_loads

__all__ = [
    "ContactAnalysis", "ContactInputs", "ContactPair", "DEFAULT_STEPS", "LIMITS", "RigidPlane", "contact_words",
    "mark_unsettled", "parse_rigid_planes", "path_deadline", "unsettled_extras", "unsettled_sentence", "unsettled_steps",
]

#: Load steps a study starts with when it names none.
DEFAULT_STEPS = 5
#: The largest Coulomb coefficient a study may give.
MAX_FRICTION = 2.0
#: The Uzawa updates a load step takes on average, as extra Newton solves, for the cost estimate.
AUGMENT_COST = 2.0
#: The contact forces still changing by more than this share after the last Uzawa update is worth a warning.
UNSETTLED = 0.1
#: A body resting on contacts whose drift springs carry more than this share of its load (or of the whole load) is not
#: held by them.
LOOSE_SHARE = 0.01
#: A node moved along the surface less than this share of the largest displacement has not slid.
SLIDE_SHARE = 0.02

#: What this lite solver leaves out, written into its output, its verdict's Details and the skill.
LIMITS = (
    "Small sliding: each surface node is paired once, before loading, with the surface it faces, so parts may slide "
    "only a fraction of an element along each other",
    "Node-to-surface contact: the pressure is resolved to the mesh, so a point or line contact needs fine elements "
    "where it touches",
    "Static: no impact or inertia; friction is Coulomb with one coefficient, the same at rest and sliding",
    "The parts stay linear elastic, and an overlap within the contact tolerance counts as just touching (no press fit)",
    "A part held only by contacts is kept from drifting where they do not hold it by a very weak spring",
)


@dataclass(frozen=True)
class ContactPair:
    """A ``contact`` connection: the two parts as the study names them, and Coulomb's μ."""

    between: tuple[str, str]
    friction: float = 0.0


@dataclass(frozen=True)
class RigidPlane:
    """A rigid plane: a point on it, its unit normal (towards the parts), the parts it holds up (all when empty), μ."""

    point: tuple[float, float, float]
    normal: tuple[float, float, float]
    parts: tuple[str, ...] = ()
    friction: float = 0.0


@dataclass(frozen=True)
class ContactInputs(NonlinearInputs):
    pairs: tuple[ContactPair, ...] = ()
    planes: tuple[RigidPlane, ...] = ()


def contact_words(between: tuple[str, str], friction: float) -> str:
    """A pair as Study and the findings name it: "pin presses on plate, friction 0.2" or ", no friction"."""
    return f"{between[0]} presses on {between[1]}, " + (f"friction {friction:g}" if friction else "no friction")


def parse_friction(entry: dict, where: str, default: float = 0.0) -> float:
    if "friction" not in entry:
        return default
    value = kinds.number(entry["friction"], where=f"{where}.friction")
    if not 0.0 <= value <= MAX_FRICTION:
        raise ValueError(f"{where}.friction: Coulomb's coefficient, from 0 (frictionless) to {MAX_FRICTION:g}, like 0.2 "
                         f"for dry steel on steel; got {value:g}")
    return value


def parse_between(entry: dict, where: str, example: str) -> tuple[str, str]:
    between = entry.get("between")
    if not isinstance(between, list) or len(between) != 2 or not all(isinstance(n, str) and n.strip() for n in between):
        raise ValueError(f"{where}.between: name the two parts, like {example}")
    return between[0].strip(), between[1].strip()


def parse_connections(document: dict, bolt: Callable[[dict, str], Any] | None = None) -> tuple[tuple[ContactPair, ...], tuple]:
    """The ``contact`` connections, and (bolt studies: ``bolt`` parses each) the ``bolt`` ones."""
    entries = document.get("connections")
    if not isinstance(entries, list):
        return (), ()
    pairs, bolts = [], []
    for index, entry in enumerate(entries):
        where = f"connections[{index}]"
        if not isinstance(entry, dict):
            continue  # study._connections says what is wrong with it
        kind = entry.get("type")
        if bolt is not None and kind == "bolt":
            bolts.append(bolt(entry, where))
            continue
        unknown = set(entry) - {"between", "type", "friction"}
        if unknown:
            named = "a connection" if bolt is None else f"a {kinds.json_text(kind)} connection"
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}; {named} takes between, type and (contact) friction")
        if kind != "contact":
            if "friction" in entry:
                raise ValueError(f"{where}.friction: only a contact {'' if bolt is None else 'or bolt '}connection slides; "
                                 f"{kinds.json_text(kind)} parts are glued or apart")
            continue
        pairs.append(ContactPair(parse_between(entry, where, '["pin", "plate"]'), parse_friction(entry, where)))
    return tuple(pairs), tuple(bolts)


def parse_rigid_planes(document: dict) -> tuple[RigidPlane, ...]:
    raw = document.get("rigid_planes")
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw:
        raise ValueError('rigid_planes: a list like [{"point_mm": [0, 0, 0], "normal": [0, 0, 1], "parts": ["plate"]}]')
    planes = []
    for index, entry in enumerate(raw):
        where = f"rigid_planes[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{where}: expected an object with point_mm and normal")
        unknown = set(entry) - {"point_mm", "normal", "parts", "friction"}
        if unknown:
            raise ValueError(f"{where}: unknown keys {sorted(unknown)}; a rigid plane takes point_mm, normal, parts and friction")
        vectors = []
        for key, words in (("point_mm", "a point on the plane as [x, y, z] in mm"),
                           ("normal", "the direction the plane faces, towards the parts, as [x, y, z], like [0, 0, 1]")):
            value = entry.get(key)
            if not isinstance(value, list) or len(value) != 3:
                raise ValueError(f"{where}.{key}: {words}")
            vectors.append(tuple(kinds.number(v, where=f"{where}.{key}") for v in value))
        length = math.sqrt(sum(c * c for c in vectors[1]))
        if not length > 0:
            raise ValueError(f"{where}.normal: the normal is zero; give the direction the plane faces, like [0, 0, 1]")
        parts = entry.get("parts", [])
        if isinstance(parts, str):
            parts = [parts]
        if not isinstance(parts, list) or not all(isinstance(p, str) and p.strip() for p in parts):
            raise ValueError(f'{where}.parts: the parts that rest on it by name, like ["plate"] (leave it out for every part)')
        planes.append(RigidPlane(vectors[0], tuple(c / length for c in vectors[1]), tuple(p.strip() for p in parts),
                                 parse_friction(entry, where)))
    return tuple(planes)


def require_held(fixtures, planes) -> None:
    if not fixtures and not planes:
        raise ValueError(
            "study: nothing holds the parts still: add 'fixtures' (faces held fixed), or 'rigid_planes' "
            "(a rigid floor they rest on)"
        )


def face_refs(fixtures, loads, *more: str) -> tuple[tuple[str, ...], tuple[str, ...], frozenset]:
    """(every face named, the fixtures' faces, the material needs): a part may rest on a contact or a rigid plane
    alone, so requires_anchor is False and solve() says when nothing holds one."""
    refs = tuple(dict.fromkeys((*(ref for group in (*fixtures, *loads) for ref in group.faces), *more)))
    anchors = tuple(dict.fromkeys(ref for fixture in fixtures for ref in fixture.faces))
    return refs, anchors, frozenset({"density"}) if any(load.body for load in loads) else frozenset()


def unsettled_sentence(percent: float, of: str = "the load") -> str:
    """What a result says of a load step whose contact forces did not settle (it was kept at the smallest step)."""
    return (f"Contact did not settle at {percent:.0f}% of {of}: the forces here do not balance, so this result is "
            "not reliable")


def unsettled_steps(path, of: str = "the load") -> list[dict]:
    """The driver's steps kept unsettled (``PathResult.unsettled``), as ``{"percent", "of"}``."""
    return [{"percent": round(factor * 100.0, 4), "of": of} for factor in path.unsettled]


def unsettled_extras(unsettled: list[dict]) -> dict:
    """``extras.analysis.unsettled``: the shares of the load whose contact did not settle, and the first."""
    return {"at_percent": [step["percent"] for step in unsettled], "first_percent": unsettled[0]["percent"]}


def unsettled_scalars(stopped: list[str], unsettled: list[dict]) -> dict:
    """The result scalars that say a path stopped short (``stopped``: its sentence) or kept unsettled steps."""
    return {
        "analysis_warnings": stopped + [unsettled_sentence(step["percent"], step["of"]) for step in unsettled[:1]],
        "unsettled": unsettled,
        "analysis_extras": {"unsettled": unsettled_extras(unsettled)} if unsettled else {},
    }


def unsettled_status(unsettled: list[dict]) -> str:
    return f"Not reliable: contact did not settle at {unsettled[0]['percent']:.0f}% of {unsettled[0]['of']}"


def path_deadline(ctx) -> float:
    """Seconds a load path may go without getting through a step before it stops cutting (``nonlinear_driver.solve_path``'s
    ``deadline_s``): the study's time target (``fit.Budget.seconds``, 600 s by default)."""
    budget = getattr(ctx, "budget", None)
    return float(getattr(budget, "seconds", 0.0) or 600.0)


def mark_stopped(judged: dict, scalars: dict) -> dict:
    """A check of a load path that stopped short of the full load says where it stopped and is judged on its own
    number at the last load carried: the stop leads the caption, so a bolt under its proof load still holds."""
    if scalars["collapsed"]:
        judged["collapsed_at_percent"] = round(scalars["factor"] * 100.0, 4)
    return judged


def mark_unsettled(judged: dict, scalars: dict) -> dict:
    """A check of a result with an unsettled step fails, saying at what share of the load (the first)."""
    unsettled = scalars.get("unsettled") or []
    if unsettled:
        judged["status"] = "fails"
        judged["unsettled_at_percent"] = unsettled[0]["percent"]
    return judged


def _plane_name(index: int, count: int) -> str:
    return "the rigid plane" if count == 1 else f"rigid plane {index + 1}"


def _diagonal(points, node_part, parts) -> float:
    import numpy as np

    chosen = np.isin(node_part, list(parts))
    if not chosen.any():
        return 0.0
    return float(np.linalg.norm(points[chosen].max(axis=0) - points[chosen].min(axis=0)))


@dataclass
class _Nodes:
    """The parts' nodes as contact sees them: which part and connected body each is in, where, and the contact
    tolerance and each part's Young's modulus."""

    part: Any
    body: Any
    points: Any
    tolerance: float
    E_of_part: dict[int, float]


class ContactAnalysis(NonlinearAnalysis):
    name: ClassVar[str] = "contact"
    word: ClassVar[str] = "Contact"
    collapse_fails: ClassVar[bool] = False
    limits: ClassVar[tuple[str, ...]] = LIMITS
    study_keys: ClassVar[frozenset[str]] = frozenset({"fixtures", "loads", "steps", "rigid_planes"})
    # The one analysis that accepts contact pairs (study._connections reads this); bonded stays the default.
    connection_types: ClassVar[tuple[str, ...]] = ("bonded", "free", "contact")
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("von_mises", "_VON_MISES", "von Mises stress", "MPa", per_frame=True),
        FieldSpec("displacement", "_DISPLACEMENT", "displacement", "mm", 3, 1000.0, per_frame=True),
        FieldSpec("contact_pressure", "_CONTACT_PRESSURE", "contact pressure", "MPa", per_frame=True),
    )
    checks: ClassVar[tuple] = (kinds.STRESS, kinds.DISPLACEMENT, kinds.CONTACT_PRESSURE)
    default_checks: ClassVar[tuple[dict, ...]] = ({"kind": "stress"},)
    # No load_scale (nonlinear's drives): contact is not proportional to the load (it opens, closes and slides).
    default_controls: ClassVar[dict[str, dict]] = {
        "frame": {"drives": "frame", "type": "number", "min": 0.0, "max": 100.0},
        "field": {"drives": "field", "type": "enum", "options": ["von_mises", "displacement", "contact_pressure"]},
        "deformation": {"drives": "deformation", "type": "number", "min": 0.0, "max": None},
    }
    governing_word: ClassVar[str] = "peak contact pressure"
    extras_word: ClassVar[str] = "contact"
    _adaptive_words = ("Let the load steps grow (up to {growth}× the {steps} starting ones) while the contacts settle in "
                       "a few Newton iterations, cutting them where they do not, to fit the time target")

    def _adaptive_accuracy(self, inputs) -> str | None:
        return "friction's slip is followed along the load in larger steps; the final contact is solved to the same tolerance"

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> ContactInputs:
        from cadgen._internal.fea.materials import material_from_spec

        fixtures = parse_fixtures(document, required=False)
        loads = parse_loads(document)
        steps = parse_steps(document, DEFAULT_STEPS)
        pairs, _ = parse_connections(document)
        planes = parse_rigid_planes(document)
        if not pairs and not planes:
            raise ValueError(
                'study: a contact study needs a contact: connections like [{"between": ["pin", "plate"], "type": "contact", '
                '"friction": 0.2}], or rigid_planes like [{"point_mm": [0, 0, 0], "normal": [0, 0, 1]}] for a rigid floor'
            )
        require_held(fixtures, planes)
        checks = view_checks(document)
        if any(material_from_spec(spec).yield_strength is None for _, spec in material_specs(document)):
            words = ", and a material here has none; give its yield_MPa, or check the contact_pressure or displacement instead"
            if not isinstance(checks, list):
                raise ValueError(f"view.checks: a stress check compares the peak with a yield strength{words}")
            refuse_stress(checks, words)
        refs, anchors, needs = face_refs(fixtures, loads)
        return ContactInputs(refs, anchors, False, fixtures=fixtures, loads=loads, material_needs=needs, steps=steps,
                             model="elastic", pairs=pairs, planes=planes)

    # -- the ladder (fit.py drives these) -------------------------------------------------------------

    def estimate(self, ctx: SolveContext, inputs: ContactInputs):
        """Nonlinear's (each Newton iteration of each load step), with the Uzawa updates' extra solves."""
        from cadgen._internal.fea import fit

        base = super().estimate(ctx, inputs)
        return fit.Estimate(dofs=base.dofs, memory_bytes=base.memory_bytes, seconds=base.seconds * AUGMENT_COST)

    def symmetric_about(self, plane, inputs: ContactInputs, ctx: SolveContext) -> bool:
        """Static's rule (one part only), and every rigid plane lies across the symmetry plane (its normal in it)."""
        from cadgen._internal.fea.analyses.static import StaticAnalysis

        if not StaticAnalysis().symmetric_about(plane, inputs, ctx):
            return False
        return all(abs(rigid.normal[plane.component]) <= 1e-9 for rigid in inputs.planes)

    def governing(self, result: AnalysisResult):
        """local_refine follows the contact pressure at the last load carried (the stress where nothing presses)."""
        pressure = result.scalars["final"]["contact_pressure"]
        if float(pressure.max()) > 0:
            return pressure, float(pressure.max())
        return super().governing(result)

    # -- solve ---------------------------------------------------------------------------------------

    def _part_index(self, ctx: SolveContext, name: str, where: str) -> int:
        if ctx.assembly is None:
            return 0
        from cadgen._internal.fea.run import find_part

        return find_part(ctx.assembly.parts, ctx.assembly.names, name, where)

    def _part_names(self, ctx: SolveContext) -> list[str]:
        return list(ctx.assembly.names) if ctx.assembly is not None else [ctx.part_name or "the part"]

    def _nodes(self, ctx: SolveContext, materials: list, names: list[str]) -> _Nodes:
        from cadgen._internal.fea import contact_ops

        node_part, node_body = contact_ops.body_of_nodes(ctx.space)
        tolerance = float(ctx.study.contact_tolerance_mm) if ctx.study is not None else 0.1
        E_of_part = {index: float(materials[min(index, len(materials) - 1)].E) for index in range(max(len(names), 1))}
        return _Nodes(node_part, node_body, ctx.space.dof_locations, tolerance, E_of_part)

    def _pair_rows(self, ctx: SolveContext, nodes: _Nodes, i: int, j: int, friction: float, n: int):
        """Contact ``n``'s constraint rows between parts ``i`` and ``j``: the smaller part's nodes on the other's surface."""
        from cadgen._internal.fea import contact_ops

        volume = (lambda k: ctx.assembly.parts[k].volume_mm3) if ctx.assembly is not None else (lambda k: 0.0)
        slave, master = (i, j) if volume(i) <= volume(j) else (j, i)
        search = max(2.0 * nodes.tolerance, 0.1 * min(_diagonal(nodes.points, nodes.part, [i]),
                                                       _diagonal(nodes.points, nodes.part, [j])))
        return contact_ops.pair_constraints(ctx.space, contact_ops.PairSpec(slave, master, friction, n), nodes.part,
                                            min(nodes.E_of_part[i], nodes.E_of_part[j]), search_mm=search,
                                            tolerance_mm=nodes.tolerance)

    @staticmethod
    def _pair_entry(name: str, names: list[str], refs: list[str], i: int, j: int, friction: float) -> dict:
        return {"kind": "pair", "name": name, "between": [names[i], names[j]], "refs": [refs[i], refs[j]],
                "friction": friction, "words": contact_words((names[i], names[j]), friction)}

    def _plane_rows(self, ctx: SolveContext, inputs: ContactInputs, nodes: _Nodes, names: list[str], refs: list[str],
                    rows: list, contacts: list[dict]) -> None:
        """Each rigid plane's constraint rows and contact entry, appended to ``rows`` and ``contacts``."""
        import numpy as np

        from cadgen._internal.fea import contact_ops

        for n, plane in enumerate(inputs.planes):
            parts = tuple(sorted({self._part_index(ctx, name, f"rigid_planes[{n}].parts") for name in plane.parts})) \
                if plane.parts and ctx.assembly is not None else tuple(range(max(len(names), 1)))
            normal = np.asarray(plane.normal, dtype=float)
            on = np.isin(nodes.part, list(parts))
            if on.any() and float((nodes.points[on].mean(axis=0) - np.asarray(plane.point)) @ normal) < 0:
                normal = -normal  # it faces the parts it holds up
            spec = contact_ops.PlaneSpec(plane.point, tuple(float(c) for c in normal), parts, plane.friction, n)
            rows.append(contact_ops.plane_constraints(
                ctx.space, spec, nodes.part, nodes.E_of_part, tolerance_mm=nodes.tolerance,
                search_mm=max(2.0 * nodes.tolerance, 0.1 * _diagonal(nodes.points, nodes.part, parts))))
            label = _plane_name(n, len(inputs.planes))
            contacts.append({"kind": "plane", "name": label, "refs": [refs[p] for p in parts], "friction": plane.friction,
                             "normal": [float(c) for c in normal], "words": f"{' and '.join(names[p] for p in parts)} on {label}"})

    def _loose_bodies(self, ctx: SolveContext, supports, constraints, nodes: _Nodes, names: list[str], pair_count: int,
                      links: list, words: str):
        """(vector DOF per node, the bodies no support holds, each one's vector DOF). What holds each body (a connected
        piece of mesh): a support, a rigid plane, or a contact (or ``links``: more (bodies, bodies) pairs) with a held
        body; a body held by none is a study error ending in ``words``."""
        import numpy as np

        from cadgen._internal.fea import contact_ops
        from cadgen._internal.fea.checks import quoted
        from cadgen._internal.fea.supports import held_bodies

        space, node_body = ctx.space, nodes.body
        bodies = int(node_body.max()) + 1 if len(node_body) else 0
        scalar_of = np.zeros(space.basis.N, dtype=np.int64)
        vdofs = contact_ops.vector_dofs(space)
        for c in range(3):
            scalar_of[vdofs[:, c]] = np.arange(space.scalar_count)
        fixed_bodies = held_bodies(space, supports, node_body[scalar_of])  # a body on rollers alone may still move
        holds = set(fixed_bodies) | set(np.unique(node_body[constraints.node[constraints.owner < 0]]).tolist())
        pair_links = []
        for k in range(pair_count):
            mine = constraints.owner == k
            if mine.any():
                pair_links.append((set(np.unique(node_body[constraints.node[mine]]).tolist()),
                                   set(np.unique(node_body[constraints.master_nodes(mine)]).tolist())))
        grown = True
        while grown:
            grown = False
            for a, b in pair_links + links:
                if (a & holds) and not b <= holds or (b & holds) and not a <= holds:
                    holds |= a | b
                    grown = True
        unheld = [b for b in range(bodies) if b not in holds]
        if unheld:
            parts_of = sorted({int(p) for p in np.unique(nodes.part[np.isin(node_body, unheld)])})
            who = " and ".join(quoted(names[p]) for p in parts_of)
            raise ValueError(f"{who} {'is' if len(parts_of) == 1 else 'are'} not held by anything: {words}")
        loose = [b for b in range(bodies) if b not in fixed_bodies]
        return vdofs, loose, [vdofs[node_body == b].ravel() for b in loose]

    @staticmethod
    def _pressing_bodies(constraints, node_body, response) -> set:
        """The bodies a contact presses at ``response``: either side of a pair, the part on a plane."""
        import numpy as np

        pressing = set(np.unique(node_body[constraints.node[response.pressing]]).tolist())
        return pressing | set(np.unique(node_body[constraints.master_nodes(response.pressing & (constraints.owner >= 0))]).tolist())

    @staticmethod
    def _warn_unsettled(problem, warnings: list[str]) -> None:
        from cadgen._internal.fea import contact_ops

        if problem.unsettled > UNSETTLED:
            warnings.append(f"the contact forces were still changing by {problem.unsettled * 100:.2g} % after "
                            f"{contact_ops.MAX_AUGMENTATIONS} updates at a load step")

    def solve(self, ctx: SolveContext, inputs: ContactInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea import contact_ops, nonlinear_driver, operators
        from cadgen._internal.fea.analyses.nonlinear import external_load, supported
        from cadgen._internal.fea.checks import quoted
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
        K = operators.stiffness(space, materials).tocsr()

        # The contacts: pairs between parts, then rigid planes.
        names = self._part_names(ctx)
        if ctx.assembly is not None:
            refs = [part.ref for part in ctx.assembly.parts]
        else:  # one part: its occurrence, as its faces' refs name it
            refs = [next((face.ref.rpartition(".f")[0] for face in ctx.volume.faces.values()), "")]
        nodes = self._nodes(ctx, materials, names)
        node_part, node_body = nodes.part, nodes.body
        rows: list = []
        contacts: list[dict] = []
        for n, pair in enumerate(inputs.pairs):
            where = f"connections (contact {n + 1})"
            i, j = (self._part_index(ctx, name, f"{where}.between") for name in pair.between)
            if i == j:
                raise ValueError(f"{where}.between: both names are {quoted(names[i])}")
            rows.append(self._pair_rows(ctx, nodes, i, j, pair.friction, n))
            contacts.append(self._pair_entry(f"{pair.between[0]} on {pair.between[1]}", names, refs, i, j, pair.friction))
        self._plane_rows(ctx, inputs, nodes, names, refs, rows, contacts)
        # Plane rows are owned by ``-1 - plane index``; number them after the pairs for one list of contacts.
        constraints = contact_ops.Constraints.concatenate(rows, space.scalar_count)
        owner = np.where(constraints.owner >= 0, constraints.owner, len(inputs.pairs) + (-1 - constraints.owner))
        vdofs, loose, floating = self._loose_bodies(
            ctx, supports, constraints, nodes, names, len(inputs.pairs), [],
            "fix a face of it, put it in contact with a held part (the faces must face each other, near enough to touch), "
            "or give it a rigid plane to rest on")
        stabilise = contact_ops.stabilising_springs(K, basis.N, floating)
        # A body resting on contacts alone first moves, rigidly, along its load until it touches (a stress-free move).
        shift = contact_ops.close_gaps(constraints, node_body, vdofs, external, loose)
        timings["assemble_s"] = time.perf_counter() - started

        history: list[tuple] = []

        def record(u, response):
            vm = operators.von_mises(operators.stress(space, materials, u))
            history.append((vm, response))

        solver = plan_value(ctx, "solver", "direct")
        method = "iterative" if solver in ("iterative", "matrix_free") else "direct"
        adaptive = bool(plan_value(ctx, "adaptive_steps", False))
        problem = contact_ops.ContactProblem(space, K, free, external, constraints, stabilise, method=method,
                                             on_commit=record)
        if ctx.log:
            ctx.log(f"{constraints.count} contact nodes over {len(contacts)} contacts; {len(floating)} bodies rest on contacts alone")
        # runaway=None: a part that rests on a contact has no stiffness of its own until the contact closes.
        path = path_to_global(nonlinear_driver.solve_path(driven(problem, supports, free), steps=inputs.steps, solver=solver,
                                                          adaptive=adaptive, runaway=None, log=ctx.log,
                                                          deadline_s=path_deadline(ctx)), supports)
        timings["solve_s"] = path.seconds
        warnings += path.warnings
        self._warn_unsettled(problem, warnings)
        if ctx.log:
            ctx.log(f"followed the load to {path.factor * 100:.4g}% in {len(path.records)} steps, {path.iterations} Newton "
                    f"iterations and {problem.augmentations} contact updates, in {path.seconds:.1f}s")

        started = time.perf_counter()
        records = path.records
        if not records:  # not even the smallest first step: the unloaded parts are the one frame
            zeros = np.zeros(problem.size)
            records = [nonlinear_driver.StepRecord(0.0, 0, zeros, 0.0)]
            history = [(np.zeros(basis.dx.shape), problem.response(zeros))]
        picked = pick_frames(len(records), max(max_frames(ctx), 1))
        # A step kept although its contact did not settle is always among the frames, and its label says so.
        loose_steps = {i for i, record in enumerate(records) if not record.settled}
        picked = sorted(set(picked) | loose_steps)
        stress_frames = [nodal(space, history[index][0]) for index in picked]
        displacement_frames = [space.nodal(records[index].u + shift) for index in picked]
        pressure_frames = [contact_ops.pressure_field(space, constraints, history[index][1].normal_force) for index in picked]
        final = records[-1]
        final_response = history[-1][1]
        final_pressure = pressure_frames[-1]  # the last step is always among the frames
        # A body resting on contacts alone whose load its drift springs take (more than a hair of it) is not held:
        # it comes loose when none of its contacts presses, else it slides away (friction cannot hold it sideways).
        pressing_bodies = self._pressing_bodies(constraints, node_body, final_response)
        loose_parts: list[dict] = []
        total = float(np.linalg.norm([final.factor * external[space.component == k].sum() for k in range(3)]))
        for b, dofs in zip(loose, floating):
            unheld = math.sqrt(sum(c * c for c in totals(space, stabilise * final.u, dofs)))
            load = [float(final.factor * c) for c in totals(space, external, dofs)]
            if unheld > LOOSE_SHARE * max(math.sqrt(sum(c * c for c in load)), total, 1e-300):
                loose_parts.append({"parts": sorted({names[int(p)] for p in np.unique(node_part[node_body == b])}),
                                    "pressing": b in pressing_bodies, "unheld_N": unheld / share})
        stats = self._contact_stats(contacts, constraints, owner, final_response, final_pressure, nodes.points, share,
                                    float(np.linalg.norm(space.nodal(final.u + shift), axis=1).max()))
        internal, _ = problem.evaluate(final.u, tangent=False) if path.records else (np.zeros(problem.size), None)
        residual = internal - final.factor * external
        reactions = [totals(space, residual, dofs) for dofs in supports.per_fixture]
        requested = totals(space, external)
        applied = tuple(float(final.factor * c) for c in requested)
        vm_gauss = float(history[-1][0].max())
        mesh, scalars, vectors, reactions, (applied, requested) = unfold(
            ctx, planes, {"von_mises": stress_frames, "contact_pressure": pressure_frames},
            {"displacement": displacement_frames}, reactions, applied, requested)
        timings["stress_s"] = time.perf_counter() - started

        factors = [records[i].factor for i in picked]
        unsettled = unsettled_steps(path)
        frame_fields = {"von_mises": scalars["von_mises"], "displacement": vectors["displacement"],
                        "contact_pressure": scalars["contact_pressure"]}
        series = self._series("%", frame_fields, [
            (round(records[i].factor * 100.0, 6), load_label(records[i].factor) + (" · did not settle" if i in loose_steps else ""))
            for i in picked])
        final_fields = {name: values[-1] for name, values in frame_fields.items()}
        curve_x = [round(record.factor * 100.0, 6) for record in path.records]
        curves = {
            "max_displacement_mm": {"x": curve_x, "x_unit": "%", "y_unit": "mm",
                                    "y": [round(float(np.linalg.norm(space.nodal(r.u + shift), axis=1).max()), 6) for r in path.records]},
            "contact_force_N": {"x": curve_x, "x_unit": "%", "y_unit": "N",
                                "y": [round(float(h[1].normal_force.sum()) / share, 6) for h in history[:len(path.records)]]},
        }
        how = f"{path.how}, {len(path.records)} {'adaptive ' if adaptive else ''}load steps, {problem.augmentations} contact updates"
        peak_node = int(final_fields["contact_pressure"].argmax())
        return self._result(mesh, frame_fields, series, curves, reactions, applied, basis, how, timings, warnings, {
            "path": path, "final": final_fields, "factor": path.factor if path.records else 0.0, "collapsed": path.collapsed,
            "requested": requested, "model": "elastic", "adaptive": adaptive, "von_mises_gauss_max": vm_gauss,
            "frame_factors": factors, "contacts": stats, "augmentations": problem.augmentations,
            "contact_nodes": constraints.count, "floating_bodies": len(floating), "loose_parts": loose_parts,
            "peak_pressure_at": xyz(mesh[0][peak_node]),
            **unsettled_scalars([stop_words(path.factor)] if path.collapsed else [], unsettled),
            "part_refs": None if ctx.assembly is None else [part.ref for part in ctx.assembly.parts],
            "part_names": names, "part_materials": part_materials(ctx),
        })

    def _contact_stats(self, contacts, constraints, owner, response, pressure, points, share: float,
                       moved: float) -> list[dict]:
        """Each contact at the last load carried: force, area, peak pressure and where, sliding, slipping."""
        import numpy as np

        stats = []
        for k, contact in enumerate(contacts):
            mine = owner == k
            p = response.normal_force[mine]
            pressing = p > 0
            slaves = constraints.node[mine][pressing]
            entry = {key: contact[key] for key in ("kind", "name", "words", "friction", "between", "refs", "normal") if key in contact}
            entry.update({"nodes": int(mine.sum()), "touching": bool(pressing.any()),
                          "force_N": float(p.sum()) / share, "area_mm2": float(constraints.area[mine][pressing].sum()) / share,
                          "friction_N": float(np.linalg.norm(response.tangential[mine].sum(axis=0))) / share})
            if pressing.any():
                values = pressure[slaves]
                peak = int(values.argmax())
                slide = np.linalg.norm(response.sliding[mine][pressing], axis=1)
                entry.update({
                    "peak_MPa": float(values[peak]), "peak_at": [float(c) for c in points[slaves[peak]]],
                    "max_slide_mm": float(slide.max()),
                    "slides": bool(slide.max() > SLIDE_SHARE * max(moved, 1e-12)),
                    "slipping_share": float(response.slipping[mine][pressing].mean()) if contact["friction"] else None,
                })
            else:
                entry.update({"peak_MPa": 0.0, "peak_at": None, "max_slide_mm": 0.0, "slides": False, "slipping_share": None})
            stats.append(entry)
        return stats

    # -- judging -------------------------------------------------------------------------------------

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: ContactInputs) -> dict:
        if check["kind"] != "contact_pressure":
            return mark_unsettled(super().judge(check, index, ctx, result, inputs), result.scalars)
        peak = field_peak(ctx, result, result.scalars["final"]["contact_pressure"], tuple(check.get("faces", ())), index)
        judged = peak_check("contact_pressure", check.get("label") or kinds.CONTACT_PRESSURE.default_label, peak,
                            float(check["limit_MPa"]), "MPa")
        if check.get("faces"):
            judged["faces"] = list(peak.faces)
        judged["at"] = self._at(result)
        judged["scaling"] = "none"  # contact opens, closes and slides: not linear in the load
        return mark_unsettled(mark_stopped(judged, result.scalars), result.scalars)

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: ContactInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        scalars = result.scalars
        path = scalars["path"]
        found: list[dict] = []
        for step in (scalars.get("unsettled") or [])[:1]:
            others = len(scalars["unsettled"]) - 1
            found.append(finding(
                "error", "contact_unsettled", unsettled_sentence(step["percent"], step["of"]),
                "the contact forces kept changing there even at the smallest load step, and the last re-solve found no "
                "equilibrium, so the parts' forces do not add up; every check fails until it settles"
                + (f" ({others} more step{'s' if others > 1 else ''} did not settle either)" if others else "")
                + ": try more load steps, a finer mesh where the parts touch, or friction 0 to see whether it is the sliding",
            ))
        if scalars["collapsed"] and getattr(path, "out_of_time", False):
            found.append(finding(
                "error", "contact_stops",
                f"The contact could not be followed past about {scalars['factor'] * 100:.0f} % of the load in the time allowed",
                f"no load step got through past {scalars['factor'] * 100:.4g} % for longer than the study's time target, "
                f"cutting down to steps of {path.smallest_step * 100:.3g} %, so the solve stopped there: it is not known "
                "whether the parts carry more; the last frame is the last load solved. A longer fit.seconds lets it "
                "keep cutting",
            ))
        elif scalars["collapsed"]:
            found.append(finding(
                "error", "contact_stops",
                f"The contact could not be followed past about {scalars['factor'] * 100:.0f} % of the load",
                f"past {scalars['factor'] * 100:.4g} % of the load Newton's method finds no equilibrium, even in steps of "
                f"{path.smallest_step * 100:.3g} %: a part may be sliding or lifting off with nothing to stop it; the last "
                "frame is the last load solved",
            ))
        for loose in scalars["loose_parts"]:
            who = " and ".join(f"'{part}'" for part in loose["parts"])
            if not loose["pressing"]:
                found.append(finding(
                    "error", "comes_loose",
                    f"{who} comes loose: it rests only on contacts, and under this load it lifts off every one of them",
                    "nothing holds it once its contacts open, so its displacement is only how far its weak drift spring "
                    "lets it go and says nothing; hold it with a fixture, or load it towards what it rests on",
                ))
            else:
                found.append(finding(
                    "error", "slides_away",
                    f"{who} slides away: its contacts cannot hold {loose['unheld_N']:.3g} N of its load sideways",
                    "it rests only on contacts, and friction (or none) holds less than the sideways load, so nothing stops "
                    "it sliding; its displacement is only how far its weak drift spring lets it go and says nothing",
                ))
        for contact in scalars["contacts"]:
            name = contact["name"]
            title = f"{name[0].upper()}{name[1:]}"
            if not contact["touching"]:
                found.append(finding(
                    "warning", "contact_separates",
                    f"{title}: they separate under this load and no longer touch"
                    if contact["kind"] == "pair" else f"Nothing presses on {name}: the part lifts off it",
                    "no point of the contact carries a force at the last load, so it passes no load (a contact never pulls)",
                ))
                continue
            at = xyz(contact["peak_at"])
            found.append(finding(
                "info", "contact_presses",
                f"{contact['words'].split(',')[0]}: {contact['force_N']:.4g} N over {contact['area_mm2']:.3g} mm², "
                f"peak contact pressure {contact['peak_MPa']:.4g} MPa",
                f"the contact carries {contact['force_N']:.6g} N at the last load; its pressure peaks at {at}",
                [{"text": "peak contact pressure", "ref": None, "at": at}],
            ))
            if contact["friction"]:
                share = contact["slipping_share"] or 0.0
                if share > 0:
                    found.append(finding(
                        "info", "contact_slides",
                        f"{title} slides over {share * 100:.0f} % of the contact (friction {contact['friction']:g}), "
                        f"up to {contact['max_slide_mm']:.3g} mm",
                        "where the sideways force reaches friction times the pressure the surfaces slip; elsewhere they stick",
                    ))
                else:
                    found.append(finding("info", "contact_sticks",
                                         f"{title} sticks: friction {contact['friction']:g} holds it from sliding",
                                         "the sideways force stays under friction times the pressure everywhere in the contact"))
            elif contact["slides"]:
                found.append(finding(
                    "info", "contact_slides",
                    f"{title} slides freely (no friction), up to {contact['max_slide_mm']:.3g} mm",
                    "with no friction nothing holds the surfaces from sliding along each other",
                ))
        touching = [c for c in scalars["contacts"] if c["touching"]]
        if touching:
            top = max(touching, key=lambda c: c["peak_MPa"])
            at = xyz(top["peak_at"])
            found.append(finding(
                "info", "peak_contact_pressure",
                f"The contact pressure peaks at {top['peak_MPa']:.4g} MPa where {top['name']} touch" if top["kind"] == "pair"
                else f"The contact pressure peaks at {top['peak_MPa']:.4g} MPa on {top['name']}",
                f"at {at}, at the last load carried",
                [{"text": "peak contact pressure", "ref": None, "at": at}],
            ))
        if path.cuts and not scalars["collapsed"]:
            found.append(finding(
                "info", "load_steps_cut",
                f"Cut the load steps down to {path.smallest_step * 100:.3g} % to get through where the contacts open, close or slip",
                f"{path.cuts} steps did not converge at first and were halved"))
        return by_severity(found)

    # -- what is written -----------------------------------------------------------------------------

    def summary(self, result: AnalysisResult, inputs: ContactInputs, check_results: list[dict]) -> dict:
        scalars = result.scalars
        path = scalars["path"]
        pressure = scalars["final"]["contact_pressure"]

        def rounded(contact: dict) -> dict:
            return {key: round(value, 6) if isinstance(value, float) else xyz(value) if key == "peak_at" and value is not None
                    else value for key, value in contact.items()}

        unsettled = scalars.get("unsettled") or []
        status = stop_words(scalars["factor"]) if scalars["collapsed"] else "Carries the full load"
        if unsettled:
            status = unsettled_status(unsettled)
        summary: dict[str, Any] = {
            "status": status,
            "collapsed": scalars["collapsed"],
            "unsettled_at_percent": [step["percent"] for step in unsettled],
            "load_percent": round(scalars["factor"] * 100.0, 4),
            # The path stopped because it got through no step for the study's time target, not for want of equilibrium.
            "out_of_time": bool(getattr(path, "out_of_time", False)),
            **self._peaks(result),
            "max_contact_pressure_MPa": round(float(pressure.max()), 4),
            "max_contact_pressure_at_mm": scalars["peak_pressure_at"] if float(pressure.max()) > 0 else None,
            "contacts": [rounded(c) for c in scalars["contacts"]],
            **self._path_record(result, inputs),
            "contact_updates": scalars["augmentations"],
            "contact_nodes": scalars["contact_nodes"],
            **self._frames_record(result),
        }
        if scalars.get("part_refs"):
            summary["parts"] = with_materials([{"ref": ref, "name": name} for ref, name in zip(scalars["part_refs"], scalars["part_names"])],
                                              scalars)
        summary["checks"] = check_results
        return summary

    def extras_head(self, summary: dict) -> dict:
        head = {"load_percent": summary["load_percent"], "collapsed": summary["collapsed"], "contacts": summary["contacts"]}
        if summary.get("unsettled_at_percent"):
            head["unsettled_at_percent"] = summary["unsettled_at_percent"]
        if summary.get("out_of_time"):
            head["out_of_time"] = True
        return head

    def study_echo(self, inputs: ContactInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        from cadgen._internal.fea.analyses.static import StaticAnalysis

        return {
            **StaticAnalysis().study_echo(inputs, bare), "steps": inputs.steps,
            "contact_pairs": [{"between": list(pair.between), "friction": pair.friction} for pair in inputs.pairs],
            "rigid_planes": [{"point_mm": list(plane.point), "normal": list(plane.normal), "parts": list(plane.parts),
                              "friction": plane.friction} for plane in inputs.planes],
        }

    def human_lines(self, summary: dict) -> list[str]:
        head = summary["status"].lower() if summary["collapsed"] or summary.get("unsettled_at_percent") else "carries the full load"
        lines = [f"{head}: max von Mises {summary['max_von_mises_MPa']:g} MPa, max displacement "
                 f"{summary['max_displacement_mm']:g} mm, peak contact pressure {summary['max_contact_pressure_MPa']:g} MPa"]
        for contact in summary["contacts"]:
            if contact["touching"]:
                lines.append(f"{contact['name']}: presses with {contact['force_N']:g} N over {contact['area_mm2']:g} mm², "
                             f"peak {contact['peak_MPa']:g} MPa")
            else:
                lines.append(f"{contact['name']}: no longer touching")
        lines.append(f"followed in {summary['steps']} {'adaptive ' if summary['adaptive'] else ''}load steps "
                     f"({summary['newton_iterations']} Newton iterations, {summary['contact_updates']} contact updates"
                     + self._cut_words(summary) + ")")
        return lines


def stop_words(factor: float) -> str:
    """"Stops at about 70 % of the load": where the contact could not be followed further."""
    percent = factor * 100.0
    if percent < 1.0:
        return "Stops before 1 % of the load"
    return f"Stops at about {percent:.0f} % of the load"
