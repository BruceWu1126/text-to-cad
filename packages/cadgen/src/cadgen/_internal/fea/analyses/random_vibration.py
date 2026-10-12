"""Random vibration: the part's response to a random shake given as a PSD, at 1σ and 3σ.

The study (spec 5.9) holds the part at its ``fixtures`` and shakes them
together along ``psd.direction`` with a random acceleration whose power
spectral density is ``psd.table``: rows of ``[Hz, g²/Hz]``, interpolated
log-log between rows (straight lines on the log-log plot every vibration spec
is drawn on) and zero outside the table. A transport or MIL-STD profile is
written as it is printed.

The answer is built by modal superposition (:mod:`cadgen._internal.fea.superposition`)
from every mode up to 1.5 × the table's top, with modal damping
``damping_ratio`` (default 0.02):

- each mode's motion relative to the base answers the shake through
  ``qᵢ = −Γᵢ Hᵢ(ω) a``, so the modal coordinates' cross-spectral density is
  ``S_qq(f) = (Γ⊙H)(Γ⊙H)ᴴ S_a(f)``;
- that is integrated over a log grid that puts 40 points across every mode's
  half-power bandwidth (2ζf) into one m × m covariance ``C = ∫ S_qq df``;
- a nodal displacement's variance is ``φᵀ C φ`` (summed over X, Y, Z), so
  ``displacement_rms`` is the RMS size of the motion;
- von Mises RMS is Segalman's quadratic form: with ``A`` the 6 × 6 matrix that
  makes ``σ_vm² = σᵀ A σ`` and ``S_σσ`` the stress's cross-spectral density,
  ``σ_vm,rms = sqrt(∫ tr(A S_σσ) df) = sqrt(Σᵢⱼ Cᵢⱼ ψᵢᵀ A ψⱼ)``, ψ each mode's
  stress (D. J. Segalman et al., "An efficient method for calculating RMS von
  Mises stress in a random vibration environment", J. Sound Vib. 230(2), 2000);
- the expected rate of up-crossings ν0+ = sqrt(m2/m0) comes from the same form
  with the covariance weighted by f² (``zero_crossing_Hz``, which fatigue reads).

Both fields are one standard deviation (1σ). The checks judge ``sigma`` times
them (default 3σ, a level a Gaussian response passes about 0.3 % of the time):
``stress`` (labelled "Random vibration") and ``displacement``.

The ladder's own rungs are ``reduce_modes`` (the fewest modes holding 90 % of
the mass moving along the shake) and ``frequency_grid``, a coarser frequency
grid (10 points per half-power bandwidth instead of 40), whose accuracy cost is
measured on a single resonance. The mesh rungs are the shared ones. Stdlib only
at import.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar

from cadgen._internal.fea.analyses import kinds
from cadgen._internal.fea.analyses.base import (
    AnalysisResult, FieldSpec, Inputs, SolveContext, by_severity, finding, xyz, yield_at,
)
from cadgen._internal.fea.analyses.modal import (
    G0_MM_S2, KEEP_SHARE, ModalResponse, axis_words, converged_finding, displacement_judged,
    finish_summary, fixtures_echo, hz_text, kept_step, mass_enough, missing_mass_finding, mode_rows, modes_estimate,
    modes_line, modes_text, no_resonance_finding, parse_damping, response_findings, response_modes, response_operators,
    stress_scalars, unit_vector,
)
from cadgen._internal.fea.study import Fixture, parse_fixtures

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "RandomVibrationAnalysis", "RandomVibrationInputs", "grid_error", "grms", "modal_covariance", "psd_at",
    "response_grid", "rms_displacement", "rms_von_mises", "von_mises_form",
]

#: Modes are found up to this multiple of the PSD table's top.
TOP_FACTOR = 1.5
#: Under this share of the mass along the shake, the modes found may miss part of the response: a finding.
MASS_WARNING = 0.8
#: Points across each mode's half-power bandwidth (2ζf): the grid's density, and after the coarser-grid rung.
POINTS_PER_BANDWIDTH = 40
COARSE_POINTS = 10
#: Whatever the damping, the grid has at least this many points over the table.
MIN_POINTS = 200
#: Frequencies whose modal transfer is formed at once, while integrating.
CHUNK = 2048
#: The most points a response curve in the sidecar keeps (the grid thinned evenly).
CURVE_POINTS = 600
SIGMAS = (1, 3)
_TWO_PI = 2.0 * math.pi


def von_mises_form():
    """A: σ_vm² = σᵀ A σ for σ stored (xx, yy, zz, xy, yz, xz)."""
    import numpy as np

    A = np.zeros((6, 6))
    A[:3, :3] = -0.5
    A[[0, 1, 2], [0, 1, 2]] = 1.0
    A[[3, 4, 5], [3, 4, 5]] = 3.0
    return A


@dataclass(frozen=True)
class RandomVibrationInputs(Inputs):
    fixtures: tuple[Fixture, ...] = ()
    #: The shake's direction, a unit vector.
    direction: tuple[float, float, float] = (0.0, 0.0, 1.0)
    #: Rows of (Hz, g²/Hz), frequencies rising.
    table: tuple[tuple[float, float], ...] = ()
    damping_ratio: float = 0.02
    #: The level the checks judge at: 1 or 3 standard deviations.
    sigma: int = 3

    @property
    def band_Hz(self) -> tuple[float, float]:
        return self.table[0][0], self.table[-1][0]

    @property
    def top_Hz(self) -> float:
        """The highest mode the response is built from: 1.5 × the table's top."""
        return TOP_FACTOR * self.table[-1][0]

    @property
    def grms(self) -> float:
        return grms(self.table)


