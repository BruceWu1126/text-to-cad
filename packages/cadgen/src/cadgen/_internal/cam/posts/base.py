"""What every post shares: one program per setup, written from the job and its ordered toolpaths.

A post is a dialect: a class stating its controller's capabilities and the few blocks that differ between controllers
(the program's wrapper, its safe start, how it goes home, how it changes a tool, how it dwells). :class:`PostBase`
writes everything else the same way for all of them:

- the header comment block (part, job, setup, WCS, units, dwell units, tools with rpm and feed, and the prove-out
  line), then units (G21 / G20), the safe start (G90, G17, G94, ...) and the WCS (G54);
- per toolpath: a tool change when the tool changes, the spindle (``S`` as a whole number, ``M3``), the coolant;
- per move: ``G0`` / ``G1``, ``G2`` / ``G3`` with ``I`` / ``J`` from the start (helical when Z changes), ``G4`` for a
  dwell, and canned cycles as cycles where the controller has them, else as the moves :func:`expand_cycle` gives;
- the footer: ``G80`` if a cycle is active, ``M5``, ``M9``, the retract, the program end.

Numbers are written the same way every time: rounded to the unit's resolution (0.001 mm, 0.0001 in), trailing zeros
trimmed, never ``-0``; a dialect that reads ``X10`` as 10 micrometres (Fanuc, Haas) keeps the decimal point
(``X10.``). A depth never comes out deeper than planned: a cut's Z rounds up (toward safe) and a canned cycle's
depth toward its R plane; everything else rounds to nearest. An inch program converts every coordinate, centre
offset, feed and cycle depth; the engine stays in mm.

Every ``M3`` is followed by ``G4`` when there is a spin-up wait (``machine.spindle_spinup_s``, else the post's
default: grbl does not wait for its spindle, the others do). A post that tells the operator where to zero or lifts
on its own (grbl, Mach3) goes up to the program's safe height before the spindle starts. A program that bores with
an oriented bar says in its header which way the bar shifts and so which way its tip must point.

A post refuses (ValueError naming the post and the operation) what its controller cannot run safely, never writing a
plausible program instead (README law 10): a tap on a controller with no tapping (an expanded tap, with the spindle
not synchronised to the feed, breaks the tap), an oriented boring cycle on one that cannot orient its spindle, a
helix on one without helical arcs, toolpaths that do not join.

Stdlib only.
"""

from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass
from typing import ClassVar, Sequence

from cadgen._internal.cam.toolpath import Move, Toolpath, cycle_return, expand_cycle, walk

__all__ = ["CYCLE_CODES", "MM_PER_INCH", "PROVE_LINE", "PostBase", "Route", "fmt", "safe_comment"]

MM_PER_INCH = 25.4
PROVE_LINE = "Prove this program on the machine: single block, feed override low, in air first."

#: The G-code each canned cycle kind is posted as (a ream with a dwell is G89).
CYCLE_CODES = {"drill": "G81", "spot": "G82", "peck": "G83", "chip_break": "G73", "tap": "G84", "rigid_tap": "G84",
               "ream": "G85", "bore": "G86", "fine_bore": "G76", "back_bore": "G87"}
_TAPS = ("tap", "rigid_tap")
_ORIENTED = ("fine_bore", "back_bore")
_COMMENT_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,:/+-_=*#'")
_EPS = 1e-6


def fmt(value: float, decimals: int, *, point: bool = False, toward: int = 0) -> str:
    """``value`` rounded to ``decimals``, trailing zeros trimmed, never ``-0``; ``point`` keeps a bare ``.``.
    ``toward`` +1 rounds up and -1 down (a depth that must never come out deeper than planned), 0 to nearest."""
    if toward:
        scaled = float(value) * 10 ** decimals
        step = math.ceil(scaled - 1e-6) if toward > 0 else math.floor(scaled + 1e-6)
        rounded = round(step / 10 ** decimals, decimals)
    else:
        rounded = round(float(value), decimals)
    if rounded == 0:
        rounded = 0.0
    text = f"{rounded:.{decimals}f}"
    if "." in text:
        text = text.rstrip("0")
        if text.endswith(".") and not point:
            text = text[:-1]
    elif point:
        text += "."
    return text


