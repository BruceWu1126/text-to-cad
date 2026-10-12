"""What a result writes for the viewer to play it over time: ``extras.analysis.motion``.

A result whose physics moves (a shaker, a shock, a load cycle, an AC field, a
flow) carries the little the viewer needs to play it truthfully, sampled
sparsely so the GLB stays small; a static result carries none. Every motion is
an object with a ``kind`` and the numbers its routine reads:

- ``base_shake`` (random vibration, shock): the part's motion relative to its
  shaker, as a sum of a few mode shapes, each a hidden vertex attribute
  (``_MOTION_MODE_1`` ..., mm per unit, its largest motion 1 mm) times its
  modal history ``q`` (mm), sampled every ``step_s`` over the window shown,
  with the shaker's own acceleration over that window (``base_g``). A random
  shake's history is synthesised from its PSD (:func:`random_lines`: one line
  per hertz at the PSD's own level, random phases from a fixed seed, so the
  signal has the PSD's spectrum and rms and is the same every run) and its
  modes answer it exactly (:func:`random_history`); a shock's base pulse is
  synthesised to match its response spectrum (:func:`srs_pulse`, wavelets, the
  method shaker controllers use) and each mode's answer is integrated exactly
  (:func:`sdof`).
- ``cycle`` (fatigue): the load cycle, the stress swinging between its minimum
  and maximum (``_CYCLE_STRESS``, the cycle's peak von Mises, MPa).
- ``ac`` (AC magnetics): the field's real and imaginary parts at each vertex,
  so a phase of the cycle is ``re·cos ωt − im·sin ωt``.
- ``tracers`` (flow): pathlines through the solved velocity field
  (:func:`pathlines`), each a run of points ``step_s`` apart in real time, for
  particles to move along.

Hidden attributes ride in ``result.scalars["motion_attributes"]`` (run.py
writes them after the fields, listed nowhere else). Stdlib only at import.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

__all__ = [
    "MAX_SAMPLES", "MODE_ATTRIBUTE", "choose_modes", "mode_attributes", "pathlines", "random_history", "random_lines",
    "rounded", "sampling", "sdof", "shake_record", "srs_of", "srs_pulse", "tracer_record",
]

G0_MM_S2 = 9806.65
_TWO_PI = 2.0 * math.pi
#: The most samples a motion's history keeps (per mode, and for the base).
MAX_SAMPLES = 1200
#: The most modes a shake plays, and the share of the motion they must carry before fewer will do.
MAX_MODES = 4
MOTION_SHARE = 0.95
#: Samples per cycle of the fastest mode played: enough to draw its swing smoothly.
SAMPLES_PER_CYCLE = 16
FEWEST_PER_CYCLE = 8
#: A random shake's window holds this many cycles of its slowest mode played.
RANDOM_CYCLES = 10
#: A random shake's lines are this far apart (Hz): the signal repeats each 1/RANDOM_SPACING_HZ seconds.
RANDOM_SPACING_HZ = 1.0
#: The seed a random shake's phases come from, so every run plays the same signal.
SEED = 20261011
MODE_ATTRIBUTE = "_MOTION_MODE_{}"


def rounded(values, figures: int = 5) -> list[float]:
    """Numbers to ``figures`` significant figures: a history written into the GLB's JSON stays short."""
    return [float(f"{float(v):.{figures}g}") for v in values]


def choose_modes(contribution, most: int = MAX_MODES, share: float = MOTION_SHARE) -> list[int]:
    """The fewest modes (at most ``most``) whose ``contribution`` (each mode's share of the motion, any scale) holds
    ``share`` of the whole, in their own order."""
    order = sorted(range(len(contribution)), key=lambda i: -float(contribution[i]))
    total = float(sum(max(float(c), 0.0) for c in contribution))
    kept: list[int] = []
    held = 0.0
    for index in order:
        if len(kept) >= most or (total > 0 and held >= share * total):
            break
        kept.append(index)
        held += max(float(contribution[index]), 0.0)
    return sorted(kept)


def sampling(frequencies_Hz, window_s: float, *, max_samples: int = MAX_SAMPLES) -> tuple[float, int, int]:
    """(step_s, count, kept): a step that draws the fastest of ``frequencies_Hz`` with SAMPLES_PER_CYCLE points (no
    fewer than FEWEST_PER_CYCLE), and the samples over ``window_s``; where even that takes more than ``max_samples``,
    the fastest modes are dropped (``kept`` the modes, slowest first, that are played)."""
    freqs = sorted(float(f) for f in frequencies_Hz)
    kept = len(freqs)
    while kept > 1:
        step = 1.0 / (FEWEST_PER_CYCLE * freqs[kept - 1])
        if window_s / step <= max_samples:
            break
        kept -= 1
    fastest = freqs[kept - 1] if freqs else 1.0
    step = 1.0 / (SAMPLES_PER_CYCLE * fastest)
    if window_s / step > max_samples:
        step = window_s / max_samples
    count = int(round(window_s / step)) + 1
    return step, min(count, max_samples + 1), kept


