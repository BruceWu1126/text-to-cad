"""What the ``cadgen.cam`` verbs do, end to end.

Not built yet: planning is phase 1 task 13. Until then ``plan_job`` says so
rather than returning a plausible empty result.
"""

from __future__ import annotations

from pathlib import Path

from cadgen.results import CamFeaturesResult, CamResult

__all__ = ["list_features", "plan_job"]


def list_features(target: Path, *, occurrence: str | None = None, direction: str | None = None,
                  verbose: bool = False) -> CamFeaturesResult:
    from cadgen._internal.cam.features import parse_direction, recognize
    from cadgen._internal.cam.part import open_part, same
    from cadgen.cli_logging import CliLogger

    wanted = None if direction is None else parse_direction(direction)
    logger = CliLogger("cam", verbose=verbose)
    part = open_part(Path(target), occurrence)
    logger.debug(f"recognizing the features of {part.ref} ({len(part.faces)} faces)")
    found = recognize(part)
    if wanted is not None:
        found = tuple(feature for feature in found if any(same(d, wanted) for d in feature.reach))
    return CamFeaturesResult(ok=True, document=Path(target), occurrence=part.ref, direction=direction,
                             features=tuple(feature.public() for feature in found))


def plan_job(target: Path, out: Path | None, *, job=None, occurrence: str | None = None, post: str | None = None,
             verbose: bool = False) -> CamResult:
    raise NotImplementedError("phase 1 task 13: cam plan is not built yet")