def safe_comment(text: str, *, upper: bool = False) -> str:
    """Text a comment can hold on every controller: printable ASCII with no parentheses, ``;`` or ``%`` (which ends
    a Fanuc tape), runs of space collapsed."""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii")
    text = " ".join("".join(char if char in _COMMENT_CHARS else " " for char in text).split())
    return text.upper() if upper else text


def _shift(cycle) -> tuple[float, float]:
    """The unit direction an oriented boring bar moves off the wall: the cycle's ``shift_xy``, or +X."""
    shift = getattr(cycle, "shift_xy", None) or (1.0, 0.0)
    length = math.hypot(*shift)
    return (shift[0] / length, shift[1] / length)


def _expand(move: Move, start) -> tuple[Move, ...]:
    if getattr(move.cycle, "shift_xy", None) is not None:
        return expand_cycle(move, start, shift_direction=_shift(move.cycle))
    return expand_cycle(move, start)


def _direction(shift: tuple[float, float]) -> str:
    for name, axis in (("+X", (1, 0)), ("-X", (-1, 0)), ("+Y", (0, 1)), ("-Y", (0, -1))):
        if math.hypot(shift[0] - axis[0], shift[1] - axis[1]) < 1e-9:
            return name
    return f"X{fmt(shift[0], 4)} Y{fmt(shift[1], 4)}"


@dataclass(frozen=True)
class Route:
    """How a post writes one cycle: ``native`` (its canned cycle), ``expand`` (plain moves), or ``special`` (the
    dialect's own block, such as LinuxCNC's G33.1)."""

    how: str