def mode_attributes(shapes) -> tuple[dict[str, "np.ndarray"], list[float]]:
    """Each shape (m, nodes, 3) scaled to move its largest node 1 mm, as hidden attributes ``_MOTION_MODE_1`` ...,
    and the scale each took (mm per unit modal coordinate), so a history in modal units times it is in mm."""
    import numpy as np

    attributes: dict[str, np.ndarray] = {}
    peaks: list[float] = []
    for index, shape in enumerate(shapes):
        peak = float(np.linalg.norm(shape, axis=1).max()) if len(shape) else 0.0
        peaks.append(peak)
        attributes[MODE_ATTRIBUTE.format(index + 1)] = (shape / peak if peak > 0 else shape).astype(np.float64)
    return attributes, peaks


def shake_record(*, label: str, step_s: float, base_g, modes: list[dict], direction, words: str, **more) -> dict:
    """``extras.analysis.motion`` for a shaken part: the window's samples (``step_s`` apart, from 0), the base's
    acceleration (g) along ``direction``, and each mode played: its attribute, frequency and history (mm)."""
    return {
        "kind": "base_shake", "label": label, "step_s": float(f"{step_s:.6g}"), "count": len(base_g),
        "direction": [round(float(c), 6) for c in direction], "base_g": rounded(base_g, 4),
        "modes": [{"attribute": m["attribute"], "frequency_Hz": round(float(m["frequency_Hz"]), 4), "q_mm": rounded(m["q_mm"], 4)}
                  for m in modes],
        "words": words, **more,
    }


# -- a random shake, synthesised from its PSD ---------------------------------------------------------------

def random_lines(table, *, spacing_Hz: float = RANDOM_SPACING_HZ, seed: int = SEED):
    """(f_Hz, amplitude) of the PSD as a sum of sines: one line every ``spacing_Hz`` across the table, its amplitude
    sqrt(2 S(f) Δf) (mm/s², S in g²/Hz from the table, log-log), its phase drawn at random from ``seed``. Its mean
    square is the PSD's area on that grid, so its rms is the input's g rms; the phases make it a random signal."""
    import numpy as np

    from cadgen._internal.fea.analyses.random_vibration import psd_at

    low, high = float(table[0][0]), float(table[-1][0])
    first = math.ceil(low / spacing_Hz - 1e-9)
    last = math.floor(high / spacing_Hz + 1e-9)
    f = np.arange(first, last + 1, dtype=float) * spacing_Hz
    level = np.sqrt(2.0 * psd_at(table, f) * spacing_Hz) * G0_MM_S2
    phase = np.random.default_rng(seed).uniform(0.0, _TWO_PI, len(f))
    return f, level * np.exp(1j * phase)


def random_history(f_Hz, amplitude, omega_modes, gamma, zeta, times) -> tuple["np.ndarray", "np.ndarray"]:
    """(a (n,) mm/s², q (m, n)): the base acceleration Re Σ A_k e^{iω_k t} and each mode's coordinate relative to the
    base, qᵢ = Re Σ −Γᵢ Hᵢ(ω_k) A_k e^{iω_k t} (its steady answer to every line), at ``times``."""
    import numpy as np

    from cadgen._internal.fea import superposition as sp

    times = np.asarray(times, dtype=float)
    a = np.zeros(len(times))
    q = np.zeros((len(omega_modes), len(times)))
    omega = _TWO_PI * np.asarray(f_Hz, dtype=float)
    for start in range(0, len(omega), 256):
        w = omega[start:start + 256]
        A = np.asarray(amplitude[start:start + 256])
        E = np.exp(1j * np.outer(w, times))                                  # (k, n)
        a += (A[:, None] * E).real.sum(axis=0)
        Q = -(sp.transfer(omega_modes, w, zeta) * np.asarray(gamma)[None, :]) * A[:, None]   # (k, m)
        q += (Q.T @ E).real
    return a, q


# -- a shock, synthesised to match its response spectrum ----------------------------------------------------

