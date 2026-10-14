/**
 * The CAM result viewer GlbRenderer loads only for a GLB that carries a job (`cadgen cam plan`): one module, so the
 * bundler makes it a chunk of its own and neither a plain GLB nor an FEA result fetches it. Everything GlbRenderer
 * uses of the CAM viewer is here, by name.
 */
import { useLayoutEffect } from "react";
import { CAM_JOB_PANEL_ID } from "./CamJobPanel.jsx";
import { panelInset } from "./feaChrome.js";
import { readCamResult } from "./camResult.js";

export { default as CamJobPanel } from "./CamJobPanel.jsx";
export { default as CamReadout, readoutWords } from "./CamReadout.jsx";
export { STEP_SECONDS, camMotion, camSummary, camTime, findingOperation, jobPlayback, operationAt, operationRow, readCamResult, setupRow, toolRow } from "./camResult.js";
export { createCamPaths, createCamTool, pathColours } from "./camPaths.js";
export { motionControl, motionPresets } from "./fea/motion.js";

// Select's own panel, which a person can close: Job. A result opens with it up, except on a phone.
export const JOB_PANEL = Object.freeze({ id: CAM_JOB_PANEL_ID, label: "Job", startsClosed: false });

/**
 * A CAM result as GlbRenderer holds it: `readCamResult`'s, or null for a scene that does not carry one.
 * @param {import("three").Object3D | null | undefined} root
 */
export const camResultOf = (root) => (root ? readCamResult(root) : null);

/**
 * The absolute path of the part a job was planned on. The file records its name (`document`) relative to the GLB's
 * own folder; an absolute one is kept. "" when it names nothing. (FEA's `resultSourcePath` reads its result the same way.)
 */
export function camSourcePath(glbPath, document) {
  const named = String(document || "").trim();
  if (!named) return "";
  const absolute = /^(?:[A-Za-z]:)?[\\/]/.test(named);
  const parts = absolute ? named.split("/") : [...String(glbPath || "").split("/").slice(0, -1), ...named.split("/")];
  const out = [];
  for (const part of parts) {
    if (part === "..") out.pop();
    else if (part !== ".") out.push(part);
  }
  return out.join("/");
}

// The gap a camera fit leaves between the model and the readout.
const READOUT_GAP_PX = 8;

/**
 * What a camera fit of a job leaves out of the view: at the left the shown panels (Job), at the foot the readout and a
 * gap over it, so no path is drawn under either. Pure but for what it reads of the page.
 * @param {HTMLElement | null | undefined} host
 */
export function camFramingInset(host) {
  const viewer = host?.closest?.("[data-cad-scene-backdrop]");
  const readout = viewer?.querySelector?.("[data-cam-readout] > div");
  const box = readout && !readout.closest("[hidden]") ? readout.getBoundingClientRect() : null;
  const base = host?.getBoundingClientRect?.();
  const bottom = box && base && box.height > 0 && base.height > 0 ? Math.max(base.bottom - box.top + READOUT_GAP_PX, 0) : 0;
  return { left: panelInset(host), bottom };
}

/**
 * A job's camera frames the model right of Job and above the readout (`runtime.framingInset`, read at each fit), as an
 * FEA result's does; Preview, which puts the panels away, takes the whole width. Installed before the shell's first
 * fit of the result: a child's layout effect runs first.
 */
export function CamFraming({ runtimeRef, hostRef }) {
  useLayoutEffect(() => {
    const runtime = runtimeRef.current;
    if (!runtime) return undefined;
    runtime.framingInset = () => camFramingInset(hostRef.current);
    return () => { if (runtime.framingInset) delete runtime.framingInset; };
  }, [runtimeRef, hostRef]);
  return null;
}
