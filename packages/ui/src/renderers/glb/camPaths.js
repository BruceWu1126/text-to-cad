/**
 * A CAM result's toolpaths and tool on the model. The file draws each operation's path as a glTF `LINES` primitive,
 * which three draws one pixel wide in the file's one blue whatever the theme: those are hidden here and each is drawn
 * again from the same buffers (`readPath`) as screen-space lines (`createScreenSpaceLineSegments`, core's fat lines),
 * one line object per kind of move (`KIND_STYLE`: feed, rapid, plunge, lead), coloured from the theme's tokens, the
 * rapids dashed and fainter. `setTime(t)` draws each path up to job time `t` (the segment under way cut where the tool
 * is); the tool (`createCamTool`) stands on the path there, turned the way its setup holds it. Everything is put back
 * as the file had it on `dispose()`.
 */
import { createScreenSpaceLineSegments } from "@text-to-cad/core/common/renderEdges.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { cssColorToHex } from "../kit/look/chromeBackdrop.js";
import { KIND_STYLE, segmentsBegun, toolPlacementAt } from "./camResult.js";

const LINES = Object.freeze({ LineSegments2, LineSegmentsGeometry, LineMaterial });

/** How each kind of move is drawn: its width in device pixels, its opacity, and whether it is dashed. */
export const PATH_LOOK = Object.freeze({
  feed: Object.freeze({ width: 2, opacity: 1, dashed: false, order: 6 }),
  plunge: Object.freeze({ width: 2, opacity: 1, dashed: false, order: 7 }),
  lead: Object.freeze({ width: 2, opacity: 1, dashed: false, order: 6 }),
  rapid: Object.freeze({ width: 1.25, opacity: 0.7, dashed: true, order: 5 }),
});

/** The theme token each kind is drawn in: a feed the info blue, a plunge the warning amber, a lead the success green, a rapid muted. */
export const PATH_TOKENS = Object.freeze({ feed: "--foreground-info", plunge: "--foreground-warning", lead: "--foreground-success", rapid: "--muted-foreground" });
// What each is drawn in where the tokens cannot be read: the tokens' own values in each theme.
const FALLBACK = Object.freeze({
  light: Object.freeze({ feed: "#1447e6", plunge: "#973c00", lead: "#016630", rapid: "#737373" }),
  dark: Object.freeze({ feed: "#7dabff", plunge: "#ffd230", lead: "#7bf1a8", rapid: "#aeaeae" }),
});

/**
 * The colours the paths are drawn in, read at draw time off the viewer's own element (a host may switch the theme on
 * any ancestor), each falling back to its token's value in `colorScheme`.
 * @param {Element | null} element
 * @param {"light" | "dark"} colorScheme
 */
export function pathColours(element, colorScheme = "light") {
  const fallback = FALLBACK[colorScheme === "dark" ? "dark" : "light"];
  const view = element?.ownerDocument?.defaultView;
  const read = (name) => {
    try { return typeof view?.getComputedStyle === "function" ? view.getComputedStyle(element).getPropertyValue(name).trim() : ""; } catch { return ""; }
  };
  return Object.fromEntries(Object.entries(PATH_TOKENS).map(([kind, token]) => [kind, cssColorToHex(read(token)) || fallback[kind]]));
}

/** One path's segments of one style, in the order they are cut: a copy of their ends and their start and end times. */
function splitPath(path) {
  const groups = new Map();
  for (let segment = 0; segment < path.count; segment += 1) {
    const style = KIND_STYLE[path.kinds[segment]] || "feed";
    if (!groups.has(style)) groups.set(style, []);
    groups.get(style).push(segment);
  }
  return Array.from(groups, ([style, segments]) => {
    const positions = new Float32Array(segments.length * 6);
    const starts = new Float32Array(segments.length);
    const ends = new Float32Array(segments.length);
    segments.forEach((segment, index) => {
      positions.set(path.positions.subarray(segment * 6, segment * 6 + 6), index * 6);
      starts[index] = path.starts[segment];
      ends[index] = path.ends[segment];
    });
    return { style, positions, starts, ends };
  });
}

/**
 * The fat-line paths of a CAM result (`readCamResult`), drawn beside the file's own `LINES` (hidden while these
 * live). `style({ colours, rapids, hidden, isolated })` colours them and says which show: rapids or not, and per
 * operation (`hidden`, a set of operation indices; `isolated`, one operation alone, or null). `setTime(t)` draws
 * each up to job time `t`. `dispose()` removes them and shows the file's lines again.
 */