class PostBase:
    """A controller's dialect. Subclasses set the capabilities and override the dialect's blocks."""

    name: ClassVar[str]
    #: The controller, as the header and the refusals name it.
    controller: ClassVar[str]
    arcs: ClassVar[bool] = True
    helical: ClassVar[bool] = True
    tcp: ClassVar[bool] = False
    #: Cycle kinds written as the controller's canned cycle; the rest are expanded (or refused).
    cycles: ClassVar[frozenset[str]] = frozenset()
    #: Of those, the kinds whose canned cycle takes a dwell (P); one with a dwell and no P is expanded.
    cycle_dwells: ClassVar[frozenset[str]] = frozenset()
    #: The spindle can be stopped and oriented (M19), so an oriented boring cycle can be expanded.
    orient: ClassVar[bool] = False
    #: "every" (N on every block), "tool_change" (N1, N2 ... on each tool change) or None.
    line_numbers: ClassVar[str | None] = None
    #: "m6" (a tool changer, or the controller's own manual change through M6) or "pause" (M0 and a comment).
    tool_change: ClassVar[str] = "m6"
    #: The longest line the controller (or the way it is fed) takes; None for no limit that matters.
    max_line_length: ClassVar[int | None] = None
    #: Numbers keep their decimal point (``X10.``): the dialect reads a bare integer in least increments.
    decimal_point: ClassVar[bool] = False
    #: Comments upper case only.
    upper_comments: ClassVar[bool] = False
    #: ``%`` before and after the program and an O-number first.
    tape: ClassVar[bool] = False
    #: The O-number's digits, when ``tape``.
    program_digits: ClassVar[int] = 4
    #: The first setup's program number; the next setup's is one more.
    program_number: ClassVar[int] = 1000
    #: "ms": P is whole milliseconds; "s": seconds; "s.": seconds with a decimal point.
    dwell_unit: ClassVar[str] = "s"
    #: How the tool reaches a toolpath's start after a tool change: "home" (XY at the machine's Z home, then G43 Z)
    #: or "safe" (Z up to the program's safe height, XY, then down).
    positioning: ClassVar[str] = "home"
    #: The safe start block after the units.
    safe_start: ClassVar[str] = "G90 G94 G17"
    #: The program end code.
    end_code: ClassVar[str] = "M30"
    #: The coolant codes: on, by mode; "air" missing means the controller has no standard air code.
    coolant_codes: ClassVar[dict] = {"flood": "M8", "mist": "M7"}
    #: Codes that turn a coolant off, by mode (M9 for the rest).
    coolant_off_codes: ClassVar[dict] = {}
    #: Seconds to wait after M3 before cutting (G4) when the job does not say (``machine.spindle_spinup_s``): 0 for
    #: a controller that waits for the spindle itself.
    spinup_s: ClassVar[float] = 0.0
    #: Lines after the retract and before the program end (cancel the tool length offset, G49).
    end_lines: ClassVar[tuple[str, ...]] = ()

    # --- what a dialect writes ------------------------------------------------------------------------------

    def home_lines(self) -> list[str]:
        """Move Z clear for a tool change and at the end ("home" posts only)."""
        return []

    def dwell_words(self, seconds: float) -> str:
        if self.dwell_unit == "ms":
            return f"P{int(round(seconds * 1000))}"
        return "P" + fmt(seconds, 3, point=self.dwell_unit == "s.")

    def route(self, move: Move, where: str) -> Route:
        """How this post writes the cycle ``move``; ValueError when the controller cannot run it safely."""
        cycle = move.cycle
        native = cycle.kind in self.cycles and (not cycle.dwell_s or cycle.kind in self.cycle_dwells)
        if cycle.kind in _TAPS:
            if cycle.kind not in self.cycles:
                what = "spindle-synchronised (rigid) tapping" if cycle.kind == "rigid_tap" else "tapping cycle"
                raise ValueError(
                    f"{self.name} post: {where} has a {cycle.kind} cycle, and {self.controller} has no {what}. "
                    "Expanded into plain moves, the feed would not follow the spindle and the tap would break; "
                    "use a post whose controller taps, or thread mill the hole")
            if not native:
                raise ValueError(f"{self.name} post: {where} has a {cycle.kind} cycle with a dwell, and "
                                 f"{self.controller}'s tapping cycle takes none; leave the dwell out")
            return Route("native")
        if native:
            return Route("native")
        if cycle.kind in _ORIENTED and not self.orient:
            raise ValueError(
                f"{self.name} post: {where} has a {cycle.kind} cycle, and {self.controller} can neither run it as a "
                "canned cycle nor stop and orient its spindle (M19), so the boring bar would drag across the wall "
                "when it shifts; bore with a plain bore cycle or use another post")
        return Route("expand")

    def special_cycle(self, prog: "_Program", move: Move, start) -> None:
        raise NotImplementedError

    def native_cycle_words(self, prog: "_Program", move: Move) -> list[str]:
        """The canned cycle block's words after G98/G99 and the code, before F."""
        cycle = move.cycle
        # the depth rounds toward R, never deeper than planned
        toward = -1 if cycle.kind == "back_bore" else 1
        words = [f"X{prog.coord(move.xyz[0])}", f"Y{prog.coord(move.xyz[1])}", f"Z{prog.coord(cycle.z, toward=toward)}",
                 f"R{prog.coord(cycle.r)}"]
        if cycle.peck_mm is not None:
            words.append(f"Q{prog.coord(cycle.peck_mm)}")
        if cycle.shift_mm is not None:
            words.extend(self.shift_words(prog, cycle))
        if cycle.kind in self.cycle_dwells and (cycle.dwell_s or cycle.kind in self.always_dwell):
            words.append(self.dwell_words(cycle.dwell_s))
        return words

    #: Kinds whose canned cycle always carries P, even P0.
    always_dwell: ClassVar[frozenset[str]] = frozenset({"spot"})

    def shift_words(self, prog: "_Program", cycle) -> list[str]:
        return [f"Q{prog.coord(cycle.shift_mm)}"]

    def before_cycle(self, prog: "_Program", move: Move) -> None:
        """Blocks a cycle group needs before its first block (Fanuc's M29 for a rigid tap)."""

    def after_cycle(self, prog: "_Program") -> None:
        """After the G80 that ends a group."""

    def stop_orient(self) -> list[str]:
        return ["M5", "M19"]

    def extra_header(self) -> list[str]:
        return []

    def safe_start_line(self, inch: bool) -> str:
        return self.safe_start

    def check_line(self, text: str) -> None:
        """ValueError when a line is one the controller cannot take."""
        if self.max_line_length is not None and len(text) > self.max_line_length:
            raise ValueError(f"{self.name} post: the line {text!r} is longer than {self.controller}'s "
                             f"{self.max_line_length} characters")

    # --- the program ---------------------------------------------------------------------------------------

    def programs(self, job, setups, toolpaths: Sequence[Toolpath], *, part: str = "", job_name: str = "") -> dict[str, str]:
        """One program per setup that has toolpaths, by setup id in the setups' order."""
        setups = tuple(setups)
        ids = [setup.id for setup in setups]
        by_setup: dict[str, list[Toolpath]] = {setup_id: [] for setup_id in ids}
        for toolpath in toolpaths:
            setup_id = job.operations[toolpath.op].setup
            if setup_id not in by_setup:
                raise ValueError(f"{self.name} post: operations[{toolpath.op}] runs in setup {setup_id!r}, "
                                 f"which is not among the setups to post ({', '.join(ids)})")
            by_setup[setup_id].append(toolpath)
        return {setup.id: self.program(job, setup, by_setup[setup.id], part=part, job_name=job_name,
                                       number=self.program_number + index)
                for index, setup in enumerate(setups) if by_setup[setup.id]}

    def program(self, job, setup, toolpaths: Sequence[Toolpath], *, part: str = "", job_name: str = "",
                number: int | None = None) -> str:
        """The program text for one setup's toolpaths, in order. Same input, same bytes."""
        toolpaths = tuple(toolpaths)
        if not toolpaths:
            raise ValueError(f"{self.name} post: setup {setup.id!r} has no toolpaths to post")
        prog = _Program(self, job.machine.units == "inch")
        spinup = getattr(job.machine, "spindle_spinup_s", None)
        prog.spinup = self.spinup_s if spinup is None else spinup
        routes = self._check(job, toolpaths)
        prog.safe_z = max(max(_heights(tp)) for tp in toolpaths)
        number = self.program_number if number is None else number

        if self.tape:
            prog.raw("%")
            prog.raw(f"O{number:0{self.program_digits}d} ({safe_comment(part or 'program', upper=True)[:24]})")
        self._header(prog, job, setup, toolpaths, part, job_name)
        prog.code("G20" if prog.inch else "G21")
        prog.code(self.safe_start_line(prog.inch))
        prog.code("G54")
        for toolpath in toolpaths:
            self._toolpath(prog, job, toolpath, routes)
        prog.cancel_cycle()
        prog.code("M5")
        prog.set_coolant("off")
        if prog.lines[-1] != ("code", "M9"):
            prog.code("M9")
        self._retract(prog)
        for line in self.end_lines:
            prog.code(line)
        prog.code(self.end_code)
        if self.tape:
            prog.raw("%")
        return prog.text()

    def _check(self, job, toolpaths) -> dict:
        routes = {}
        for toolpath in toolpaths:
            operation = job.operations[toolpath.op]
            where = f"operations[{toolpath.op}] ({operation.op})"
            if toolpath.rpm is None:
                raise ValueError(f"{self.name} post: {where} has no spindle speed, and a mill's tool must turn")
            for start, move in walk(toolpath.moves, toolpath.start):
                if move.kind in ("arc_cw", "arc_ccw"):
                    if not self.arcs:
                        raise ValueError(f"{self.name} post: {where} has arcs, and {self.controller} has none")
                    if abs(move.xyz[2] - start[2]) > _EPS and not self.helical:
                        raise ValueError(f"{self.name} post: {where} has a helix, and {self.controller} has no "
                                         "helical arcs")
                elif move.kind == "cycle":
                    routes[id(move)] = self.route(move, where)
        return routes

    def _header(self, prog, job, setup, toolpaths, part, job_name) -> None:
        unit = "in" if prog.inch else "mm"
        origin = setup.origin
        if not isinstance(origin, str):
            origin = "X{} Y{} Z{} mm in the part's frame".format(*(fmt(value, 3) for value in origin))
        dwell = {"ms": "milliseconds, whole", "s": "seconds", "s.": "seconds, with a decimal point"}[self.dwell_unit]
        prog.comment(f"Part: {part or 'unnamed'}")
        prog.comment(f"Job: {job_name or 'inline'}")
        prog.comment(f"Setup: {setup.id}, tool axis {setup.direction}")
        prog.comment(f"WCS: G54 at {origin}")
        prog.comment(f"Post: {self.name}, for {self.controller}, units {unit}, dwell P in {dwell}")
        for line in self.extra_header():
            prog.comment(line)
        if prog.spinup:
            prog.comment(f"Spindle spin-up: G4 P {fmt(prog.spinup, 3)} s after every M3")
        shifts = []
        for toolpath in toolpaths:
            for move in toolpath.moves:
                if move.kind == "cycle" and move.cycle.kind in _ORIENTED:
                    direction = _direction(_shift(move.cycle))
                    if direction not in shifts:
                        shifts.append(direction)
        for direction in shifts:
            prog.comment(f"Boring bar: the bar shifts {direction} off the wall, so orient it with its tip pointing "
                         f"opposite, {_opposite(direction)}, at the spindle orient position")
        tools: dict[str, list[str]] = {}
        for toolpath in toolpaths:
            feeds = [move.feed for move in toolpath.moves if move.feed is not None]
            rate = f"{int(round(toolpath.rpm))} rpm"
            if feeds:
                rate += f", feed {prog.feed_text(max(feeds))} {unit}/min"
            entry = tools.setdefault(toolpath.tool, [])
            if rate not in entry:
                entry.append(rate)
        for tool_id, rates in tools.items():
            tool = job.tool(tool_id)
            size = f"D{prog.coord(tool.diameter_mm)} {unit} " if tool.diameter_mm is not None else ""
            flutes = f" {tool.flutes} flutes" if tool.flutes else ""
            note = f" {tool.comment}" if tool.comment else ""
            prog.comment(f"{tool.id}: {size}{tool.type}{flutes}{note}: {'; '.join(rates)}")
        prog.comment(PROVE_LINE)

    def _retract(self, prog) -> None:
        if self.positioning == "home":
            for line in self.home_lines():
                prog.code(line)
            prog.forget(z=True)
        else:
            prog.motion("G0", (None, None, prog.safe_z))

    def _toolpath(self, prog, job, toolpath: Toolpath, routes) -> None:
        operation = job.operations[toolpath.op]
        tool = job.tool(toolpath.tool)
        where = f"operations[{toolpath.op}] ({operation.op})"
        prog.cancel_cycle()
        prog.comment(f"Op {toolpath.op}: {operation.op} with {tool.id}")
        if prog.tool != tool.number:
            self._change_tool(prog, tool, toolpath)
        else:
            here = prog.at
            if any(value is None for value in here) or math.dist(here, toolpath.start) > 1e-3:
                raise ValueError(f"{self.name} post: {where} starts at {_point(toolpath.start)}, but the tool is at "
                                 f"{_point(here)}: toolpaths with one tool must join")
            prog.spindle(toolpath.rpm)
        prog.set_coolant(toolpath.coolant)
        for start, move in walk(toolpath.moves, toolpath.start):
            if move.kind == "cycle":
                how = routes[id(move)].how
                if how == "native":
                    prog.native_cycle(move, start)
                elif how == "special":
                    prog.cancel_cycle()
                    self.special_cycle(prog, move, start)
                else:
                    prog.cancel_cycle()
                    prog.expanded_cycle(move, start)
            else:
                prog.cancel_cycle()
                prog.move(move, start)

    def _change_tool(self, prog, tool, toolpath: Toolpath) -> None:
        first = prog.tool is None
        if not first:
            prog.code("M5")
            prog.rpm = prog.speed = None
            if prog.coolant != "off":
                prog.set_coolant("off")
        x, y, z = toolpath.start
        label = f"{tool.id}" + (f", D{prog.coord(tool.diameter_mm)} {'in' if prog.inch else 'mm'}"
                                if tool.diameter_mm is not None else "") + f" {tool.type}"
        if self.tool_change == "pause":
            if first:
                prog.comment(f"Load {label} and zero Z on it before starting")
            else:
                prog.motion("G0", (None, None, prog.safe_z))
                prog.comment(f"Tool change: load {label}, zero Z on it, then resume")
                prog.code("M0")
            prog.forget(x=True, y=True, z=True)
            prog.tool = tool.number
            prog.motion("G0", (None, None, prog.safe_z))
            prog.spindle(toolpath.rpm)
            prog.motion("G0", (x, y, None))
            prog.motion("G0", (None, None, z))
            return
        if self.positioning == "home":
            for line in self.home_lines():
                prog.code(line)
        elif not first:
            prog.motion("G0", (None, None, prog.safe_z))
        prog.code(f"T{tool.number} M6", tool_change=True)
        prog.tool = tool.number
        prog.forget(x=True, y=True, z=True)
        if self.positioning == "home":
            prog.spindle(toolpath.rpm)
            prog.motion("G0", (x, y, None))
            prog.motion("G43", (None, None, z), extra=[f"H{tool.number}"], force_z=True)
        else:
            prog.code(f"G43 H{tool.number}")
            prog.motion("G0", (None, None, prog.safe_z))
            prog.spindle(toolpath.rpm)
            prog.motion("G0", (x, y, None))
            prog.motion("G0", (None, None, z))


