"""Shock: the peak response of a held part to a shock given as a shock response spectrum (SRS).

The study (spec 5.10) holds the part at its ``fixtures`` and gives the shock
as a spectrum (``srs``): a table of [frequency Hz, peak acceleration g] along
``direction``, computed at ``damping_ratio`` (default 0.05, a Q of 10). The
spectrum is read between its rows log-log, and held at its end values outside
them.

The answer is a response-spectrum analysis by modal superposition
(:mod:`cadgen._internal.fea.superposition`) over every mode up to the
spectrum's top frequency:

- each mode i answers on its own with its peak modal coordinate
  qᵢ = Γᵢ·S_a(fᵢ)/ωᵢ², Γᵢ its participation along the shock;
- each displacement component and each stress component is combined over
  the modes, by SRSS (√Σ Rᵢ²) or by CQC (√ΣΣ ρᵢⱼ Rᵢ Rⱼ, the Der Kiureghian
  correlation coefficients), and von Mises is taken from the combined stress
  components;
- the fields are envelopes: ``von_mises`` and ``displacement``. A combined
  component has no sign; the displacement shown takes, per component, the
  sign of the mode that contributes most there, so the shape reads right.

Checks ``stress`` (labelled "Shock" unless it is given a label) and
``displacement``. The ladder's own rung is ``reduce_modes``: only the fewest
modes holding 90 % of the mass moving along the shock are kept, and its words
say the share and the mass left out (the missing mass). The mesh rungs are the
shared ones. Stdlib only at import.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import AnalysisResult, FieldSpec, Inputs, SolveContext, by_severity, finding, xyz, yield_at
from cadgen._internal.fea.analyses.modal import (
    G0_MM_S2, KEEP_SHARE, ModalResponse, axis_words, converged_finding, displacement_judged, finish_summary,
    fixtures_echo, hz_text, kept_step, mass_enough, modes_estimate, modes_line, modes_text, response_findings,
    response_modes, response_operators, scaled_deformation, stress_scalars, unit_vector,
)
from cadgen._internal.fea.study import VIEW_DRIVES, VIEW_FIELDS, Fixture, parse_fixtures

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "COMBINATIONS", "ShockAnalysis", "ShockInputs", "combine_modal", "cqc_coefficients", "spectrum_g", "spectrum_words",
]

#: The ways the modes' peaks are combined.
COMBINATIONS = ("srss", "cqc")
#: Under this share of the mass along the shock, the modes used may miss part of the response: a finding.
MASS_WARNING = 0.9
#: Two modes closer than this (relative) are closely spaced: SRSS may read them wrong, CQC does not.
CLOSE_SPACING = 0.1
#: A mode carrying under this share of the mass along the shock is too small to matter for spacing.
SPACING_MASS = 0.01
_TWO_PI = 2.0 * math.pi


@dataclass(frozen=True)
class ShockInputs(Inputs):
    fixtures: tuple[Fixture, ...] = ()
    #: The shock's direction, a unit vector.
    direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
    #: The spectrum, ((Hz, g), ...) with the frequencies rising.
    table: tuple[tuple[float, float], ...] = ()
    #: The damping the spectrum was computed at; the modes are damped the same.
    damping_ratio: float = 0.05
    #: "srss" or "cqc".
    combination: str = "srss"

    @property
    def top_Hz(self) -> float:
        """The highest mode the response is built from: the spectrum's top frequency."""
        return self.table[-1][0]


def _table(raw, where: str) -> tuple[tuple[float, float], ...]:
    example = "like [[10, 5], [100, 50], [2000, 50]] (Hz, peak g)"
    if not isinstance(raw, list) or len(raw) < 2:
        raise ValueError(f"{where}: the shock response spectrum as at least two [frequency Hz, peak g] rows, {example}")
    rows = []
    for index, row in enumerate(raw):
        if not isinstance(row, list) or len(row) != 2:
            raise ValueError(f"{where}[{index}]: a row is [frequency Hz, peak g], {example}")
        rows.append((kinds.number(row[0], where=f"{where}[{index}][0]", positive=True),
                     kinds.number(row[1], where=f"{where}[{index}][1]", positive=True)))
    for index in range(1, len(rows)):
        if not rows[index][0] > rows[index - 1][0]:
            raise ValueError(f"{where}[{index}]: the frequencies must rise from row to row "
                             f"({rows[index][0]:g} Hz follows {rows[index - 1][0]:g} Hz)")
    return tuple(rows)


