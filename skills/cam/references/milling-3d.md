# 3D milling (not built yet)

Surfaces that curve in more than one direction (a mould, a sculpted face, a blend, a fillet run along
a floor) need 3D toolpaths over a simulated stock. They are phase 2, and this cadgen refuses each by
name with its phase: `operations[0].op: "parallel" is phase 2, not built yet; built today: ...`.

| `op` | plain word |
| --- | --- |
| `adaptive_3d` | 3D adaptive roughing |
| `pocket_3d` | Z-level roughing |
| `parallel` | Parallel |
| `contour_3d` | Waterline |
| `scallop` | Scallop |
| `pencil` | Pencil |
| `spiral` | Spiral |
| `radial` | Radial |
| `morph` | Morph |
| `steep_shallow` | Steep and shallow |
| `ramp` | Ramp |
| `horizontal` | Flats |
| `rest` | Rest machining |

Until then:

- Tell the user plainly that 3D surfaces are not in this version. Do not stand `pocket_2d` or
  `contour_2d` levels in for a curved surface: they leave steps where it curves, and the part is not
  the one they drew.
- The part's flat features (faces, pockets with flat floors, profiles, holes) can still be planned
  with the 2.5D operations ([milling-2d.md](milling-2d.md), [drilling.md](drilling.md)); say which
  surfaces are left uncut and need another program.

See [planned-next.md](planned-next.md).