def _discrete(numerator, denominator, step_s: float):
    import numpy as np
    from scipy import signal

    b, a, _ = signal.cont2discrete((numerator, denominator), step_s, method="foh")
    return np.ravel(b), np.ravel(a)


def sdof(acceleration, step_s: float, omega: float, zeta: float, *, absolute: bool = False) -> "np.ndarray":
    """One damped oscillator on a base whose acceleration is ``acceleration`` (mm/s², sampled ``step_s`` apart,
    straight between samples, so the answer is exact for it): its displacement relative to the base, z'' + 2ζωz' +
    ω²z = −a, or with ``absolute`` its own acceleration, −(2ζωz' + ω²z)."""
    import numpy as np
    from scipy import signal

    den = [1.0, 2.0 * zeta * omega, omega * omega]
    num = [2.0 * zeta * omega, omega * omega] if absolute else [-1.0]
    b, a = _discrete(num, den, step_s)
    return signal.lfilter(b, a, np.asarray(acceleration, dtype=float))


def srs_of(acceleration, step_s: float, f_Hz, zeta: float) -> "np.ndarray":
    """The maximax absolute-acceleration shock response spectrum of ``acceleration`` at ``f_Hz`` (same units)."""
    import numpy as np

    return np.array([float(np.abs(sdof(acceleration, step_s, _TWO_PI * f, zeta, absolute=True)).max()) for f in f_Hz])


def _wavelet(t, f: float, half_sines: int):
    import numpy as np

    end = half_sines / (2.0 * f)
    inside = (t >= 0) & (t <= end)
    return np.where(inside, np.sin(_TWO_PI * f * t / half_sines) * np.sin(_TWO_PI * f * t), 0.0)


def srs_pulse(table, zeta: float, band_Hz: tuple[float, float], *, step_s: float, tail_s: float, iterations: int = 40):
    """A base pulse whose response spectrum matches ``table`` ([Hz, g], read log-log, held at its ends) over
    ``band_Hz``: a sum of wavelets (sin(2πft/N)·sin(2πft), N odd half-sines, each starting from rest and ending at
    rest, so the base ends where it began), one each sixth of an octave, their amplitudes iterated until each one's
    spectrum point meets the table (Smallwood's method). Returns (t, a mm/s², f_Hz, achieved g): the pulse sampled
    every ``step_s`` and ``tail_s`` of quiet after it, and the spectrum it reaches at the wavelets' frequencies."""
    import numpy as np

    from cadgen._internal.fea.analyses.shock import spectrum_g

    low, high = band_Hz
    count = max(2, int(math.ceil(6.0 * math.log2(high / low))) + 1)
    f = np.exp(np.linspace(math.log(low), math.log(high), count))
    target = np.atleast_1d(spectrum_g(table, f)) * G0_MM_S2
    # Every wavelet lasts about as long as the slowest one (three half-sines): N = the odd number nearest 3 f / low.
    half = np.maximum(3, 2 * np.round((3.0 * f / low - 1.0) / 2.0) + 1).astype(int)
    duration = float((half / (2.0 * f)).max())
    t = np.arange(0.0, duration + tail_s + step_s / 2, step_s)
    shapes = np.stack([_wavelet(t, fj, nj) for fj, nj in zip(f, half)])
    amplitude = target / 5.0
    best = (math.inf, amplitude)
    for _ in range(iterations):
        achieved = srs_of(amplitude @ shapes, step_s, f, zeta)
        ratio = np.where(achieved > 0, target / achieved, 1.0)
        miss = float(np.abs(ratio - 1.0).max())
        if miss < best[0]:
            best = (miss, amplitude)
        if miss < 0.01:
            break
        # Part of the way each time: neighbouring wavelets answer at each other's frequencies, so a full step overshoots.
        amplitude = amplitude * ratio ** 0.7
    a = best[1] @ shapes
    return t, a, f, srs_of(a, step_s, f, zeta) / G0_MM_S2


# -- tracers through a solved flow ---------------------------------------------------------------------------

