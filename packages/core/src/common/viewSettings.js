import { DEFAULT_PART_COLOR_SETTINGS, normalizeExplodedViewSettings, validateDisplaySettings } from "./displaySettings.js";
import { DEFAULT_STEP_CLIP_SETTINGS, normalizeStepClipSettings } from "../lib/viewer/clipPlane.js";

/** @typedef {"solid" | "xray" | "hidden-line" | "wireframe"} ViewMode */
/** @typedef {ViewMode | "render"} ViewLook */
/** @typedef {{colorMode?: "original" | "single" | "by-part", color?: string, colors?: string[]}} ViewParts */
/** @typedef {{enabled?: boolean, color?: string, opacity?: number, density?: number}} ViewGrid */
/** @typedef {{enabled?: boolean, color?: string, opacity?: number}} ViewAxes */
/**
 * A mode's own settings, sparse: only what the person changed. Each mode has its own list
 * (`VIEW_MODE_SETTING_KEYS`); a setting outside it is refused. Render's are kept here too, though
 * Render is no mode: it is asked for on its own (`resolveViewSettings`' `render`).
 * @typedef {{
 *   solid?: {parts?: ViewParts, flat?: boolean, edges?: {enabled?: boolean, color?: string}, grid?: ViewGrid, axes?: ViewAxes},
 *   render?: {parts?: ViewParts, lighting?: {quality?: "preview" | "final", exposure?: number, rotation?: number, size?: number, fill?: number},
 *     background?: {color?: string, opacity?: number},
 *     floor?: {enabled?: boolean, placement?: "origin" | "lowest", color?: string, opacity?: number, finish?: "matte" | "glossy"}},
 *   xray?: {parts?: ViewParts, opacity?: number, edges?: {color?: string}, grid?: ViewGrid, axes?: ViewAxes},
 *   "hidden-line"?: {edges?: {color?: string}, grid?: ViewGrid, axes?: ViewAxes},
 *   wireframe?: {edges?: {color?: string}, grid?: ViewGrid, axes?: ViewAxes}
 * }} ViewModes
 */
/** @typedef {{enabled?: boolean, axis?: "x" | "y" | "z", at?: number, invert?: boolean}} ViewClip */
/** @typedef {{enabled?: boolean, amount?: number}} ViewExploded */
/**
 * The public Viewer / snapshot vocabulary: the file's mode, what every mode shares (appearance,
 * camera, clip, exploded) and each mode's own settings, and Render's, under `modes`.
 * @typedef {Object} ViewSettings
 * @property {ViewMode} [mode]
 * @property {"light" | "dark"} [appearance]
 * @property {{projection?: "orthographic" | "perspective", focalLength?: number}} [camera]
 * @property {ViewClip} [clip]
 * @property {ViewExploded} [exploded]
 * @property {ViewModes} [modes]
 */
/** @typedef {{enabled: boolean, projection: "orthographic" | "perspective", focalLength: number}} ViewCamera */
/** @typedef {{enabled: boolean, style: "shaded" | "flat" | "hidden" | "off", colorMode: "original" | "single" | "by-part", color: string, colors: string[], opacity: number}} ViewSurfaces */
/** @typedef {{enabled: boolean, visibility: "visible" | "all", color: string}} ViewEdges */
/** @typedef {{enabled: boolean, quality: "preview" | "final", exposure: number, rotation: number, size: number, fill: number}} ViewLighting */
/** @typedef {{enabled: boolean, color: string, opacity: number}} ViewPaint */
/** @typedef {ViewPaint & {placement: "origin" | "lowest", finish: "matte" | "glossy"}} ViewFloor */
/**
 * What the renderer draws: the look on screen's settings mapped onto the scene's groups.
 * @typedef {Object} ResolvedViewSettings
 * @property {ViewLook} mode
 * @property {"light" | "dark"} appearance
 * @property {ViewCamera} camera
 * @property {ViewSurfaces} surfaces
 * @property {ViewEdges} edges
 * @property {ViewLighting} lighting
 * @property {ViewPaint} background
 * @property {ViewFloor} floor
 * @property {ViewPaint & {density: number}} grid
 * @property {ViewPaint} axes
 * @property {ReturnType<typeof normalizeStepClipSettings>} clip
 * @property {{enabled: boolean, amount: number}} exploded
 */
/** @typedef {{appearance?: "light" | "dark", lightingQuality?: "preview" | "final"}} ViewDefaults */

