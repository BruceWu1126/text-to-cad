"""What an operation plans with: one :class:`PlanContext` per operation, and what its ``plan`` returns.

``run.py`` builds a context for each operation of a job (:meth:`PlanContext.build`) and hands it to the operation's
``plan`` and ``checks``. It holds everything the operation reads, already in the setup's frame (mm, the tool along
+Z): the job and the operation, the placed setup, the part, the features the setup's tool reaches, the tool's shape,
the feeds, the travel heights, the stock, where the tool is before it starts, the tolerance and a logger.

``plan`` returns a :class:`Planned`: the toolpath, the numbers its ``human_lines`` and the checks read (``stats``),
and any findings it made on the way (the feeds' clamps first). An operation that cuts with a second tool too puts
that toolpath in ``before``.

Tests build a context straight from a STEP and a job document with :meth:`PlanContext.from_step`, without
``run.py``; they pass one ``shared`` dict per part so the part is opened, and each setup placed and recognised, once.

Stdlib only at import, like every module under ``operations/``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from cadgen._internal.cam.feeds import Feeds
    from cadgen._internal.cam.features import SetupFeature
    from cadgen._internal.cam.job import Job, Operation, Tool
    from cadgen._internal.cam.part import Part
    from cadgen._internal.cam.setups import SetupFrame
    from cadgen._internal.cam.stock import StockSolid
    from cadgen._internal.cam.toolpath import Heights, Toolpath
    from cadgen._internal.cam.tools import ToolProfile

__all__ = ["DEFAULT_TOL_MM", "PlanContext", "Planned"]

#: How closely a toolpath follows the geometry it was planned from, mm: arcs are fitted and curves sampled within it.
DEFAULT_TOL_MM = 0.01


def _quiet(message: str) -> None:
    """The logger a context has when nobody listens."""


@dataclass(frozen=True)
class Planned:
    """What an operation's ``plan`` returns: its toolpath, its numbers (for ``human_lines``, the checks and the
    sidecar; plain JSON values) and the findings it made (the feeds' clamps among them)."""

    toolpath: "Toolpath"
    stats: dict = field(default_factory=dict)
    findings: tuple[dict, ...] = ()
    #: Toolpaths the operation runs before :attr:`toolpath`, each with its own tool (an engraving's flat-bottom
    #: clearing with its ``clear_tool``); most operations have none. :attr:`toolpaths` lists them in running order.
    before: tuple["Toolpath", ...] = ()

    @property
    def toolpaths(self) -> tuple["Toolpath", ...]:
        """Every toolpath the operation runs, in order: :attr:`before`, then :attr:`toolpath`."""
        return (*self.before, self.toolpath)


@dataclass
class PlanContext:
    """Everything one operation plans with, in its setup's frame (mm, rpm, mm/min, s)."""

    job: "Job"
    #: The job's operation (``job.operations[index]``): its common keys resolved, its own keys in ``params``.
    operation: "Operation"
    #: Its index in the job: every move it makes carries it.
    index: int
    setup: "SetupFrame"
    part: "Part"
    #: The features the setup's tool reaches, in the setup's frame.
    features: tuple["SetupFeature", ...]
    tool: "Tool"
    profile: "ToolProfile"
    feeds: "Feeds"
    heights: "Heights"
    #: The stock the setup starts from, in the part's frame (``stock.shape``): the job's stock less what earlier
    #: setups faced off (``setups.setup_frames``); :attr:`stock_shape` has it in the setup's frame.
    stock: "StockSolid"
    #: Where the tool is when the operation starts (the previous toolpath's end, with the same tool); ``None`` after a
    #: tool change: the toolpath then starts at the clearance height over its first entry.
    start: tuple[float, float, float] | None = None
    #: The top of the material the operation starts from, z in the setup's frame: the stock top, or the floor of an
    #: earlier whole-stock facing in the same setup.
    top_z: float | None = None
    tol: float = DEFAULT_TOL_MM
    log: Callable[[str], None] = _quiet
    #: Geometry worth keeping between operations of one setup (the part in the setup's frame, its sections), keyed
    #: by name; one dict per setup, owned by whoever builds the contexts.
    cache: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        if self.top_z is None:
            self.top_z = self.setup.stock_top_z

    # --- naming ------------------------------------------------------------------------------------------

    @property
    def where(self) -> str:
        """``operations[2]``: the operation's field path, for errors."""
        return f"operations[{self.index}]"

    @property
    def label(self) -> str:
        """``operations[2] (pocket_2d)``: how a finding names the operation."""
        return f"{self.where} ({self.operation.op})"

    @property
    def params(self) -> Any:
        return self.operation.params

    @property
    def radius(self) -> float:
        """The tool's cutting radius, mm."""
        return self.profile.diameter_mm / 2

    # --- geometry in the setup's frame ---------------------------------------------------------------------

    def cached(self, key, make: Callable[[], Any]) -> Any:
        if key not in self.cache:
            self.cache[key] = make()
        return self.cache[key]

    @property
    def part_shape(self) -> Any:
        """The part as an OCP shape in the setup's frame."""
        return self.cached("part_shape", lambda: self.setup.frame.shape_to_setup(self.part.shape))

    @property
    def stock_shape(self) -> Any:
        """The stock as an OCP shape in the setup's frame."""
        return self.cached("stock_shape", lambda: self.setup.frame.shape_to_setup(self.stock.shape))

    @property
    def part_top_z(self) -> float:
        return self.setup.part_bounds[1][2]

    @property
    def part_bottom_z(self) -> float:
        return self.setup.part_bounds[0][2]

    def to_setup(self, point) -> tuple[float, float, float]:
        """A point from the part's frame to the setup's."""
        return tuple(float(value) for value in self.setup.frame.point_to_setup(point))

    def to_part(self, point) -> tuple[float, float, float]:
        return tuple(float(value) for value in self.setup.frame.point_to_part(point))

    def targets(self, kinds: tuple[str, ...]) -> list[tuple[str, "SetupFeature"]]:
        """The operation's ``features``, each resolved to the feature it names among those the setup's tool reaches:
        ``[(selector, feature)]`` in the job's order. A selector naming no such feature (another kind) is a
        ValueError naming its field; one this setup's tool cannot reach is a ``PlanError`` (``unreachable``)."""
        from cadgen._internal.cam.features import feature_for, recognize
        from cadgen._internal.cam.values import words

        reached = [found.feature for found in self.features]
        out = []
        for index, ref in enumerate(self.operation.features):
            at = f"{self.where}.features[{index}]"
            feature = feature_for(reached, _local(ref, self.part.ref), kinds)
            if feature is None:
                every = recognize(self.part)
                other = feature_for(every, _local(ref, self.part.ref))
                if other is None:
                    raise ValueError(f"{at}: {ref} is not part of a feature `cadgen cam features` lists")
                if other.kind not in kinds:
                    raise ValueError(f"{at}: {ref} is a {other.kind}, and {self.operation.op} cuts a {words(kinds)}")
                from cadgen._internal.cam.operations.base import PlanError

                raise PlanError("unreachable", f"{at}: {ref} ({other.kind}) is not reached from setup "
                                f"{self.setup.setup.id}'s tool direction {self.setup.setup.direction}", items=[ref])
            out.append((ref, next(found for found in self.features if found.feature is feature)))
        return out

    # --- building ----------------------------------------------------------------------------------------------

    @classmethod
    def build(cls, job: "Job", index: int, part: "Part", *, stock: "StockSolid", setup: "SetupFrame",
              features: tuple["SetupFeature", ...], start=None, tol: float = DEFAULT_TOL_MM,
              log: Callable[[str], None] | None = None, cache: dict | None = None) -> "PlanContext":
        """The context of ``job.operations[index]``, from what ``run.py`` has made once per job (the part, the stock)
        and once per setup (the placed setup, its features, its cache)."""
        from cadgen._internal.cam.feeds import feeds_for_operation
        from cadgen._internal.cam.toolpath import Heights
        from cadgen._internal.cam.tools import profile

        operation = job.operations[index]
        tool = job.tool(operation.tool)
        feeds = feeds_for_operation(operation, tool, job.material, job.machine,
                                    where=f"operations[{index}] ({operation.op})")
        return cls(job, operation, index, setup, part, tuple(features), tool, profile(tool), feeds,
                   Heights.from_frame(setup, operation), setup.stock or stock, start=None if start is None else tuple(start),
                   top_z=material_top(job, index, setup), tol=tol, log=log or _quiet,
                   cache={} if cache is None else cache)

    @classmethod
    def from_step(cls, step, job, index: int = 0, *, shared: dict | None = None, start=None,
                  tol: float = DEFAULT_TOL_MM, log: Callable[[str], None] | None = None) -> "PlanContext":
        """A context straight from a STEP and a job (a document or a parsed :class:`Job`), for tests and scripts.

        ``shared`` keeps the opened part, the stock, each placed setup, its features and its geometry cache between
        calls on the same STEP; pass the same dict to plan several operations of one part cheaply.
        """
        from cadgen._internal.cam.features import features_in_frame
        from cadgen._internal.cam.job import Job, parse_job
        from cadgen._internal.cam.part import open_part
        from cadgen._internal.cam.setups import setup_frames
        from cadgen._internal.cam.stock import make_stock

        shared = {} if shared is None else shared
        parsed = job if isinstance(job, Job) else parse_job(job)
        key = str(Path(step).resolve())
        if shared.get("step") != key:
            shared.clear()
            shared.update(step=key, part=open_part(Path(step)), setups={})
        part = shared["part"]
        stock_key = ("stock", repr(parsed.stock))
        if stock_key not in shared:
            shared[stock_key] = make_stock(parsed.stock, part.shape)
        stock = shared[stock_key]
        operation = parsed.operations[index]
        frames_key = ("frames", repr(parsed.setups), repr(parsed.operations), stock_key)
        if frames_key not in shared:
            shared[frames_key] = setup_frames(parsed, part.shape, stock, face_normal=lambda ref: _face_normal(part, ref))
        frame = shared[frames_key][operation.setup]
        # A setup placed the same way (its own keys, and the stock it starts from) recognises the same features.
        setup_key = (repr(parsed.setup(operation.setup)), stock_key, frame.stock.bounds)
        if setup_key not in shared["setups"]:
            shared["setups"][setup_key] = (features_in_frame(part, frame), {})
        features, cache = shared["setups"][setup_key]
        return cls.build(parsed, index, part, stock=stock, setup=frame, features=features, start=start, tol=tol,
                         log=log, cache=cache)


def _local(ref: str, occurrence: str) -> str:
    """A selector as the part's features name it: ``#o1.f12`` for ``f12`` or ``#o1.f12``."""
    ref = ref.strip()
    return ref if ref.startswith("#") else f"{occurrence}.{ref}"


def _face_normal(part: "Part", ref: str):
    ordinal = int(ref.rsplit(".f", 1)[1])
    return part.face(ordinal).normal


def material_top(job: "Job", index: int, setup: "SetupFrame") -> float:
    """The top of the material ``job.operations[index]`` starts from, z in the setup's frame: the lowest floor of an
    earlier facing in the same setup that covered the stock's whole footprint, else the stock top (of the stock the
    setup starts from: an earlier setup's facing has already taken it down, ``setups.setup_frames``). A whole-stock
    facing covers it level by level; a region facing only when its region holds the stock's footprint (a box or
    sheet seen square, whose footprint is its bounds), since the tool's centre stays inside the region. Other
    operations only cut where they cut, so nothing else lowers it (there is no stock simulation before phase 2)."""
    top = setup.stock_top_z
    for operation in job.operations[:index]:
        if operation.op != "facing" or operation.setup != setup.setup.id:
            continue
        if operation.region is not None and not _region_covers(operation.region, job, setup):
            continue
        from cadgen._internal.cam.operations.milling_2d import facing_floor

        top = min(top, facing_floor(operation.params, setup))
    return top


def _region_covers(region, job: "Job", setup: "SetupFrame") -> bool:
    """A facing region holds the stock's whole footprint."""
    from shapely.geometry import Polygon, box

    rotation = setup.frame.rotation
    square = all(sorted(abs(round(c, 9)) for c in row) == [0.0, 0.0, 1.0] for row in rotation)
    if job.stock.type not in ("box", "sheet") or not square:
        return False
    (x0, y0, _), (x1, y1, _) = setup.stock_bounds
    poly = Polygon(region)
    return poly.is_valid and poly.buffer(1e-6).contains(box(x0, y0, x1, y1))
