// The display tolerance is cadgen's (cadgen.tessellation_policy): the chord (RELATIVE to a
// component's bounding diagonal) and angle (radians) tolerances every STEP part is meshed at.
// It arrives with the server's description -- the CAD Viewer's server info, the CAD app's
// launch -- and the host installs it once, before a STEP model is drawn. The page holds no
// tolerance of its own: every mesh request and cache key names this pair.
let installed = null;

function finitePositive(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0;
}

/** Install the display tolerance cadgen published: `{ chordTolerance, angleTolerance }`. */
export function installDisplayTessellation(published) {
  const { chordTolerance, angleTolerance } = published ?? {};
  if (!finitePositive(chordTolerance) || !finitePositive(angleTolerance)) {
    throw new Error(
      "cadgen's display tolerance is { chordTolerance, angleTolerance }; "
      + `received ${JSON.stringify(published)}. Update cadgen and the app together.`
    );
  }
  installed = Object.freeze({ chordTolerance, angleTolerance });
  return installed;
}

/** The display tolerance, both named: what every mesh request and its cache key say. */
export function displayTessellation() {
  if (!installed) {
    throw new Error(
      "The display tolerance is cadgen's: the host installs it (installDisplayTessellation) from the "
      + "server's description before a STEP model is drawn."
    );
  }
  return installed;
}