// A file's display modes. Render is none of them: it is the photographic look Preview and a
// snapshot's `render` ask for on their own, drawn with its own settings (`modes.render`).
export const VIEW_MODE_VALUES = Object.freeze(["solid", "xray", "hidden-line", "wireframe"]);
// Whose settings `modes` keeps: each display mode's, and Render's.
export const VIEW_LOOK_VALUES = Object.freeze([...VIEW_MODE_VALUES, "render"]);
export const VIEW_MODES = Object.freeze([
  Object.freeze({ id: "solid", label: "Solid" }),
  Object.freeze({ id: "xray", label: "X-ray" }),
  Object.freeze({ id: "hidden-line", label: "Hidden line" }),
  Object.freeze({ id: "wireframe", label: "Wireframe" })
]);
export const VIEW_SETTINGS_KEYS = Object.freeze(["mode", "appearance", "camera", "clip", "exploded", "modes"]);
export const VIEW_CAMERA_KEYS = Object.freeze(["projection", "focalLength"]);
// Each look's settings, in the order its menu lists them.
export const VIEW_MODE_SETTING_KEYS = Object.freeze({
  solid: Object.freeze(["parts", "flat", "edges", "grid", "axes"]),
  render: Object.freeze(["parts", "lighting", "background", "floor"]),
  xray: Object.freeze(["parts", "opacity", "edges", "grid", "axes"]),
  "hidden-line": Object.freeze(["edges", "grid", "axes"]),
  wireframe: Object.freeze(["edges", "grid", "axes"])
});
// The fields of a setting that is a group. Solid's edges alone can be turned off: the other
// modes are made of theirs.
const GROUP_FIELDS = {
  parts: ["colorMode", "color", "colors"],
  edges: ["color"],
  grid: ["enabled", "color", "opacity", "density"],
  axes: ["enabled", "color", "opacity"],
  lighting: ["quality", "exposure", "rotation", "size", "fill"],
  background: ["color", "opacity"],
  floor: ["enabled", "placement", "color", "opacity", "finish"]
};
const groupFields = (mode, group) => (mode === "solid" && group === "edges" ? ["enabled", "color"] : GROUP_FIELDS[group]);
export const VIEW_COLOR_MODE_VALUES = Object.freeze(["original", "single", "by-part"]);
// Matte is the studio floor; glossy also reflects the model (photographicStudio.js).
export const VIEW_FLOOR_FINISH_VALUES = Object.freeze(["matte", "glossy"]);
export const VIEW_FLOOR_PLACEMENT_VALUES = Object.freeze(["lowest", "origin"]);
export const VIEW_LIGHTING_QUALITY_VALUES = Object.freeze(["preview", "final"]);

const HEX = /^#(?:[0-9a-fA-F]{3}){1,2}$/;
const object = value => Boolean(value) && typeof value === "object" && !Array.isArray(value);
function keys(value, allowed, field) {
  if (!object(value)) throw new Error(`${field} must be an object`);
  const unknown = Object.keys(value).filter(key => !allowed.includes(key));
  if (unknown.length) throw new Error(`Unsupported ${field} fields: ${unknown.join(", ")}`);
}
function choice(value, allowed, field) {
  if (!allowed.includes(value)) throw new Error(`${field} must be one of: ${allowed.join(", ")}`);
  return value;
}
function number(value, min, max, field) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new Error(`${field} must be a finite number between ${min} and ${max}`);
  }
  return value;
}
function boolean(value, field) {
  if (typeof value !== "boolean") throw new Error(`${field} must be a boolean`);
  return value;
}
function color(value, field) {
  if (typeof value !== "string" || !HEX.test(value.trim())) throw new Error(`${field} must be a hex color`);
  const text = value.trim().toLowerCase();
  return text.length === 4 ? `#${[...text.slice(1)].map(c => c + c).join("")}` : text;
}
const CHOICES = {
  projection: ["orthographic", "perspective"], colorMode: VIEW_COLOR_MODE_VALUES, quality: VIEW_LIGHTING_QUALITY_VALUES,
  placement: VIEW_FLOOR_PLACEMENT_VALUES, finish: VIEW_FLOOR_FINISH_VALUES
};
function field(key, entry, path) {
  if (key === "enabled") return boolean(entry, path);
  if (key === "color") return color(entry, path);
  if (key === "colors") {
    if (!Array.isArray(entry) || entry.length < 1 || entry.length > 50) throw new Error(`${path} must contain 1 to 50 hex colors`);
    return entry.map((c, i) => color(c, `${path}[${i}]`));
  }
  if (key === "opacity" || key === "fill") return number(entry, 0, 1, path);
  if (key === "focalLength") return number(entry, 20, 200, path);
  if (key === "exposure") return number(entry, -5, 5, path);
  if (key === "rotation") return number(entry, -180, 180, path);
  if (key === "size") return number(entry, 0.25, 3, path);
  if (key === "density") return number(entry, 0.25, 4, path);
  return choice(entry, CHOICES[key], path);
}
function group(value, allowed, path) {
  keys(value, allowed, path);
  return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, field(key, entry, `${path}.${key}`)]));
}
function normalizeMode(mode, value) {
  const path = `display.modes.${mode}`;
  keys(value, VIEW_MODE_SETTING_KEYS[mode], path);
  const result = {};
  for (const [key, entry] of Object.entries(value)) {
    if (key === "flat") result.flat = boolean(entry, `${path}.flat`);
    else if (key === "opacity") result.opacity = number(entry, 0, 1, `${path}.opacity`);
    else result[key] = group(entry, groupFields(mode, key), `${path}.${key}`);
  }
  return result;
}

