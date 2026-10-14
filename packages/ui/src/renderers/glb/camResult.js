/**
 * What a GLB `cadgen cam plan` wrote holds, read once (`readCamResult`): the job on the part mesh's extras, the
 * scene's stock, fixtures and tools by their node names, and each operation's toolpath as the `LINES` primitive it is
 * drawn as, with its time, kind and operation per vertex. The contract is cadgen's
 * (`packages/cadgen/src/cadgen/_internal/cam/outputs.py`): the whole file in the part's frame, in glTF metres and
 * axes; nodes `part`, `stock`, `fixture:<setup>`, `tool:<id>` (its mesh in the tool's own frame, the tip at the
 * origin, the axis along +Y), `path:<op index>:<op>`; on each path `_T` (the job's clock, seconds, non-decreasing
 * along it), `_KIND` (rapid 0, feed 1, plunge 2, arc 3, lead 4, cycle 5) and `_OP`, which GLTFLoader hands over
 * lower-cased. Pure: no React, and three.js only through the scene it is given.
 */

/** What cadgen writes as `generator` on the part mesh's extras. */
export const CAM_GENERATOR = "cadgen cam";

/** `_KIND`'s codes, by the move each stands for. */
export const KIND_CODES = Object.freeze({ rapid: 0, feed: 1, plunge: 2, arc: 3, lead: 4, cycle: 5 });

/**
 * How each kind is drawn (`camPaths.js`): an arc as a feed (it is one, round), a canned cycle as the plunges it
 * stands for, so a path is four kinds of line: feed, rapid, plunge and lead.
 */
export const KIND_STYLE = Object.freeze({ 0: "rapid", 1: "feed", 2: "plunge", 3: "feed", 4: "lead", 5: "plunge" });

const EMPTY = Object.freeze([]);
const finite = (value, fallback = 0) => (Number.isFinite(Number(value)) && value !== null && value !== "" ? Number(value) : fallback);
const text = (value) => (typeof value === "string" ? value : value === null || value === undefined ? "" : String(value));
const list = (value) => (Array.isArray(value) ? value : EMPTY);
const record = (value) => (value && typeof value === "object" && !Array.isArray(value) ? value : {});

/** The name a node was written with: GLTFLoader keeps it in `userData.name` and sanitises `name` (a ":" goes). */
export const nodeName = (object) => text(object?.userData?.name || object?.name);

/** The part mesh of a CAM result: the mesh `cadgen cam plan` wrote, with its operations. null for any other scene. */
export function camPartMesh(root) {
  let found = null;
  root?.traverse?.((object) => {
    if (!found && object?.isMesh && object.userData?.generator === CAM_GENERATOR && Array.isArray(object.userData.operations)) found = object;
  });
  return found;
}

/**
 * One operation's toolpath as its `LINES` primitive holds it: `positions` (six numbers a segment, start then end, in
 * the node's frame), `starts` and `ends` (the job time at each end of each segment, seconds) and `kinds` (`_KIND`
 * per segment). null for a node that is not one (no positions, or no `_t`).
 */
export function readPath(object) {
  const geometry = object?.geometry;
  const position = geometry?.getAttribute?.("position");
  const time = geometry?.getAttribute?.("_t");
  if (!position || !time || position.count < 2 || time.count !== position.count) return null;
  const kind = geometry.getAttribute("_kind");
  const count = Math.floor(position.count / 2);
  const positions = new Float32Array(count * 6);
  const starts = new Float32Array(count);
  const ends = new Float32Array(count);
  const kinds = new Uint8Array(count);
  for (let segment = 0; segment < count; segment += 1) {
    const a = segment * 2;
    const b = a + 1;
    positions.set([position.getX(a), position.getY(a), position.getZ(a), position.getX(b), position.getY(b), position.getZ(b)], segment * 6);
    starts[segment] = time.getX(a);
    ends[segment] = Math.max(time.getX(b), time.getX(a));
    kinds[segment] = kind ? Math.round(kind.getX(a)) : KIND_CODES.feed;
  }
  return { object, positions, starts, ends, kinds, count };
}

/**
 * Where a path has the tool's tip at job time `t`, in its node's frame (`out`, three numbers): on the segment it is
 * moving along, by its share of that segment's time; between two (a dwell, a tool change) at the end of the last one
 * begun; before the path starts at its start, after it at its end.
 */
