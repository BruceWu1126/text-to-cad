"""What the ``cadgen.cam`` verbs do, end to end.

``plan_job`` runs a job: parse it (stdlib, before anything heavy), open the part, make the stock, place every setup and
recognise what its tool reaches, then plan each operation in the job's order, check the plan, post it and write the
three outputs beside the GLB OUT names:

- ``<stem>.nc`` (one setup with toolpaths) or ``<stem>.<setup id>.nc`` (several): one program per setup;
- ``<stem>.cam.glb``: the result GLB (``outputs.py`` holds its contract);
- ``<stem>.cam.json``: the sidecar, the whole result plus the job.

The stem is OUT's name without ``.cam.glb`` (or ``.glb``); OUT defaults to ``<document stem>.cam.glb`` beside the
document.

Planning
    Each operation gets a :class:`~cadgen._internal.cam.operations.context.PlanContext`, with one geometry cache per
    setup. It starts where the last toolpath of the same setup left the tool when that toolpath has the same tool
    (the post joins them with no tool change), else at its own clearance height. A toolpath an operation runs before
    its own with another tool (an engraving's clear_tool) starts at its clearance height; when that tool is the one
    the last toolpath of the setup ran, it is joined to where that one ended (:func:`_join`: up to the clearance
    height, across, down), since the post runs them with no tool change between. An operation that raises
    ``PlanError`` becomes that error finding and the job is not planned; every operation is still tried, so one run
    lists every refusal. Anything else an operation raises propagates: a ValueError names the job's field, any other
    error is a bug.

Time
    Every move is timed at its feed and rapids at ``machine.rapid_mm_min``; a job that does not state it is timed at
    :data:`DEFAULT_RAPID_MM_MIN`, and a warning says the cycle time assumed it. The cycle time is the moves' and the
    dwells': tool changes, spindle spin-up and setup changes are not in it. Toolpaths are timed one after another in
    running order, so ``_T`` in the GLB is the job's clock.

Checks
    :data:`PlannedOp` is what the job's checks read. The run calls ``checks.check_job(job, planned_ops, part=part,
    stock=stock, setups=frames, features=features)`` once, after every operation is planned; it returns every finding
    of the planned operations (their own, the feeds' clamps merged per tool) and of the checks. An operation that
    raised ``PlanError`` is not among ``planned_ops``; its finding is the run's. The result's ``checks`` lists each of
    ``checks.CHECK_KINDS`` with its verdict: the worst severity among its findings, else ``"pass"``.

Posting
    A job with no error is posted. A post that refuses it (``PostRefusal``: a tap on grbl, which has no tapping
    cycle) is a ``no_post_support`` error finding, not an exception: the job is not fit for that post.

A job with an error finding is ``ok=False``: no G-code and no GLB are written (a stale one at those paths is
removed, README law 10), and the sidecar still is, with the findings, so a person reads why.
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from cadgen.results import CamFeaturesResult, CamResult

if TYPE_CHECKING:
    from cadgen._internal.cam.job import Job, Operation
    from cadgen._internal.cam.operations.context import PlanContext, Planned

__all__ = ["DEFAULT_RAPID_MM_MIN", "PlannedOp", "list_features", "output_paths", "plan_job"]

#: The rapid rate a job that does not state ``machine.rapid_mm_min`` is timed at, mm/min: a hobby router's or a small
#: mill's, so the cycle time errs long on a bigger machine rather than short.
DEFAULT_RAPID_MM_MIN = 5000.0
#: Progress lines without --verbose: at most one every this many seconds.
PROGRESS_S = 10.0
#: What a setup id may hold when it names a G-code file (``<stem>.<setup id>.nc``).
_FILE_SAFE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class PlannedOp:
    """One operation of a job, planned: what the checks (``checks.check_job``) read.

    ``index`` is its place in ``job.operations``; ``operation`` the job's entry (``job.operations[index]``); ``ctx``
    the context it was planned in (setup frame, part, features, tool, profile, feeds, heights); ``planned`` what its
    ``plan`` returned, with every toolpath timed on the job's clock (``Move.t``).
    """

    index: int
    operation: "Operation"
    ctx: "PlanContext"
    planned: "Planned"


def list_features(target: Path, *, occurrence: str | None = None, direction: str | None = None,
                  verbose: bool = False) -> CamFeaturesResult:
    from cadgen._internal.cam.features import parse_direction, recognize
    from cadgen._internal.cam.part import open_part, same
    from cadgen.cli_logging import CliLogger

    wanted = None if direction is None else parse_direction(direction)
    logger = CliLogger("cam", verbose=verbose)
    part = open_part(Path(target), occurrence)
    logger.debug(f"recognizing the features of {part.ref} ({len(part.faces)} faces)")
    found = recognize(part)
    if wanted is not None:
        found = tuple(feature for feature in found if any(same(d, wanted) for d in feature.reach))
    return CamFeaturesResult(ok=True, document=Path(target), occurrence=part.ref, direction=direction,
                             features=tuple(feature.public() for feature in found))


# --- naming ------------------------------------------------------------------------------------------------

def output_paths(document: Path, out: Path | None) -> tuple[Path, Path, str]:
    """``(glb, sidecar, stem)``: where the GLB and the sidecar go, and the stem the G-code files are named from."""
    glb = Path(out) if out is not None else document.with_name(f"{document.stem}.cam.glb")
    name = glb.name
    if not name.lower().endswith(".glb"):
        raise ValueError(f"OUT names the result GLB and must end in .cam.glb or .glb: {out}")
    stem = name[:-len(".cam.glb")] if name.lower().endswith(".cam.glb") else name[:-len(".glb")]
    if not stem:
        raise ValueError(f"OUT needs a name before .glb: {out}")
    return glb, glb.with_name(f"{stem}.cam.json"), stem


def _nc_names(stem: str, setup_ids) -> dict[str, str]:
    ids = list(setup_ids)
    if len(ids) == 1:
        return {ids[0]: f"{stem}.nc"}
    return {setup_id: f"{stem}.{setup_id}.nc" for setup_id in ids}


def _relative(path: Path, folder: Path) -> str:
    """``path`` from ``folder``; absolute only when they share no root (another Windows drive)."""
    try:
        return Path(os.path.relpath(path.resolve(), folder.resolve())).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def _job_name(job) -> str:
    if isinstance(job, Path):
        return job.name
    if isinstance(job, str) and not job.strip().startswith("{"):
        return Path(job.strip()).name
    return ""


# --- the run -----------------------------------------------------------------------------------------------

def _progress(logger):
    """Every line with --verbose; without, one only once ``PROGRESS_S`` has passed since the last (the run's start
    the first), as FEA's ``progress_log``."""
    if logger.verbose:
        return logger.debug
    import time

    said = [time.perf_counter()]

    def log(message: str) -> None:
        now = time.perf_counter()
        if now - said[0] >= PROGRESS_S:
            said[0] = now
            logger.info(message)

    return log


