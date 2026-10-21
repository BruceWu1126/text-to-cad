// The engine's look (`xbfLook.js`) as the engine serves it: `__fixtures__/look.json` is
// `GET /api/look`'s answer, which cadgen's tests hold to `render/look.py`. What it says, the rig it
// lights an engine document with, the highlight it asks of three, and that what it says of edges,
// the palette and the modes is what the viewer's own modes draw.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { resolveCadEdgeSettings } from "../../common/cadInk.js";
import { resolveViewSettings } from "../../common/viewSettings.js";
import { isSceneRig } from "../viewer/sceneRig.js";
import { parseXbfLook, xbfLookHighlight, xbfLookRig, xbfLookSurface } from "./xbfLook.js";

const SERVED = JSON.parse(fs.readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), "__fixtures__", "look.json"), "utf8"));
const near = (actual, expected, tolerance = 1e-9) => assert.ok(Math.abs(actual - expected) < tolerance, `${actual} is not ${expected}`);

test("the look as served: every field read, frozen; another version or a missing field refused by name", () => {
  const look = parseXbfLook(SERVED);
  assert.equal(Object.isFrozen(look), true);
  assert.deepEqual([look.backdrop, look.ambient, look.colourPower, look.specular, look.shininess, look.environment, look.tone, look.exposure],
    ["#f0f4f9", 0.5, 2, 0.08, 0.35, 0, "clamp", 0]);
  assert.deepEqual(look.lights.map(light => [light.name, light.direction, light.radiance]),
    [["key", [-0.35, 0.55, -1], 1], ["fill", [0.6, 0.2, -0.3], 1]]);
  assert.deepEqual(look.modes.xray, { faces: "lit", edges: "all", opacity: 0.22, palette: false });
  assert.throws(() => parseXbfLook({ ...SERVED, version: 2 }), /version 2; this viewer reads 1/);
  assert.throws(() => parseXbfLook({ ...SERVED, lights: [] }), /no lights/);
  assert.throws(() => parseXbfLook({ ...SERVED, ambient: null }), /no ambient/);
  assert.throws(() => parseXbfLook({ ...SERVED, backdrop: "blue" }), /backdrop is a #rrggbb colour/);
});

test("the rig: OCCT's radiance is three's intensity times π, each light shining from where it comes", () => {
  const rig = xbfLookRig(parseXbfLook(SERVED));
  assert.equal(isSceneRig(rig), true);
  near(rig.ambient.intensity, Math.PI * 0.5);
  const [key, fill] = rig.lights;
  near(key.intensity, Math.PI);
  near(fill.intensity, Math.PI);
  const length = Math.hypot(0.35, 0.55, 1);
  [0.35 / length, -0.55 / length, 1 / length].forEach((value, at) => near(key.towards[at], value));
  assert.deepEqual([rig.environment, rig.exposure, rig.toneCurve], [0, 1, "clamp"]);
});

test("the highlight: a GGX lobe as wide as Phong's 128 x 0.35, and as bright at its peak", () => {
  const { roughness, specularIntensity } = xbfLookHighlight(0.08, 0.35);
  near(roughness ** 4, 2 / (128 * 0.35 + 2), 1e-12);
  near(specularIntensity, 0.2768, 1e-3);
  const surface = xbfLookSurface(parseXbfLook(SERVED));
  assert.equal(surface.colorPower, 2);
  assert.deepEqual([surface.materialSettings.metalness, surface.materialSettings.emissiveIntensity, surface.materialSettings.saturation], [0, 0, 1]);
});

test("what the look says of edges, the palette and each mode is what the viewer's modes draw", () => {
  const look = parseXbfLook(SERVED);
  const ink = resolveCadEdgeSettings();
  for (const [line, colour] of Object.entries(look.edges.colours)) assert.equal(ink.classes[line].color, colour, line);
  assert.deepEqual(resolveViewSettings({ mode: "parts" }).surfaces.colors, [...look.palette]);
  assert.equal(look.fill, look.palette[0]);
  const faces = { shaded: "lit", hidden: "backdrop", off: "none" };
  for (const [mode, recipe] of Object.entries(look.modes)) {
    const view = resolveViewSettings({ mode });
    assert.deepEqual({ faces: faces[view.surfaces.style], edges: view.edges.enabled ? view.edges.visibility : "none",
      opacity: view.surfaces.opacity, palette: view.surfaces.colorMode === "by-part" }, recipe, mode);
  }
});
