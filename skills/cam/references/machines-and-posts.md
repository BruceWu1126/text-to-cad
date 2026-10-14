# Machines and posts

A post writes the planned toolpaths as one controller's G-code. Controllers read the same words
differently (a Fanuc reads `X10` as 10 micrometres, grbl has no tool changer, LinuxCNC's G76 is a
lathe threading cycle), so a program written for the wrong one can crash the machine. The job must
name its post (`machine.post`), cadgen never guesses one, and neither do you: ask the user which
controller the machine runs. `--post` writes the same job for another controller without editing it.

## The posts built today

| `post` | Controller | Machines | Tool change | Canned cycles | Dwell `P` |
| --- | --- | --- | --- | --- | --- |
| `grbl` | Grbl 1.1 (hobby routers and mills) | mill, router | pause (`M0`) for the operator | none: every cycle written as moves | seconds |
| `linuxcnc` | LinuxCNC 2.9 and later | mill, router | `T<n> M6`, `G43 Z H<n>` | G81, G82, G83, G73, G84, G85, G89, G86; rigid tap as `G33.1` | seconds |
| `fanuc` | Fanuc 0i / 30i machining centres | mill, router | `G91 G28 Z0.`, `T<n> M6`, `G43 Z H<n>` | G81, G82, G83, G73, G84 (`M29` rigid), G85, G89, G86, G76, G87 | whole milliseconds |
| `haas` | Haas NGC and classic mills | mill | `G53 G0 Z0.`, `T<n> M6`, `G43 Z H<n>` | G81, G82, G83, G73, G84, G85, G89, G86, G76 | seconds, with a decimal point |
| `mach3` | Mach3, stock settings | mill, router | `T<n> M6` (Mach3's stock M6 waits for the operator), `G43 H<n>` | G81, G82, G83, G73, G84 (floating tap), G85, G89, G86 | seconds |

Every post writes `G21` (or `G20` for an inch machine), its safe start, `G54`, then each operation
under a comment `(Op 0: facing with T1)`, arcs as `G2` / `G3` with `I` / `J` (helical where Z
changes), and ends with `G80` if a cycle is open, `M5`, `M9`, its retract and its program end. A cycle
the controller does not have is written as the moves it stands for. Numbers are rounded to 0.001 mm
(0.0001 in); a cut's depth always rounds toward safe, never deeper than planned.

Built later (refused today with their phase): `fanuc_lathe`, `linuxcnc_lathe`, `grbl_laser`,
`linuxcnc_plasma`, `waterjet`, `fanuc_wire`, `sinker_iso` (phase 3); `linuxcnc_5x`, `fanuc_5x`,
`haas_umc`, `fanuc_g68_2` (phase 4). See [planned-next.md](planned-next.md).

## The header

Every program opens with a comment block. Read it to the user when you hand the program over: it is
what the program assumes.

```
(Part: plate.step)
(Job: plate.job.json)
(Setup: top, tool axis +Z)
(WCS: G54 at stock_top_center)
(Post: grbl, for Grbl 1.1, units mm, dwell P in seconds)
(Laser mode must be off: setting 32 = 0)
(Spindle spin-up: G4 P 3 s after every M3)
(T1: D6 mm flat_endmill 3 flutes: 21221 rpm, feed 2546.5 mm/min, plunge 764 mm/min)
(T2: D6 mm drill: 14854 rpm, feed 1782.5 mm/min)
(Prove this program on the machine: single block, feed override low, in air first.)
```

A post's own notes come after the `Post:` line (grbl's laser mode, Mach3's settings, Fanuc's rigid
tap). A program that bores with an oriented bar (`fine_bore`, `back_bore`) also says which way the bar
shifts off the wall, and so which way its tip must point at the spindle orient position.

## Each post

### `grbl`

