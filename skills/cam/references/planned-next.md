# Planned next

Every operation and post cadgen knows is registered by name with its phase. One not built yet is
refused in a sentence naming its phase and listing what is built today, never with a plausible plan.
When the user asks for one, say plainly that it is not in this version, and do not stand another
operation in for it.

## Phase 1: 2.5D milling and drilling

All 20 built today, with the checks over the whole planned job ([findings.md](findings.md)).

## Phase 2: 3D milling, adaptive clearing and stock simulation

`adaptive_2d` (constant-engagement roughing, trochoidal in narrow places: moved here from phase 1, to
be designed with `adaptive_3d`; rough with `pocket_2d` meanwhile, [milling-2d.md](milling-2d.md)),
`adaptive_3d`, `pocket_3d`, `parallel`, `contour_3d`, `scallop`, `pencil`, `spiral`, `radial`,
`morph`, `steep_shallow`, `ramp`, `horizontal`, `rest`: [milling-3d.md](milling-3d.md). With them the
stock simulation, so a plan sees what earlier operations left, and the Viewer shows the stock after
each operation.

## Phase 3: turning, sheet cutting and EDM

`lathe_face`, `lathe_rough`, `lathe_finish`, `lathe_groove`, `lathe_part`, `lathe_thread`,
`lathe_bore`, `lathe_drill` ([turning.md](turning.md)); `laser_cut`, `laser_engrave`, `plasma_cut`,
`waterjet_cut` ([cutting.md](cutting.md)); `wire_contour`, `wire_open`, `sinker` ([edm.md](edm.md)).
Posts: `fanuc_lathe`, `linuxcnc_lathe`, `grbl_laser`, `linuxcnc_plasma`, `waterjet`, `fanuc_wire`,
`sinker_iso`.

## Phase 4: multi-axis

`indexed`, `rotary_wrap`, `rotary_parallel`, `swarf`, `multiaxis_contour`, `flowline_5x`
([multi-axis.md](multi-axis.md)). Posts: `linuxcnc_5x`, `fanuc_5x`, `haas_umc`, `fanuc_g68_2`.
