import assert from "node:assert/strict";
import test from "node:test";

import { loadSource, packageSourceFromBaseUrl } from "./source.js";
import { meshFixture } from "../lib/parts/__tests__/meshFixtures.js";

// Mesh files served as a static host serves them (`files`, by URL), every read recorded.
function serveMeshFiles(t, files) {
  const oldFetch = globalThis.fetch;
  const fetched = [];
  globalThis.fetch = async (url) => {
    fetched.push(String(url));
    const bytes = files[String(url)];
    return bytes ? new Response(bytes.slice(), { status: 200 }) : new Response(null, { status: 404 });
  };
  t.after(() => { globalThis.fetch = oldFetch; });
  return fetched;
}

function meshData() {
  return {
    vertices: new Float32Array([
      0, 0, 0,
      1, 0, 0,
      0, 1, 0
    ]),
    indices: new Uint32Array([0, 1, 2]),
    bounds: {
      min: [0, 0, 0],
      max: [1, 1, 0]
    },
    parts: []
  };
}

// The roller as a one-component package, naming its fixture part.
function rollerPackage(extra = {}, component = {}) {
  const { part } = meshFixture("cam_follower_roller");
  return { kind: "step", package: {
    descriptor: { components: { roller: { part, ...component } },
      occurrences: [{ id: "o1.1", name: "roller", component: "roller" }],
      assembly: { root: { id: "o1", name: "macro", nodeType: "assembly", children: [
        { id: "o1.1", name: "roller", nodeType: "part", children: [] }
      ] } } },
    ...extra,
  } };
}

// A component cadgen could not mesh says why in its tree entry (`failure`): the page leaves
// it out on its own, as the viewer's loader does (`componentFailed`), and draws the rest. Only a
// package none of whose components is left fails, with the first failure.
test("a component cadgen could not mesh is left out and the rest drawn; with none left the load fails", async (t) => {
  const roller = meshFixture("cam_follower_roller");
  const fetched = serveMeshFiles(t, { "/hero/components/roller.glb": roller.bytes });
  const failure = "MeshProductionError: ValueError: boom";
  const job = rollerPackage({ meshUrls: { roller: "/hero/components/roller.glb" } });
  const { descriptor } = job.package;
  descriptor.components.peg = { part: `${"1".repeat(64)}-v1`, failure };
  descriptor.occurrences.push({ id: "o1.2", name: "peg", component: "peg" });
  descriptor.assembly.root.children.push({ id: "o1.2", name: "peg", nodeType: "part", children: [] });
  const stages = {};
  const source = await loadSource(job, { stageTimings: stages });
  assert.deepEqual(source.meshData.parts.map((part) => part.occurrenceId), ["o1.1"]);
  assert.deepEqual(fetched, ["/hero/components/roller.glb"], "nothing is read for the part cadgen could not mesh");
  assert.equal(stages.sourceLoad.componentCount, 1);
  descriptor.components.roller.failure = failure;
  await assert.rejects(loadSource(job),
    /component roller could not be meshed \(MeshProductionError: ValueError: boom\)/);
});

