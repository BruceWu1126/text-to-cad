"""Mach3 (mills and routers), with its stock settings.

- Tool change ``T<n> M6`` (Mach3's stock M6 stops and waits for the operator) then ``G43 H<n>``. Many Mach3 machines
  are never homed, so the program never goes to machine Z: it lifts to its own safe height in the work coordinates
  before a change and at the end, and comes back down from there after one.
- Canned cycles G81, G82, G83, G73, G85, G89, G86 and G84 with G98 / G99. Mach3's G84 reverses the spindle at the
  bottom without synchronising the feed to it, so it is for a floating (tension-compression) tap holder; a rigid
  tap is refused. No spindle orientation, so the oriented boring cycles (fine bore, back bore) are refused.
- Dwell P is seconds: Mach3's General Config option "G04 Dwell param in Ms" must be off (its default); the header
  says so.
- ``G91.1`` (arc centres incremental) is stated in the safe start, since Mach3's IJ mode is a setting.
- Mach3 waits its own spin-up delay after M3 (Spindle Setup, "Spin UP"); the header says to set it. A job's
  ``machine.spindle_spinup_s`` adds a G4 after every M3 as well.
- The end cancels the tool length offset (``G49``).
"""

from __future__ import annotations

from cadgen._internal.cam.posts.base import PostBase


class Mach3Post(PostBase):
    name = "mach3"
    controller = "Mach3"
    cycles = frozenset({"drill", "spot", "peck", "chip_break", "ream", "bore", "tap"})
    cycle_dwells = frozenset({"spot", "ream", "bore"})
    always_dwell = frozenset({"spot", "bore"})
    positioning = "safe"
    dwell_unit = "s"
    safe_start = "G90 G94 G17 G40 G49 G80 G91.1"
    end_code = "M30"
    end_lines = ("G49",)

    def extra_header(self, kinds: frozenset[str] = frozenset()) -> list[str]:
        tap = ["G84 is a floating-holder tap: Mach3 reverses the spindle but does not synchronise the feed to it"]
        return ["Dwell P is seconds: General Config G04 Dwell param in Ms must be off",
                *(tap if "tap" in kinds else []),
                "Set the spindle spin-up delay in Spindle Setup: Mach3 waits it after M3 before cutting"]