# -- the PSD -----------------------------------------------------------------------------------------

def _slopes(table) -> list[float]:
    return [math.log(p1 / p0) / math.log(f1 / f0) for (f0, p0), (f1, p1) in zip(table, table[1:])]


def grms(table) -> float:
    """The input's RMS acceleration in g: the square root of the PSD's area, each log-log segment integrated
    exactly (S = S0 (f/f0)^s has area S0 f0 ((f1/f0)^(s+1) − 1)/(s + 1), or S0 f0 ln(f1/f0) at s = −1)."""
    area = 0.0
    for ((f0, p0), (f1, _)), s in zip(zip(table, table[1:]), _slopes(table)):
        log_ratio = math.log(f1 / f0)
        e = s + 1.0
        area += p0 * f0 * (log_ratio if abs(e * log_ratio) < 1e-12 else math.expm1(e * log_ratio) / e)
    return math.sqrt(area)


def psd_at(table, f_Hz) -> "np.ndarray":
    """The PSD (g²/Hz) at frequencies ``f_Hz``: log-log between the table's rows, zero outside it."""
    import numpy as np

    f = np.atleast_1d(np.asarray(f_Hz, dtype=float))
    freqs = np.array([row[0] for row in table])
    logs = np.log(np.array([row[1] for row in table]))
    out = np.zeros_like(f)
    # The ends within rounding are on the table (a log grid's exp(log 20) may be a hair under 20).
    inside = (f >= freqs[0] * (1 - 1e-12)) & (f <= freqs[-1] * (1 + 1e-12))
    out[inside] = np.exp(np.interp(np.log(f[inside]), np.log(freqs), logs))
    return out


def response_grid(table, zeta: float, points: int = POINTS_PER_BANDWIDTH) -> "np.ndarray":
    """Frequencies (Hz) the response PSD is integrated over: log-spaced across the table, ``points`` per
    half-power bandwidth (a mode's 2ζf is the same width, 2ζ, on a log axis, so every mode gets the same
    count), at least :data:`MIN_POINTS`, with every row of the table on it."""
    import numpy as np

    low, high = table[0][0], table[-1][0]
    span = math.log(high / low)
    step = min(2.0 * zeta / points, span / MIN_POINTS)
    count = int(math.ceil(span / step)) + 1
    grid = np.unique(np.concatenate([np.exp(np.linspace(math.log(low), math.log(high), count)), [row[0] for row in table]]))
    # A row that falls on a grid point within rounding is that point.
    return grid[np.concatenate([[True], np.diff(np.log(grid)) > 1e-9])]


def trapezoid_weights(f: "np.ndarray") -> "np.ndarray":
    """Weights w with Σ w g(f) the trapezoid rule's ∫ g df over the points ``f``."""
    import numpy as np

    w = np.zeros(len(f))
    if len(f) > 1:
        d = np.diff(f)
        w[:-1] += 0.5 * d
        w[1:] += 0.5 * d
    return w


def grid_error(zeta: float, points: int) -> float:
    """The trapezoid rule's relative error on a grid of ``points`` per half-power bandwidth, for one resonance
    under a flat PSD: ∫ |H|² df on that grid against the same integral on a grid ten times finer, measured."""
    import numpy as np

    f_n = 1.0
    span = 60.0 * zeta  # ±30 bandwidths around the mode, in log

    def area(per: int) -> float:
        f = np.exp(np.arange(-span, span, 2.0 * zeta / per))
        w = _TWO_PI * f
        return float(trapezoid_weights(f) @ (1.0 / np.abs((_TWO_PI * f_n) ** 2 - w ** 2 + 2j * zeta * _TWO_PI * f_n * w) ** 2))

    coarse, fine = area(points), area(10 * points)
    return abs(coarse - fine) / fine