export function pathPointAt(path, t, out = [0, 0, 0]) {
  const { positions, starts, ends, count } = path;
  if (!count) return out;
  // The last segment that has begun by `t`.
  let low = 0;
  let high = count - 1;
  if (t <= starts[0]) { out[0] = positions[0]; out[1] = positions[1]; out[2] = positions[2]; return out; }
  while (low < high) {
    const middle = (low + high + 1) >> 1;
    if (starts[middle] <= t) low = middle; else high = middle - 1;
  }
  const at = low * 6;
  const span = ends[low] - starts[low];
  const share = span > 0 ? Math.min(Math.max((t - starts[low]) / span, 0), 1) : 1;
  for (let axis = 0; axis < 3; axis += 1) out[axis] = positions[at + axis] + (positions[at + 3 + axis] - positions[at + axis]) * share;
  return out;
}

/** How many of a path's segments have begun by job time `t` (the ones drawn while it plays to `t`). */
export function segmentsBegun(starts, t) {
  let low = 0;
  let high = starts.length;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (starts[middle] < t) low = middle + 1; else high = middle;
  }
  return low;
}

/** The operation a finding's item names (cadgen writes "operations[2] (contour_2d)"), as its index; null where it names none. */
function operationIndex(value) {
  const match = /^operations\[(\d+)\]/.exec(text(value));
  return match ? Number(match[1]) : null;
}

/** The first operation a finding is about, as its index, or null: what choosing it isolates. */
export const findingOperation = (finding) => finding?.items?.find((item) => Number.isInteger(item.operation))?.operation ?? null;

/** A finding as the findings card lists it (`kit/status/findings.jsx`), `index` its place in the file; null for a malformed one. */
function readFinding(finding, index) {
  if (!finding || (typeof finding.summary !== "string" && typeof finding.description !== "string")) return null;
  return {
    check: text(finding.check || "cam"), severity: finding.severity === "error" ? "error" : finding.severity === "info" ? "info" : "warning",
    type: text(finding.type), summary: text(finding.summary), description: text(finding.description), index,
    items: list(finding.items).map((item) => ({ text: text(item?.text), ref: item?.ref ? text(item.ref) : null, operation: operationIndex(item?.operation) })),
  };
}

function readOperation(raw, position) {
  const entry = record(raw);
  const start = finite(entry.start_s);
  const time = Math.max(finite(entry.time_s), 0);
  return {
    index: Number.isInteger(entry.index) ? entry.index : position, op: text(entry.op), word: text(entry.word || entry.op), setup: text(entry.setup),
    tool: text(entry.tool), tools: list(entry.tools).map(text), rpm: finite(entry.rpm, null), feed: finite(entry.feed_mm_min, null),
    plunge: finite(entry.plunge_mm_min, null), start, time, end: start + time, cut: finite(entry.cut_length_mm, null),
    rapid: finite(entry.rapid_length_mm, null), moves: finite(entry.moves, 0), node: entry.node ? text(entry.node) : null, lines: list(entry.lines).map(text),
  };
}

function readTool(raw) {
  const entry = record(raw);
  const use = record(list(entry.uses)[0]);
  return {
    id: text(entry.id), number: finite(entry.number, null), type: text(entry.type), diameter: finite(entry.diameter_mm, null),
    flutes: finite(entry.flutes, null), stickout: finite(entry.stickout_mm, null), rpm: finite(use.rpm, null), feed: finite(use.feed_mm_min, null),
    operations: list(entry.uses).map((each) => finite(record(each).operation, -1)).filter((index) => index >= 0),
    node: entry.node ? text(entry.node) : null,
  };
}

function readSetup(raw) {
  const entry = record(raw);
  const quaternion = list(entry.quaternion).map(Number);
  const fixture = record(entry.fixture);
  return {
    id: text(entry.id), direction: text(entry.direction), origin: Array.isArray(entry.origin) ? entry.origin.map(Number).join(", ") : text(entry.origin),
    quaternion: quaternion.length === 4 && quaternion.every(Number.isFinite) ? quaternion : [0, 0, 0, 1],
    fixture: { type: text(fixture.type || "none"), node: fixture.node ? text(fixture.node) : null }, nc: entry.nc ? text(entry.nc) : null,
  };
}

/**
 * A CAM result as the viewer reads it, or null for a scene that is not one: `mesh` (the part, with the extras),
 * `document` and `occurrence` (the part it was planned from), `machine`, `setups`, `tools`, `operations` (in the
 * job's order, each with its `start`, `time` and `end` on the job's clock), `summary`, `findings` (the card's shape;
 * `info` ones are the summary's to say), `duration` (the job's clock, seconds), `show` (the view's `show`), and
 * `nodes`: `stock`, `fixtures` (by setup), `tools` (by id) and `paths` (by operation index, each `readPath`'s).
 */
