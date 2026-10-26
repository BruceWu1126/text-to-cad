import { CAD_EDGE_COVERAGE_GLSL } from "./cadEdgeCoverage.js";
import assert from "node:assert/strict";
import test from "node:test";

import * as THREE from "three";
import { Line2 } from "three/examples/jsm/lines/Line2.js";
import { LineGeometry } from "three/examples/jsm/lines/LineGeometry.js";
import { LineSegments2 } from "three/examples/jsm/lines/LineSegments2.js";
import { LineSegmentsGeometry } from "three/examples/jsm/lines/LineSegmentsGeometry.js";
import { LineMaterial } from "three/examples/jsm/lines/LineMaterial.js";

import {
  TOPOLOGY_LINE_DEPTH_BIAS,
  applyLineDepthBias,
  createCadEdgeLineSegments,
  createDisplayEdgeObject,
  createScreenSpaceLineSegments,
  lineSegmentPositionsFromGeometry,
  screenSpaceLineDeviceResolution,
  syncLineMaterialOpacity,
  syncRecordEdgeMaterials,
  syncScreenSpaceLineMaterialResolution,
  topologyLineDepthBiasForWidth
} from "./renderEdges.js";
import { CAD_EDGE_FEATHER_PIXELS } from "./cadEdgeInstances.js";

function edgeContext(materials = new Set()) {
  return {
    THREE,
    Line2,
    LineGeometry,
    LineSegments2,
    LineSegmentsGeometry,
    LineMaterial,
    registerScreenSpaceLineMaterial: (material) => materials.add(material),
    unregisterScreenSpaceLineMaterial: (material) => materials.delete(material)
  };
}

function twoPointGeometry() {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array([
    0, 0, 0,
    1, 0, 0
  ]), 3));
  return geometry;
}

test("line segment extraction uses the geometry position buffer unchanged", () => {
  const geometry = twoPointGeometry();
  const positions = geometry.getAttribute("position").array;

  assert.equal(lineSegmentPositionsFromGeometry(geometry), positions);
});

test("screen-space display edge creation registers material settings", () => {
  const materials = new Set();
  const { edgeMesh, edgeMaterial } = createDisplayEdgeObject(edgeContext(materials), {
    geometry: twoPointGeometry(),
    edgeSettings: {
      color: "#123456",
      opacity: 0.42,
      thickness: 2
    },
    baseTheme: {
      edge: "#000000",
      edgeOpacity: 0.84
    },
    partId: "part-a",
    displayMode: "shaded_edges",
    thickness: 2
  }, materials);

  assert.equal(edgeMesh.userData.partId, "part-a");
  assert.equal(edgeMaterial.opacity, 0.42);
  assert.equal(edgeMaterial.linewidth, 2);
  assert.equal(edgeMaterial.polygonOffset, true);
  assert.equal(edgeMaterial.polygonOffsetFactor, 0);
  assert.equal(edgeMaterial.polygonOffsetUnits, -5);
  assert.equal(materials.has(edgeMaterial), true);

  syncScreenSpaceLineMaterialResolution(materials, 640, 480);
  assert.equal(edgeMaterial.resolution.x, 640);
  assert.equal(edgeMaterial.resolution.y, 480);
});

test("screen-space display edges can render through surfaces", () => {
  const { edgeMaterial } = createDisplayEdgeObject(edgeContext(), {
    geometry: twoPointGeometry(),
    edgeSettings: {
      color: "#123456",
      opacity: 0.42,
      thickness: 2,
      depthTest: false
    },
    baseTheme: {
      edge: "#000000",
      edgeOpacity: 0.84
    },
    partId: "part-a",
    displayMode: "hidden_edges",
    thickness: 2
  });

  assert.equal(edgeMaterial.depthTest, false);
});

