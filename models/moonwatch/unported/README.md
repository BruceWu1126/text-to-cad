# moonwatch: unported models

The four models of the old `moonwatch` project that the port to `cadgen.engine` left
out (`../src/README.md`, "Not ported"), kept with their old sources in the layout they
had. The port took the two main documents, the case and the movement, and names these
four as not ported; for the full watch it adds that the animation is dropped by
decision (the xbf has no animation).

| Model | What it is (the old README's) |
| --- | --- |
| `moonwatch` | Full watch: case + dial/hands + movement + bracelet; carries its `KINEMATICS` mates and couplings and its `ANIMATION` clips |
| `dial` | Three-register step dial, applied indices, printed tracks, full hand stack |
| `bracelet` | Flat three-link bracelet, end links, fold-over clasp |
| `finishing_sampler` | Standing coupon exercising the shared finishing vocabulary |

`moonwatch.py` composes the case, dial, movement and bracelet by importing their
modules, so `src/` holds the whole old source tree: `case.py`, `movement.py`,
`movement_base.py`, `keyless_works.py`, `chrono_works.py` and `lib/` are the old
versions of what `../src/` ports, kept so the four stay as they were written. The
sources are unchanged, written for cadgen before the engine.
