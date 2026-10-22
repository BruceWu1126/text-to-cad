import { SURFACE_BRDFS } from "../viewer/surfaceBrdf.js";

// The engine's look of the regular display modes (`GET /api/look` and its environment image,
// `GET /api/look/environment`: `cadgen/engine/render/look.py`), and what an engine document's scene
// draws by it in those modes: the rig a stage lights it with in place of the theme's
// (`../viewer/sceneContract.js`'s `rig`, put up by `../viewer/sceneRig.js`) and what its surfaces
// wear (`xbfLookSurface`). Preview's photographic look is not this: there the scene is drawn as
// any scene is.
//
// The look is physically based, in the terms three's physical lights already use: its key is a
// directional light of the look's intensity, its environment lights every surface at the look's
// intensity, every surface answers both by the look's surface model (`brdf`: OCCT's PBR program,
// `../viewer/surfaceBrdf.js`), and the sum is clamped and sRGB-encoded with no tone curve. A part
// without a material (and every fill) wears the look's plain finish over its colour raised to the
// look's power; a part with one wears its own finish. The OCCT prototype draws the same record
// (`render/finish.py`).

const HEX = /^#[0-9a-f]{6}$/i;
const LOOK_VERSION = 3;
// The image's frame to three's: its rows run from +Z down, which three's equirectangular lookup
// reads as +Y; turning the lookup a quarter about X sends each of the document's directions to its
// texel (`scene.environmentRotation`).
export const XBF_ENVIRONMENT_ROTATION = Object.freeze([Math.PI / 2, 0, 0]);

function number(value, name) {
  const found = Number(value);
  if (value === null || value === "" || !Number.isFinite(found)) throw new Error(`the engine's look has no ${name}`);
  return found;
}

function colour(value, name) {
  if (!HEX.test(String(value))) throw new Error(`the engine's look's ${name} is a #rrggbb colour, not ${JSON.stringify(value)}`);
  return String(value).toLowerCase();
}

function brdf(value) {
  if (!Object.hasOwn(SURFACE_BRDFS, value)) {
    throw new Error(`the engine's look's brdf is one of ${Object.keys(SURFACE_BRDFS).join(", ")}, not ${JSON.stringify(value)}`);
  }
  return value;
}

/**
 * A Radiance RGBE image (flat or new run-length scanlines, `-Y H +X W`) as RGBA floats, the bottom
 * row first (three's texture order), each pixel `m / 255 * 2 ** (e - 128)` as the engine decodes it.
 * @returns {{ width: number, height: number, data: Float32Array }}
 */