/**
 * Validate without filling omitted settings or pinning inherited appearance.
 * @param {unknown} input
 * @returns {ViewSettings & {mode: ViewMode}}
 */
export function normalizeViewSettings(input = {}) {
  keys(input, VIEW_SETTINGS_KEYS, "display");
  const result = { mode: choice(Object.hasOwn(input, "mode") ? input.mode : "solid", VIEW_MODE_VALUES, "display.mode") };
  if (Object.hasOwn(input, "appearance")) result.appearance = choice(input.appearance, ["light", "dark"], "display.appearance");
  if (Object.hasOwn(input, "camera")) result.camera = group(input.camera, VIEW_CAMERA_KEYS, "display.camera");
  // Tools keep their established validation and sparse payload; no mode owns them.
  for (const name of ["clip", "exploded"]) {
    if (Object.hasOwn(input, name)) {
      validateDisplaySettings({ [name]: input[name] });
      result[name] = structuredClone(input[name]);
    }
  }
  if (Object.hasOwn(input, "modes")) {
    keys(input.modes, VIEW_LOOK_VALUES, "display.modes");
    const modes = {};
    for (const mode of VIEW_LOOK_VALUES) {
      if (Object.hasOwn(input.modes, mode)) modes[mode] = normalizeMode(mode, input.modes[mode]);
    }
    result.modes = modes;
  }
  return result;
}

// Where Render's floor stands: at the model's lowest point, under it, rather than the document's
// Z=0 plane ("origin", Model origin), which a person may still choose. The Render recipe's own
// backdrop default (`sceneSettings.js`) is this too: one value for the Viewer and the snapshot.
export const RENDER_FLOOR_PLACEMENT = "lowest";

// A line drawing's edges (Hidden line, Wireframe) have no surfaces behind them, only the backdrop:
// the dark edges that read on a part's faces vanish on a dark one, so there they are light.
function lineDrawingEdgeColor(appearance) {
  return appearance === "dark" ? "#c9d3de" : "#253443";
}

/**
 * Every setting of `mode` (a display mode, or Render), at its defaults, in `appearance`. A grid
 * turned on is a measuring grid, plain to see without competing with the model's edges; the axes
 * are their own toggle.
 * @param {ViewLook} mode @param {ViewDefaults} options
 */
export function viewModeDefaults(mode, { appearance = "light", lightingQuality = "final" } = {}) {
  choice(mode, VIEW_LOOK_VALUES, "mode");
  const dark = appearance === "dark";
  const all = {
    parts: { colorMode: "original", color: DEFAULT_PART_COLOR_SETTINGS.color, colors: [...DEFAULT_PART_COLOR_SETTINGS.colors] },
    flat: false,
    opacity: 0.22,
    edges: mode === "solid" ? { enabled: true, color: "#253443" }
      : { color: mode === "xray" ? "#253443" : lineDrawingEdgeColor(appearance) },
    grid: { enabled: false, color: dark ? "#64768a" : "#94a3b8", opacity: dark ? 0.42 : 0.38, density: 2 },
    axes: { enabled: false, color: dark ? "#495665" : "#cbd5e1", opacity: 0.28 },
    lighting: { quality: choice(lightingQuality, VIEW_LIGHTING_QUALITY_VALUES, "lightingQuality"), exposure: 0, rotation: 0, size: 1, fill: 0.25 },
    background: { color: dark ? "#121315" : "#ffffff", opacity: 1 },
    floor: { enabled: true, placement: RENDER_FLOOR_PLACEMENT, color: dark ? "#121315" : "#e7e7e5", opacity: 0.6, finish: "matte" }
  };
  return Object.fromEntries(VIEW_MODE_SETTING_KEYS[mode].map(key => [key, all[key]]));
}