# -- modal superposition of a PSD --------------------------------------------------------------------

def modal_covariance(omega_modes: "np.ndarray", gamma: "np.ndarray", grid_Hz: "np.ndarray", psd_mm2_s4_Hz: "np.ndarray",
                     zeta: float, *, chunk: int = CHUNK) -> tuple["np.ndarray", "np.ndarray"]:
    """(C0, C2), each (m, m) real: the modal coordinates' covariance ∫ S_qq df and its second moment
    ∫ f² S_qq df, for a base shaken with acceleration PSD ``psd_mm2_s4_Hz`` ((mm/s²)²/Hz) on ``grid_Hz``.
    S_qq(f) = (Γ⊙H)(Γ⊙H)ᴴ S_a(f); its real part is what a real response reads (the imaginary part is odd)."""
    import numpy as np

    from cadgen._internal.fea import superposition as sp

    m = len(omega_modes)
    weights = trapezoid_weights(grid_Hz) * psd_mm2_s4_Hz
    C0 = np.zeros((m, m), dtype=complex)
    C2 = np.zeros((m, m), dtype=complex)
    for start in range(0, len(grid_Hz), chunk):
        stop = min(start + chunk, len(grid_Hz))
        G = sp.transfer(omega_modes, _TWO_PI * grid_Hz[start:stop], zeta) * gamma[None, :]   # (n, m)
        w = weights[start:stop]
        C0 += (G.conj().T * w[None, :]) @ G
        C2 += (G.conj().T * (w * grid_Hz[start:stop] ** 2)[None, :]) @ G
    return C0.real.copy(), C2.real.copy()


def rms_displacement(shapes: "np.ndarray", C: "np.ndarray") -> "np.ndarray":
    """(nodes,): the RMS size of each node's motion, sqrt(Σ_c φ_cᵀ C φ_c), ``shapes`` (m, nodes, 3) the modes per node."""
    import numpy as np

    if len(shapes) == 0:
        return np.zeros(shapes.shape[1])
    T = np.tensordot(C, shapes, axes=(1, 0))
    return np.sqrt(np.maximum(np.einsum("inc,inc->n", T, shapes), 0.0))


def rms_von_mises(stresses: "np.ndarray", C: "np.ndarray") -> "np.ndarray":
    """(nodes,): Segalman's RMS von Mises, sqrt(tr(A S_σσ)) = sqrt(Σᵢⱼ Cᵢⱼ ψᵢᵀ A ψⱼ), ``stresses`` (m, 6, nodes)."""
    import numpy as np

    if len(stresses) == 0:
        return np.zeros(stresses.shape[2])
    A = von_mises_form()
    AS = np.einsum("kl,iln->ikn", A, stresses)
    T = np.tensordot(C, stresses, axes=(1, 0))
    return np.sqrt(np.maximum(np.einsum("ikn,ikn->n", T, AS), 0.0))


def _thin(values, count: int) -> list[int]:
    n = len(values)
    if n <= count:
        return list(range(n))
    step = int(math.ceil(n / count))
    keep = list(range(0, n, step))
    if keep[-1] != n - 1:
        keep.append(n - 1)
    return keep


def _sigma_text(value: float, unit: str, sigma: int) -> str:
    return f"{value:.3g} {unit} at {sigma}σ"


