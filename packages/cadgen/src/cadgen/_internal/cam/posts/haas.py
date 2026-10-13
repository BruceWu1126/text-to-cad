"""Haas (NGC and classic mills).

- ``%`` wrappers and an ``O`` number of five digits with the part in a comment; ``N1``, ``N2``, ... on each tool
  change, so the operator can restart at a tool. Numbers keep their decimal point.
- Tool change: ``G53 G0 Z0.`` to go up, ``T<n> M6``, then ``G43 Z H<n>`` on the way down.
- Canned cycles G81, G82, G83, G73, G84, G85, G89, G86 and G76 with G98 / G99. Haas's G84 taps rigidly when the
  machine has the option and works with a floating holder too, so both taps are G84 with ``F = S x pitch`` (Haas has
  no M29 for tapping). G76 shifts by ``I`` / ``J`` along the cycle's own direction. Haas's back bore is G77, not G87
  (Haas's G87 is a bore with a manual retract), so a back bore is expanded with the spindle stopped and oriented
  (``M19``).
- Dwell P is seconds written with a decimal point (``P0.5``, ``P2.``): Haas reads a P with no point as milliseconds.
  G81, G83, G73, G84 and G86 take no P here, so one with a dwell is expanded (a tap with one is refused).
- Air is the auto air jet: ``M83`` on, ``M84`` off.
- The end cancels the tool length offset (``G49``) after going up.
"""

from __future__ import annotations

from cadgen._internal.cam.posts.base import PostBase, _shift


class HaasPost(PostBase):
    name = "haas"
    controller = "Haas"
    cycles = frozenset({"drill", "spot", "peck", "chip_break", "tap", "rigid_tap", "ream", "bore", "fine_bore"})
    cycle_dwells = frozenset({"spot", "ream", "fine_bore"})
    orient = True
    line_numbers = "tool_change"
    decimal_point = True
    tape = True
    program_digits = 5
    dwell_unit = "s."
    safe_start = "G0 G17 G40 G49 G80 G90 G94"
    end_code = "M30"
    end_lines = ("G49",)
    coolant_codes = {"flood": "M8", "mist": "M7", "air": "M83"}
    coolant_off_codes = {"air": "M84"}

    def home_lines(self) -> list[str]:
        return ["G53 G0 Z0."]

    def shift_words(self, prog, cycle) -> list[str]:
        ux, uy = _shift(cycle)
        return [f"I{prog.coord(cycle.shift_mm * ux)}", f"J{prog.coord(cycle.shift_mm * uy)}"]
