"""What the ``cadgen.cam`` verbs do, end to end.

Not built yet: feature recognition is phase 1 task 4 and planning is phase 1
task 13. Until then each verb says so rather than returning a plausible
empty result.
"""

from __future__ import annotations

from pathlib import Path

from cadgen.results import CamFeaturesResult, CamResult

__all__ = ["list_features", "plan_job"]


def list_features(target: Path, *, occurrence: str | None = None, setup: str | None = None,
                  verbose: bool = False) -> CamFeaturesResult:
    raise NotImplementedError("phase 1 task 4: cam features is not built yet")


def plan_job(target: Path, out: Path | None, *, job=None, occurrence: str | None = None, post: str | None = None,
             verbose: bool = False) -> CamResult:
    raise NotImplementedError("phase 1 task 13: cam plan is not built yet")