test("wireframe display edges preserve high opacity and basic line material", () => {
  const { edgeMesh, edgeMaterial } = createDisplayEdgeObject(edgeContext(), {
    geometry: twoPointGeometry(),
    edgeSettings: {
      color: "#123456",
      opacity: 0.2
    },
    baseTheme: {
      edge: "#000000",
      edgeOpacity: 0.84
    },
    partId: "wire-a",
    displayMode: "wireframe",
    wireframeEdgeColor: "#abcdef"
  });

  assert.equal(edgeMesh.userData.partId, "wire-a");
  assert.equal(edgeMaterial.isLineBasicMaterial, true);
  assert.equal(edgeMaterial.opacity, 0.9);
  assert.equal(edgeMaterial.depthTest, false);
});

test("line depth bias uses fixed-function polygon offset", () => {
  const material = new THREE.LineBasicMaterial();
  applyLineDepthBias(material, 0.0003);
  const shader = {
    uniforms: {},
    vertexShader: "void main() {\n#include <logdepthbuf_vertex>\n}"
  };

  material.onBeforeCompile(shader, {});

  assert.equal(material.polygonOffset, true);
  assert.equal(material.polygonOffsetFactor, 0);
  assert.equal(material.polygonOffsetUnits, -1);
  assert.deepEqual(shader.uniforms, {});
  assert.doesNotMatch(shader.vertexShader, /lineDepthBias/);
});

test("line material opacity helper clamps transparency consistently", () => {
  const material = new THREE.LineBasicMaterial({ opacity: 1, transparent: false });

  syncLineMaterialOpacity(material, 0.25);
  assert.equal(material.opacity, 0.25);
  assert.equal(material.transparent, true);

  syncLineMaterialOpacity(material, 5);
  assert.equal(material.opacity, 1);
  assert.equal(material.transparent, false);
});

test("CAD edge line segments wrap a shared vertex-coloured geometry", () => {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0]), 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(new Uint16Array(8), 4, true));
  geometry.setIndex(new THREE.BufferAttribute(new Uint32Array([0, 1]), 1));
  const line = createCadEdgeLineSegments(THREE, geometry, { depthTest: false, depthBias: TOPOLOGY_LINE_DEPTH_BIAS });
  assert.equal(line.isLineSegments, true);
  assert.equal(line.geometry, geometry);
  assert.equal(line.userData.disposeGeometry, false);
  assert.equal(line.material.vertexColors, true);
  assert.equal(line.material.transparent, true);
  assert.equal(line.material.depthTest, false);
  assert.equal(line.material.polygonOffsetUnits, -5);
  assert.equal(line.material.userData.cadEdgeVertexColors, true);
});

test("record edge materials: uniform overrides, scaled restores, per material kind", () => {
  const plain = new THREE.LineBasicMaterial({ color: "#000000", transparent: true, opacity: 1 });
  const based = new THREE.LineBasicMaterial({ color: "#000000", transparent: true, opacity: 1 });
  based.userData.cadEdgeBaseColor = "#111111";
  based.userData.cadEdgeBaseOpacity = 0.25;
  const classed = createCadEdgeLineSegments(THREE, new THREE.BufferGeometry()).material;
  const record = { edgeMaterials: [plain, based, classed] };

  // Scaled pass (no highlight): plain takes the fallback, based its base style,
  // the vertex-coloured material keeps its classes and scales them.
  syncRecordEdgeMaterials(record, { opacityScale: 0.5, fallbackColor: "#abcdef", fallbackOpacity: 0.84 });
  assert.equal(plain.color.getHexString(), "abcdef");
  assert.equal(plain.opacity, 0.42);
  assert.equal(based.color.getHexString(), "111111");
  assert.equal(based.opacity, 0.125);
  assert.equal(classed.vertexColors, true);
  assert.equal(classed.color.getHexString(), "ffffff");
  assert.equal(classed.opacity, 0.5);
  assert.equal(classed.transparent, true);

  // Uniform pass (highlight): every material takes the colour and opacity.
  syncRecordEdgeMaterials(record, { color: "#ff0000", opacity: 1, fallbackColor: "#abcdef" });
  for (const material of record.edgeMaterials) {
    assert.equal(material.color.getHexString(), "ff0000");
    assert.equal(material.opacity, 1);
  }
  assert.equal(classed.vertexColors, false);

  // An effect edge colour in a scaled pass recolours the classes too.
  syncRecordEdgeMaterials(record, { color: "#00ff00", opacityScale: 0.5, fallbackColor: "#abcdef", fallbackOpacity: 0.84 });
  assert.equal(classed.vertexColors, false);
  assert.equal(classed.color.getHexString(), "00ff00");
  assert.equal(classed.opacity, 0.5);
  assert.equal(based.opacity, 0.125);
  syncRecordEdgeMaterials({ edgeMaterials: null }, { fallbackColor: "#abcdef" });
});