class RandomVibrationAnalysis(ModalResponse):
    name: ClassVar[str] = "random_vibration"
    word: ClassVar[str] = "Random vibration"
    extras_word: ClassVar[str] = "random vibration"
    study_keys: ClassVar[frozenset[str]] = frozenset({"fixtures", "psd", "damping_ratio", "sigma"})
    fields: ClassVar[tuple[FieldSpec, ...]] = (
        FieldSpec("von_mises_rms", "_VON_MISES_RMS", "von Mises stress (RMS, 1σ)", "MPa"),
        FieldSpec("displacement_rms", "_DISPLACEMENT_RMS", "displacement (RMS, 1σ, relative to the base)", "mm"),
    )
    checks: ClassVar[tuple] = (kinds.STRESS, kinds.DISPLACEMENT)
    default_checks: ClassVar[tuple[dict, ...]] = ({"kind": "stress"},)
    drives: ClassVar[tuple[str, ...]] = ("field", "load_scale", "threshold", "sigma")
    default_controls: ClassVar[dict[str, dict]] = {
        "field": {"drives": "field", "type": "enum", "options": ["von_mises_rms", "displacement_rms"]},
        "sigma": {"drives": "sigma", "type": "enum", "options": [1, 3], "default": 3},
    }
    ladder: ClassVar[tuple[str, ...]] = (
        "reduce_modes", "frequency_grid", "iterative", "local_refine", "defeature", "linear_elements", "symmetry",
    )
    noun: ClassVar[str] = "this shake"
    governing_word: ClassVar[str] = "RMS stress"
    stress_label: ClassVar[str] = "Random vibration"
    envelope: ClassVar[str] = "von_mises_rms"

    # -- parse ---------------------------------------------------------------------------------------

    def parse(self, document: dict) -> RandomVibrationInputs:
        fixtures = parse_fixtures(document)
        raw = document.get("psd")
        if not isinstance(raw, dict):
            raise ValueError('psd: the random shake, like {"direction": [0, 0, 1], "table": [[20, 0.01], [80, 0.04], '
                             '[350, 0.04], [2000, 0.007]]} (Hz against g²/Hz)')
        unknown = set(raw) - {"direction", "table"}
        if unknown:
            raise ValueError(f"psd: unknown keys {sorted(unknown)}; a psd takes direction and table")
        unit = unit_vector(raw.get("direction"), "psd.direction")
        table = raw.get("table")
        if not isinstance(table, list) or len(table) < 2:
            raise ValueError("psd.table: at least two rows of [Hz, g²/Hz], like [[20, 0.01], [2000, 0.01]]")
        rows: list[tuple[float, float]] = []
        for index, row in enumerate(table):
            where = f"psd.table[{index}]"
            if not isinstance(row, list) or len(row) != 2:
                raise ValueError(f"{where}: a row is [Hz, g²/Hz], like [80, 0.04]")
            f = kinds.number(row[0], where=f"{where}[0]", positive=True)
            p = kinds.number(row[1], where=f"{where}[1]", positive=True)
            if rows and not f > rows[-1][0]:
                raise ValueError(f"{where}: frequencies must rise from row to row; {f:g} Hz follows {rows[-1][0]:g} Hz")
            rows.append((f, p))
        zeta = parse_damping(document)
        sigma = document.get("sigma", 3)
        if isinstance(sigma, bool) or sigma not in SIGMAS:
            raise ValueError(f"sigma: the level the checks judge at, 1 or 3 (standard deviations), got {kinds.json_text(sigma)}")
        refs = tuple(dict.fromkeys(ref for fixture in fixtures for ref in fixture.faces))
        return RandomVibrationInputs(refs, refs, True, fixtures=fixtures, direction=unit, table=tuple(rows),
                                     damping_ratio=zeta, sigma=int(sigma))

    # -- the ladder (fit.py drives these) -------------------------------------------------------------

    def _grid_points(self, ctx, inputs: RandomVibrationInputs) -> int:
        plan = getattr(ctx, "plan", None)
        per = COARSE_POINTS if getattr(plan, "frequency_grid", False) else POINTS_PER_BANDWIDTH
        low, high = inputs.band_Hz
        span = math.log(high / low)
        return int(span / min(2.0 * inputs.damping_ratio / per, span / MIN_POINTS)) + 1

    def estimate(self, ctx: SolveContext, inputs: RandomVibrationInputs):
        """Finding the modes (:func:`modal.eigen_estimate`), each kept mode's stress (six components per node)
        and shape, the frequency grid's m × m covariance, and the two quadratic forms over every node."""
        from cadgen._internal.fea import fit

        modes, base, nodes, seconds = modes_estimate(ctx)
        grid = self._grid_points(ctx, inputs)
        memory = base.memory_bytes + 8.0 * 9 * nodes * modes * 2 + 16.0 * min(grid, CHUNK) * modes * 2 + 8.0 * 4 * nodes
        seconds = seconds + 2e-8 * grid * modes * modes + 4e-9 * 2 * 9 * modes * modes * nodes
        return fit.Estimate(dofs=base.dofs, memory_bytes=int(memory), seconds=float(seconds))

    def apply(self, rung, ctx: SolveContext, inputs: RandomVibrationInputs):
        """``frequency_grid``: a coarser frequency grid, its accuracy measured on one resonance; the rest
        :meth:`ModalResponse.apply`."""
        from cadgen._internal.fea import fit

        plan = ctx.plan
        if rung == "frequency_grid":
            if plan.frequency_grid:
                return None
            plan.frequency_grid = True
            error = grid_error(inputs.damping_ratio, COARSE_POINTS) * 100.0
            moved = "under 0.1%" if error < 0.1 else f"about {error:.2g}%"
            return fit.Step(
                "frequency_grid",
                f"Integrated the response on a coarser frequency grid, {COARSE_POINTS} points across each resonance "
                f"instead of {POINTS_PER_BANDWIDTH}, to fit",
                f"a single resonance's RMS moves {moved} on the coarser grid", error,
                detail={"points_per_bandwidth": COARSE_POINTS, "from_points_per_bandwidth": POINTS_PER_BANDWIDTH},
            )
        return super().apply(rung, ctx, inputs)

    # -- solve ---------------------------------------------------------------------------------------

    def solve(self, ctx: SolveContext, inputs: RandomVibrationInputs) -> AnalysisResult:
        import time

        import numpy as np

        from cadgen._internal.fea import superposition as sp

        space = ctx.space
        plan = getattr(ctx, "plan", None)
        timings: dict[str, float] = {}
        warnings: list[str] = []
        started = time.perf_counter()
        materials, K, M, held = response_operators(ctx, inputs.fixtures)
        zeta = inputs.damping_ratio
        direction = np.asarray(inputs.direction, dtype=float)
        r = sp.rigid_translation(space.component, direction)
        Mr = M @ r
        total_along = float(r @ Mr)
        timings["assemble_s"] = time.perf_counter() - started

        reduce = "reduce_modes" in (getattr(plan, "taken", None) or ())
        enough = mass_enough(Mr, total_along) if reduce and total_along > 0 else None
        found = response_modes(ctx, K, M, held, inputs.top_Hz, enough, timings, warnings, "random_vibration")

        fractions = sp.mass_fractions(found, M, space.component)
        gamma_all = sp.participation(found, M, r)
        masses = gamma_all ** 2
        along_share = float(masses.sum() / total_along) if total_along > 0 else None
        kept = np.arange(len(found))
        kept_share = None
        reduce_step = None
        if reduce:
            kept, kept_share = sp.fewest_modes(masses, total_along, KEEP_SHARE)
            reduce_step = kept_step(found, kept, kept_share, f"of the mass moving along {axis_words(inputs.direction)}")
        used = found.take(kept)
        gamma = gamma_all[kept]

        started = time.perf_counter()
        shapes = sp.nodal_shapes(space, used)
        stresses = sp.modal_stresses(space, materials, used)
        timings["stress_s"] = time.perf_counter() - started

        started = time.perf_counter()
        per = COARSE_POINTS if getattr(plan, "frequency_grid", False) else POINTS_PER_BANDWIDTH
        grid = response_grid(inputs.table, zeta, per)
        S_a = psd_at(inputs.table, grid) * G0_MM_S2 ** 2
        C0, C2 = modal_covariance(used.omega, gamma, grid, S_a, zeta)
        displacement = rms_displacement(shapes, C0)
        von_mises = rms_von_mises(stresses, C0)
        with np.errstate(divide="ignore", invalid="ignore"):
            nu_stress = np.sqrt(np.where(von_mises > 0, rms_von_mises(stresses, C2) ** 2 / np.maximum(von_mises, 1e-300) ** 2, 0.0))
        s_node = int(von_mises.argmax())
        d_node = int(displacement.argmax())
        nu_disp_num = float(rms_displacement(shapes[:, d_node:d_node + 1], C2)[0])
        nu_disp = nu_disp_num / float(displacement[d_node]) if displacement[d_node] > 0 else 0.0
        # Each mode's own share of the RMS stress at the peak (the diagonal terms; cross terms are shared).
        A = von_mises_form()
        psi = stresses[:, :, s_node]                                                       # (m, 6)
        diagonal = np.diag(C0) * np.einsum("ik,kl,il->i", psi, A, psi)
        stress_share = diagonal / diagonal.sum() if diagonal.sum() > 0 else np.zeros(len(used))
        timings["psd_s"] = time.perf_counter() - started

        curves = self._curves(inputs, used, gamma, grid, S_a, stresses[:, :, s_node], shapes[:, d_node, :], zeta)
        started = time.perf_counter()
        motion, motion_attributes = self._motion(inputs, used, gamma, shapes, C0)
        timings["motion_s"] = time.perf_counter() - started
        in_band = [i for i, f in enumerate(found.frequencies_Hz) if inputs.band_Hz[0] <= f <= inputs.band_Hz[1]]
        return AnalysisResult(
            dof_locations=space.dof_locations,
            vertices=space.vertices,
            tets=space.tets,
            boundary_quadratic=space.boundary_quadratic,
            element_dofs=space.element_dofs,
            fields={"von_mises_rms": von_mises, "displacement_rms": displacement},
            deformation=None,
            curves=curves,
            scalars={
                "frequencies_Hz": [float(f) for f in found.frequencies_Hz],
                "effective_mass_fractions": fractions.tolist(),
                "participation": [float(g) for g in gamma_all],
                "stress_share": {int(i): float(s) for i, s in zip(kept, stress_share)},
                "used_modes": [int(i) for i in kept],
                "modes_in_band": in_band,
                "kept_share": kept_share,
                "reduce_step": reduce_step,
                "along_share": along_share,
                "direction": [float(c) for c in direction],
                "searched_Hz": found.searched_Hz,
                "total_mass_kg": float(sum(M @ (space.component == 0).astype(float))) * 1000.0,
                "grid_points": int(len(grid)),
                "points_per_bandwidth": per,
                "stress_node": s_node,
                "displacement_node": d_node,
                "zero_crossing_Hz": float(nu_stress[s_node]),
                "displacement_zero_crossing_Hz": nu_disp,
                "stress_zero_crossing_Hz": nu_stress,
                "analysis_warnings": [],
                # What the viewer's Shake routine plays: a shake synthesised from the PSD and the modes' answer to it.
                "analysis_extras": {"motion": motion} if motion else {},
                "motion_attributes": motion_attributes,
                **stress_scalars(ctx),
            },
            dofs=int(space.dofs),
            solver=f"{found.how}; modal superposition of {modes_text(len(used))} over {len(grid)} frequencies",
            timings=timings,
            warnings=warnings,
        )

    @staticmethod
    def _motion(inputs: RandomVibrationInputs, used, gamma, shapes, C0) -> tuple[dict | None, dict]:
        """What the viewer plays: a stretch of a random base acceleration whose spectrum is the PSD (one line per hertz
        at the PSD's level, phases from a fixed seed: :func:`motion.random_lines`) and the part's motion relative to
        the base, the modes that carry most of it (each mode's variance at its largest node, ``C0ᵢᵢ·max|φᵢ|²``)
        answering every line exactly (:func:`motion.random_history`), over ten cycles of the slowest of them."""
        import numpy as np

        from cadgen._internal.fea import motion

        if not len(used):
            return None, {}
        reach = np.linalg.norm(shapes, axis=2).max(axis=1)                         # (m,) each mode's largest node
        contribution = np.diag(C0) * reach ** 2
        chosen = motion.choose_modes(contribution)
        frequencies = used.frequencies_Hz[chosen]
        window = motion.RANDOM_CYCLES / float(frequencies.min())
        step, count, kept = motion.sampling(frequencies, window)
        chosen = [chosen[i] for i in np.argsort(frequencies)[:kept]]
        f, amplitude = motion.random_lines(inputs.table)
        times = np.arange(count) * step
        base, q = motion.random_history(f, amplitude, used.omega[chosen], gamma[chosen], inputs.damping_ratio, times)
        attributes, peaks = motion.mode_attributes(shapes[chosen])
        modes = [{"attribute": name, "frequency_Hz": float(used.frequencies_Hz[i]), "q_mm": q[k] * peaks[k]}
                 for k, (name, i) in enumerate(zip(attributes, chosen))]
        share = float(contribution[chosen].sum() / contribution.sum()) if contribution.sum() > 0 else 1.0
        record = motion.shake_record(
            label="Random shake", step_s=step, base_g=base / G0_MM_S2, modes=modes, direction=inputs.direction,
            words="synthesised from the PSD", rms_g=round(float(np.sqrt((np.abs(amplitude) ** 2).sum() / 2.0)) / G0_MM_S2, 6),
            share=round(share, 4), seed=motion.SEED)
        return record, attributes

    @staticmethod
    def _curves(inputs, used, gamma, grid, S_a, psi, phi, zeta) -> dict:
        """The input PSD and the response PSDs at the peaks (von Mises's equivalent tr(A S_σσ), and the
        displacement's size), on the grid thinned to at most :data:`CURVE_POINTS`."""
        import numpy as np

        from cadgen._internal.fea import superposition as sp

        keep = _thin(grid, CURVE_POINTS)
        f = grid[keep]
        G = sp.transfer(used.omega, _TWO_PI * f, zeta) * gamma[None, :]                 # (n, m)
        A = von_mises_form()
        Z = G @ psi                                                                      # (n, 6)
        stress = np.einsum("nk,kl,nl->n", Z.conj(), A, Z).real * S_a[keep]
        U = G @ phi                                                                      # (n, 3)
        moved = np.einsum("nc,nc->n", U.conj(), U).real * S_a[keep]
        x = [round(float(v), 6) for v in f]
        return {
            "input_psd_g2_Hz": {"x": x, "x_unit": "Hz", "y": [float(f"{v:.6g}") for v in S_a[keep] / G0_MM_S2 ** 2],
                                "y_unit": "g²/Hz"},
            "stress_psd_MPa2_Hz": {"x": x, "x_unit": "Hz", "y": [float(f"{v:.6g}") for v in stress], "y_unit": "MPa²/Hz"},
            "displacement_psd_mm2_Hz": {"x": x, "x_unit": "Hz", "y": [float(f"{v:.6g}") for v in moved], "y_unit": "mm²/Hz"},
        }

    # -- checks, findings ----------------------------------------------------------------------------

    def _peak(self, result: AnalysisResult, inputs: RandomVibrationInputs) -> tuple[int, float]:
        """sigma × the RMS stress, at its peak."""
        node = result.scalars["stress_node"]
        return node, inputs.sigma * float(result.fields["von_mises_rms"][node])

    def refined_record(self, result: AnalysisResult, size_mm: float, finer_mm: float, *, assembly: bool) -> dict:
        return {"from_size_mm": round(size_mm, 4), "from_max_von_mises_rms_MPa": round(float(result.fields["von_mises_rms"].max()), 4),
                "size_mm": round(finer_mm, 4), "max_von_mises_rms_MPa": None}

    def merge_finer(self, coarse: AnalysisResult, finer: AnalysisResult, refined: dict) -> AnalysisResult:
        refined["max_von_mises_rms_MPa"] = round(float(finer.fields["von_mises_rms"].max()), 4)
        finer.scalars["coarser_peak_MPa"] = float(coarse.fields["von_mises_rms"].max())
        return finer

    def judge(self, check: dict, index: int, ctx: SolveContext, result: AnalysisResult,
              inputs: RandomVibrationInputs) -> dict:
        if check["kind"] == "stress":
            return self._yield_check(ctx, result, inputs, check.get("label") or self.stress_label)
        return displacement_judged(ctx, result, inputs.sigma * result.fields["displacement_rms"], check, index)[0]

    def findings(self, ctx: SolveContext, result: AnalysisResult, inputs: RandomVibrationInputs,
                 check_results: list[dict], *, assembly: bool) -> list[dict]:
        scalars = result.scalars
        whole = "the assembly" if assembly else "the part"
        sigma = inputs.sigma
        shaken = f"Shaken at random at {inputs.grms:.3g} g rms along {axis_words(inputs.direction)}"
        found = response_findings(
            check_results, "random", lambda check: f"{shaken},", whole,
            lambda check: (f"von Mises RMS {check['value'] / sigma:.4g} MPa, times {sigma} for {sigma}σ "
                           f"(a Gaussian response stays under {sigma}σ {'68.3' if sigma == 1 else '99.7'}% of the time)"),
            after=f" at {sigma}σ")
        node = scalars["stress_node"]
        stress_rms = float(result.fields["von_mises_rms"][node])
        moved_rms = float(result.fields["displacement_rms"].max())
        shares = scalars["stress_share"]
        frequencies = scalars["frequencies_Hz"]
        dominant = max(shares, key=shares.get) if shares else None
        mode_words = ""
        if dominant is not None and shares[dominant] > 0:
            mode_words = (f"; mode {dominant + 1} at {hz_text(frequencies[dominant])} carries "
                          f"{shares[dominant] * 100:.0f}% of the stress")
        found.append(finding(
            "info", "random_response",
            f"{shaken}, its stress is {stress_rms:.3g} MPa rms ({_sigma_text(sigma * stress_rms, 'MPa', sigma)}) and it moves "
            f"{moved_rms:.3g} mm rms ({_sigma_text(sigma * moved_rms, 'mm', sigma)}){mode_words}",
            f"the stress swings through zero about {scalars['zero_crossing_Hz']:.3g} times a second at its peak "
            "(the rate fatigue counts cycles at)",
            [{"text": "stressed most here", "ref": None, "at": xyz(result.dof_locations[node])}],
        ))
        if not scalars["modes_in_band"]:
            found.append(no_resonance_finding("no_resonance_in_band", *inputs.band_Hz, whole, frequencies,
                                              "the response stays near its static value under the PSD"))
        share = scalars["along_share"]
        if share is not None and share < MASS_WARNING:
            found.append(missing_mass_finding(scalars, share, "PSD's"))
        if coarser := scalars.get("coarser_peak_MPa"):
            found.append(converged_finding("The RMS stress", float(result.fields["von_mises_rms"].max()), coarser))
        return by_severity(found)

    # -- what is written: an RMS has no sign or phase to deform by, so the model is drawn as it is -----------

    def summary(self, result: AnalysisResult, inputs: RandomVibrationInputs, check_results: list[dict]) -> dict:
        scalars = result.scalars
        sigma = inputs.sigma
        s_node = scalars["stress_node"]
        d_node = scalars["displacement_node"]
        stress_rms = float(result.fields["von_mises_rms"][s_node])
        moved_rms = float(result.fields["displacement_rms"][d_node])
        yield_MPa = yield_at(scalars, s_node)
        factor = yield_MPa / (sigma * stress_rms) if stress_rms > 0 else None
        used = set(scalars["used_modes"])
        direction = scalars["direction"]
        axis = max(range(3), key=lambda c: abs(direction[c]))
        shares = scalars["stress_share"]
        grms_input = inputs.grms
        summary: dict[str, Any] = {
            "psd": {"direction": list(inputs.direction), "table": [list(row) for row in inputs.table],
                    "band_Hz": list(inputs.band_Hz)},
            "grms_input": round(grms_input, 6),
            "damping_ratio": inputs.damping_ratio,
            "sigma": sigma,
            "max_von_mises_rms_MPa": round(stress_rms, 4),
            "max_von_mises_MPa": round(sigma * stress_rms, 4),
            "max_von_mises_at_mm": xyz(result.dof_locations[s_node]),
            "yield_MPa": yield_MPa,
            "safety_factor": None if factor is None else math.floor(factor * 1000) / 1000,
            "max_displacement_rms_mm": round(moved_rms, 6),
            "max_displacement_mm": round(sigma * moved_rms, 6),
            "max_displacement_at_mm": xyz(result.dof_locations[d_node]),
            "zero_crossing_Hz": round(scalars["zero_crossing_Hz"], 4),
            "displacement_zero_crossing_Hz": round(scalars["displacement_zero_crossing_Hz"], 4),
            "modes": [
                {**row, "participation_factor": round(scalars["participation"][i] * math.sqrt(1000.0), 6),
                 "stress_share": round(shares.get(i, 0.0), 4), "used": i in used}
                for i, row in mode_rows(scalars)
            ],
            "modes_used": len(used),
            "modes_up_to_Hz": round(inputs.top_Hz, 4),
            "effective_mass_fraction": None if scalars["along_share"] is None else round(scalars["along_share"], 4),
            "effective_mass_axis": "XYZ"[axis],
            "total_mass_kg": round(scalars["total_mass_kg"], 6),
            "frequency_points": scalars["grid_points"],
            "points_per_bandwidth": scalars["points_per_bandwidth"],
            "deformation_scale": scalars["deformation_scale"],
        }
        return finish_summary(summary, scalars, check_results, kept_share=True)

    def field_ranges(self, summary: dict, result: AnalysisResult) -> dict[str, tuple[float, float]]:
        return {"von_mises_rms": (0.0, summary["max_von_mises_rms_MPa"]),
                "displacement_rms": (0.0, summary["max_displacement_rms_mm"])}

    def extras_head(self, summary: dict) -> dict:
        return {"safety_factor": summary["safety_factor"], "grms_input": summary["grms_input"],
                "zero_crossing_Hz": summary["zero_crossing_Hz"]}

    def study_echo(self, inputs: RandomVibrationInputs, bare: Callable[[tuple[str, ...]], list[str]]) -> dict:
        return {
            "fixtures": fixtures_echo(inputs.fixtures, bare),
            "psd": {"direction": list(inputs.direction), "table": [list(row) for row in inputs.table],
                    "grms": round(inputs.grms, 6)},
            "damping_ratio": inputs.damping_ratio,
            "sigma": inputs.sigma,
        }

    def human_lines(self, summary: dict) -> list[str]:
        psd = summary["psd"]
        low, high = psd["band_Hz"]
        sigma = summary["sigma"]
        return [
            f"shaken at random, {summary['grms_input']:.3g} g rms along {axis_words(psd['direction'])} from {low:g} to "
            f"{high:g} Hz, {summary['damping_ratio'] * 100:g}% damping; judged at {sigma}σ",
            f"stress {summary['max_von_mises_rms_MPa']:.4g} MPa rms ({summary['max_von_mises_MPa']:.4g} MPa at {sigma}σ); "
            f"moves {summary['max_displacement_rms_mm']:.4g} mm rms ({summary['max_displacement_mm']:.4g} mm at {sigma}σ); "
            f"about {summary['zero_crossing_Hz']:.4g} stress cycles a second",
            modes_line(summary),
        ]
