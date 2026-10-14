import assert from "node:assert/strict";
import test from "node:test";

import { normalizeSketch } from "./sketch.js";

const PLACEMENT = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1];
const stroke = (fields = {}) => ({ id: "1", kind: "stroke", part: "o1.2", points: [0, 0, 0, 1, 1, 1], color: "#ff2d55", width: 2, ...fields });

test("a sketch file is read with each mark's defaults filled", () => {
  const sketch = normalizeSketch({
    units: "mm",
    parts: { "o1.2": { placement: PLACEMENT } },
    marks: [
      stroke({ placement: "surface", view: "view 1", widths: [1, 0.5], group: "g1", touches: [{ part: "o1.2" }] }),
      { id: "2", kind: "area", part: null, points: [0, 0, 0, 1, 0, 0, 1, 1, 0], triangles: [0, 1, 2], color: "#007AFF", opacity: 0.5 },
      { id: "3", kind: "text", part: "o1.2", points: [0, 0, 0, 1, 0, 0, 1, 1, 0, 0, 1, 0], color: "#000000", text: "M6", fontPx: 20 },
    ],
  });
  assert.deepEqual(sketch.parts, { "o1.2": { placement: PLACEMENT } });
  assert.deepEqual(sketch.marks.map(mark => mark.kind), ["stroke", "area", "text"]);
  assert.deepEqual([sketch.marks[0].dash, sketch.marks[0].closed, sketch.marks[0].widths], ["solid", false, [1, 0.5]]);
  assert.equal(sketch.marks[1].outline, 3, "an area's outline is all its points unless it says otherwise");
  assert.equal(normalizeSketch({ marks: [] }).units, "mm");
});

test("a sketch that is not one is refused by what is wrong, an unknown field included", () => {
  for (const [raw, said] of [
    [[], "a sketch is an object"],
    [{ marks: [], colour: "red" }, 'unknown field "colour"'],
    [{ marks: {} }, "marks must be an array"],
    [{ marks: [stroke({ shade: 1 })] }, 'marks[0]: unknown field "shade"'],
    [{ marks: [stroke(), stroke()] }, "marks[1].id must be a unique"],
    [{ marks: [stroke({ kind: "blob" })] }, "marks[0].kind must be one of stroke, area, text"],
    [{ marks: [stroke({ color: "red" })] }, "marks[0].color must be a #rrggbb colour"],
    [{ marks: [stroke({ points: [0, 0] })] }, "marks[0].points must be x, y, z triples"],
    [{ marks: [stroke({ points: [0, 0, 0] })] }, "a stroke has at least two points"],
    [{ marks: [stroke({ width: 0 })] }, "marks[0].width must be a positive number"],
    [{ marks: [stroke({ dash: "wavy" })] }, "marks[0].dash must be solid, dashed or dotted"],
    [{ parts: { a: { placement: [1, 2] } }, marks: [] }, "parts.a.placement must be a 4×4 matrix"],
    [{ marks: [{ ...stroke(), kind: "area", triangles: [0, 1, 5] }] }, "marks[0].triangles must index its points"],
    [{ marks: [{ ...stroke(), kind: "text", fontPx: 12 }] }, "a text mark's points are its four corners"],
  ]) {
    assert.throws(() => normalizeSketch(raw), (error) => error.message.startsWith("sketch: ") && error.message.includes(said), said);
  }
});