export function readCamResult(root) {
  const mesh = camPartMesh(root);
  if (!mesh) return null;
  const extras = mesh.userData;
  const named = new Map();
  root.traverse((object) => { const name = nodeName(object); if (name && !named.has(name)) named.set(name, object); });
  const operations = list(extras.operations).map(readOperation);
  const tools = list(extras.tools).map(readTool);
  const setups = list(extras.setups).map(readSetup);
  const paths = new Map();
  for (const operation of operations) {
    const path = operation.node ? readPath(named.get(operation.node)) : null;
    if (path) paths.set(operation.index, path);
  }
  const toolNodes = new Map();
  for (const tool of tools) { const node = tool.node ? named.get(tool.node) : null; if (node) toolNodes.set(tool.id, node); }
  const fixtures = new Map();
  for (const setup of setups) { const node = setup.fixture.node ? named.get(setup.fixture.node) : null; if (node) fixtures.set(setup.id, node); }
  const summary = record(extras.summary);
  const machine = record(extras.machine);
  const lastEnd = Math.max(0, ...operations.map((operation) => operation.end), ...Array.from(paths.values(), (path) => path.ends[path.count - 1] || 0));
  const duration = finite(record(extras.playback).duration_s, 0) || finite(summary.cycle_time_s, 0) || lastEnd;
  return {
    mesh, root, document: text(extras.document), occurrence: text(extras.occurrence),
    machine: { kind: text(machine.kind), post: text(machine.post || summary.post), units: text(machine.units), rapidAssumed: machine.rapid_assumed === true },
    setups, tools, operations,
    summary: { cycleTime: finite(summary.cycle_time_s, duration), cutLength: finite(summary.cut_length_mm, null), rapidLength: finite(summary.rapid_length_mm, null),
      toolChanges: finite(summary.tool_changes, 0), post: text(summary.post || machine.post) },
    findings: list(extras.findings).map(readFinding).filter((finding) => finding && finding.severity !== "info"),
    duration, show: record(record(extras.view).show),
    nodes: { stock: named.get("stock") || null, fixtures, tools: toolNodes, paths },
  };
}

/** The operation the job is at on its clock: the last one begun by `t` (of those that move), else the first. */
export function operationAt(cam, t) {
  const moving = cam.operations.filter((operation) => cam.nodes.paths.has(operation.index));
  let found = moving[0] || null;
  for (const operation of moving) if (operation.start <= t + 1e-9) found = operation;
  return found;
}

/**
 * Where the tool is at job time `t`: the operation it is in, its tool's id, the tip's place (in the path's frame, which
 * is the tool node's parent's) and the setup's rotation to turn the tool by (glTF `[x, y, z, w]`). null for a result
 * with no path.
 */
export function toolPlacementAt(cam, t, out = [0, 0, 0]) {
  const operation = operationAt(cam, t);
  if (!operation) return null;
  const path = cam.nodes.paths.get(operation.index);
  const setup = cam.setups.find((entry) => entry.id === operation.setup) || null;
  return { operation, tool: operation.tool || operation.tools[0] || "", point: pathPointAt(path, t, out), quaternion: setup ? setup.quaternion : [0, 0, 0, 1] };
}

/** A time on the job's clock as the panel says it: "2.5 s", "15 s", "1 min 16 s", "1 h 02 min". */
export function camTime(seconds) {
  const value = Math.max(finite(seconds), 0);
  if (value < 10) return `${(Math.round(value * 10) / 10).toFixed(value % 1 ? 1 : 0)} s`;
  const whole = Math.round(value);
  if (whole < 60) return `${whole} s`;
  if (whole < 3600) {
    const minutes = Math.floor(whole / 60);
    const rest = whole % 60;
    return rest ? `${minutes} min ${String(rest).padStart(2, "0")} s` : `${minutes} min`;
  }
  const hours = Math.floor(whole / 3600);
  const minutes = Math.round((whole % 3600) / 60);
  return `${hours} h ${String(minutes).padStart(2, "0")} min`;
}

/**
 * A time on the job's clock as the Time row's value field says it, short enough to leave its label room in the narrow
 * panel: "38.2 s" under a minute, then minutes and seconds as a clock ("3:38", "1:02:05").
 */
