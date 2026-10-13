"""LinuxCNC 2.9 and later (mills and routers).

- Tool change ``T<n> M6`` then ``G43 Z H<n>``. Before a change and at the end the tool length offset is cancelled
  (``G49``: ``G53`` moves still apply it) and the tool goes up with ``G53 G0 Z0`` (LinuxCNC refuses to run a
  program on an unhomed machine by default, and a mill's Z home is at the top).
- The safe start holds path blending to ``G64 P0.01`` (mm; ``P0.0005`` in inch), so corners are not rounded off by
  more than that.
- Canned cycles G81, G82, G83, G73, G84, G85, G89 and G86 with G98 / G99; P is seconds, written on G82 and G86
  always and on G84 when the tap dwells. G84 is the floating-holder tap (it reverses the spindle at the bottom); a
  rigid tap is ``G33.1``, spindle-synchronised: down to the R plane, then ``G33.1 Z K<pitch>`` cuts, reverses and
  comes back to R (it takes no dwell, so a rigid tap with one is refused).
- LinuxCNC's G76 is a lathe threading cycle, not a fine bore, and it has no G87 back bore: both are expanded, with
  the spindle stopped and oriented before the bar shifts off the wall (``M5``, ``M19 R0 Q10``: without Q, M19 does
  not wait for the orient to finish).
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
    cycle_dwells = frozenset({"spot", "ream", "bore", "tap"})
    always_dwell = frozenset({"spot", "bore"})
    orient = True
    dwell_unit = "s"
    safe_start = "G90 G94 G17 G40 G49 G80 G91.1"
    end_code = "M2"

    def safe_start_line(self, inch: bool) -> str:
        return f"{self.safe_start} G64 P{'0.0005' if inch else '0.01'}"

    def home_lines(self) -> list[str]:
        return ["G49", "G53 G0 Z0"]

    def stop_orient(self) -> list[str]:
        return ["M5", "M19 R0 Q10"]

    def route(self, move, where):
        if move.cycle.kind == "rigid_tap":
            if move.cycle.dwell_s:
                raise ValueError(f"{self.name} post: {where} has a rigid_tap cycle with a dwell, and LinuxCNC's "
                                 "G33.1 takes none; leave the dwell out")
            return Route("special")
        return super().route(move, where)

    def special_cycle(self, prog, move, start) -> None:
        cycle = move.cycle
        x, y, end_z = move.xyz
        prog.ensure_spindle()
        prog.motion("G0", (x, y, None))
        prog.motion("G0", (None, None, cycle.r))
        prog.code(f"G33.1 Z{prog.coord(cycle.z, toward=1)} K{prog.pitch(cycle.pitch_mm)}")
        prog.motion("G0", (None, None, end_z))
