"""Grbl 1.1 (hobby routers and mills on an Arduino-class board).

What grbl has and has not, and what this post does about it:

- No tool changer and no ``M6`` (grbl 1.1 rejects it as unsupported): a tool change stops the spindle and coolant,
  lifts to the program's safe height, names the tool in a comment and pauses with ``M0``; the operator changes the
  tool, zeroes Z on it and resumes. No ``G43`` either: the new Z zero is the length offset.
- No canned cycles: every cycle is written as the moves it stands for. A bore's spindle stop at the bottom is
  ``M5``, and ``M3`` after the retract. No tapping and no spindle orientation, so a tap or an oriented boring cycle
  is refused.
- ``G4 P`` is seconds.
- Lines: grbl keeps up to 80 characters of a block once spaces and comments are dropped, and a sender that counts
  characters into its 128-byte serial buffer needs each whole line under that; every line here stays within 127
  characters and its code within 79, plain ASCII.
- The program ends with ``M2``: grbl treats ``M2`` and ``M30`` alike, and a streamed program has nothing to rewind.
- No ``G28`` or ``G53`` retract (both depend on homing, which a grbl machine may not have): the tool lifts to the
  program's safe height in the work coordinates instead.
"""

from __future__ import annotations

from cadgen._internal.cam.posts.base import PostBase


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

    def check_line(self, text: str) -> None:
        super().check_line(text)
        code = "".join(text.split("(")[0].split())
        if len(code) > 79:
            raise ValueError(f"{self.name} post: the block {text!r} is longer than grbl's 79-character line buffer")
        if not text.isascii():
            raise ValueError(f"{self.name} post: the line {text!r} is not plain ASCII")