// --- The screen-space line pass: device-pixel width, and the analytic feather.
//
// A configured `thickness` is a FULL width in DEVICE pixels — the unit the
// retired surface-shader pass measured in (its `pixelDistance` was a per-fragment
// derivative) and therefore the unit every stored theme value is authored in.
// Two things decide whether that still holds: the `resolution` a line material is
// given, and what the shader does with it.

function screenSpaceLine(lineWidth = 1.15, options = {}) {
  return createScreenSpaceLineSegments(edgeContext(), [0, 0, 0, 1, 0, 0], {
    color: "#132232",
    opacity: 1,
    lineWidth,
    ...options
  });
}

// A renderer whose CSS viewport and drawing buffer disagree, i.e. any Retina one.
// `getViewport` reports the CSS size (three's own hook reads this one);
// `getCurrentViewport` reports the drawing buffer, which is what a width means.
function retinaRenderer(cssWidth, cssHeight, pixelRatio) {
  return {
    getPixelRatio: () => pixelRatio,
    getViewport: (target) => target.set(0, 0, cssWidth, cssHeight),
    getCurrentViewport: (target) => target.copy({
      x: 0,
      y: 0,
      z: cssWidth * pixelRatio,
      w: cssHeight * pixelRatio
    })
  };
}

test("screen-space line resolution is the drawing buffer, not the CSS size", () => {
  assert.deepEqual(
    screenSpaceLineDeviceResolution({ getPixelRatio: () => 2 }, 640, 480),
    { width: 1280, height: 960 }
  );
  assert.deepEqual(
    screenSpaceLineDeviceResolution({ getPixelRatio: () => 3 }, 640, 480),
    { width: 1920, height: 1440 }
  );
  assert.deepEqual(
    screenSpaceLineDeviceResolution({ getPixelRatio: () => 1 }, 640, 480),
    { width: 640, height: 480 }
  );
  // No renderer, or a nonsense ratio: the size as given, and never zero.
  assert.deepEqual(screenSpaceLineDeviceResolution(null, 640, 480), { width: 640, height: 480 });
  assert.deepEqual(screenSpaceLineDeviceResolution({ getPixelRatio: () => 0 }, 640, 480), { width: 640, height: 480 });
  // A degenerate size falls back to one CSS pixel, scaled like any other.
  assert.deepEqual(screenSpaceLineDeviceResolution({ getPixelRatio: () => 2 }, 0, 0), { width: 2, height: 2 });
});

test("a screen-space line draws its configured thickness in DEVICE pixels", () => {
  const line = screenSpaceLine(1.15);
  const renderer = retinaRenderer(400, 300, 2);

  // three's LineSegments2 rewrites `resolution` on every draw from the CSS
  // viewport; the CAD hook writes the drawing buffer instead.
  line.material.resolution.set(400, 300);
  line.onBeforeRender(renderer);
  assert.equal(line.material.resolution.x, 800);
  assert.equal(line.material.resolution.y, 600);

  // The shader extrudes `linewidth / resolution.y` into NDC, so the ink a
  // fragment ends up covering is linewidth * drawingBufferHeight / resolution.y.
  const inkCssPixels = (material, { cssHeight, dpr }) => (
    material.linewidth * (cssHeight * dpr) / material.resolution.y / dpr
  );
  assert.equal(inkCssPixels(line.material, { cssHeight: 300, dpr: 2 }), 1.15 / 2);

  // The same thickness at dpr 1: 1.15 device px there too, which is 1.15 CSS px.
  // One authored number, one on-screen weight, every ratio.
  const plainLine = screenSpaceLine(1.15);
  plainLine.onBeforeRender(retinaRenderer(400, 300, 1));
  assert.equal(plainLine.material.resolution.y, 300);
  assert.equal(inkCssPixels(plainLine.material, { cssHeight: 300, dpr: 1 }), 1.15);

  // What three's own hook does, stated in numbers: it writes the CSS viewport,
  // so the buffer's pixel ratio never cancels and the same 1.15 paints 1.15 CSS
  // px on the Retina viewport — twice the authored weight, 3x at dpr 3.
  const cssLine = screenSpaceLine(1.15);
  cssLine.material.resolution.set(400, 300);
  assert.equal(inkCssPixels(cssLine.material, { cssHeight: 300, dpr: 2 }), 1.15);

  // No live viewport (an off-screen pass): keep the synced drawing-buffer size
  // rather than falling back to something in CSS pixels.
  syncScreenSpaceLineMaterialResolution([line.material], 1280, 960);
  line.onBeforeRender({});
  assert.equal(line.material.resolution.x, 1280);
  assert.equal(line.material.resolution.y, 960);
});