// What a view offers is declared by whoever shows it, as explicit opt-in lists: the modes it
// lists and the settings it shows. Nothing here knows who opts in to what.
export const VIEW_SETTING_IDS = Object.freeze(["parts", "flat", "opacity", "edges", "lighting", "background", "floor", "grid", "axes", "clip", "exploded"]);

/**
 * @typedef {object} ViewFeatures
 * @property {readonly string[]} modes  Modes it lists (`VIEW_MODE_VALUES`).
 * @property {readonly string[]} settings  Settings it shows (`VIEW_SETTING_IDS`).
 */

/** Every mode and setting. @type {ViewFeatures} */
export const ALL_VIEW_FEATURES = Object.freeze({ modes: VIEW_MODE_VALUES, settings: VIEW_SETTING_IDS });

// A view without edges. X-ray, Hidden line and Wireframe are made of edges; drawn from
// triangles alone they would show the tessellation. Clip and Explode section solids and move
// parts, which such a view does not have either.
/** @type {ViewFeatures} */
export const EDGELESS_VIEW_FEATURES = Object.freeze({
  modes: Object.freeze(["solid"]),
  settings: Object.freeze(VIEW_SETTING_IDS.filter(id => !["edges", "clip", "exploded"].includes(id)))
});

// A triangle mesh, a robot and a GLB have no edges either, but Clip cuts whatever a scene drew
// (`lib/viewer/sceneClip.js`), and a GLB of several parts moves them apart (`sceneExplode.js`).
/** STL, 3MF, URDF, SRDF, SDF and a GLB of one part: Solid, cut by Clip. @type {ViewFeatures} */
export const MESH_VIEW_FEATURES = Object.freeze({
  modes: EDGELESS_VIEW_FEATURES.modes,
  settings: Object.freeze([...EDGELESS_VIEW_FEATURES.settings, "clip"])
});
/** A GLB of several parts: a mesh's, and Explode. @type {ViewFeatures} */
export const PARTED_MESH_VIEW_FEATURES = Object.freeze({
  modes: EDGELESS_VIEW_FEATURES.modes,
  settings: Object.freeze([...MESH_VIEW_FEATURES.settings, "exploded"])
});

/**
 * What a file of `kind` offers, for the viewer and a snapshot alike: a STEP everything; a GLB a
 * mesh's, and Explode when it has several parts (`parts`); every other 3D file a mesh's; a
 * drawing (DXF) no more than an edgeless view.
 * @param {string} kind  The file's extension, without the dot.
 * @param {{ parts?: number }} [scene]
 * @returns {ViewFeatures}
 */
export function viewFeaturesForKind(kind, { parts = 0 } = {}) {
  const normalized = String(kind || "").trim().toLowerCase();
  if (normalized === "step" || normalized === "stp") return ALL_VIEW_FEATURES;
  // A drawing is no scene: nothing in it to cut.
  if (normalized === "dxf") return EDGELESS_VIEW_FEATURES;
  return normalized === "glb" && parts > 1 ? PARTED_MESH_VIEW_FEATURES : MESH_VIEW_FEATURES;
}

/** A caller's lists, with the full set standing in for any it left out. @returns {ViewFeatures} */
export function normalizeViewFeatures(features = null) {
  return {
    modes: Array.isArray(features?.modes) ? features.modes : ALL_VIEW_FEATURES.modes,
    settings: Array.isArray(features?.settings) ? features.settings : ALL_VIEW_FEATURES.settings
  };
}

/**
 * Every setting of `mode` as it stands: its defaults under what the person changed, without the
 * settings `features` does not offer.
 * @param {unknown} input @param {ViewLook} mode @param {ViewDefaults & { features?: ViewFeatures }} options
 */