def _round(value: float, places: int = 3) -> float:
    return round(float(value), places) + 0.0


def plan_job(target: Path, out: Path | None, *, job=None, occurrence: str | None = None, post: str | None = None,
             verbose: bool = False) -> CamResult:
    """Plan ``job`` on the part ``target`` holds and write its G-code, GLB and sidecar (see the module docstring)."""
    import time

    from cadgen._internal.cam.job import parse_job
    from cadgen._internal.cam.posts import get_post
    from cadgen.cli_logging import CliLogger

    document = Path(target)
    parsed = parse_job(job, post=post)  # stdlib, before anything heavy
    glb_path, sidecar_path, stem = output_paths(document, out)
    if len(parsed.setups) > 1:
        for index, setup in enumerate(parsed.setups):
            if not _FILE_SAFE.fullmatch(setup.id):
                raise ValueError(f"setups[{index}].id: {setup.id!r} names its G-code file ({stem}.<id>.nc) in a job "
                                 "of several setups; use letters, digits, '-', '_' and '.'")
    poster = get_post(parsed.machine.post)
    logger = CliLogger("cam", verbose=verbose)
    log = _progress(logger)
    clock = time.perf_counter()
    timings: dict[str, float] = {}

    from cadgen._internal.cam.features import features_in_frame
    from cadgen._internal.cam.operations import get_operation
    from cadgen._internal.cam.operations.base import PlanError, by_severity, naming_operation
    from cadgen._internal.cam.operations.context import PlanContext, _face_normal
    from cadgen._internal.cam.part import open_part
    from cadgen._internal.cam.setups import setup_frames
    from cadgen._internal.cam.stock import make_stock

    part = open_part(document, occurrence)
    log(f"opened {part.ref} of {document.name} ({len(part.faces)} faces)")
    stock = make_stock(parsed.stock, part.shape)
    frames = setup_frames(parsed, part.shape, stock, face_normal=lambda ref: _face_normal(part, ref))
    features = {setup_id: features_in_frame(part, frame) for setup_id, frame in frames.items()}
    caches: dict[str, dict] = {setup_id: {} for setup_id in frames}
    timings["open_s"] = time.perf_counter() - clock

    warnings: list[str] = []
    rapid = parsed.machine.rapid_mm_min
    if rapid is None:
        rapid = DEFAULT_RAPID_MM_MIN
        warnings.append(f"machine.rapid_mm_min is not stated: the cycle time assumes rapids at {rapid:g} mm/min")

    clock = time.perf_counter()
    planned_ops: list[PlannedOp] = []
    findings: list[dict] = []
    last: dict[str, Any] = {}        # setup id -> the last toolpath planned in it
    starts: dict[int, tuple[float, ...]] = {}   # operation index -> the job time each of its toolpaths starts at
    t = 0.0
    for index, operation in enumerate(parsed.operations):
        previous = last.get(operation.setup)
        start = previous.end if previous is not None and previous.tool == operation.tool else None
        ctx = PlanContext.build(parsed, index, part, stock=stock, setup=frames[operation.setup],
                                features=features[operation.setup], start=start, log=log,
                                cache=caches[operation.setup])
        log(f"planning operations[{index}] ({operation.op})")
        try:
            planned = get_operation(operation.op).plan(ctx.params, ctx)
        except PlanError as exc:
            findings.append(naming_operation(exc.finding(), index, ctx.label))
            continue
        if planned.before and previous is not None:
            planned = dataclasses.replace(planned, before=(_join(previous, planned.before[0], ctx),
                                                           *planned.before[1:]))
        timed, began = [], []
        for toolpath in planned.toolpaths:
            began.append(t)
            toolpath = toolpath.timed(rapid, t0=t)
            t = toolpath.moves[-1].t if toolpath.moves else t
            timed.append(toolpath)
        starts[index] = tuple(began)
        planned = dataclasses.replace(planned, before=tuple(timed[:-1]), toolpath=timed[-1])
        planned_ops.append(PlannedOp(index, operation, ctx, planned))
        if planned.toolpath.moves or planned.before:
            last[operation.setup] = planned.toolpath
    timings["plan_s"] = time.perf_counter() - clock

    from cadgen._internal.cam import checks
    from cadgen._internal.cam.posts.base import PostRefusal

    clock = time.perf_counter()
    log("checking the plan")
    findings.extend(checks.check_job(parsed, tuple(planned_ops), part=part, stock=stock, setups=frames,
                                     features=features))
    timings["check_s"] = time.perf_counter() - clock

    toolpaths = [toolpath for planned_op in planned_ops for toolpath in planned_op.planned.toolpaths]
    programs: dict[str, str] = {}
    if not any(finding["severity"] == "error" for finding in findings):
        clock = time.perf_counter()
        try:
            programs = poster.programs(parsed, parsed.setups, toolpaths, part=document.name,
                                       job_name=_job_name(job))
        except PostRefusal as exc:
            findings.append(_refused(poster, exc))
            programs = {}
        timings["post_s"] = time.perf_counter() - clock
    findings = by_severity(findings)
    verdicts = [{"type": kind, "status": next((f["severity"] for f in findings if f["type"] == kind), "pass")}
                for kind in checks.CHECK_KINDS]
    ok = not any(finding["severity"] == "error" for finding in findings)
    nc_names = _nc_names(stem, programs)

    operations = [_operation_entry(planned_op, starts[planned_op.index]) for planned_op in planned_ops]
    summary = _summary(parsed, operations, toolpaths, rapid, poster.name, len(programs))
    setups = [_setup_entry(frames[setup.id], nc_names.get(setup.id)) for setup in parsed.setups]
    tools = _tool_entries(parsed, planned_ops)
    machine = _machine_entry(parsed, rapid)

    from cadgen._internal.atomic_replace import write_bytes_atomic

    clock = time.perf_counter()
    folder = glb_path.parent
    if ok:
        for setup_id, text in programs.items():
            write_bytes_atomic(folder / nc_names[setup_id], text.encode("ascii"))
        extras = {
            "generator": "cadgen cam",
            "document": _relative(document, folder),
            "occurrence": part.ref,
            "frame": {"of": "part", "units": "m", "axes": "glTF: x is the part's x, y its z, z its -y"},
            "machine": machine,
            "setups": setups,
            "tools": tools,
            "operations": operations,
            "summary": summary,
            "findings": findings,
            "checks": verdicts,
            "playback": {"duration_s": summary["cycle_time_s"]},
            "view": parsed.view,
        }
        write_bytes_atomic(glb_path, _glb_bytes(extras, part, stock, frames, planned_ops, starts, parsed, rapid))
    else:
        stale = [glb_path, *(folder / name for name in _nc_names(stem, [s.id for s in parsed.setups]).values()),
                 folder / f"{stem}.nc"]
        for path in stale:
            path.unlink(missing_ok=True)
    sidecar = {
        "generator": "cadgen cam",
        "ok": ok,
        "document": _relative(document, sidecar_path.parent),
        "occurrence": part.ref,
        "job": parsed.source,
        "machine": machine,
        "post": poster.name,
        "material": dataclasses.asdict(parsed.material),
        "setups": setups,
        "tools": tools,
        "operations": operations,
        "summary": summary,
        "findings": findings,
        "checks": verdicts,
        "warnings": warnings,
        "files": {"nc": [nc_names[setup_id] for setup_id in programs], "glb": glb_path.name if ok else None},
    }
    write_bytes_atomic(sidecar_path, json.dumps(sidecar, indent=2, allow_nan=False).encode("utf-8"))
    timings["write_s"] = time.perf_counter() - clock
    timings = {key: _round(value) for key, value in timings.items()}
    for key, value in timings.items():
        logger.timing(key.removesuffix("_s"), value)
    written = [*(nc_names[setup_id] for setup_id in programs), *([glb_path.name] if ok else []), sidecar_path.name]
    logger.debug(f"wrote {', '.join(written)}")
    return CamResult(
        ok=ok, document=document, occurrence=part.ref,
        nc=tuple(folder / nc_names[setup_id] for setup_id in programs), glb=glb_path if ok else None,
        sidecar=sidecar_path, summary=summary, operations=tuple(operations), timings=timings,
        warnings=tuple(warnings), findings=tuple(findings),
    )