- No tool changer and no `M6`: a tool change stops the spindle and coolant, lifts to the program's
  safe height, names the tool in a comment and pauses with `M0`. The operator changes the tool,
  zeroes Z on it, and resumes. There is no tool length offset: the new Z zero is the offset. The
  first tool is a comment, `(Load T1, D6 mm flat_endmill and zero Z on it before starting)`.
- No canned cycles: every drilling cycle is written as moves. A boring cycle's spindle stop at the
  bottom is `M5`, and `M3` after the retract.
- **Refuses** a `tap` or `rigid_tap` (grbl has no tapping: written as moves the feed would not follow
  the spindle and the tap would break) and the oriented boring cycles (`fine_bore`, `back_bore`: grbl
  cannot orient its spindle). Thread mill the hole instead, or use another post.
- grbl does not wait for the spindle to reach speed, so every `M3` is followed by `G4 P3` (3 s,
  or the job's `machine.spindle_spinup_s`).
- Coolant: flood is `M8`. Mist and air are a comment asking the operator to switch them by hand (a
  stock grbl build has no `M7`).
- The header asks for laser mode off (`$32=0`): in laser mode grbl does not stop for `M3` or `M5`.
- No `G28` or `G53` (both need homing, which a grbl machine may not have): the tool lifts to the
  program's own safe height in work coordinates before the spindle starts and after every pause.
- Lines stay within 127 characters and their code within 79, plain ASCII. The program ends `M2`.

**Check on the machine:** `$32=0`; Z zeroed on each tool after every pause; the spindle is up to
speed in the 3 s wait (raise `spindle_spinup_s` if not).

### `linuxcnc`

- `T<n> M6`, then `G43 Z H<n>`. Before a change and at the end, `G49` and `G53 G0 Z0` (LinuxCNC runs a
  program only on a homed machine by default, and a mill's Z home is at the top).
- The safe start holds path blending to `G64 P0.01` (`P0.0005` in inch), so corners are not rounded
  off by more than that, and states `G91.1` (arc centres incremental).
- Canned cycles G81, G82, G83, G73, G84, G85, G89, G86, with G98 / G99. `G84` is a floating-holder tap.
  A rigid tap is `G33.1 Z K<pitch>`, spindle-synchronised; it takes no dwell, so a `rigid_tap` with a
  dwell is refused.
- LinuxCNC's G76 is a lathe threading cycle and it has no G87, so a fine bore and a back bore are
  written as moves, with the spindle stopped and oriented (`M5`, `M19 R0 Q10`) before the bar shifts.
- Its own peck clearance (G83) and chip-break back-off (G73) are fixed at 0.254 mm.
- The program ends `M2`.

**Check on the machine:** the tool table has each `T<n>` with its length; `M19` orients where the
header says the bar's tip must point.

### `fanuc`

- `%` before and after, an `O` number of four digits (`O1000`, then `O1001` for the next setup) with
  the part in a comment, and `N` on every block, from `N10` in tens.
- Every number keeps its decimal point (`X10.`): without one a Fanuc reads least increments. Comments
  are upper case.
- Tool change `G91 G28 Z0.`, `G90`, `T<n> M6`, then `G43 Z H<n>` on the way down. The end cancels the
  length offset (`G49`) after going home, and the program ends `M30`.
- Canned cycles G81, G82, G83, G73, G84, G85, G89, G86, G76 and G87 with G98 / G99. A rigid tap is
  `M29 S<rpm>` before G84 with `F = S x pitch`; a floating tap is G84 alone. G81, G83, G73 and G86 take
  no `P` here, so one with a dwell is written as moves.
- G76 and G87 shift by `Q` along the direction the machine's parameters set, assumed +X; a bar that
  shifts another way is written as moves with `M19`.
- Peck clearance (G83) and chip-break back-off (G73) are the machine's parameters 5115 and 5114.

**Check on the machine:** the rigid tap mode (the header says some controls want `M29` before
`M3`); parameters 5114 and 5115; the G76 / G87 shift direction (RD1 / RD2) is +X.

### `haas`

- `%` before and after, an `O` number of five digits (`O01000`) with the part in a comment; `N1`,
  `N2`, ... on each tool change, so the operator can restart at a tool. Numbers keep their decimal
  point.
- Tool change `G53 G0 Z0.`, `T<n> M6`, then `G43 Z H<n>` on the way down. The end goes up, cancels the
  length offset (`G49`) and ends `M30`.
- Canned cycles G81, G82, G83, G73, G84, G85, G89, G86 and G76 with G98 / G99. Both taps are G84 with
  `F = S x pitch` (Haas taps rigidly with the option and works with a floating holder too; it has no
  `M29`). G76 shifts by `I` / `J`. Haas's back bore is G77, not G87, so a back bore is written as moves
  with the spindle stopped and oriented (`M19`).
- Dwell `P` is seconds with a decimal point (`P0.5`, `P2.`): Haas reads a `P` with no point as
  milliseconds. G81, G83, G73, G84 and G86 take no `P` here, so one with a dwell is written as moves (a
  tap with one is refused).
- Coolant: flood `M8`, mist `M7`, air is the auto air jet (`M83` on, `M84` off).
- A Haas post is for a mill, not a router.

**Check on the machine:** rigid tapping is installed when a `tap` runs; the tool offsets for each
`H<n>`.

### `mach3`

- `T<n> M6` (Mach3's stock M6 stops and waits for the operator), then `G43 H<n>`. Many Mach3 machines
  are never homed, so the program never goes to machine Z: it lifts to its own safe height before a
  change and at the end.
- Canned cycles G81, G82, G83, G73, G85, G89, G86 and G84 with G98 / G99. Mach3's G84 reverses the
  spindle without synchronising the feed: it is for a floating (tension-compression) tap holder, and
  a `rigid_tap` is refused. Mach3 cannot orient its spindle, so a fine bore and a back bore are
  refused.
- Dwell `P` is seconds: Mach3's General Config option "G04 Dwell param in Ms" must be off (its
  default); the header says so. `G91.1` is in the safe start, since Mach3's IJ mode is a setting.
- Mach3 waits its own spin-up delay after `M3` (Spindle Setup, "Spin UP"); set it. A job's
  `machine.spindle_spinup_s` adds a `G4` after every `M3` as well.
- The end cancels the length offset (`G49`) and ends `M30`.

**Check on the machine:** "G04 Dwell param in Ms" off; the spin-up delay set; a floating tap holder
for `tap`.

## Refusals

A post refuses what its controller cannot run safely as a `no_post_support` error finding, naming
the post and the operation: no G-code and no GLB are written, the sidecar holds the finding, and the
run exits 1 ([findings.md](findings.md)):

```
error: The grbl post cannot write this job: operations[1] (tap) has a tap cycle, and Grbl 1.1 has no tapping cycle
```

Its description goes on: "Expanded into plain moves, the feed would not follow the spindle and the
tap would break; use a post whose controller taps, or thread mill the hole. No G-code was written".

| The job has | Refused by |
| --- | --- |
| `tap` | `grbl` |
| `rigid_tap` | `grbl`, `mach3` |
| `tap` with a `dwell_s` | `haas`, `mach3` |
| `rigid_tap` with a `dwell_s` | `linuxcnc`, `haas` |
| `boring_cycle` with `cycle: fine_bore`, or `back_bore` | `grbl`, `mach3` |

A post named for another kind of machine, a post with more axes than the machine, a post not built
yet and a name cadgen does not know are refused when the job is read, with the posts built for this
machine.

## Units and files

`machine.units: "inch"` writes `G20` and converts every coordinate, centre offset, feed and cycle
depth; the job stays in mm. A job with one setup writes `<stem>.nc`; with several, one program per
setup, `<stem>.<setup id>.nc`, each with its own header, work zero and tools. Tell the user which file
runs first and how to re-fixture between them.