def pathlines(locations, element_dofs, velocity, seeds, *, step_s: float, count: int, substeps: int = 2,
              inside_tol: float = 1e-4):
    """Where particles let go at ``seeds`` (mm) are carried by the solved ``velocity`` (mm/s at each node of
    ``element_dofs``' tetrahedra, P2 on ten nodes or P1 on four), every ``step_s`` seconds for ``count`` steps:
    (seeds, count, 3) mm, a particle that leaves the fluid (an outlet) held where it left and its run ended there
    (``lengths``, the samples each has). Each step is ``substeps`` midpoint (RK2) steps; one that would leave the
    fluid is drawn back toward where it came from until it is inside (a particle grazing a curved wall goes on along
    it), and a particle that then barely moves is leaving through an opening: its run ends."""
    import numpy as np
    from scipy.spatial import cKDTree

    locations = np.asarray(locations, dtype=float)
    velocity = np.asarray(velocity, dtype=float)
    tets = np.asarray(element_dofs, dtype=np.int64)
    corners = locations[tets[:, :4]]                                   # (T, 4, 3)
    centres = corners.mean(axis=1)
    tree = cKDTree(centres)
    # Barycentric coordinates λ = Minv (x − corner0): one 3x3 per element.
    edges = np.stack([corners[:, 1] - corners[:, 0], corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 0]], axis=2)
    usable = np.abs(np.linalg.det(edges)) > 1e-18
    inverse = np.zeros_like(edges)
    inverse[usable] = np.linalg.inv(edges[usable])
    quadratic = tets.shape[1] == 10
    # skfem's quadratic tet: corners, then edges (0,1) (1,2) (0,2) (0,3) (1,3) (2,3).
    edge_pairs = ((0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3))

    def at(points):
        """(velocity (p, 3), inside (p,)) at ``points``."""
        out = np.zeros((len(points), 3))
        found = np.zeros(len(points), dtype=bool)
        if not len(points):
            return out, found
        # The nearest centres first (a long thin element's centre can be far from a point inside it: then every one).
        _, near = tree.query(points, k=min(32, len(centres)))
        near = np.atleast_2d(near).reshape(len(points), -1)
        columns = [near[:, column] for column in range(near.shape[1])]
        for column in range(len(columns) + 1):
            todo = ~found
            if not todo.any():
                break
            if column == len(columns):
                # Every element, for the few points none of the nearest holds.
                e = near[todo, 0].copy()
                for k, row in enumerate(np.flatnonzero(todo)):
                    l123 = np.einsum("tij,tj->ti", inverse, points[row] - corners[:, 0])
                    inside = np.flatnonzero(((1.0 - l123.sum(axis=1)) >= -inside_tol) & (l123 >= -inside_tol).all(axis=1) & usable)
                    if len(inside):
                        e[k] = inside[0]
            else:
                e = columns[column][todo]
            rel = points[todo] - corners[e, 0]
            l123 = np.einsum("pij,pj->pi", inverse[e], rel)
            lam = np.column_stack([1.0 - l123.sum(axis=1), l123])
            ok = (lam >= -inside_tol).all(axis=1) & usable[e]
            if not ok.any():
                continue
            rows = np.flatnonzero(todo)[ok]
            lam, e = lam[ok], e[ok]
            if quadratic:
                weights = np.empty((len(e), 10))
                weights[:, :4] = lam * (2.0 * lam - 1.0)
                for k, (i, j) in enumerate(edge_pairs):
                    weights[:, 4 + k] = 4.0 * lam[:, i] * lam[:, j]
            else:
                weights = lam
            out[rows] = np.einsum("pk,pkc->pc", weights, velocity[tets[e]])
            found[rows] = True
        return out, found

    def midpoint(points, h):
        v0, in0 = at(points)
        v1, in1 = at(points + 0.5 * h * v0)
        moved = points + h * v1
        return moved, in0 & in1 & at(moved)[1]

    seeds = np.asarray(seeds, dtype=float)
    paths = np.repeat(seeds[:, None, :], count, axis=1).copy()
    lengths = np.ones(len(seeds), dtype=int)
    alive = at(seeds)[1]
    position = seeds.copy()
    h = step_s / max(1, int(substeps))
    for step in range(1, count):
        for _ in range(max(1, int(substeps))):
            index = np.flatnonzero(alive)
            if not len(index):
                break
            moved, ok = midpoint(position[index], h)
            position[index[ok]] = moved[ok]
            retry = index[~ok]
            if len(retry):
                # A step that leaves the fluid grazes a wall it should run along (a straight step cuts a curved
                # wall's corner): drawn back toward the fluid it came from, part of the way at a time, it goes on.
                # One that barely moves for it is pressing out through an opening: its run ends there.
                start = position[retry].copy()
                wanted = np.linalg.norm(moved[~ok] - start, axis=1)
                target = moved[~ok]
                found = np.zeros(len(retry), dtype=bool)
                for _ in range(6):
                    target = 0.5 * (target + start)
                    inside = at(target)[1] & ~found
                    position[retry[inside]] = target[inside]
                    found |= inside
                gone = np.linalg.norm(position[retry] - start, axis=1)
                stuck = ~found | (gone < 0.1 * np.maximum(wanted, 1e-300))
                alive[retry[stuck]] = False
        paths[:, step] = position
        lengths[alive] = step + 1
        if not alive.any():
            break
    return paths, lengths