export function decodeRadiance(buffer) {
  const bytes = new Uint8Array(buffer);
  let at = 0;
  const line = () => {
    const start = at;
    while (at < bytes.length && bytes[at] !== 10) at += 1;
    return String.fromCharCode(...bytes.subarray(start, at++));
  };
  if (!line().startsWith("#?")) throw new Error("the engine's environment is not a Radiance image");
  while (line() !== "") { /* the header's lines */ }
  const axes = line().split(" ");
  if (axes.length !== 4 || axes[0] !== "-Y" || axes[2] !== "+X") throw new Error("the engine's environment is a -Y H +X W Radiance image");
  const height = Number(axes[1]), width = Number(axes[3]);
  const rgbe = new Uint8Array(width * 4);
  const data = new Float32Array(width * height * 4);
  for (let row = 0; row < height; row += 1) {
    if (bytes[at] === 2 && bytes[at + 1] === 2 && ((bytes[at + 2] << 8) | bytes[at + 3]) === width) {
      at += 4;
      for (let channel = 0; channel < 4; channel += 1) {
        for (let x = 0; x < width;) {
          let count = bytes[at++];
          if (count > 128) {
            count -= 128;
            const value = bytes[at++];
            for (let end = x + count; x < end; x += 1) rgbe[x * 4 + channel] = value;
          } else {
            for (let end = x + count; x < end; x += 1) rgbe[x * 4 + channel] = bytes[at++];
          }
        }
      }
    } else {
      rgbe.set(bytes.subarray(at, at + width * 4));
      at += width * 4;
    }
    const into = (height - 1 - row) * width * 4;
    for (let x = 0; x < width; x += 1) {
      const e = rgbe[x * 4 + 3];
      for (let channel = 0; channel < 3; channel += 1) {
        data[into + x * 4 + channel] = e ? rgbe[x * 4 + channel] * 2 ** (e - 128) / 255 : 0;
      }
      data[into + x * 4 + 3] = 1;
    }
  }
  return { width, height, data };
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
      intensity: number(light.intensity, `lights[${at}].intensity`) });
  });
  if (!lights.length) throw new Error("the engine's look has no lights");
  if (typeof json.environment?.url !== "string") throw new Error("the engine's look has no environment");
  const modes = json.modes && typeof json.modes === "object" ? json.modes : {};
  return Object.freeze({
    version: LOOK_VERSION,
    backdrop: colour(json.backdrop, "backdrop"),
    lights: Object.freeze(lights),
    environment: Object.freeze({ url: json.environment.url, intensity: number(json.environment.intensity, "environment.intensity") }),
    colourPower: number(json.colourPower, "colourPower"),
    plain: Object.freeze({ metallic: number(json.plain?.metallic, "plain.metallic"),
      roughness: number(json.plain?.roughness, "plain.roughness"), ior: number(json.plain?.ior, "plain.ior") }),
    brdf: brdf(json.brdf),
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

/**
 * The look of the engine at `origin`: `GET <origin>/api/look`, parsed, and its environment image
 * read and decoded (`environment.image`).
 */
export async function fetchXbfLook(origin, { fetch: fetchImpl = globalThis.fetch, signal } = {}) {
  const response = await fetchImpl(`${origin}/api/look`, { signal, cache: "no-store" });
  if (!response.ok) throw new Error(`the engine's look: the engine answered ${response.status}`);
  const look = parseXbfLook(await response.json());
  const image = await fetchImpl(`${origin}${look.environment.url}`, { signal });
  if (!image.ok) throw new Error(`the engine's look's environment: the engine answered ${image.status}`);
  return Object.freeze({ ...look, environment: Object.freeze({ ...look.environment, image: decodeRadiance(await image.arrayBuffer()) }) });
}

/**
 * The rig a stage lights an engine document's scene with in the regular modes
 * (`../viewer/sceneRig.js`): the look's key lights, each shining from where its direction comes, its
 * environment image at its intensity turned into the document's frame, exposure 1 and the look's
 * curve. A look read without its image lights with none.
 */
export function xbfLookRig(look) {
  const image = look.environment.image || null;
  return Object.freeze({
    id: `xbf-look/${look.version}`,
    ambient: null,
    lights: Object.freeze(look.lights.map((light) => {
      const length = Math.hypot(...light.direction);
      return Object.freeze({ name: light.name, towards: Object.freeze(light.direction.map((value) => -value / length)),
        color: "#ffffff", intensity: light.intensity });
    })),
    environment: image ? Object.freeze({ width: image.width, height: image.height, data: image.data,
      intensity: look.environment.intensity, rotation: XBF_ENVIRONMENT_ROTATION }) : null,
    exposure: 2 ** look.exposure,
    toneCurve: look.tone
  });
}

/**
 * What a surface wears in the regular modes under the look (`../viewer/surfaceLook.js`): the
 * colour of a surface without a finish of its own, and of every fill, raised to the look's power,
 * every colour ungraded and untinted; and where no finish of the document's is kept (no material,
 * a fill, X-ray) the look's plain finish, with no clear coat and no lift. Every surface reflects
 * the rig's environment and is drawn by the look's surface model.
 */
export function xbfLookSurface(look) {
  return Object.freeze({
    colorPower: look.colourPower,
    brdf: look.brdf,
    materialSettings: Object.freeze({
      saturation: 1, contrast: 1, brightness: 1, tintStrength: 0,
      roughness: look.plain.roughness, metalness: look.plain.metallic,
      clearcoat: 0, clearcoatRoughness: 0, envMapIntensity: 1, emissiveIntensity: 0
    })
  });
}
