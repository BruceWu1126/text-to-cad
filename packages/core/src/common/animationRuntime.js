// The choreography half: play the clips a document's sidecar carries. A clip is
// KEYFRAMES that cadgen baked when its model built (cadgen.clip in Python, keyed by
// cadgen/_internal/animation_bake.py, which validated them); this module plays
// them the way a glTF player plays its samplers, and works nothing else out.
// It runs no model code, and knows nothing of mates, DOFs, presets, or the Pose
// tab: it pushes matrices and styles through the same effect records the viewer
// already composes.
//
// The section is {clips: [clip, ...]} in the model's declared order; a clip is
// {id, label, duration, loop, tracks}. A track drives ONE channel of the
// occurrences it lists (document leaf ids), with one value per time; before its
// first time and after its last it holds the end value:
//   transform  [d, q, d', q']: the track's pivot moves by d while the part turns
//              by q about it, T(pivot + d) R(q) T(-pivot). Between keys each of
//              the seven follows glTF's CUBICSPLINE sampler, a cubic Hermite curve
//              through the keys' values and their rates d' and q' (per second),
//              and q is normalized.
//   opacity    0..1, or null for the material's own; lerps between numbers.
//   visible    true, false, or null for the rest state; held.
// Every evaluation starts from rest: a clip is a pure function of t, so scrub,
// loop and seek are free.

/** The clips of an animation section, by id. `order` is each clip's place in the
 * model's declaration (the first is the one a viewer opens on): an object lists
 * integer-like keys first, whatever order they were added in. Tracks are copied, so
 * nothing done to them reaches the sidecar they came from. */
export function normalizeAnimationClips(block) {
  const clips = {};
  for (const [order, clip] of (block?.clips || []).entries()) {
    clips[clip.id] = {
      id: clip.id, label: clip.label || clip.id, duration: clip.duration, loop: clip.loop !== false,
      tracks: clip.tracks.map((track) => ({ ...track })), order
    };
  }
  return clips;
}

/** Whether `clip` is a playable clip record. */
export function isAnimationClip(clip) {
  return Array.isArray(clip?.tracks);
}

/** Load a sidecar's clips: `{clips}`, or null when it declares none. */
export async function loadSourceAnimation(sidecar, { signal } = {}) {
  signal?.throwIfAborted();
  if (!sidecar?.animation?.clips?.length) return null;
  return { clips: normalizeAnimationClips(sidecar.animation) };
}

// (index of the key at or before t, fraction of the way to the next key)
function bracket(times, t) {
  const last = times.length - 1;
  if (t >= times[last]) return [last, 0];
  if (t <= times[0]) return [0, 0];
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid;
    else hi = mid;
  }
  return [lo, (t - times[lo]) / (times[hi] - times[lo])];
}

// A transform key's placement a fraction u toward the next, about the track's
// pivot: glTF's CUBICSPLINE step (the spec's, and three's glTF loader's), each
// component through the keys' values and their rates over the span between them.
function transformAt(THREE, track, index, u) {
  const a = track.transform[index];
  const b = u > 0 ? track.transform[index + 1] : a;
  const span = u > 0 ? track.times[index + 1] - track.times[index] : 0;
  const u2 = u * u;
  const u3 = u2 * u;
  const h00 = 2 * u3 - 3 * u2 + 1;
  const h10 = (u3 - 2 * u2 + u) * span;
  const h01 = 3 * u2 - 2 * u3;
  const h11 = (u3 - u2) * span;
  const at = (n) => h00 * a[n] + h10 * a[7 + n] + h01 * b[n] + h11 * b[7 + n];
  const turn = new THREE.Quaternion(at(3), at(4), at(5), at(6)).normalize();
  const pivot = new THREE.Vector3().fromArray(track.pivot);
  const turned = pivot.clone().applyQuaternion(turn);
  return new THREE.Matrix4().makeRotationFromQuaternion(turn).setPosition(
    pivot.x + at(0) - turned.x, pivot.y + at(1) - turned.y, pivot.z + at(2) - turned.z
  );
}

const lerp = (a, b, u) => a + (b - a) * u;

/** Evaluate one clip at time t: `{matrices, styles}`, each keyed by
 * occurrence id. A looping clip wraps t; one that does not holds its end. */
export function evaluateAnimationClip(THREE, clip, t) {
  const duration = clip.duration || 1;
  let localT = Math.max(0, Number(t) || 0);
  localT = clip.loop !== false ? localT % duration : Math.min(localT, duration);
  const matrices = new Map();
  const styles = new Map();
  const style = (id, key, value) => {
    const current = styles.get(id) || {};
    current[key] = value;
    styles.set(id, current);
  };
  for (const track of clip.tracks) {
    const [index, u] = bracket(track.times, localT);
    if (track.transform) {
      const matrix = transformAt(THREE, track, index, u);
      for (const id of track.targets) matrices.set(id, matrix);
    } else if (track.opacity) {
      const a = track.opacity[index];
      const b = u > 0 ? track.opacity[index + 1] : null;
      if (a === null) continue;
      const value = b === null ? a : lerp(a, b, u);
      for (const id of track.targets) style(id, "opacity", value);
    } else if (track.visible) {
      const value = track.visible[index];
      if (value === null) continue;
      for (const id of track.targets) style(id, "visible", value);
    }
  }
  return { matrices, styles };
}

// Merge an evaluated frame into the viewer's per-part effect records — the same
// records the articulation writes (`applyArticulationToEffects`), so animation
// COMPOSES OVER pose without either system knowing about the other. The
// animation matrix premultiplies whatever is already there (pose first, then
// choreography on top, in world space). Returns the number of parts whose
// transform the frame touched, which is what tells the caller its edge runtimes
// need re-deriving.
export function applyAnimationFrameToEffects(THREE, effectsByPartId, frame) {
  if (!effectsByPartId || !frame) {
    return 0;
  }
  const ensureEffect = (partId) => {
    const id = String(partId || "").trim();
    if (!id) {
      return null;
    }
    const current = effectsByPartId.get(id) || {
      matrix: null,
      style: null,
      visible: null,
      highlighted: false
    };
    effectsByPartId.set(id, current);
    return current;
  };
  let transformCount = 0;
  for (const [partId, matrix] of frame.matrices || []) {
    const effect = ensureEffect(partId);
    if (!effect) {
      continue;
    }
    effect.matrix = effect.matrix
      ? new THREE.Matrix4().multiplyMatrices(matrix, effect.matrix)
      : matrix.clone();
    transformCount += 1;
  }
  for (const [partId, style] of frame.styles || []) {
    const effect = ensureEffect(partId);
    if (!effect) {
      continue;
    }
    if (style && Object.hasOwn(style, "opacity")) {
      effect.style = { ...(effect.style || {}), opacity: style.opacity };
    }
    if (style && Object.hasOwn(style, "visible")) {
      effect.visible = style.visible !== false;
    }
  }
  return transformCount;
}