def spectrum_g(table, f_Hz):
    """S_a in g at ``f_Hz`` (a number or an array): log-log between the rows, held at the end values outside them."""
    import numpy as np

    f = np.asarray(table, dtype=float)
    x = np.log(np.maximum(np.asarray(f_Hz, dtype=float), 1e-300))
    out = np.exp(np.interp(x, np.log(f[:, 0]), np.log(f[:, 1])))
    return float(out) if np.ndim(out) == 0 else out


def spectrum_words(table, direction, damping_ratio: float) -> str:
    """The spectrum as a person says it: "50 g above 100 Hz along Z, 5% damping", "up to 30 g at 400 Hz along X, ..."."""
    peak = max(g for _, g in table)
    at_peak = [g >= peak * (1 - 1e-9) for _, g in table]
    # The plateau is the run of rows at the peak from its first; a peak reached twice is said by its first run.
    first = at_peak.index(True)
    last = first
    while last + 1 < len(table) and at_peak[last + 1]:
        last += 1
    g = f"{peak:.3g} g"
    fa, fb = table[first][0], table[last][0]
    if first == last:
        level = f"up to {g} at {fa:g} Hz"
    elif last == len(table) - 1 and first > 0:
        level = f"{g} above {fa:g} Hz"
    elif first == 0 and last == len(table) - 1:
        level = f"{g} from {fa:g} to {fb:g} Hz"
    elif first == 0:
        level = f"{g} up to {fb:g} Hz"
    else:
        level = f"{g} from {fa:g} to {fb:g} Hz"
    return f"{level} along {axis_words(direction)}, {damping_ratio * 100:g}% damping"


def cqc_coefficients(omega: "np.ndarray", zeta) -> "np.ndarray":
    """ρᵢⱼ, (m, m): the Der Kiureghian (1981) correlation of two modes' peaks, for modal damping ``zeta`` (one or per mode).

    ρᵢⱼ = 8 √(ζᵢζⱼ) (ζᵢ + r ζⱼ) r^{3/2} / ((1 − r²)² + 4 ζᵢ ζⱼ r (1 + r²) + 4 (ζᵢ² + ζⱼ²) r²), r = ωⱼ/ωᵢ:
    1 on the diagonal, near 0 for modes far apart."""
    import numpy as np

    w = np.asarray(omega, dtype=float)
    z = np.broadcast_to(np.asarray(zeta, dtype=float), w.shape)
    wi, wj = w[:, None], w[None, :]
    zi, zj = z[:, None], z[None, :]
    r = wj / np.where(wi > 0, wi, 1e-300)
    numerator = 8.0 * np.sqrt(zi * zj) * (zi + r * zj) * r ** 1.5
    denominator = (1 - r ** 2) ** 2 + 4 * zi * zj * r * (1 + r ** 2) + 4 * (zi ** 2 + zj ** 2) * r ** 2
    rho = numerator / np.where(denominator > 0, denominator, 1e-300)
    np.fill_diagonal(rho, 1.0)
    return rho


def combine_modal(per_mode: "np.ndarray", rho: "np.ndarray | None" = None) -> "np.ndarray":
    """The peaks of per-mode responses (m, ...) combined over the modes: SRSS with ``rho`` None, else CQC."""
    import numpy as np

    flat = np.asarray(per_mode, dtype=float).reshape(per_mode.shape[0], -1)
    if rho is None:
        squared = np.einsum("ij,ij->j", flat, flat)
    else:
        squared = np.einsum("ij,ij->j", flat, rho @ flat)
    return np.sqrt(np.maximum(squared, 0.0)).reshape(per_mode.shape[1:])