def _opposite(direction: str) -> str:
    if direction[0] in "+-" and direction[1:] in ("X", "Y"):
        return ("-" if direction[0] == "+" else "+") + direction[1:]
    x, y = (float(word[1:]) for word in direction.split())
    return f"X{fmt(-x, 4)} Y{fmt(-y, 4)}"


def _point(point) -> str:
    return "(" + ", ".join("?" if value is None else fmt(value, 3) for value in point) + ")"


def _heights(toolpath: Toolpath) -> list[float]:
    heights = [toolpath.start[2]]
    for move in toolpath.moves:
        heights.append(move.xyz[2])
        if move.kind == "cycle":
            heights.extend((move.cycle.r, move.cycle.z))
    return heights


class _Program:
    """One program being written: its lines and what the controller's modal state is."""

    def __init__(self, post: PostBase, inch: bool):
        self.post = post
        self.inch = inch
        self.lines: list[tuple[str, str]] = []      # (kind, text): code | comment | raw | tool_change
        self.written: list[str | None] = [None, None, None]
        self.at: list[float | None] = [None, None, None]
        self.feed: str | None = None
        self.cycle_key = None
        self.cycle_kind: str | None = None
        #: What the spindle was last commanded to (None: not turning, or not known to be) and what the toolpath
        #: wants it to turn at.
        self.rpm: int | None = None
        self.speed: int | None = None
        self.spinup = 0.0
        self.coolant = "off"
        self.tool: int | None = None
        self.safe_z = 0.0

    # numbers
    def coord(self, mm: float, *, toward: int = 0) -> str:
        return fmt(mm / MM_PER_INCH if self.inch else mm, 4 if self.inch else 3, point=self.post.decimal_point,
                   toward=toward)

    def pitch(self, mm: float) -> str:
        """A thread pitch: finer than a coordinate, since its error adds up every turn (1 mm is 0.03937 in)."""
        return fmt(mm / MM_PER_INCH if self.inch else mm, 6 if self.inch else 4, point=self.post.decimal_point)

    def feed_text(self, mm_min: float, *, exact: bool = False) -> str:
        decimals = (4 if self.inch else 3) if exact else (2 if self.inch else 1)
        return fmt(mm_min / MM_PER_INCH if self.inch else mm_min, decimals, point=self.post.decimal_point)

    # lines
    def code(self, text: str, *, tool_change: bool = False) -> None:
        self.lines.append(("tool_change" if tool_change else "code", text))

    def raw(self, text: str) -> None:
        self.lines.append(("raw", text))

    def comment(self, text: str) -> None:
        text = safe_comment(text, upper=self.post.upper_comments)
        limit = self.post.max_line_length
        if limit is None or len(text) + 2 <= limit:
            self.lines.append(("comment", f"({text})"))
            return
        line = ""
        for word in text.split(" "):
            if line and len(line) + 1 + len(word) + 2 > limit:
                self.lines.append(("comment", f"({line})"))
                line = word
            else:
                line = f"{line} {word}" if line else word
        self.lines.append(("comment", f"({line})"))

    def forget(self, *, x: bool = False, y: bool = False, z: bool = False) -> None:
        for axis, gone in enumerate((x, y, z)):
            if gone:
                self.written[axis] = None
                self.at[axis] = None

    # modal state
    def spindle(self, rpm: float) -> None:
        whole = int(round(rpm))
        if whole <= 0:
            raise ValueError(f"{self.post.name} post: a spindle speed of {rpm:g} rpm rounds to {whole}")
        self.speed = whole
        self.ensure_spindle()

    def ensure_spindle(self) -> None:
        """The spindle turning forward at the toolpath's speed (again, after a rigid tap's G80 on Fanuc)."""
        if self.speed is not None and self.rpm != self.speed:
            self.cancel_cycle()
            self.start_spindle()
            self.rpm = self.speed

    def start_spindle(self) -> None:
        """``S M3``, then the spin-up wait when there is one."""
        self.code(f"S{self.speed} M3")
        if self.spinup > 0:
            self.code("G4 " + self.post.dwell_words(self.spinup))

    def set_coolant(self, mode: str) -> None:
        if mode == self.coolant:
            return
        self.cancel_cycle()
        old = self.coolant
        if old != "off":
            off = self.post.coolant_off_codes.get(old, "M9")
            if old in self.post.coolant_codes:
                self.code(off)
            else:
                self.comment(f"Coolant {old} off: this controller has no standard code for it, "
                             "so switch it off by hand")
        if mode != "off":
            if mode in self.post.coolant_codes:
                self.code(self.post.coolant_codes[mode])
            else:
                self.comment(f"Coolant {mode}: this controller has no standard code for it, "
                             "so switch it on by hand")
        self.coolant = mode

    def cancel_cycle(self) -> None:
        if self.cycle_key is not None:
            self.code("G80")
            self.cycle_key = None
            self.post.after_cycle(self)
            self.cycle_kind = None

    # motion
    def motion(self, g: str, xyz, *, feed: float | None = None, extra=(), force_z: bool = False,
               force_xy: bool = False, exact_feed: bool = False, z_toward: int | None = None) -> None:
        """A move; on a cutting move (with a feed) Z rounds up, toward safe, so it never cuts deeper than planned
        (``z_toward`` says otherwise: a back bore cuts upward, so its top rounds down)."""
        if z_toward is None:
            z_toward = 1 if feed is not None else 0
        words = [g]
        for axis, (letter, value) in enumerate(zip("XYZ", xyz)):
            if value is None:
                continue
            text = self.coord(value, toward=z_toward if axis == 2 else 0)
            if text != self.written[axis] or (force_z and axis == 2) or (force_xy and axis < 2):
                words.append(letter + text)
                self.written[axis] = text
            self.at[axis] = value
        words.extend(extra)
        if len(words) == 1:
            return
        if feed is not None:
            text = self.feed_text(feed, exact=exact_feed)
            if text != self.feed:
                words.append("F" + text)
                self.feed = text
        self.code(" ".join(words))

    def dwell(self, seconds: float) -> None:
        if seconds > 0:
            self.code("G4 " + self.post.dwell_words(seconds))

    def move(self, move: Move, start, *, z_toward: int | None = None) -> None:
        if move.kind != "rapid":
            self.ensure_spindle()
        if move.kind == "rapid":
            self.motion("G0", move.xyz)
        elif move.kind in ("arc_cw", "arc_ccw"):
            cx, cy = move.center
            extra = [f"I{self.coord(cx - start[0])}", f"J{self.coord(cy - start[1])}"]
            self.motion("G2" if move.kind == "arc_cw" else "G3", move.xyz, feed=move.feed, extra=extra, force_xy=True)
        else:
            self.motion("G1", move.xyz, feed=move.feed, z_toward=z_toward)
        self.dwell(move.dwell_s)

    def native_cycle(self, move: Move, start) -> None:
        if any(value is None for value in self.at):
            raise ValueError(f"{self.post.name} post: a canned cycle must start from a known position")
        self.ensure_spindle()
        cycle = move.cycle
        g99 = cycle_return(move, start) == "r"
        code = "G89" if cycle.kind == "ream" and cycle.dwell_s else CYCLE_CODES[cycle.kind]
        key = (cycle, move.feed, g99)
        tap = cycle.kind in _TAPS
        # a tap's feed is exactly rpm x pitch at the spindle speed written
        feed = self.speed * cycle.pitch_mm if tap else move.feed
        if key == self.cycle_key:
            self.code(f"X{self.coord(move.xyz[0])} Y{self.coord(move.xyz[1])}")
        else:
            self.cancel_cycle()
            self.post.before_cycle(self, move)
            words = ["G99" if g99 else "G98", code, *self.post.native_cycle_words(self, move)]
            self.feed = self.feed_text(feed, exact=tap)
            words.append("F" + self.feed)
            self.code(" ".join(words))
            self.cycle_key = key
            self.cycle_kind = cycle.kind
        for axis, value in enumerate(move.xyz):
            self.at[axis] = value
            self.written[axis] = self.coord(value)

    def expanded_cycle(self, move: Move, start) -> None:
        self.ensure_spindle()
        cycle = move.cycle
        x, y = move.xyz[0], move.xyz[1]
        running = True
        for part_start, part in walk(_expand(move, start), start):
            if cycle.kind in _ORIENTED:
                off_centre = math.hypot(part.xyz[0] - x, part.xyz[1] - y) > _EPS
                if off_centre and running:
                    for line in self.post.stop_orient():
                        self.code(line)
                    running = False
                if part.kind in ("plunge", "feed") and not running:
                    self.start_spindle()
                    running = True
            upward = cycle.kind == "back_bore" and part.kind == "feed"
            self.move(part, part_start, z_toward=-1 if upward else None)
            if cycle.kind == "bore" and part.kind == "plunge":
                self.code("M5")
                running = False
        if not running:
            self.start_spindle()

    # out
    def text(self) -> str:
        post = self.post
        out: list[str] = []
        sequence, changes = 10, 0
        for kind, text in self.lines:
            if kind in ("code", "tool_change"):
                if post.line_numbers == "every":
                    text = f"N{sequence} {text}"
                    sequence = sequence + 10 if sequence < 99990 else 10
                elif post.line_numbers == "tool_change" and kind == "tool_change":
                    changes += 1
                    text = f"N{changes} {text}"
                post.check_line(text)
            out.append(text)
        return "\n".join(out) + "\n"