def _join(previous, toolpath, ctx):
    """``toolpath`` (run before an operation's own, with ``previous``'s tool, in the same setup) started where
    ``previous`` left the tool: straight up to the clearance height, across, and down to where it started."""
    from cadgen._internal.cam.toolpath import Move

    if previous.tool != toolpath.tool or previous.end == toolpath.start:
        return toolpath
    here, there = previous.end, toolpath.start
    top = max(here[2], there[2], ctx.heights.clearance)
    links = [(here[0], here[1], top), (there[0], there[1], top), there]
    moves, last = [], here
    for point in links:
        if tuple(point) != tuple(last):
            moves.append(Move("rapid", point, None, toolpath.op))
            last = point
    return dataclasses.replace(toolpath, start=here, moves=(*moves, *toolpath.moves))


def _refused(poster, exc: Exception) -> dict:
    """A post's refusal as the ``no_post_support`` error finding."""
    from cadgen._internal.cam.operations.base import finding

    text = str(exc).removeprefix(f"{poster.name} post: ")
    first, _, rest = text.partition(". ")
    description = (f"{rest.rstrip('.')}. No G-code was written" if rest else
                   "No G-code was written; use a post whose controller can run it, or change the operation")
    item = {"text": text, "post": poster.name}
    named = re.match(r"operations\[(\d+)\]", text)   # the post names the operation it refuses by its field path
    if named:
        item["operation"] = int(named.group(1))
    return finding("error", "no_post_support", f"The {poster.name} post cannot write this job: {first.rstrip('.')}",
                   description, [item])


