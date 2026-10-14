"""Fanuc (0i / 30i series machining centres), in the conservative ISO form.

- ``%`` wrappers, an ``O`` number of four digits with the part in a comment, and ``N`` on every block (from N10 in
  tens, back to N10 after N99990: Fanuc takes five digits).
- Every number keeps its decimal point (``X10.``): without one Fanuc reads least increments (``X10`` is 0.01 mm).
  Comments are upper case.
- Tool change: ``G91 G28 Z0.`` and ``G90`` to go home, ``T<n> M6``, then ``G43 Z H<n>`` on the way down.
- Canned cycles G81, G82, G83, G73, G84, G85, G89, G86, G76 and G87 with G98 / G99. A rigid tap is ``M29 S<rpm>``
  before G84, with ``F = S x pitch``; a floating tap is G84 alone. Dwell P is whole milliseconds (``G4 P500``); G81,
  G83, G73 and G86 take no P here, so one with a dwell is expanded. G76 and G87 shift by Q along the direction the
  machine's parameters set (RD1 / RD2), assumed +X; a cycle shifting another way is expanded with ``M19`` (the usual
  spindle orient code).
- Peck clearance (G83) and chip-break back-off (G73) are the machine's parameters 5115 and 5114.
- The end cancels the tool length offset (``G49``) after going home.
"""

from __future__ import annotations

import math

from cadgen._internal.cam.posts.base import PostBase, Route, _shift


class FanucPost(PostBase):
    name = "fanuc"
    controller = "Fanuc"
    cycles = frozenset({"drill", "spot", "peck", "chip_break", "tap", "rigid_tap", "ream", "bore", "fine_bore",
                        "back_bore"})
    cycle_dwells = frozenset({"spot", "tap", "rigid_tap", "ream", "fine_bore", "back_bore"})
    orient = True
    line_numbers = "every"
    decimal_point = True
    upper_comments = True
    tape = True
    program_digits = 4
    dwell_unit = "ms"
    safe_start = "G0 G17 G40 G49 G80 G90 G94"
    end_code = "M30"
    end_lines = ("G49",)

    def home_lines(self) -> list[str]:
        return ["G91 G28 Z0.", "G90"]

    def extra_header(self, kinds: frozenset[str] = frozenset()) -> list[str]:
        if "rigid_tap" not in kinds:
            return []
        return ["Rigid tap written as M29 S then G84; check your control's rigid tap mode (some want M29 before M3)"]

    def route(self, move, where):
        route = super().route(move, where)
        if route.how == "native" and move.cycle.kind in ("fine_bore", "back_bore"):
            ux, uy = _shift(move.cycle)
            if math.hypot(ux - 1.0, uy) > 1e-9:
                return Route("expand")
        return route

    def before_cycle(self, prog, move) -> None:
        if move.cycle.kind == "rigid_tap":
            prog.code(f"M29 S{prog.speed}")

    def after_cycle(self, prog) -> None:
        if prog.cycle_kind == "rigid_tap":
            prog.rpm = None   # rigid tapping ends with G80: M3 again before the spindle next cuts