test("screen-space lines antialias with an analytic feather, not a hard edge", () => {
  const material = screenSpaceLine(1.15).material;

  // Not alphaToCoverage: three only feathers the round ENDCAPS with it (the
  // `abs(vUv.y) > 1.0` block), never the body a CAD drawing is made of, and it
  // would quantise per-class opacity into sample counts. The feather is analytic.
  assert.equal(material.alphaToCoverage, false);
  assert.equal(material.userData.cadEdgeAnalyticCoverage, true);
  assert.equal(typeof material.onBeforeCompile, "function");

  const shader = { vertexShader: material.vertexShader, fragmentShader: material.fragmentShader };
  material.onBeforeCompile(shader);

  // The quad is widened by the feather on each side so the ramp has room.
  const padding = shader.vertexShader.match(/offset \*= linewidth \+ ([0-9.]+) \* 2\.0;/);
  assert.ok(padding, "the patched vertex shader does not widen the quad");
  assert.equal(Number(padding[1]), CAD_EDGE_FEATHER_PIXELS);

  // Both line paths use the same width-preserving filter, including endcaps.
  assert.ok(shader.fragmentShader.includes(CAD_EDGE_COVERAGE_GLSL));
  assert.match(shader.fragmentShader, /alpha \*= cadEdgeCoverage\( cadEdgeDistance, cadEdgeInkHalfWidth \);/);
});

test("the screen-space line feather fails loudly if three's shader moves", () => {
  const material = screenSpaceLine().material;
  assert.throws(
    () => material.onBeforeCompile({ vertexShader: "void main() {}", fragmentShader: "void main() {}" }),
    /has no unique/
  );
});

test("screen-space line materials share one feather callback, so one program", () => {
  const first = screenSpaceLine(1.15).material;
  const second = screenSpaceLine(2).material;
  assert.equal(first.onBeforeCompile, second.onBeforeCompile);
  assert.equal(first.customProgramCacheKey(), second.customProgramCacheKey());
});

test("a CAD edge line keeps blending at full opacity so the feather survives", () => {
  const material = screenSpaceLine(1.15, { opacity: 1 }).material;
  assert.equal(material.opacity, 1);
  assert.equal(material.transparent, true);
  syncLineMaterialOpacity(material, 1);
  assert.equal(material.transparent, true);

  // A material with no analytic coverage still goes opaque at 1, as it should.
  const plain = new THREE.LineBasicMaterial({ opacity: 0.5, transparent: true });
  syncLineMaterialOpacity(plain, 1);
  assert.equal(plain.transparent, false);
});

test("thick edges and coplanar classes get stronger depth separation", () => {
  assert.equal(topologyLineDepthBiasForWidth(1), TOPOLOGY_LINE_DEPTH_BIAS);
  assert.equal(topologyLineDepthBiasForWidth(6), 0.00575);
  assert.equal(topologyLineDepthBiasForWidth(1, { visibilityClass: "tangent" }), 0.006);
  assert.equal(topologyLineDepthBiasForWidth(6, { visibilityClass: "seam" }), 0.00725);
});