# --- what the result says ------------------------------------------------------------------------------------

def _json_value(value):
    """Dataclasses and tuples as plain JSON."""
    def plain(item):
        if dataclasses.is_dataclass(item):
            return dataclasses.asdict(item)
        raise TypeError(f"internal: {type(item).__name__} is not plain JSON")

    return json.loads(json.dumps(value, allow_nan=False, default=plain))


def _machine_entry(job: "Job", rapid: float) -> dict:
    machine = job.machine
    return {"kind": machine.kind, "axes": machine.axes, "post": machine.post, "units": machine.units,
            "travel_mm": None if machine.travel_mm is None else list(machine.travel_mm),
            "max_rpm": machine.max_rpm, "max_feed_mm_min": machine.max_feed_mm_min,
            "rapid_mm_min": rapid, "rapid_assumed": machine.rapid_mm_min is None}


def _setup_entry(frame, nc_name: str | None) -> dict:
    from cadgen._internal.cam.outputs import gltf_quaternion

    setup = frame.setup
    fixture = frame.fixture
    return {
        "id": setup.id,
        "direction": setup.direction,
        "axis": list(frame.direction),
        "origin": setup.origin if isinstance(setup.origin, str) else list(setup.origin),
        # The WCS zero in the part's frame, mm, and the rows of the rotation (the setup's x, y, z in the part's frame):
        # p_setup = rotation @ (p_part - origin_mm).
        "origin_mm": list(frame.frame.origin),
        "rotation": [list(row) for row in frame.frame.rotation],
        # The same rotation, setup frame to part frame, as a glTF quaternion [x, y, z, w] in glTF axes.
        "quaternion": gltf_quaternion(frame.frame.rotation),
        "stock_bounds_mm": [list(corner) for corner in frame.stock_bounds],
        "clearance_z": frame.clearance_z,
        "retract_z": frame.retract_z,
        "fixture": {"type": fixture.type, "sizes": dict(sorted(fixture.sizes.items())), "assumed": list(fixture.assumed),
                    "notes": list(fixture.notes), "node": f"fixture:{setup.id}" if fixture.volumes else None},
        "nc": nc_name,
    }


