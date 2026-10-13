"""LinuxCNC 2.8 and later (mills and routers).

- Tool change ``T<n> M6`` then ``G43 Z H<n>``; the tool goes up with ``G53 G0 Z0`` before a change and at the end
  (LinuxCNC refuses to run a program on an unhomed machine by default, and a mill's Z home is at the top).
- Canned cycles G81, G82, G83, G73, G85, G89 and G86 with G98 / G99; P is seconds, written on G82, G86 and G89.
  LinuxCNC's G76 is a lathe threading cycle, not a fine bore, and it has no G87 back bore: both are expanded, with
  the spindle stopped and oriented (``M5``, ``M19 R0``) before the bar shifts off the wall.
- Tapping is ``G33.1`` (spindle-synchronised): down to the R plane, then ``G33.1 Z K<pitch>`` cuts, reverses and
  comes back to R; LinuxCNC has no G84 here. A tap with a dwell is refused.
- ``G91.1`` (arc centres incremental, LinuxCNC's default) is stated in the safe start.
- LinuxCNC's own peck clearance (G83) and chip-break back-off (G73) are fixed at 0.010 in (0.254 mm).
- The program ends with ``M2`` (``M30`` also exchanges pallet shuttles).
"""

from __future__ import annotations

from cadgen._internal.cam.posts.base import PostBase, Route


class LinuxcncPost(PostBase):
    name = "linuxcnc"
    controller = "LinuxCNC"
    cycles = frozenset({"drill", "spot", "peck", "chip_break", "ream", "bore", "tap", "rigid_tap"})
    cycle_dwells = frozenset({"spot", "ream", "bore"})
    always_dwell = frozenset({"spot", "bore"})
    orient = True
    dwell_unit = "s"
    safe_start = "G90 G94 G17 G40 G49 G80 G91.1"
    end_code = "M2"

    def home_lines(self) -> list[str]:
        return ["G53 G0 Z0"]

    def stop_orient(self) -> list[str]:
        return ["M5", "M19 R0"]

    def route(self, move, where):
        route = super().route(move, where)
        return Route("special") if move.cycle.kind in ("tap", "rigid_tap") else route

    def special_cycle(self, prog, move, start) -> None:
        cycle = move.cycle
        x, y, end_z = move.xyz
        prog.ensure_spindle()
        prog.motion("G0", (x, y, None))
        prog.motion("G0", (None, None, cycle.r))
        prog.code(f"G33.1 Z{prog.coord(cycle.z)} K{prog.pitch(cycle.pitch_mm)}")
        prog.motion("G0", (None, None, end_z))