export function resolveViewModeSettings(input, mode, { appearance = "light", lightingQuality = "final", features = ALL_VIEW_FEATURES } = {}) {
  const saved = normalizeViewSettings(input);
  const offered = normalizeViewFeatures(features).settings;
  const base = viewModeDefaults(mode, { appearance: saved.appearance ?? (appearance === "dark" ? "dark" : "light"), lightingQuality });
  const own = saved.modes?.[mode] || {};
  const result = {};
  for (const [key, value] of Object.entries(base)) {
    if (!offered.includes(key)) continue;
    result[key] = object(value) ? { ...value, ...own[key] } : own[key] ?? value;
  }
  return result;
}

/** Whether `mode`'s settings differ from its defaults: what Reset <Mode> takes away. @param {unknown} input @param {ViewLook} mode */
export function viewModeChanged(input, mode, options = {}) {
  const { appearance } = normalizeViewSettings(input);
  return JSON.stringify(resolveViewModeSettings(input, mode, options)) !==
    JSON.stringify(resolveViewModeSettings(appearance ? { appearance } : {}, mode, options));
}

/** `mode`'s settings back at its defaults; every other mode's, and what modes share, kept. @returns {ViewSettings & {mode: ViewMode}} */
export function resetViewMode(input, mode) {
  const source = normalizeViewSettings(input);
  choice(mode, VIEW_LOOK_VALUES, "mode");
  if (!source.modes?.[mode]) return source;
  const { [mode]: _reset, ...modes } = source.modes;
  const { modes: _all, ...rest } = source;
  return Object.keys(modes).length ? { ...rest, modes } : rest;
}

/**
 * The look on screen's settings, mapped onto the scene's groups: the file's mode, or with `render`
 * Render, whatever the mode (Preview's, a snapshot's `render`). `features` resolves a view that
 * did not opt into everything: a saved mode it does not list resolves as Solid, and a setting it
 * does not show is its default (a group off). The saved settings are left alone, so they still
 * mean X-ray, or edges, in the next view that offers them. Render is always in perspective.
 *
 * @param {unknown} input @param {ViewDefaults & { features?: ViewFeatures, render?: boolean }} options @returns {ResolvedViewSettings}
 */
export function resolveViewSettings(input = {}, { appearance = "light", lightingQuality = "final", features = ALL_VIEW_FEATURES, render: rendered = false } = {}) {
  const offered = normalizeViewFeatures(features);
  const offers = id => offered.settings.includes(id);
  const saved = normalizeViewSettings(input);
  const mode = rendered ? "render" : offered.modes.includes(saved.mode) ? saved.mode : "solid";
  const resolvedAppearance = saved.appearance ?? (appearance === "dark" ? "dark" : "light");
  const options = { appearance: resolvedAppearance, lightingQuality, features };
  const own = resolveViewModeSettings(saved, mode, options);
  const solid = viewModeDefaults("solid", options);
  const render = viewModeDefaults("render", options);
  const parts = own.parts ?? solid.parts;
  const style = mode === "hidden-line" ? "hidden" : mode === "wireframe" ? "off" : own.flat ? "flat" : "shaded";
  const edges = mode === "render" ? { enabled: false, visibility: "visible", color: solid.edges.color }
    : { enabled: own.edges?.enabled ?? true, visibility: mode === "xray" || mode === "wireframe" ? "all" : "visible",
      color: own.edges?.color ?? solid.edges.color };
  const guide = name => ({ ...(own[name] ?? { ...solid[name], enabled: false }) });
  const lit = mode === "render";
  return {
    mode,
    appearance: resolvedAppearance,
    camera: {
      enabled: true,
      projection: lit ? "perspective" : saved.camera?.projection ?? "orthographic",
      focalLength: saved.camera?.focalLength ?? 50
    },
    surfaces: { enabled: true, style, ...parts, colors: [...parts.colors], opacity: mode === "xray" ? own.opacity ?? 0.22 : 1 },
    edges: offers("edges") ? edges : { ...edges, enabled: false },
    lighting: { enabled: lit && offers("lighting"), ...(own.lighting ?? render.lighting) },
    background: { enabled: lit && offers("background"), ...(own.background ?? render.background) },
    floor: { ...(own.floor ?? render.floor), enabled: lit && offers("floor") && (own.floor ?? render.floor).enabled },
    grid: guide("grid"),
    axes: guide("axes"),
    clip: normalizeStepClipSettings(offers("clip") ? saved.clip ?? DEFAULT_STEP_CLIP_SETTINGS : DEFAULT_STEP_CLIP_SETTINGS),
    exploded: normalizeExplodedViewSettings(offers("exploded") ? saved.exploded : null)
  };
}
