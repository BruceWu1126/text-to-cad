# EDM (not built yet)

Wire and sinker EDM (cutting by spark erosion) are phase 3. A job whose machine is a `wire_edm` or
`sinker_edm` is read, but no EDM post is built yet, so it is refused when the job is read. Each EDM
operation is refused by name with its phase.

| `op` | plain word |
| --- | --- |
| `wire_contour` | Wire contour |
| `wire_open` | Wire open profile |
| `sinker` | Sinker |

Posts planned with them: `fanuc_wire`, `sinker_iso`.

Tell the user plainly that EDM is not in this version, and do not stand a milling program in for it:
the reason to use EDM (a hard material, a sharp inside corner, a fine slot) is usually what a milling
cutter cannot do. See [planned-next.md](planned-next.md).