def _operation_entry(planned_op: PlannedOp, starts: tuple[float, ...]) -> dict:
    from cadgen._internal.cam.operations import REGISTRY, get_operation
    from cadgen._internal.cam.toolpath import lengths

    ctx, planned = planned_op.ctx, planned_op.planned
    toolpaths = planned.toolpaths
    moved = [(toolpath, t0) for toolpath, t0 in zip(toolpaths, starts) if toolpath.moves]
    cut = sum(lengths(tp.moves, start=tp.start).cut_mm for tp in toolpaths)
    rapid_mm = sum(lengths(tp.moves, start=tp.start).rapid_mm for tp in toolpaths)
    start_s = moved[0][1] if moved else starts[0]
    end_s = moved[-1][0].moves[-1].t if moved else start_s
    operation = planned_op.operation
    return {
        "index": planned_op.index,
        "op": operation.op,
        "word": REGISTRY[operation.op].word,
        "setup": operation.setup,
        "tool": operation.tool,
        "tools": list(dict.fromkeys(tp.tool for tp in toolpaths)),
        "rpm": _round(ctx.feeds.rpm, 1),
        "feed_mm_min": _round(ctx.feeds.feed_mm_min, 1),
        "plunge_mm_min": _round(ctx.feeds.plunge_mm_min, 1),
        "coolant": operation.coolant,
        "start_s": _round(start_s),
        "time_s": _round(end_s - start_s),
        "cut_length_mm": _round(cut),
        "rapid_length_mm": _round(rapid_mm),
        "moves": sum(len(tp.moves) for tp in toolpaths),
        "node": f"path:{planned_op.index}:{operation.op}" if moved else None,
        "stats": _json_value(planned.stats),
        "lines": list(get_operation(operation.op).human_lines(planned.stats)),
    }


def _summary(job: "Job", operations: list[dict], toolpaths, rapid: float, post: str, programs: int) -> dict:
    ends = [tp.moves[-1].t for tp in toolpaths if tp.moves]
    running = [tp.tool for tp in toolpaths if tp.moves]
    changes = sum(1 for a, b in zip(running, running[1:]) if a != b)
    return {
        "cycle_time_s": _round(max(ends, default=0.0)),
        "cut_length_mm": _round(sum(op["cut_length_mm"] for op in operations)),
        "rapid_length_mm": _round(sum(op["rapid_length_mm"] for op in operations)),
        # Each time the running order switches tool, across setups too; the first tool's load is not one.
        "tool_changes": changes,
        "operations": len(operations),
        "setups": len(job.setups),
        "programs": programs,
        "post": post,
        "rapid_mm_min": rapid,
    }


