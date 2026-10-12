"""Heat stress: the stress and movement a temperature field puts in a part, with any mechanical loads on top.

The study (spec 5.7) takes the thermal keys (``temperatures``, ``heat``,
``convection`` and ``radiation``, as a steady ``thermal`` study: radiation
reaches the stress through the upstream thermal solve), ``fixtures`` and optional
``loads`` (as ``static``, gravity and acceleration included) and
``reference_C``, the temperature at which the part is stress-free (default
20 °C). The thermal analysis runs first on the same element space (its
``upstream``); its temperature T becomes the stress-free strain α (T - T_ref)
of a linear static solve (:func:`cadgen._internal.fea.solve.solve_linear_static`'s
``initial_strain``), whose stress is σ = C:(ε - α ΔT I).

Everything after the solve is static's, plus the ``temperature`` field and
the temperatures in the summary. The load control scales ΔT and the mechanical
loads together, which is exact because both act linearly. Stdlib only at import.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import AnalysisResult, FieldSpec, SolveContext, finding
from cadgen._internal.fea.analyses.static import StaticAnalysis, StaticInputs, colour_range, floored, solved_records
from cadgen._internal.fea.analyses.thermal import HEAT_KEYS, RADIATION_KEY, ThermalAnalysis, heat_echo, plan_solver
from cadgen._internal.fea.study import parse_fixtures, parse_loads

__all__ = ["ThermalStressAnalysis", "ThermalStressInputs"]

#: The stress-free temperature when the study does not say: a room's.
REFERENCE_C = 20.0


def mpa(value: float) -> str:
    """A stress in MPa as the findings say it: whole megapascals from 100 up, three figures below."""
    return f"{value:.0f}" if abs(value) >= 100 else f"{value:.3g}"


@dataclass(frozen=True)
class ThermalStressInputs(StaticInputs):
    #: What the upstream thermal solve parsed of the same study.
    thermal: Any = None
    #: The temperature at which the part is stress-free, °C.
    reference_C: float = REFERENCE_C


class ThermalStressAnalysis(StaticAnalysis):
    name: ClassVar[str] = "thermal_stress"
    word: ClassVar[str] = "Heat stress"
    extras_word: ClassVar[str] = "heat stress"
    study_keys: ClassVar[frozenset[str]] = frozenset({"fixtures", "loads", "reference_C", *HEAT_KEYS, RADIATION_KEY})
    material_needs: ClassVar[frozenset[str]] = frozenset({"conductivity", "expansion"})
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("von_mises", "_VON_MISES", "von Mises stress", "MPa"),
        FieldSpec("displacement", "_DISPLACEMENT", "displacement", "mm", 3, 1000.0),
        FieldSpec("temperature", "_TEMPERATURE", "temperature", "°C", signed=True),
    )
    default_controls: ClassVar[dict[str, dict]] = {
        "field": {"drives": "field", "type": "enum", "options": ["von_mises", "displacement", "temperature"]},
        "deformation": {"drives": "deformation", "type": "number", "min": 0.0, "max": None},
    }
    upstream: ClassVar[tuple[str, ...]] = ("thermal",)
    noun: ClassVar[str] = "this heat"

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> ThermalStressInputs:
        thermal = ThermalAnalysis().parse(document)
        fixtures = parse_fixtures(document)
        loads = parse_loads(document, required=False)
        reference = kinds.number(document.get("reference_C", REFERENCE_C), where="reference_C")
        refs = tuple(dict.fromkeys([*(ref for group in (*fixtures, *loads) for ref in group.faces), *thermal.face_refs]))
        anchors = tuple(dict.fromkeys(ref for fixture in fixtures for ref in fixture.faces))
        needs = frozenset({"density"}) if any(load.body for load in loads) else frozenset()
        return ThermalStressInputs(refs, anchors, True, fixtures=fixtures, loads=loads, material_needs=needs,
                                   thermal=thermal, reference_C=reference)

    # -- the ladder ----------------------------------------------------------------------------------

    def estimate(self, ctx: SolveContext, inputs: ThermalStressInputs):
        """The elastic solve (static's estimate) and the temperature solve before it, on the same mesh."""
        from cadgen._internal.fea import fit
        from cadgen._internal.fea.analyses.thermal import ladder_estimate

        mechanical, heat = fit.solid_estimate(ctx), ladder_estimate(ctx, steps=6 if inputs.thermal.radiation else 0)
        return fit.Estimate(dofs=mechanical.dofs, memory_bytes=max(mechanical.memory_bytes, heat.memory_bytes),
                            seconds=mechanical.seconds + heat.seconds)

    def symmetric_about(self, plane, inputs: ThermalStressInputs, ctx: SolveContext) -> bool:
        """Never yet: the temperatures are not solved on a half, so neither is their stress."""
        return False

    # -- solve ---------------------------------------------------------------------------------------

    def thermal_strain(self, ctx: SolveContext, temperature, reference: float):
        """α (T - T_ref) at the element space's quadrature points: (elements, quadrature), α per part."""
        from cadgen._internal.fea import operators

        space = ctx.space
        rise = space.scalar.interpolate(temperature - reference).value          # (elements, quadrature)
        alpha = operators.domain_values(space, [m.expansion for m in ctx.materials], space.scalar)
        return alpha * rise

    def solve(self, ctx: SolveContext, inputs: ThermalStressInputs) -> AnalysisResult:
        from cadgen._internal.fea import solve

        upstream = ctx.upstream.get("thermal")
        if upstream is None:  # solved on its own, outside run.py's upstream chaining
            upstream = ThermalAnalysis().solve(ctx, inputs.thermal)
            ctx.upstream["thermal"] = upstream
        temperature = upstream.fields["temperature"]
        volume, plan = ctx.volume, ctx.assembly
        materials = ctx.study.material if plan is None else plan.materials
        extra: dict[str, Any] = {"initial_strain": self.thermal_strain(ctx, temperature, inputs.reference_C), "space": ctx.space}
        if any(load.body for load in inputs.loads):
            extra["body_loads"] = inputs.body_accelerations
        if (solver := plan_solver(ctx)) is not None:
            extra["solver"] = solver
        outcome = solve.solve_linear_static(
            volume, materials, inputs.fixtures, inputs.surface_loads, ctx.ordinal_of, log=ctx.log, automatic=ctx.automatic, **extra
        )
        result = self.static_result(outcome, solved_records(volume, outcome, ctx, inputs.fixtures), plan)
        result.fields["temperature"] = temperature
        result.scalars["reference_C"] = inputs.reference_C
        result.scalars["thermal"] = upstream.scalars.get("summary")
        result.timings.update({f"thermal_{key}": value for key, value in upstream.timings.items()})
        result.warnings = list(upstream.scalars.get("analysis_warnings", ())) + list(result.warnings)
        result.scalars["analysis_warnings"] = list(upstream.scalars.get("analysis_warnings", ()))
        return result

    # -- judging and findings ------------------------------------------------------------------------

    def clamped_edge(self, result: AnalysisResult) -> tuple | None:
        """``(solved, cap, quantile)`` where the peak sits on a clamped face and the colours stop below it
        (:func:`colour_range` capped): the weakest part's record, the stress the colours stop at and its quantile.
        None otherwise."""
        import numpy as np

        solved = self.weakest(result)
        if solved.peak_face is None or solved.peak_face not in solved.fixed_faces or not solved.peak_MPa > 0:
            return None
        ranged = colour_range(np.asarray(result.fields["von_mises"], dtype=float), solved.peak_MPa)
        if len(ranged) < 3:
            return None
        return solved, ranged[1], ranged[2]["quantile"]

    def read_away(self, result: AnalysisResult) -> tuple | None:
        """``(solved, peak)`` where the peak is a clamped edge's singularity: the weakest part's record with its peak
        replaced by the stress a little away from the edge (where the colours stop), which the strength check and the
        safety factor judge, and the singular peak itself. None where the peak is not such an edge's."""
        import dataclasses

        edge = self.clamped_edge(result)
        if edge is None:
            return None
        solved, cap, _ = edge
        return dataclasses.replace(solved, peak_MPa=cap, peak_gauss_MPa=min(solved.peak_gauss_MPa, cap)), solved.peak_MPa

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: ThermalStressInputs) -> dict:
        """Static's, but where the peak is a clamped edge's singularity (:meth:`singular_edge`), strength is judged on
        the stress a little away from the edge, the colour bar's top, and the check keeps the singular peak beside it."""
        from cadgen._internal.fea import checks

        away = self.read_away(result) if check["kind"] == "stress" else None
        if away is None:
            return super().judge(check, index, ctx, result, inputs)
        solved, peak = away
        return {**checks.stress_check(solved, label=check.get("label")), "edge_peak": round(peak, 4)}

    def singular_edge(self, result: AnalysisResult) -> dict | None:
        """The finding for a peak on a clamped face whose colours stop below it (:func:`colour_range` capped), else None.

        A face held fast while heat makes the part grow is a singular edge in heat stress: its peak grows every time
        the mesh is refined, so the number at the edge is the mesh's, and the stress is read a little away from it."""
        edge = self.clamped_edge(result)
        if edge is None:
            return None
        solved, cap, quantile = edge
        peak = solved.peak_MPa
        coarser = solved.coarser_peak_MPa
        rose = f" (it already rose from {mpa(coarser)} to {mpa(peak)} MPa on a finer mesh)" if coarser and peak > coarser else ""
        return finding(
            "warning", "singular_clamped_edge",
            f"The peak stress ({mpa(peak)} MPa) sits on the clamped edge, where a part held fast while heat makes it grow "
            f"has a singularity: it grows every time the mesh is refined{rose}, so strength is judged a little away from "
            f"the edge, where the colour bar stops at {mpa(cap)} MPa",
            f"colours stop at the {round(quantile * 100)}th percentile of the stress, {mpa(cap)} MPa, which the strength "
            f"check and the safety factor judge; nodal peak {mpa(peak)} MPa at the fixed face",
            [{"text": "the peak stress", "ref": solved.peak_face, "at": list(solved.peak_at)}])

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: ThermalStressInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        found = []
        singular = self.singular_edge(result)
        for found_one in super().findings(ctx, result, inputs, check_results, assembly=assembly):
            if found_one["type"] == "peak_at_fixture" and singular is not None:
                continue  # the singular edge's finding says it, and why
            if found_one["type"] == "no_load":
                found_one = {**found_one, "summary": "Nothing stresses the part: its temperature stays at the stress-free "
                                                     f"{inputs.reference_C:g} °C and no load reaches it, or it is free to grow"}
            found.append(found_one)
        if singular is not None:
            found.append(singular)
        thermal = result.scalars.get("thermal") or {}
        if thermal:
            hottest, coolest = thermal["max_temperature_C"], thermal["min_temperature_C"]
            rise = max(abs(hottest - inputs.reference_C), abs(coolest - inputs.reference_C))
            found.append(finding(
                "info", "heated", f"Heated to {hottest:g} °C at most (coolest {coolest:g} °C), against a stress-free "
                                  f"{inputs.reference_C:g} °C: up to {rise:.3g} °C of change drives the stress",
                "the temperature comes from the thermal solve of the same study, on the same mesh",
                [{"text": "the hottest point", "ref": None, "at": thermal["max_at_mm"]}]))
        return found

    # -- what is written -----------------------------------------------------------------------------

    def summary(self, result: AnalysisResult, inputs: ThermalStressInputs, check_results: list[dict]) -> dict:
        summary = super().summary(result, inputs, check_results)
        temperature = result.fields["temperature"]
        checks = summary.pop("checks")
        away = self.read_away(result)
        if away is not None:
            # The safety factor is the strength check's: the stress a little away from the singular clamped edge.
            from cadgen._internal.fea import checks as judged

            solved, peak = away
            summary["safety_factor"] = floored(judged.safety_factor(solved))
            summary["judged_away_from_edge_MPa"] = round(solved.peak_MPa, 4)
        summary.update({
            "max_temperature_C": round(float(temperature.max()), 4),
            "min_temperature_C": round(float(temperature.min()), 4),
            "reference_C": round(inputs.reference_C, 4),
            "checks": checks,
        })
        return summary

    def field_ranges(self, summary: dict, result: AnalysisResult) -> dict[str, tuple]:
        """Static's, the stress colours stopped at the 99th percentile where the peak is a spike above it (a clamped
        edge, where heat stress is singular: the part would read all blue), and the temperatures'."""
        return {**super().field_ranges(summary, result),
                "von_mises": colour_range(result.fields["von_mises"], summary["max_von_mises_MPa"]),
                "temperature": (summary["min_temperature_C"], summary["max_temperature_C"])}

    def study_echo(self, inputs: ThermalStressInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        return {**super().study_echo(inputs, bare), **heat_echo(inputs.thermal, bare), "reference_C": inputs.reference_C}

    def human_lines(self, summary: dict) -> list[str]:
        factor = summary.get("safety_factor")
        away = summary.get("judged_away_from_edge_MPa")
        lines = [
            f"max von Mises {summary['max_von_mises_MPa']:g} MPa at {summary['max_von_mises_at_mm']} mm "
            + (f"on the clamped edge (singular); judged {away:g} MPa a little away from it " if away is not None else "")
            + f"(yield {summary['yield_MPa']:g} MPa" + (f", safety factor {factor:g})" if factor is not None else ")"),
            f"max displacement {summary['max_displacement_mm']:g} mm at {summary['max_displacement_at_mm']} mm",
            f"temperatures {summary['min_temperature_C']:g} to {summary['max_temperature_C']:g} °C, "
            f"stress-free at {summary['reference_C']:g} °C",
        ]
        if any(summary["applied_force_N"]):
            lines.append(f"applied {summary['applied_force_N']} N, reactions {summary['reaction_force_N']} N")
        return lines