class ShockAnalysis(ModalResponse):
    name: ClassVar[str] = "shock"
    word: ClassVar[str] = "Shock"
    extras_word: ClassVar[str] = "shock"
    study_keys: ClassVar[frozenset[str]] = frozenset({"fixtures", "srs", "combination"})
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("von_mises", "_VON_MISES", "von Mises stress (peak, modes combined)", "MPa"),
        FieldSpec("displacement", "_DISPLACEMENT", "displacement (peak, relative to the base)", "mm", 3, 1000.0),
    )
    checks: ClassVar[tuple] = (dataclasses.replace(kinds.STRESS, default_label="Shock"), kinds.DISPLACEMENT)
    default_checks: ClassVar[tuple[dict, ...]] = ({"kind": "stress", "label": "Shock"},)
    drives: ClassVar[tuple[str, ...]] = VIEW_DRIVES
    default_controls: ClassVar[dict[str, dict]] = {
        "field": {"drives": "field", "type": "enum", "options": list(VIEW_FIELDS)},
        "deformation": {"drives": "deformation", "type": "number", "min": 0.0, "max": None},
    }
    ladder: ClassVar[tuple[str, ...]] = ("reduce_modes", "iterative", "local_refine", "defeature", "linear_elements", "symmetry")
    noun: ClassVar[str] = "this shock"
    governing_word: ClassVar[str] = "peak stress"

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> ShockInputs:
        fixtures = parse_fixtures(document)
        raw = document.get("srs")
        if not isinstance(raw, dict):
            raise ValueError('srs: the shock as a response spectrum, like {"direction": [0, 0, 1], '
                             '"table": [[10, 5], [100, 50], [2000, 50]], "damping_ratio": 0.05} (Hz, peak g)')
        unknown = set(raw) - {"direction", "table", "damping_ratio"}
        if unknown:
            raise ValueError(f"srs: unknown keys {sorted(unknown)}; a spectrum takes direction, table and damping_ratio")
        if "direction" not in raw:
            raise ValueError("srs.direction: the direction of the shock as [x, y, z], like [0, 0, 1] for Z")
        direction = tuple(c + 0.0 for c in unit_vector(raw["direction"], "srs.direction", "of the shock", "the shock comes along"))
        table = _table(raw.get("table"), "srs.table")
        zeta = kinds.number(raw.get("damping_ratio", 0.05), where="srs.damping_ratio", positive=True)
        if zeta >= 1:
            raise ValueError(f"srs.damping_ratio: the damping the spectrum was computed at, a share of critical like 0.05 "
                             f"(a Q of 10); must be under 1, got {zeta:g}")
        combination = document.get("combination", "srss")
        if combination not in COMBINATIONS:
            raise ValueError(f"combination: {kinds.json_text(combination)} is not one of \"srss\" (the square root of the "
                             "sum of squares) or \"cqc\" (complete quadratic combination, for modes close together)")
        refs = tuple(dict.fromkeys(ref for fixture in fixtures for ref in fixture.faces))
        return ShockInputs(refs, refs, True, fixtures=fixtures, direction=direction, table=table, damping_ratio=zeta,
                           combination=combination)

    # -- the ladder (fit.py drives these) -------------------------------------------------------------

    def estimate(self, ctx: SolveContext, inputs: ShockInputs):
        """Finding the modes (:func:`modal.eigen_estimate`), then per kept mode its stress (six components per node) and
        shape, and the combination (CQC is a product over every pair of modes)."""
        from cadgen._internal.fea import fit

        modes, base, nodes, seconds = modes_estimate(ctx)
        pairs = modes * modes if inputs.combination == "cqc" else modes
        memory = base.memory_bytes + 8.0 * 2 * 9 * nodes * modes + 8.0 * 12 * nodes
        seconds = seconds + 2e-9 * pairs * 9 * nodes
        return fit.Estimate(dofs=base.dofs, memory_bytes=int(memory), seconds=float(seconds))

    # -- solve ---------------------------------------------------------------------------------------

    def solve(self, ctx: SolveContext, inputs: ShockInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea import superposition as sp

        space = ctx.space
        plan = getattr(ctx, "plan", None)
        timings: dict[str, float] = {}
        warnings: list[str] = []
        started = time.perf_counter()
        materials, K, M, held = response_operators(ctx, inputs.fixtures)
        r = sp.rigid_translation(space.component, inputs.direction)
        Mr = M @ r
        total_along = float(r @ Mr)
        timings["assemble_s"] = time.perf_counter() - started

        reduce = "reduce_modes" in (getattr(plan, "taken", None) or ())
        enough = mass_enough(Mr, total_along) if reduce else None
        found = response_modes(ctx, K, M, held, inputs.top_Hz, enough, timings, warnings, "shock")

        fractions = sp.mass_fractions(found, M, space.component)
        gamma = sp.participation(found, M, r)
        masses = gamma ** 2
        along_share = float(masses.sum() / total_along) if total_along > 0 else 0.0
        kept = np.arange(len(found))
        kept_share = None
        reduce_step = None
        if reduce:
            kept, kept_share = sp.fewest_modes(masses, total_along, KEEP_SHARE)
            reduce_step = self._reduce_step(inputs, found, kept, kept_share)
        used = found.take(kept)
        used_gamma = gamma[kept]
        used_share = float(masses[kept].sum() / total_along) if total_along > 0 else 0.0

        # Each mode's peak: q = Γ·S_a(f)/ω², the spectrum read at the mode's own frequency.
        srs_g = np.atleast_1d(spectrum_g(inputs.table, used.frequencies_Hz))
        q = used_gamma * srs_g * G0_MM_S2 / np.maximum(used.omega ** 2, 1e-300)

        started = time.perf_counter()
        shapes = sp.nodal_shapes(space, used)                       # (m, nodes, 3)
        stresses = sp.modal_stresses(space, materials, used)        # (m, 6, nodes)
        timings["stress_s"] = time.perf_counter() - started

        started = time.perf_counter()
        rho = cqc_coefficients(used.omega, inputs.damping_ratio) if inputs.combination == "cqc" else None
        per_mode = shapes * q[:, None, None]
        combined = combine_modal(per_mode, rho)                     # (nodes, 3), each component's peak
        # Signs are lost in the combination: each component takes the sign of the mode that moves it most, so the shape reads.
        dominant = np.abs(per_mode).argmax(axis=0)
        sign = np.sign(np.take_along_axis(per_mode, dominant[None], axis=0)[0])
        displacement = combined * np.where(sign == 0, 1.0, sign)
        magnitude = np.linalg.norm(combined, axis=1)
        von_mises = sp.von_mises_of(combine_modal(stresses * q[:, None, None], rho))
        timings["combine_s"] = time.perf_counter() - started

        # What each mode alone does: the most it moves the part, and its share of the combined peak stress.
        mode_peak_mm = np.abs(q) * np.linalg.norm(shapes, axis=2).max(axis=1) if len(used) else np.zeros(0)
        below = [int(i) for i, f in zip(kept, used.frequencies_Hz) if f < inputs.table[0][0]]
        started = time.perf_counter()
        motion, motion_attributes = self._motion(inputs, used, used_gamma, shapes, mode_peak_mm)
        timings["motion_s"] = time.perf_counter() - started
        closely = self._close_pairs(used.frequencies_Hz, masses[kept] / max(total_along, 1e-300), kept)
        return AnalysisResult(
            dof_locations=space.dof_locations,
            vertices=space.vertices,
            tets=space.tets,
            boundary_quadratic=space.boundary_quadratic,
            element_dofs=space.element_dofs,
            fields={"von_mises": von_mises, "displacement": displacement},
            deformation=displacement,
            scalars={
                "frequencies_Hz": [float(f) for f in found.frequencies_Hz],
                "effective_mass_fractions": fractions.tolist(),
                "participation": [float(g) for g in gamma],
                "effective_mass_kg": [float(m) * 1000.0 for m in masses],
                "used_modes": [int(i) for i in kept],
                "srs_g": {int(i): float(g) for i, g in zip(kept, srs_g)},
                "modal_peak_mm": {int(i): float(v) for i, v in zip(kept, mode_peak_mm)},
                "kept_share": kept_share,
                "reduce_step": reduce_step,
                "along_share": along_share,
                "used_share": used_share,
                "modes_below_spectrum": below,
                "close_pairs": closely,
                "direction": list(inputs.direction),
                "searched_Hz": found.searched_Hz,
                "complete": found.complete,
                "total_mass_kg": float(sum((M @ (space.component == 0).astype(float)))) * 1000.0,
                "displacement_magnitude": magnitude,
                "analysis_warnings": [],
                # What the viewer's Shock routine plays: a base pulse matched to the spectrum and the modes ringing.
                "analysis_extras": {"motion": motion} if motion else {},
                "motion_attributes": motion_attributes,
                **stress_scalars(ctx),
            },
            dofs=int(space.dofs),
            solver=f"{found.how}; response spectrum, {modes_text(len(used))} combined by {inputs.combination.upper()}",
            timings=timings,
            warnings=warnings,
        )

    @staticmethod
    def _motion(inputs: ShockInputs, used, gamma, shapes, mode_peak_mm) -> tuple[dict | None, dict]:
        """What the viewer plays: a base pulse whose response spectrum matches the study's (wavelets,
        :func:`motion.srs_pulse`) from a quarter of the slowest mode played up to the spectrum's top, and each of the
        modes that move the part most answering it exactly (:func:`motion.sdof`), from rest until its ringing has died
        to about 2 % (four time constants of the slowest). A spectrum gives each mode's peak, not a time history; this
        is one history that has that spectrum, and the record says how closely it meets it at each mode."""
        import numpy as np

        from cadgen._internal.fea import motion

        if not len(used):
            return None, {}
        chosen = motion.choose_modes(np.asarray(mode_peak_mm) ** 2)
        frequencies = used.frequencies_Hz[chosen]
        top = float(inputs.table[-1][0])
        low = min(max(float(inputs.table[0][0]), float(frequencies.min()) / 4.0), top / 2.0)
        zeta = inputs.damping_ratio
        tail = 4.0 / (zeta * _TWO_PI * float(frequencies.min()))
        fine = 1.0 / (20.0 * max(top, float(frequencies.max())))
        t, a, _, _ = motion.srs_pulse(inputs.table, zeta, (low, top), step_s=fine, tail_s=tail)
        step, count, kept = motion.sampling(frequencies, float(t[-1]))
        chosen = [chosen[i] for i in np.argsort(frequencies)[:kept]]
        times = np.arange(count) * step
        attributes, peaks = motion.mode_attributes(shapes[chosen])
        modes = []
        match = []
        for k, (name, i) in enumerate(zip(attributes, chosen)):
            omega = float(used.omega[i])
            z = motion.sdof(a, fine, omega, zeta)
            modes.append({"attribute": name, "frequency_Hz": float(used.frequencies_Hz[i]),
                          "q_mm": np.interp(times, t, z) * float(gamma[i]) * peaks[k]})
            # The spectrum the pulse reaches at this mode against the study's.
            reached = float(motion.srs_of(a, fine, [used.frequencies_Hz[i]], zeta)[0]) / G0_MM_S2
            match.append(round(reached / spectrum_g(inputs.table, float(used.frequencies_Hz[i])), 4))
        record = motion.shake_record(
            label="Shock", step_s=step, base_g=np.interp(times, t, a) / G0_MM_S2, modes=modes, direction=inputs.direction,
            words="a pulse matched to the spectrum", band_Hz=[round(low, 3), round(top, 3)], match=match,
            pulse_s=round(float(t[-1]) - tail, 9), peak_g=round(float(np.abs(a).max()) / G0_MM_S2, 4))
        return record, attributes

    @staticmethod
    def _close_pairs(frequencies, shares, indices) -> list[list[int]]:
        """Pairs of modes (0-based, as found) within 10 % of each other, both moving at least 1 % of the mass along the shock."""
        pairs = []
        order = [k for k in range(len(frequencies)) if shares[k] >= SPACING_MASS]
        for a, b in zip(order, order[1:]):
            fa, fb = float(frequencies[a]), float(frequencies[b])
            if fa > 0 and (fb - fa) / fa < CLOSE_SPACING:
                pairs.append([int(indices[a]), int(indices[b])])
        return pairs

    @staticmethod
    def _reduce_step(inputs: ShockInputs, found, kept, share: float) -> dict:
        """reduce_modes' words once the modes are found, and the mass left out (the missing mass), for
        :meth:`settle_steps`."""
        along = axis_words(inputs.direction)
        missing = max(0.0, 1.0 - share)
        kept_words = kept_step(found, kept, share, f"of the mass moving along {along}")
        accuracy = (f"the other {missing * 100:.0f}% of the mass along {along}, the missing mass, is left out, "
                    "so the peak may read a little low") if missing >= 0.005 else None
        return {"words": kept_words["words"], "accuracy": accuracy,
                "accuracy_pct": round(missing * 100, 2) if accuracy else None,
                "detail": {**kept_words["detail"], "missing_mass_share": round(missing, 4)}}

    # -- checks, findings ----------------------------------------------------------------------------

    def _peak(self, result: AnalysisResult, inputs=None) -> tuple[int, float]:
        values = result.fields["von_mises"]
        node = int(values.argmax())
        return node, float(values[node])

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult, inputs: ShockInputs) -> dict:
        if check["kind"] == "stress":
            return self._yield_check(ctx, result, inputs, check.get("label") or "Shock")
        return displacement_judged(ctx, result, result.scalars["displacement_magnitude"], check, index)[0]

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: ShockInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        scalars = result.scalars
        whole = "the assembly" if assembly else "the part"
        along = axis_words(inputs.direction)
        found = response_findings(
            check_results, "shock", lambda check: "In this shock", whole,
            lambda check: f"peak von Mises of the modes combined by {inputs.combination.upper()}, {check['value']:.4g} MPa")
        frequencies = scalars["frequencies_Hz"]
        used = scalars["used_modes"]
        peaks = scalars["modal_peak_mm"]
        if used:
            top = max(used, key=lambda i: peaks[i])
            magnitude = scalars["displacement_magnitude"]
            node = int(magnitude.argmax())
            found.append(finding(
                "info", "shock_response",
                f"Mode {top + 1} ({hz_text(frequencies[top])}) carries most of the shock: the spectrum gives "
                f"{scalars['srs_g'][top]:.3g} g there, and {whole} moves up to {float(magnitude.max()):.3g} mm",
                "the mode whose own peak motion is largest; a response spectrum gives each mode's peak, "
                "not when it happens", [{"text": "moves most here", "ref": None, "at": xyz(result.dof_locations[node])}],
            ))
        share = scalars["used_share"]
        if share < MASS_WARNING:
            found.append(finding(
                "warning", "modes_missing_mass",
                f"The modes used move only {share * 100:.0f}% of the mass along {along}: the rest answers in modes above "
                f"{hz_text(scalars['searched_Hz'])}, so the stress may read a little low",
                f"effective mass of the modes used {share:.3f} of the total along {along}, under 0.9; a fixed face's own "
                "mass never moves, and modes above the spectrum's top frequency are left out (no missing-mass correction)",
                [],
            ))
        below = scalars["modes_below_spectrum"]
        if below:
            low = inputs.table[0]
            found.append(finding(
                "info", "modes_below_spectrum",
                f"Mode {below[0] + 1} ({hz_text(frequencies[below[0]])}) is below the spectrum's lowest frequency, "
                f"{low[0]:g} Hz: it was given that row's {low[1]:g} g",
                "the spectrum is held at its end values outside its rows; extend the table down if that is not right", [],
            ))
        pairs = scalars["close_pairs"]
        if pairs and inputs.combination == "srss":
            a, b = pairs[0]
            found.append(finding(
                "warning", "closely_spaced_modes",
                f"Modes {a + 1} and {b + 1} ({hz_text(frequencies[a])} and {hz_text(frequencies[b])}) are within 10% of "
                'each other: SRSS can misjudge modes this close; "combination": "cqc" accounts for them',
                "SRSS assumes the modes peak independently, which holds only for well-separated modes", [],
            ))
        if coarser := scalars.get("coarser_peak_MPa"):
            found.append(converged_finding("The peak stress", self._peak(result)[1], coarser))
        return by_severity(found)

    # -- what is written -----------------------------------------------------------------------------

    def deformation_scale(self, result: AnalysisResult, bbox_diagonal: float, requested: float | None) -> float | None:
        return scaled_deformation(float(result.scalars["displacement_magnitude"].max()), bbox_diagonal, requested)

    def summary(self, result: AnalysisResult, inputs: ShockInputs, check_results: list[dict]) -> dict:
        scalars = result.scalars
        s_node, s_peak = self._peak(result)
        magnitude = scalars["displacement_magnitude"]
        d_node = int(magnitude.argmax())
        yield_MPa = yield_at(scalars, s_node)
        factor = yield_MPa / s_peak if s_peak > 0 else None
        used = set(scalars["used_modes"])
        axis = max(range(3), key=lambda c: abs(inputs.direction[c]))
        modes = []
        for i, (f, share) in enumerate(zip(scalars["frequencies_Hz"], scalars["effective_mass_fractions"])):
            entry: dict[str, Any] = {
                "mode": i + 1, "frequency_Hz": round(f, 4),
                "participation": round(scalars["participation"][i] * math.sqrt(1000.0), 6),
                "effective_mass_kg": round(scalars["effective_mass_kg"][i], 9),
                "effective_mass_fraction": [round(x, 4) for x in share], "used": i in used,
            }
            if i in used:
                entry["srs_g"] = round(scalars["srs_g"][i], 4)
                entry["peak_mm"] = round(scalars["modal_peak_mm"][i], 9)
            modes.append(entry)
        summary: dict[str, Any] = {
            "srs": {"direction": list(inputs.direction), "table": [list(row) for row in inputs.table],
                    "damping_ratio": inputs.damping_ratio},
            "spectrum": spectrum_words(inputs.table, inputs.direction, inputs.damping_ratio),
            "combination": inputs.combination,
            "max_von_mises_MPa": round(s_peak, 4),
            "max_von_mises_at_mm": xyz(result.dof_locations[s_node]),
            "yield_MPa": yield_MPa,
            "safety_factor": None if factor is None else math.floor(factor * 1000) / 1000,
            "max_displacement_mm": round(float(magnitude[d_node]), 6),
            "max_displacement_at_mm": xyz(result.dof_locations[d_node]),
            "modes": modes,
            "modes_used": len(used),
            "modes_up_to_Hz": round(inputs.top_Hz, 4),
            "effective_mass_fraction": round(scalars["used_share"], 4),
            "effective_mass_axis": "XYZ"[axis],
            "total_mass_kg": round(scalars["total_mass_kg"], 6),
            "deformation_scale": scalars["deformation_scale"],
        }
        return finish_summary(summary, scalars, check_results, kept_share=True)

    def field_ranges(self, summary: dict, result: AnalysisResult) -> dict[str, tuple[float, float]]:
        return {"von_mises": (0.0, summary["max_von_mises_MPa"]), "displacement": (0.0, summary["max_displacement_mm"])}

    def extras_head(self, summary: dict) -> dict:
        return {"safety_factor": summary["safety_factor"]}

    def study_echo(self, inputs: ShockInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        return {
            "fixtures": fixtures_echo(inputs.fixtures, bare),
            "loads": [],
            "srs": {"direction": list(inputs.direction), "table": [list(row) for row in inputs.table],
                    "damping_ratio": inputs.damping_ratio},
            "combination": inputs.combination,
        }

    def human_lines(self, summary: dict) -> list[str]:
        return [
            f"shock of {summary['spectrum']}; modes combined by {summary['combination'].upper()}",
            f"peak stress {summary['max_von_mises_MPa']:.4g} MPa; moves up to {summary['max_displacement_mm']:.4g} mm",
            modes_line(summary),
        ]
