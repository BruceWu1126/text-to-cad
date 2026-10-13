"""The public ``cam`` namespace: computer-aided manufacturing for a STEP part.

CAM turns a part into machine instructions. A job file names the machine and
its post (the G-code dialect), the material, the stock, the setups, the tools
and the operations; ``plan`` turns it into G-code, a result GLB the viewer
renders (part, stock, fixtures, toolpaths and tools) and a JSON sidecar with
the numbers. cadgen never contacts a machine: it writes files, and a person
loads them on the controller.

Two verbs, each mirrored by a generated CLI (``cadgen cam features``,
``cadgen cam plan``): ``features`` lists what a part offers a machinist with
the ``#o1.fN`` selectors a job names them by, ``plan`` runs one job. An
operation this cadgen has not built yet says so plainly, with the phase it is
planned for.

No extra is needed: the engine uses OCP, shapely and numpy, all base
dependencies. Import discipline: nothing here may pull in OCP, build123d,
shapely or numpy at module scope (see :mod:`cadgen.step`), so ``--help`` stays
cheap.
"""

from __future__ import annotations

from pathlib import Path

from cadgen.results import CamFeaturesResult, CamResult

__all__ = ["features", "plan"]


def features(
    target: Path,
    *,
    occurrence: str | None = None,
    setup: str | None = None,
    verbose: bool = False,
) -> CamFeaturesResult:
    """List what a part offers a machinist: faces, holes, pockets, slots, bosses and turned diameters.

    target: the STEP/STP document holding the part.
    occurrence: which part occurrence to list (``#o1``) when the document is an
        assembly. Omitted, the document must hold one part.
    setup: list only what can be reached from this tool direction (``+Z``,
        ``-X``, ...). Omitted, each feature lists every axis direction that
        reaches it.
    verbose: show progress on stderr.
    """
    from cadgen._internal.cam.run import list_features

    return list_features(target, occurrence=occurrence, setup=setup, verbose=verbose)


def plan(
    target: Path,
    out: Path | None = None,
    *,
    job: str | dict | None = None,
    occurrence: str | None = None,
    post: str | None = None,
    verbose: bool = False,
) -> CamResult:
    """Plan one job (stock, setups, tools and operations) and write its G-code, result GLB and JSON sidecar.

    target: the STEP/STP document holding the part.
    out: the result .glb path (ending in ``.cam.glb`` or ``.glb``). Omitted,
        writes ``<document>.cam.glb`` beside the document. The sidecar
        (``<stem>.cam.json``) and the G-code (``<stem>.nc``, or one
        ``<stem>.<setup>.nc`` per setup) sit beside it.
    job: the job: a JSON file path, inline JSON, or (library callers) a dict.
        It names the machine and its post, the material, the stock, the
        setups, the tools and the operations; see the cam skill for the
        schema. Feature refs are the ones ``cadgen cam features`` prints.
    occurrence: plan for this part occurrence (``#o1.2``) when the document is
        an assembly. Omitted, the document must hold one part.
    post: the post processor (G-code dialect) to write, overriding the job's
        machine.post for a one-off export.
    verbose: show every progress line and the timings on stderr. Without it, a
        plan that runs long still says how far it has got on stderr, about every 10 s.
    """
    from cadgen._internal.cam.run import plan_job

    return plan_job(target, out, job=job, occurrence=occurrence, post=post, verbose=verbose)
