"""Grbl 1.1 (hobby routers and mills on an Arduino-class board).

What grbl has and has not, and what this post does about it:

- No tool changer and no ``M6`` (grbl 1.1 rejects it as unsupported): a tool change stops the spindle and coolant,
  lifts to the program's safe height, names the tool in a comment and pauses with ``M0``; the operator changes the
  tool, zeroes Z on it and resumes. No ``G43`` either: the new Z zero is the length offset.
- No canned cycles: every cycle is written as the moves it stands for. A bore's spindle stop at the bottom is
  ``M5``, and ``M3`` after the retract. No tapping and no spindle orientation, so a tap or an oriented boring cycle
  is refused.
- ``G4 P`` is seconds. grbl does not wait for the spindle to reach speed, so every ``M3`` is followed by
  ``G4 P<spin-up>`` (the job's ``machine.spindle_spinup_s``, else 3 s).
- Coolant: only flood (``M8``) is in a stock build (``M7`` mist is compiled out, ENABLE_M7); mist and air are a
  comment asking the operator to switch them by hand.
- The spindle must be a spindle: the header asks for laser mode off (``$32=0``), since in laser mode grbl does not
  stop for M3 or M5 changes.
- Lines: grbl keeps up to 80 characters of a block once spaces and comments are dropped, and a sender that counts
  characters into its 128-byte serial buffer needs each whole line under that; every line here stays within 127
  characters and its code within 79, plain ASCII.
- The program ends with ``M2``: grbl treats ``M2`` and ``M30`` alike, and a streamed program has nothing to rewind.
- No ``G28`` or ``G53`` retract (both depend on homing, which a grbl machine may not have): the tool lifts to the
  program's safe height in the work coordinates instead, before the spindle starts and after every pause. No G49
  either: grbl has no tool length offset in use here.
"""

from __future__ import annotations

from cadgen._internal.cam.posts.base import PostBase, PostRefusal


class GrblPost(PostBase):
    name = "grbl"
    controller = "Grbl 1.1"
    cycles = frozenset()
    tool_change = "pause"
    positioning = "safe"
    max_line_length = 127
    dwell_unit = "s"
    safe_start = "G90 G94 G17"
    end_code = "M2"
    spinup_s = 3.0
    coolant_codes = {"flood": "M8"}

    def extra_header(self) -> list[str]:
        return ["Laser mode must be off: setting 32 = 0"]

    def check_line(self, text: str) -> None:
        super().check_line(text)
        code = "".join(text.split("(")[0].split())
        if len(code) > 79:
            raise PostRefusal(f"{self.name} post: the block {text!r} is longer than grbl's 79-character line buffer")
        if not text.isascii():
            raise PostRefusal(f"{self.name} post: the line {text!r} is not plain ASCII")
