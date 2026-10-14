# Multi-axis (not built yet)

A tilted tool (3+2), a fourth axis or simultaneous five-axis motion is phase 4. A job may say its
mill has 4 or 5 axes today, but every operation that runs plans 3-axis toolpaths along its setup's
direction, and the multi-axis operations are refused by name with their phase.

| `op` | plain word |
| --- | --- |
| `indexed` | 3+2 indexed |
| `rotary_wrap` | Rotary wrap |
| `rotary_parallel` | Rotary surface |
| `swarf` | Swarf |
| `multiaxis_contour` | 5-axis contour |
| `flowline_5x` | 5-axis flowline |

Posts planned with them: `linuxcnc_5x`, `fanuc_5x`, `haas_umc`, `fanuc_g68_2`.

Until then, a part with features on several sides is several setups, one per direction
(`"direction": "-Z"` for the part turned over, or a face selector to look straight down a face's
normal), each its own program for the operator to re-fixture between
([job-file.md](job-file.md#setups)). A feature on a tilted face can be its own setup only when the
user can hold the part at that angle. See [planned-next.md](planned-next.md).
