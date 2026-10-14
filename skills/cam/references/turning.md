# Turning (not built yet)

Turning a part on a lathe is phase 3. A job whose machine is a `lathe` is read (its stock a
`cylinder` or `from`, its fixture a `chuck`, `collet` or `none`, its tools the lathe inserts), but no
lathe post is built yet, so it is refused when the job is read: `machine.post: "fanuc_lathe" is phase
3, not built yet`. Each turning operation is refused by name with its phase.

| `op` | plain word |
| --- | --- |
| `lathe_face` | Face |
| `lathe_rough` | Rough turn |
| `lathe_finish` | Finish turn |
| `lathe_groove` | Groove |
| `lathe_part` | Part off |
| `lathe_thread` | Thread |
| `lathe_bore` | Bore |
| `lathe_drill` | Drill on centre |

Posts planned with them: `fanuc_lathe`, `linuxcnc_lathe`.

Tell the user plainly that turning is not in this version. A round part's flat ends, cross holes and
flats can be milled on a mill with the 2.5D operations when the user holds it in a vise or a collet
block; say that this is milling, not turning. See [planned-next.md](planned-next.md).
