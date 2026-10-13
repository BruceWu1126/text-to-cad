"""The protocol every operation implements.

An operation is what a job writes in ``"op"``: it reads its own keys (``parse``), turns them into a toolpath
(``plan``, given a :class:`~cadgen._internal.cam.operations.context.PlanContext`, returning a
:class:`~cadgen._internal.cam.operations.context.Planned`: the toolpath, its numbers and its findings), says what is
wrong with it (``checks``) and says its numbers in a line or two (``human_lines``, from ``Planned.stats``).
The job parser reads the keys every operation shares (``job.COMMON_KEYS``) and checks the operation's
``machine_kinds``, ``tool_types`` and ``targets`` before handing the rest to ``parse``.

Stdlib only at import: geometry and numerics live inside ``plan`` and ``checks``, so a malformed job fails in
milliseconds. Units are the engine's: mm, mm/min, rpm, s.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Protocol

if TYPE_CHECKING:
    from cadgen._internal.cam.job import Machine, Material, Setup, Tool

__all__ = ["Operation", "OperationBase", "ParseContext", "Phase", "TARGETS", "by_severity", "finding"]

#: The phase an operation is built in (the design's four): 2.5D, 3D, turning/cutting/EDM, multi-axis.
Phase = Literal[1, 2, 3, 4]
#: What an operation can be pointed at: feature selectors, a closed region drawn in the setup's plane, or the
#: whole part (``all: true``).
TARGETS = ("features", "region", "all")


@dataclass(frozen=True)
class ParseContext:
    """What an operation's ``parse`` may read besides its own keys: the job's parts it runs with."""

    #: The operation's field path, for errors: ``operations[2]``.
    where: str
    machine: "Machine"
    material: "Material"
    setup: "Setup"
    tool: "Tool"


class Operation(Protocol):
    name: ClassVar[str]                       # what a job writes in "op": "pocket_2d"
    phase: ClassVar[Phase]
    word: ClassVar[str]                       # what the viewer calls it: "Pocket"
    keys: ClassVar[frozenset[str]]            # its own keys, beyond job.COMMON_KEYS
    tool_types: ClassVar[tuple[str, ...]]     # the tool types it cuts with (job.TOOL_TYPES)
    machine_kinds: ClassVar[tuple[str, ...]]  # the machines it runs on (job.MACHINE_KINDS)
    targets: ClassVar[tuple[str, ...]]        # which of TARGETS it may be pointed at
    target_required: ClassVar[bool]           # False when it has a whole-part default (facing)

    def parse(self, raw: dict, ctx: ParseContext) -> Any: ...       # stdlib; ValueError names the field
    def plan(self, params: Any, ctx: Any) -> Any: ...               # context.Planned: toolpath, stats, findings
    def checks(self, params: Any, ctx: Any, moves: list) -> list[dict]: ...   # findings (``finding``)
    def human_lines(self, stats: dict) -> list[str]: ...


class OperationBase:
    """The protocol's defaults: a subclass states only what is its own."""

    keys: ClassVar[frozenset[str]] = frozenset()
    machine_kinds: ClassVar[tuple[str, ...]] = ("mill", "router")
    targets: ClassVar[tuple[str, ...]] = ("features",)
    target_required: ClassVar[bool] = True

    def parse(self, raw: dict, ctx: ParseContext) -> Any:
        return dict(raw)

    def checks(self, params: Any, ctx: Any, moves: list) -> list[dict]:
        return []

    def human_lines(self, stats: dict) -> list[str]:
        return []


def finding(severity: str, kind: str, summary: str, description: str, items=()) -> dict:
    """One entry of a plan's findings, in the KiCad findings' shape."""
    return {"check": "cam", "severity": severity, "type": kind, "summary": summary, "description": description,
            "items": list(items)}


_SEVERITY = {"error": 0, "warning": 1, "info": 2}


def by_severity(found: list[dict]) -> list[dict]:
    """Findings errors first, then warnings, then notes; stable within each."""
    return sorted(found, key=lambda item: _SEVERITY[item["severity"]])