test("a static package reads each component's own mesh file; a component nothing meshed is an error", async (t) => {
  const oldFetch = globalThis.fetch;
  const mesh = meshFixture("cam_follower_roller");
  const fetched = [];
  globalThis.fetch = async (url) => {
    fetched.push(String(url));
    return String(url).endsWith("/roller.glb")
      ? new Response(mesh.bytes.slice(), { status: 200 })
      : new Response(null, { status: 404 });
  };
  t.after(() => { globalThis.fetch = oldFetch; });
  // The docs hero: each component's tree entry names its mesh file beside the tree.
  const hero = (component) => packageSourceFromBaseUrl("/hero/", rollerPackage({}, component).package.descriptor);
  const job = hero({ glb: "components/roller.glb" });
  assert.deepEqual(job.package.meshUrls, { roller: "/hero/components/roller.glb" });
  const source = await loadSource(job);
  assert.ok(source.meshData.indices.length > 0);
  assert.deepEqual(fetched, ["/hero/components/roller.glb"], "only the mesh file is read");
  // A file of another part is not this component's mesh.
  await assert.rejects(loadSource(hero({ glb: "components/roller.glb", part: `${"0".repeat(64)}-v1` })),
    /is not its part's mesh/);
  assert.throws(() => hero({}), /component roller declares no glb path/);
  await assert.rejects(loadSource(rollerPackage()), /cadgen meshes every component before a page draws it/);
});

test("a package draws the finish, colour and opacity cadgen composed onto its occurrences", async (t) => {
  const { part, bytes } = meshFixture("cam_follower_roller");
  serveMeshFiles(t, { "/hero/components/roller.glb": bytes });
  // The descriptor as cadgen serves it for display: the
  // assigned occurrence already carries what its material resolves to.
  const descriptor = {
    kind: "assembly-package",
    components: { "appearance-cid": { part } },
    occurrences: [{
      id: "o1.1", name: "roller", component: "appearance-cid",
      materialId: "polished", materialName: "Polished",
      material: { roughness: 0.15, metalness: 0.03, clearcoat: 0.8, clearcoatRoughness: 0.26, opacity: 0.5 },
      baseColor: "#336699", opacity: 0.5
    }],
    assembly: { root: { id: "o1", name: "appearance", nodeType: "assembly", children: [
      { id: "o1.1", name: "roller", nodeType: "part", children: [] }
    ] } }
  };
  const source = await loadSource({ kind: "step", package: { descriptor, meshUrls: { "appearance-cid": "/hero/components/roller.glb" } } });
  const [occurrence] = source.meshData.parts;
  assert.deepEqual(occurrence.material, descriptor.occurrences[0].material);
  assert.equal(occurrence.materialId, "polished");
  assert.equal(occurrence.materialName, "Polished");
  assert.equal(occurrence.color, "#336699");
  assert.equal(occurrence.opacity, 0.5, "the opacity is the one cadgen folded, not multiplied again here");
});

// A snapshot job carries what cadgen resolved: the articulation and the control vector it
// validated at the door (a pose NAME is resolved there too). The page plays, it checks nothing.
const HINGE_ARTICULATION = {
  schemaVersion: 1,
  controls: [{ id: "swing", label: "swing", unit: "deg", min: 0, max: 120, default: 0 }],
  joints: [{ id: "swing", parent: null, kind: "revolute", origin: [0, 0, 0], axis: [0, 0, 1],
    turn: { bias: 0, terms: [["swing", 1]] } }],
  carries: { swing: ["flap"] },
  handles: [{ id: "swing", joint: "swing", dof: "turn", control: "swing", weight: 1, label: "swing", unit: "deg", min: 0, max: 120 }],
  poses: { open: { swing: 90 }, ajar: { swing: 15 } },
  opening: { swing: 0 }
};

function poseJob(controls, articulation = HINGE_ARTICULATION) {
  return {
    kind: "step",
    meshData: meshData(),
    resolved: { kind: "step", articulation, controls, inputPath: "/models/hinge.step" }
  };
}

test("a job's pose is cadgen's articulation at the control vector cadgen validated", async () => {
  const source = await loadSource(poseJob({ swing: 90 }));
  assert.equal(source.pose.articulation, HINGE_ARTICULATION);
  assert.deepEqual(source.pose.values, { swing: 90 });
});

test("a job with an articulation and no controls poses the opening", async () => {
  const source = await loadSource(poseJob(undefined));
  assert.deepEqual(source.pose.values, { swing: 0 });
});

test("control values against a model with no articulation have nothing to drive", async () => {
  await assert.rejects(() => loadSource(poseJob({ swing: 45 }, null)), /declares no kinematics/);
  assert.equal((await loadSource(poseJob(undefined, null))).pose, null);
});

test("loadSource takes an articulation and animation inline for STEP sources", async () => {
  const animation = { clips: [{ id: "swing", label: "Swing", duration: 4, loop: true, tracks: [] }] };
  const source = await loadSource({
    kind: "step",
    meshData: meshData(),
    cadPath: "part.step",
    articulation: HINGE_ARTICULATION,
    controls: { swing: 90 },
    sourceAnimation: animation
  });

  assert.equal(source.kind, "step");
  assert.deepEqual(source.pose.values, { swing: 90 });
  assert.equal(source.animation, animation);
});

test("render display source loading keeps kinematics and a supplied selector runtime", async () => {
  const source = await loadSource({
    kind: "step",
    meshData: meshData(),
    preview: true,
    cadPath: "hinge.step",
    articulation: HINGE_ARTICULATION,
    controls: { swing: 45 },
    selectorRuntime: { stale: true }
  });
  assert.equal(source.kind, "step");
  assert.deepEqual(source.selectorRuntime, { stale: true });
  assert.deepEqual(source.pose.values, { swing: 45 });
});

// Every other file family is drawn by its own scene builder, the one its viewer renderer
// uses (common/headlessScene.js); loadSource flattens none of them into mesh data.
test("loadSource composes STEP documents and refuses every other file family by name, fetching nothing", async (t) => {
  const originalFetch = globalThis.fetch;
  let fetches = 0;
  globalThis.fetch = async () => { fetches += 1; throw new Error("unexpected fetch"); };
  t.after(() => { globalThis.fetch = originalFetch; });
  for (const input of ["/models/part.stl", "/models/part.glb", "/models/robot.urdf",
    { kind: "3mf", meshData: meshData() }, { kind: "glb", url: "/models/part.glb" },
    { resolved: { kind: "sdf", url: "/models/robot.sdf", inputPath: "/models/robot.sdf" } }, { kind: "srdf", url: "/models/robot.srdf" }]) {
    await assert.rejects(() => loadSource(input), /loadSource composes a STEP document; a (STL|GLB|URDF|3MF|SDF|SRDF) is drawn by its own scene builder/,
      JSON.stringify(input));
  }
  // A STEP is drawn from its package: a URL alone names nothing to draw.
  await assert.rejects(() => loadSource("/models/part.step"), /requires a STEP document's package/);
  assert.equal(fetches, 0);
});

