from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class SelectorProfile(str, Enum):
    SUMMARY = "summary"
    REFS = "refs"
    ARTIFACT = "artifact"


@dataclass
class SelectorBundle:
    manifest: dict[str, Any]
