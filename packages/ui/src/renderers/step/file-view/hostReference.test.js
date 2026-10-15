// The host's half of references (docs/file-view.md, "References and
// captures"): the canonical references built for a copied line, and how a
// selector a host names (`selectReference`) maps onto the surface's own
// selection ids.
import assert from "node:assert/strict";
import test from "node:test";

import {
  findStepTreeNodeForSelector,
  referenceFromCopyText,
  referenceLabel,
  referencesFromCopyText,
  resolveSelectorSelection,
} from "./hostReference.js";

// Tokens as cadgen mints them (`o:`, `f:`, `e:` + 12 hex); a ref is `<occurrence>/<element>`.
const ROOT = "o:1a2b3c4d5e6f";
const BRACKET = "o:3f2a9c1d7e5b";
const SHAFT = "o:a1b2c3d4e5f6";
const ELSEWHERE = "o:0f1e2d3c4b5a";
const F3 = "f:9d2e8c1b5a70";
const F7 = "f:0c7b6a594837";
const F45 = "f:77aa88bb99cc";
const E3 = "e:4b1f9e2a7c03";

test("referenceFromCopyText: the file by its absolute path, the selector without its #, the text as copied", () => {
  assert.deepEqual(referenceFromCopyText(`/models/STEP/bracket.step#${SHAFT}`, "/models/STEP/bracket.step"), {
    file: "/models/STEP/bracket.step",
    selector: SHAFT,
    text: `/models/STEP/bracket.step#${SHAFT}`,
  });
  assert.deepEqual(referenceFromCopyText(`#${F45},${E3}`, "/models/a.step"), { file: "/models/a.step", selector: `${F45},${E3}`, text: `#${F45},${E3}` });
  assert.deepEqual(referenceFromCopyText(`C:/models/bracket.step#label/${F45}`, "C:/models/bracket.step"), {
    file: "C:/models/bracket.step",
    selector: `label/${F45}`,
    text: `C:/models/bracket.step#label/${F45}`,
  });
  // A whole-file line has no selector; a path with no `#` is one too.
  assert.deepEqual(referenceFromCopyText("/models/bracket.step#", "/models/bracket.step"), { file: "/models/bracket.step", selector: "", text: "/models/bracket.step#" });
  assert.deepEqual(referenceFromCopyText("/models/bracket.step", "/models/bracket.step"), { file: "/models/bracket.step", selector: "", text: "/models/bracket.step" });
});

test("referencesFromCopyText: one per line, blanks dropped", () => {
  assert.deepEqual(
    referencesFromCopyText(`a.step#${BRACKET}\n\n a.step#${SHAFT} \n`, "a.step").map((reference) => reference.selector),
    [BRACKET, SHAFT],
  );
});

const TREE = {
  id: "root",
  occurrenceId: ROOT,
  name: "assembly",
  children: [
    { id: BRACKET, occurrenceId: BRACKET, name: "bracket", label: "bracket", children: [] },
    {
      id: SHAFT,
      occurrenceId: SHAFT,
      name: "shaft",
      children: [
        { id: `step-topology:${SHAFT}:face:${SHAFT}/${F3}`, nodeType: "topology-face", topologyReferenceId: `topology|${SHAFT}|face|${SHAFT}/${F3}`, displaySelector: `${SHAFT}/${F3}`, children: [] },
      ],
    },
  ],
};

test("referenceLabel: known parts have display names, unresolved or mixed selections do not", () => {
  assert.equal(referenceLabel(BRACKET, TREE), "bracket");
  assert.equal(referenceLabel(SHAFT, TREE), "shaft");
  assert.equal(referenceLabel(`${SHAFT}/${F3}`, TREE), "");
  assert.equal(referenceLabel(`${BRACKET},${SHAFT}`, TREE), "");
  assert.equal(referenceLabel(ELSEWHERE, TREE), "");
});

test("findStepTreeNodeForSelector: by occurrence token, name, label and topology ref", () => {
  assert.equal(findStepTreeNodeForSelector(TREE, BRACKET)?.name, "bracket");
  assert.equal(findStepTreeNodeForSelector(TREE, "bracket")?.id, BRACKET);
  assert.equal(findStepTreeNodeForSelector(TREE, "shaft")?.id, SHAFT);
  assert.equal(findStepTreeNodeForSelector(TREE, `${SHAFT}/${F3}`)?.nodeType, "topology-face");
  assert.equal(findStepTreeNodeForSelector(TREE, ELSEWHERE), null);
  assert.equal(findStepTreeNodeForSelector(null, ROOT), null);
});

test("resolveSelectorSelection: reference map first, then the tree, then a labelled element", () => {
  const referenceMap = new Map([
    [`${SHAFT}/${F7}`, { id: `ref:${SHAFT}/${F7}` }],
    [F7, { id: `ref:${SHAFT}/${F7}` }],
    [`${BRACKET}/${F45}`, { id: `ref:${BRACKET}/${F45}` }],
  ]);
  const options = { referenceMap, treeRoot: TREE };
  assert.deepEqual(resolveSelectorSelection(`${SHAFT}/${F7}`, options), { kind: "reference", id: `ref:${SHAFT}/${F7}` });
  assert.deepEqual(resolveSelectorSelection(SHAFT, options), { kind: "part", id: SHAFT });
  assert.deepEqual(resolveSelectorSelection("bracket", options), { kind: "part", id: BRACKET });
  assert.deepEqual(resolveSelectorSelection(`${SHAFT}/${F3}`, options), { kind: "reference", id: `topology|${SHAFT}|face|${SHAFT}/${F3}` });
  // `bracket/f:…`: the labelled node's occurrence, then its element in the map by token.
  assert.deepEqual(resolveSelectorSelection(`bracket/${F45}`, options), { kind: "reference", id: `ref:${BRACKET}/${F45}` });
  // A list selects its first member; nothing known is null, not a guess.
  assert.deepEqual(resolveSelectorSelection(`${BRACKET},${SHAFT}`, options), { kind: "part", id: BRACKET });
  assert.equal(resolveSelectorSelection(`${ELSEWHERE}/${F3}`, options), null);
  assert.equal(resolveSelectorSelection("", options), null);
  assert.equal(resolveSelectorSelection(ROOT, {}), null);
});

test("quoted imported paths preserve the group rather than becoming whole-file references", () => {
  const text = `"Hex Drive Screw (2).STEP"#${BRACKET}/${F3},${BRACKET}/${F7}`;
  assert.deepEqual(referenceFromCopyText(text, 'models/Hex Drive Screw (2).STEP'), {
    file:'models/Hex Drive Screw (2).STEP', selector:`${BRACKET}/${F3},${BRACKET}/${F7}`, text,
  });
});
