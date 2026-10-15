/**
 * `cadgen pcb snapshot` in the page: a `GET /__cad/plot` payload — a KiCad board drawn from its
 * file, geometry per layer — fitted on a 2D canvas, as a PNG.
 *
 * This is the CLI half of the viewer's plot pane, and shares its whole drawing path
 * (`../lib/plot2d`): the same layout of the sheet, the same fit (drawing2d's), the same frame
 * (`drawPlot`: the sheet on KiCad's board background, every layer of the board as it is made,
 * back to front, poured, seen from the top), on the same theme surround. The pane keeps rasters
 * of a view so a pan costs nothing; this draws the geometry itself, once, which is what those
 * rasters are made of. So a CLI render cannot show what the pane cannot.
 *
 * The payload arrives over the snapshot host's loopback asset server rather than inside the
 * job, as a drawing's does: cadgen reads the board, caches the payload in the store and writes
 * the bytes to a file the page fetches. A large board's pours run to megabytes; inlined in the
 * job they would cross the Playwright driver pipe as one escaped protocol message.
 */
import { appThemeColors } from "../lib/appTheme.js";
import { clearSurface } from "../lib/drawing2d/index.js";
import { drawPlot, fitPlotTransform, layoutPlot } from "../lib/plot2d/index.js";
import { paintOutput, renderOutputs, renderScale } from "./headlessCanvas.js";

/** One output's PNG: the plot fitted to it, on the appearance's background. */
function drawOutput(layout, { width, height, scale, background }) {
  return paintOutput({ width, height, scale }, (context) => {
    clearSurface(context, { width, height, pixelRatio: scale, background });
    drawPlot(context, layout, { transform: fitPlotTransform(layout, width, height), pixelRatio: scale });
  });
}

/**
 * Render one resolved `plot` snapshot job.
 *
 * @param {object} job The resolved render job; `job.resolved.plotUrl` names the payload.
 * @returns {Promise<object>} The host's result shape: `{ ok, mode, outputs, warnings }`.
 */
export async function runHeadlessPlotJob(job) {
  const url = String(job?.resolved?.plotUrl || "");
  if (!url) {
    throw new Error(
      "a plot render job carries no plotUrl: cadgen resolves a board to its plot payload before "
      + "the page is asked to draw it"
    );
  }
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`the plot payload could not be read (HTTP ${response.status} for ${url})`);
  }
  const layout = layoutPlot(await response.json());
  const warnings = [];
  if (layout.unrouted) {
    // A true answer, and one a thin line in a PNG does not shout.
    warnings.push(
      `${layout.unrouted} connection${layout.unrouted === 1 ? " is" : "s are"} still unrouted: `
      + "the image draws each as a straight ratsnest line"
    );
  }
  const { background } = appThemeColors(job?.display?.appearance);
  const scale = renderScale(job);
  const transparent = job?.output?.transparent === true;
  const outputs = renderOutputs(job, (width, height) => drawOutput(layout, {
    width,
    height,
    scale,
    background: transparent ? null : background
  }));
  return {
    ok: true,
    mode: "view",
    appearance: job?.display?.appearance === "dark" ? "dark" : "light",
    sheetCount: layout.sheets.length,
    outputs,
    warnings
  };
}