def _tool_entries(job: "Job", planned_ops: list[PlannedOp]) -> list[dict]:
    from cadgen._internal.cam.tools import profile

    uses: dict[str, list[dict]] = {tool.id: [] for tool in job.tools}
    for planned_op in planned_ops:
        ctx = planned_op.ctx
        for toolpath in planned_op.planned.toolpaths:
            feeds = [m.feed for m in toolpath.moves if m.feed is not None and m.kind != "plunge"]
            main = toolpath.tool == ctx.tool.id
            uses[toolpath.tool].append({
                "operation": planned_op.index,
                "rpm": None if toolpath.rpm is None else _round(toolpath.rpm, 1),
                "feed_mm_min": _round(ctx.feeds.feed_mm_min, 1) if main else (_round(max(feeds), 1) if feeds else None),
                "plunge_mm_min": _round(ctx.feeds.plunge_mm_min, 1) if main else None,
            })
    out = []
    for tool in job.tools:
        entry = {key: value for key, value in _json_value(tool).items() if value is not None and value != {}}
        if uses[tool.id]:
            # Only a tool that runs is drawn: a job may list one whose type has no shape yet and use it nowhere.
            shape = profile(tool)
            entry.update({"length_mm": _round(shape.length_mm, 6), "stickout_mm": _round(shape.stickout_mm, 6)})
        entry.update({"uses": uses[tool.id], "node": f"tool:{tool.id}" if uses[tool.id] else None})
        out.append(entry)
    return out


# --- the GLB ---------------------------------------------------------------------------------------------------

def _glb_bytes(extras: dict, part, stock, frames: dict, planned_ops: list[PlannedOp], starts: dict, job: "Job",
               rapid: float) -> bytes:
    import numpy as np

    from cadgen._internal.cam.outputs import Glb, gltf_quaternion, lines_of, path_segments, tessellate
    from cadgen._internal.cam.tools import profile

    (x0, y0, z0), (x1, y1, z1) = stock.bounds
    deflection = max(0.01, 5e-4 * ((x1 - x0) ** 2 + (y1 - y0) ** 2 + (z1 - z0) ** 2) ** 0.5)
    glb = Glb()
    positions, triangles, faces = tessellate(part.shape, deflection_mm=deflection, faces=True)
    if glb.triangles("part", positions, triangles, material="part", extras=extras, attributes={"_FACE": faces}) != 0:
        raise ValueError(f"internal: {part.ref} has no faces to draw")
    positions, triangles, _ = tessellate(stock.shape, deflection_mm=deflection)
    glb.triangles("stock", positions, triangles, material="stock")
    for setup in job.setups:
        frame = frames[setup.id]
        pieces = [tessellate(frame.frame.shape_to_part(shape), deflection_mm=deflection)
                  for shape in frame.fixture.shapes()]
        if pieces:
            offsets = np.cumsum([0] + [len(p[0]) for p in pieces[:-1]])
            glb.triangles(f"fixture:{setup.id}", np.vstack([p[0] for p in pieces]),
                          np.vstack([p[1] + offset for p, offset in zip(pieces, offsets)]), material="fixture")
    first: dict[str, tuple] = {}
    for planned_op in planned_ops:
        for toolpath in planned_op.planned.toolpaths:
            if toolpath.moves and toolpath.tool not in first:
                first[toolpath.tool] = (planned_op.ctx.setup.frame, toolpath.start)
    for tool in job.tools:
        if tool.id not in first:
            continue
        frame, start = first[tool.id]
        positions, triangles = profile(tool).mesh()
        x, y, z = (float(value) for value in frame.point_to_part(start))
        tip = [round(value / 1000.0, 9) + 0.0 for value in (x, z, -y)]
        glb.triangles(f"tool:{tool.id}", positions, triangles, material="tool", translation=tip,
                      rotation=gltf_quaternion(frame.rotation))
    tool_index = {tool.id: index for index, tool in enumerate(job.tools)}   # extras.tools is the job's, in order
    for planned_op in planned_ops:
        segments, tools = [], []
        for toolpath, t0 in zip(planned_op.planned.toolpaths, starts[planned_op.index]):
            more = path_segments(toolpath, t0=t0, rapid_mm_min=rapid)
            segments += more
            tools += [tool_index[toolpath.tool]] * len(more)
        points, attributes = lines_of(segments, planned_op.index, planned_op.ctx.setup.frame.point_to_part, tools)
        glb.lines(f"path:{planned_op.index}:{planned_op.operation.op}", points, attributes, material="toolpath")
    return glb.bytes()
