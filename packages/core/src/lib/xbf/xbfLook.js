// The engine's look of the regular display modes (`GET /api/look`, `cadgen/engine/render/look.py`:
// how the OCCT prototype draws them), and what an engine document's scene draws by it in those
// modes: the rig a stage lights it with in place of the theme's (`../viewer/sceneContract.js`'s
// `rig`, put up by `../viewer/sceneRig.js`) and what its surfaces wear (`xbfLookSurface`).
// Preview's photographic look is not this: there the scene is drawn as any scene is.
//
// The look is OCCT's Phong, in OCCT's terms: a face of colour `c` (linear) reflects
// `c ** colourPower` lit by an ambient term and directional lights of a radiance each, plus a small
// white highlight, clamped and sRGB-encoded with no tone curve. Three's physical lights divide a
// Lambertian surface's light by π, so a radiance `r` is a light of intensity `π r` here; the
// highlight is three's GGX lobe sized to the Phong one (`xbfLookSurface`).

const HEX = /^#[0-9a-f]{6}$/i;
const LOOK_VERSION = 1;

/**
 * A three GGX lobe as wide as OCCT's Phong highlight (`pow(N.H, 128 * shininess)`): Blinn-Phong's
 * exponent `n` is a microfacet slope of `α² = 2 / (n + 2)`, and three's roughness is `√α`. Its
 * peak against the Phong one's sets the specular intensity: three's peak at `N.H = 1` is
 * `0.04 s · V · N.L / α²` against OCCT's `specular`, taken where `N.L = N.V = 0.8`
 * (`V`, Smith's visibility there, `0.5 / (2 · 0.8 · √(α² + (1 − α²) · 0.64))`).
 */
export function xbfLookHighlight(specular, shininess) {
  const exponent = 128 * shininess;
  const slope = 2 / (exponent + 2);
  const roughness = Math.sqrt(Math.sqrt(slope));
  const cosine = 0.8;
  const visibility = 0.5 / (2 * cosine * Math.sqrt(slope + (1 - slope) * cosine * cosine));
  const specularIntensity = Math.min(Math.max(specular * slope / (0.04 * visibility * cosine), 0), 1);
  return { roughness, specularIntensity };
}

function number(value, name) {
  const found = Number(value);
  if (value === null || value === "" || !Number.isFinite(found)) throw new Error(`the engine's look has no ${name}`);
  return found;
}

function colour(value, name) {
  if (!HEX.test(String(value))) throw new Error(`the engine's look's ${name} is a #rrggbb colour, not ${JSON.stringify(value)}`);
  return String(value).toLowerCase();
}

/**
 * The look as the engine serves it, checked field by field and frozen. Throws, by name, for a look
 * of another version or a field it lacks.
 */
export function parseXbfLook(json) {
  if (!json || typeof json !== "object") throw new Error("the engine's look is a JSON object");
  if (json.version !== LOOK_VERSION) throw new Error(`the engine's look is version ${JSON.stringify(json.version)}; this viewer reads ${LOOK_VERSION}`);
  const lights = (Array.isArray(json.lights) ? json.lights : []).map((light, at) => {
    const direction = (Array.isArray(light?.direction) ? light.direction : []).map((value) => number(value, `lights[${at}].direction`));
    if (direction.length !== 3 || !Math.hypot(...direction)) throw new Error(`the engine's look's lights[${at}] has no direction`);
    return Object.freeze({ name: String(light.name || `light ${at + 1}`), direction: Object.freeze(direction),
      radiance: number(light.radiance, `lights[${at}].radiance`) });
  });
  if (!lights.length) throw new Error("the engine's look has no lights");
  const modes = json.modes && typeof json.modes === "object" ? json.modes : {};
  return Object.freeze({
    version: LOOK_VERSION,
    backdrop: colour(json.backdrop, "backdrop"),
    ambient: number(json.ambient, "ambient"),
    lights: Object.freeze(lights),
    colourPower: number(json.colourPower, "colourPower"),
    specular: number(json.specular, "specular"),
    shininess: number(json.shininess, "shininess"),
    environment: number(json.environment, "environment"),
    tone: String(json.tone || ""),
    exposure: number(json.exposure, "exposure"),
    fill: colour(json.fill, "fill"),
    palette: Object.freeze((Array.isArray(json.palette) ? json.palette : []).map((value, at) => colour(value, `palette[${at}]`))),
    edges: Object.freeze({
      colours: Object.freeze(Object.fromEntries(Object.entries(json.edges?.colours || {}).map(([line, value]) => [line, colour(value, `edges.${line}`)]))),
      width: number(json.edges?.width, "edges.width")
    }),
    modes: Object.freeze(Object.fromEntries(Object.entries(modes).map(([name, mode]) => [name, Object.freeze({
      faces: String(mode?.faces), edges: String(mode?.edges), opacity: number(mode?.opacity, `modes.${name}.opacity`),
      palette: mode?.palette === true
    })]))),
    grid: Object.freeze({ ...(json.grid || {}) })
  });
}

/** The look of the engine at `origin` (`GET <origin>/api/look`), parsed. */
export async function fetchXbfLook(origin, { fetch: fetchImpl = globalThis.fetch, signal } = {}) {
  const response = await fetchImpl(`${origin}/api/look`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`the engine's look: the engine answered ${response.status}`);
  return parseXbfLook(await response.json());
}

/**
 * The rig a stage lights an engine document's scene with in the regular modes
 * (`../viewer/sceneRig.js`): the look's ambient and lights at `π` times their radiance, each light
 * shining from where the look's direction comes from, no environment, its exposure and its curve.
 */
export function xbfLookRig(look) {
  return Object.freeze({
    id: `xbf-look/${look.version}`,
    ambient: Object.freeze({ color: "#ffffff", intensity: Math.PI * look.ambient }),
    lights: Object.freeze(look.lights.map((light) => {
      const length = Math.hypot(...light.direction);
      return Object.freeze({ name: light.name, towards: Object.freeze(light.direction.map((value) => -value / length)),
        color: "#ffffff", intensity: Math.PI * light.radiance });
    })),
    environment: look.environment,
    exposure: 2 ** look.exposure,
    toneCurve: look.tone
  });
}

/**
 * What a surface wears in the regular modes under the look (`../viewer/surfaceLook.js`): its
 * colour raised to the look's power, ungraded and untinted, as a dielectric with the Phong
 * highlight's GGX lobe (`xbfLookHighlight`), no clear coat, no environment and no lift. OCCT's
 * Phong draws no material's finish, so neither does the look: a part's metal, glass or coat (and
 * its glow) is drawn in Preview, its colour here.
 */
export function xbfLookSurface(look) {
  const highlight = xbfLookHighlight(look.specular, look.shininess);
  return Object.freeze({
    colorPower: look.colourPower,
    finishes: false,
    materialSettings: Object.freeze({
      saturation: 1, contrast: 1, brightness: 1, tintStrength: 0,
      roughness: highlight.roughness, metalness: 0, specularIntensity: highlight.specularIntensity,
      clearcoat: 0, clearcoatRoughness: 0, envMapIntensity: 0, emissiveIntensity: 0
    })
  });
}