export function clockTime(seconds) {
  const value = Math.max(finite(seconds), 0);
  if (value < 60) return `${(Math.round(value * 10) / 10).toFixed(1)} s`;
  const whole = Math.round(value);
  const hours = Math.floor(whole / 3600);
  const minutes = Math.floor((whole % 3600) / 60);
  const rest = String(whole % 60).padStart(2, "0");
  return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

const grouped = (value, digits = 0) => Number(value).toLocaleString("en-US", { maximumFractionDigits: digits });
const plural = (count, word) => `${count} ${word}${count === 1 ? "" : "s"}`;
/** A tool type in words: "flat_endmill" is "flat endmill". */
const words = (value) => text(value).replace(/_/g, " ");

/** A tool's row: its id, then its size and type, and the speed and feed cadgen computed ("6 mm flat endmill · 21,221 rpm · 2,547 mm/min"). */
export function toolRow(tool) {
  const size = tool.diameter !== null ? `${grouped(tool.diameter, 2)} mm ${words(tool.type)}` : words(tool.type);
  const speeds = [tool.rpm !== null ? `${grouped(tool.rpm)} rpm` : "", tool.feed !== null ? `${grouped(tool.feed)} mm/min` : ""].filter(Boolean);
  return { id: `tool:${tool.id}`, label: tool.id, detail: size, speeds: speeds.join(" · "), hint: [size, ...speeds].join(" · ") };
}

/** A setup's row: its id, then the way the tool points and what holds the stock ("+Z · vise"). */
export function setupRow(setup) {
  const held = setup.fixture.type && setup.fixture.type !== "none" ? words(setup.fixture.type) : "no fixture";
  return { id: `setup:${setup.id}`, label: setup.id, detail: [setup.direction, held].filter(Boolean).join(" · "),
    hint: `Tool along ${setup.direction || "+Z"}, origin ${words(setup.origin) || "as the job says"}, held by ${held}` };
}

/** An operation's row: its place and word ("1 Face"), its time, and in its hint its tool, speeds and lengths. */
export function operationRow(operation, position) {
  const lengths = [operation.cut !== null ? `cut ${grouped(operation.cut, 0)} mm` : "", operation.rapid !== null ? `rapid ${grouped(operation.rapid, 0)} mm` : ""].filter(Boolean);
  return { id: `op:${operation.index}`, index: operation.index, label: `${position + 1} ${operation.word}`, detail: camTime(operation.time),
    hint: [`${operation.op} with ${operation.tool}`, ...lengths, ...operation.lines].join(" · ") };
}

/** The summary at the top of the Job panel: the cycle time, then what it takes ("4 operations · 1 tool change · linuxcnc"). */
export function camSummary(cam) {
  const parts = [plural(cam.operations.length, "operation"), plural(cam.summary.toolChanges, "tool change"), cam.summary.post].filter(Boolean);
  return { title: camTime(cam.summary.cycleTime), caption: parts.join(" · "),
    note: cam.machine.rapidAssumed ? "Rapids assumed at the default speed: the machine does not state its own" : "" };
}

/**
 * The job as a motion (`fea/motion.js`'s contract), so its Time row, its Preset entries and its routine are drawn the
 * way a moving FEA result's are: the timeline is the job's clock; it opens at the end, the whole job shown; each
 * operation's start is a state ("Start of 2 Pocket"); the row says the job time there. It has no `play` span: with
 * reduced motion the job is played in steps instead (`jobTimeAt`).
 */
export function camMotion(cam) {
  const states = cam.operations.filter((operation) => cam.nodes.paths.has(operation.index))
    .map((operation, position) => ({ id: `op-${operation.index}`, label: `Start of ${position + 1} ${operation.word}`, at: Math.min(operation.start, cam.duration) }));
  return {
    kind: "cam", label: "Job", row: "Time", duration: cam.duration, defaultAt: cam.duration, states,
    timeAt: (at) => camTime(at), moments: states.map((state) => state.at), hint: "Where the job is on its clock: the tool there, the path cut up to it",
  };
}

/** Seconds a step lasts when the job is played in steps (reduced motion): one operation a second. */
export const STEP_SECONDS = 1;

/**
 * How the job plays: `duration` on the playbar and the job time at each moment of it (`jobTimeAt(elapsed)`). It plays
 * at `pace` times real time (`playbackPace`), the tool moving along the path. With reduced motion nothing sweeps: one
 * operation a second, each drawn whole at once with the tool at its end (`steps`).
 */
export function jobPlayback(cam, reducedMotion = false) {
  if (reducedMotion) {
    const ends = cam.operations.filter((operation) => cam.nodes.paths.has(operation.index)).map((operation) => Math.min(operation.end, cam.duration));
    return { duration: ends.length * STEP_SECONDS, pace: null, steps: ends,
      jobTimeAt: (elapsed) => ends[Math.min(Math.max(Math.floor(elapsed / STEP_SECONDS), 0), ends.length - 1)] ?? 0 };
  }
  const pace = playbackPace(cam.duration);
  return { duration: cam.duration / pace, pace, steps: null, jobTimeAt: (elapsed) => Math.min(Math.max(elapsed * pace, 0), cam.duration) };
}

/** How much faster than real time the playbar plays a job: a round factor (1, 2 or 5 × 10ⁿ) that makes it about `seconds` long. */
export function playbackPace(duration, seconds = 20) {
  const wanted = Math.max(duration / seconds, 1);
  const power = 10 ** Math.floor(Math.log10(wanted));
  const step = [1, 2, 5, 10].find((each) => each * power >= wanted * 0.75) || 10;
  return step * power;
}