export function createCamPaths(cam) {
  const drawn = [];
  const restored = [];
  let size = 0;
  for (const [index, path] of cam.nodes.paths) {
    const object = path.object;
    restored.push([object, object.visible]);
    object.visible = false;
    object.geometry.computeBoundingSphere?.();
    size = Math.max(size, object.geometry.boundingSphere?.radius || 0);
    for (const group of splitPath(path)) {
      const look = PATH_LOOK[group.style];
      const line = createScreenSpaceLineSegments(LINES, group.positions.slice(), { color: "#000000", opacity: look.opacity, lineWidth: look.width,
        renderOrder: look.order, depthTest: true, depthWrite: false });
      if (!line) continue;
      line.name = `cam-path:${index}:${group.style}`;
      line.raycast = () => {};
      line.position.copy(object.position);
      line.quaternion.copy(object.quaternion);
      line.scale.copy(object.scale);
      object.parent?.add(line);
      const buffer = line.geometry.attributes.instanceEnd.data;
      drawn.push({ index, style: group.style, line, starts: group.starts, ends: group.ends, source: group.positions, buffer, cut: -1, wanted: true });
    }
  }
  // Rapids dashed, at a dash a model's size can read: a hundredth of the paths' reach.
  const dash = Math.max(size * 0.012, 1e-6);
  for (const entry of drawn) {
    if (!PATH_LOOK[entry.style].dashed) continue;
    entry.line.computeLineDistances();
    entry.line.material.dashed = true;
    entry.line.material.dashSize = dash;
    entry.line.material.gapSize = dash * 0.8;
  }

  let time = Infinity;
  const restoreCut = (entry) => {
    if (entry.cut < 0) return;
    entry.buffer.array.set(entry.source.subarray(entry.cut * 6 + 3, entry.cut * 6 + 6), entry.cut * 6 + 3);
    entry.buffer.needsUpdate = true;
    entry.cut = -1;
  };
  const clip = (entry) => {
    const count = entry.starts.length;
    const begun = time >= entry.ends[count - 1] ? count : segmentsBegun(entry.starts, time);
    entry.line.geometry.instanceCount = begun;
    entry.line.visible = entry.wanted && begun > 0;
    const last = begun - 1;
    const under = last >= 0 && time < entry.ends[last];
    if (entry.cut !== last || !under) restoreCut(entry);
    if (!under) return;
    // The segment the tool is on stops where the tool is.
    const span = entry.ends[last] - entry.starts[last];
    const share = span > 0 ? Math.min(Math.max((time - entry.starts[last]) / span, 0), 1) : 1;
    const at = last * 6;
    for (let axis = 0; axis < 3; axis += 1) {
      entry.buffer.array[at + 3 + axis] = entry.source[at + axis] + (entry.source[at + 3 + axis] - entry.source[at + axis]) * share;
    }
    entry.buffer.needsUpdate = true;
    entry.cut = last;
  };

  return {
    lines: drawn.map((entry) => entry.line),
    style({ colours, rapids = true, hidden = null, isolated = null }) {
      for (const entry of drawn) {
        entry.line.material.color.set(colours[entry.style]);
        entry.wanted = (rapids || entry.style !== "rapid") && (isolated === null ? !hidden?.has(entry.index) : isolated === entry.index);
        clip(entry);
      }
    },
    setTime(t) {
      time = Number.isFinite(t) ? t : Infinity;
      for (const entry of drawn) clip(entry);
    },
    dispose() {
      for (const entry of drawn) {
        entry.line.removeFromParent();
        entry.line.userData.beforeDispose?.();
        entry.line.geometry.dispose();
        entry.line.material.dispose();
      }
      drawn.length = 0;
      for (const [object, visible] of restored) object.visible = visible;
    },
  };
}

/**
 * The result's tools and what holds the stock, as the job is played: `place(t, show)` stands the tool cutting at job
 * time `t` on its path, turned the way its setup holds it, and hides every other tool; `show` gates the tool, the
 * stock and the fixtures (Display's). `dispose()` puts every node back as the file placed it.
 */
export function createCamTool(cam) {
  const rest = new Map();
  const remember = (node) => { if (node && !rest.has(node)) rest.set(node, { visible: node.visible, position: node.position.clone(), quaternion: node.quaternion.clone() }); };
  for (const node of cam.nodes.tools.values()) remember(node);
  for (const node of cam.nodes.fixtures.values()) remember(node);
  remember(cam.nodes.stock);
  const point = [0, 0, 0];
  let placed = null;
  return {
    /** Where the tool stood last: the operation, the tool's id and its tip. */
    get placed() { return placed; },
    place(t, show = {}) {
      const at = toolPlacementAt(cam, Number.isFinite(t) ? t : cam.duration, point);
      placed = at ? { operation: at.operation, tool: at.tool, point: [...at.point] } : null;
      for (const [id, node] of cam.nodes.tools) {
        const active = Boolean(at) && id === at.tool;
        node.visible = show.tool !== false && active;
        if (!active) continue;
        node.position.fromArray(at.point);
        node.quaternion.fromArray(at.quaternion);
      }
      if (cam.nodes.stock) cam.nodes.stock.visible = show.stock !== false && rest.get(cam.nodes.stock).visible;
      for (const node of cam.nodes.fixtures.values()) node.visible = show.fixtures !== false && rest.get(node).visible;
    },
    dispose() {
      for (const [node, was] of rest) {
        node.visible = was.visible;
        node.position.copy(was.position);
        node.quaternion.copy(was.quaternion);
      }
      placed = null;
    },
  };
}
