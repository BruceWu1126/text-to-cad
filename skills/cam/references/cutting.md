# Sheet cutting (not built yet)

Cutting a flat part from sheet by laser, plasma or waterjet is phase 3. A job whose machine is a
`laser`, `plasma` or `waterjet` is read (its stock a `sheet`, its tool a kerf), but no post for those
machines is built yet, so it is refused when the job is read. Each cutting operation is refused by
name with its phase.

| `op` | plain word |
| --- | --- |
| `laser_cut` | Laser cut |
| `laser_engrave` | Laser engrave |
| `plasma_cut` | Plasma cut |
| `waterjet_cut` | Waterjet cut |

Posts planned with them: `grbl_laser`, `linuxcnc_plasma`, `waterjet`.

Until then:

- A flat part on a CNC router is milling, and that runs today: a `sheet` stock on a `router`, the
  outline cut free with `contour_2d` (with `tabs`; through sheet stock it goes 0.3 mm into the
  spoilboard), holes with the drilling operations ([milling-2d.md](milling-2d.md#contour_2d)).
- A flat part for a cutting service is a DXF: the `$dxf` skill draws it, and `$sendcutsend` checks it
  before upload.

See [planned-next.md](planned-next.md).