#: A flow's tracers: how many particles are let go, and the most samples each path keeps.
TRACERS = 64
TRACER_SAMPLES = 100


def tracer_record(space, row_kind, velocity_mm_s, *, label: str = "Flow", near_box=None, seeds: int = TRACERS,
                  samples: int = TRACER_SAMPLES, seed: int = SEED) -> dict | None:
    """``extras.analysis.motion`` for a flow (kind ``tracers``): particles let go where the fluid comes in (on the
    inlets, each at a point drawn by the flow through it, so they are as dense as the flow; an external flow's only
    across the part's own width, ``near_box`` (low, high) mm, widened by half of it), carried by the solved velocity
    (:func:`pathlines`) every ``step_s`` seconds of real time, the step set so the median particle's trip from inlet
    to outlet takes about ``samples`` steps. Each path is its points, flattened, mm, to 0.01 mm."""
    import numpy as np

    rows = np.flatnonzero(np.asarray(row_kind) == "inlet")
    if not len(rows):
        return None
    locations = np.asarray(space.dof_locations, dtype=float)
    velocity = np.asarray(velocity_mm_s, dtype=float)
    triangles = np.asarray(space.boundary_quadratic)[rows][:, :3]
    a, b, c = locations[triangles[:, 0]], locations[triangles[:, 1]], locations[triangles[:, 2]]
    normal = np.cross(b - a, c - a)
    area = 0.5 * np.linalg.norm(normal, axis=1)
    mean_v = velocity[triangles].mean(axis=1)
    weight = np.abs((mean_v * normal).sum(axis=1)) * 0.5                     # |v·n| A, the flow through it
    centre = (a + b + c) / 3.0
    if near_box is not None:
        low, high = (np.asarray(v, dtype=float) for v in near_box)
        pad = 0.5 * (high - low)
        # The flow's own axis is not limited: the inlet plane lies upstream, outside the part's box along it.
        axis = int(np.abs(mean_v.mean(axis=0)).argmax())
        keep = np.ones(len(centre), dtype=bool)
        for k in range(3):
            if k != axis:
                keep &= (centre[:, k] >= low[k] - pad[k]) & (centre[:, k] <= high[k] + pad[k])
        weight = np.where(keep, weight, 0.0)
    if not weight.sum() > 0:
        return None
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(weight), size=seeds, p=weight / weight.sum())
    r1, r2 = rng.random(seeds), rng.random(seeds)
    flip = r1 + r2 > 1
    r1[flip], r2[flip] = 1 - r1[flip], 1 - r2[flip]
    points = a[pick] + r1[:, None] * (b[pick] - a[pick]) + r2[:, None] * (c[pick] - a[pick])
    # A hair inside, along the flow coming in, so each starts in the fluid rather than on its boundary.
    tets = np.asarray(space.element_dofs, dtype=np.int64)
    edge = float(np.median(np.linalg.norm(locations[tets[:, 1]] - locations[tets[:, 0]], axis=1)))
    inward = mean_v[pick] / np.maximum(np.linalg.norm(mean_v[pick], axis=1, keepdims=True), 1e-300)
    points = points + 0.05 * edge * inward
    # The step: the trip through the region (its extent along the flow) at the inlets' mean speed over ``samples``.
    speed_in = float((np.linalg.norm(mean_v, axis=1) * area).sum() / max(area.sum(), 1e-300))
    span = locations.max(axis=0) - locations.min(axis=0)
    direction = mean_v.sum(axis=0)
    direction = direction / max(np.linalg.norm(direction), 1e-300)
    length = float(np.abs(span @ direction)) or float(np.linalg.norm(span))
    if not speed_in > 0:
        return None
    step = 1.5 * length / speed_in / samples
    fastest = float(np.linalg.norm(velocity, axis=1).max())
    substeps = int(min(24, max(2, math.ceil(fastest * step / (0.5 * edge)))))
    paths, lengths = pathlines(locations, tets, velocity, points, step_s=step, count=samples, substeps=substeps)
    kept = [(path[:n]) for path, n in zip(paths, lengths) if n >= 3]
    if not kept:
        return None
    return {
        "kind": "tracers", "label": label, "step_s": float(f"{step:.6g}"),
        "paths": [[round(float(v), 2) for v in path.ravel()] for path in kept],
        "fastest_m_s": round(fastest / 1000.0, 6),
    }
