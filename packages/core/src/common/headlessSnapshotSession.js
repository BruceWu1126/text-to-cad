import * as THREE from 'three';
import { headlessResources, headlessSceneDress, headlessSceneFamily, headlessSceneModel } from './headlessScene.js';
import { captureModel, renderJobContext, renderModel } from './renderMeshScene.js';
import { outputSize } from './renderOptions.js';
import { scenePartCount } from './viewSettings.js';
import { validateSnapshotRenderJob } from './snapshotJobValidation.js';
import { createHeadlessPreview } from './headlessPreview.js';
import { xbfMotions, xbfMotionTimeline } from '../lib/xbf/xbfMotion.js';
import { createGlbAnimationRuntime, setGlbAnimationTime, disposeGlbAnimationRuntime } from '../lib/render/glbAnimationRuntime.js';

function applyRoutine(session, index, time) {
  const item = session.routines[index];
  if (session.family.name === 'xbf') {
    let timeline = session.timelines.get(index);
    if (!timeline) { timeline = xbfMotionTimeline(session.scene.kinematics, item.name); session.timelines.set(index, timeline); }
    session.scene.setPose(timeline.values(time));
  } else {
    let animation = session.animations.get(index);
    if (!animation) { animation = createGlbAnimationRuntime(THREE, session.scene.document.scene, session.entries[index]); session.animations.set(index, animation); }
    setGlbAnimationTime(animation, time);
  }
  session.scene.object3D.updateMatrixWorld(true);
}

// Fit the camera once to the bounds at every exported time; no animated part
// leaves the frame and a later frame never repositions the camera.
export function frameBoundsFor(session, index) {
  const item = session.routines[index];
  if (session.frameBounds.has(item.id)) return session.frameBounds.get(item.id);
  const fps = Number(session.job.animation?.fps ?? 30);
  if (!Number.isFinite(fps) || fps <= 0 || fps > 60) throw new Error('Animation fps must be between 0 and 60');
  const count = Math.max(1, Math.ceil(item.duration * fps));
  if (count > 3600) throw new Error('Animation exceeds the 3,600 frame limit');
  const bounds = new THREE.Box3();
  for (let i = 0; i < count; i++) {
    applyRoutine(session, index, Math.min(i / fps, item.duration));
    const b = session.family.name === 'xbf' ? session.scene.bounds : null;
    if (b) bounds.union(new THREE.Box3(new THREE.Vector3(...b.min), new THREE.Vector3(...b.max)));
    else bounds.union(new THREE.Box3().setFromObject(session.scene.object3D, true));
  }
  if (bounds.isEmpty()) throw new Error('Animation has no visible bounds');
  const result = { min: bounds.min.toArray(), max: bounds.max.toArray() };
  session.frameBounds.set(item.id, result);
  return result;
}

// One page owns one session, scene and tracer across frames. The host chooses a
// routine and absolute times; this layer never uses a wall clock for motion.
export function openHeadlessSnapshotSession() {
  let current = null;
  async function close() {
    const session = current; current = null;
    if (!session) return;
    session.preview?.dispose();
    for (const runtime of session.animations.values()) disposeGlbAnimationRuntime(runtime);
    session.viewport.dispose();
  }
  return {
    get progress() { return current?.preview?.progress || null; },
    async open(job) {
      await close();
      validateSnapshotRenderJob(job);
      const family = headlessSceneFamily(job);
      if (!family) throw new Error('Snapshot session requires a supported 3D scene');
      const loaded = await family.load(job, { resources: headlessResources() });
      let scene;
      try { scene = family.build(THREE, loaded, job); }
      catch (error) { family.release(loaded); throw error; }
      let viewport;
      try {
        const context = renderJobContext({ bounds: scene.bounds, partCount: scenePartCount(scene), explodeParts: scene.explodeParts || 0 }, job);
        const model = headlessSceneModel(THREE, scene, headlessSceneDress(context.sceneSettings));
        viewport = renderModel(THREE, model, { job, context });
        await viewport.ready;
        const entries = family.name === 'xbf' ? xbfMotions(scene.kinematics) : family.name === 'glb' ? (scene.document.clips || []) : [];
        const routines = entries.map((entry, index) => ({ id: `${family.name}:${index}`, name: String(entry.name || `Animation ${index + 1}`), duration: Number(entry.duration) || 0 }));
        current = { family, scene, viewport, job, entries, routines, animations: new Map(), timelines: new Map(), cameraCache: new Map(), frameBounds: new Map(), activeRoutine: undefined, preview: job.preview === true ? createHeadlessPreview(viewport) : null, busy: false };
        return { routines, ...outputSize(context.outputs[0] || {}, job) };
      } catch (error) { if (viewport) viewport.dispose(); else scene.dispose(); throw error; }
    },
    async renderFrame({ routine = null, time = 0, outputIndex = 0 } = {}) {
      const session = current;
      if (!session) throw new Error('Snapshot session is not open');
      if (session.busy) throw new Error('A snapshot frame is already rendering');
      if (!Number.isFinite(time) || time < 0) throw new Error('Snapshot animation time must be a nonnegative finite number');
      session.busy = true;
      try {
        if (routine !== session.activeRoutine) {
          session.cameraCache.clear();
          for (const animation of session.animations.values()) disposeGlbAnimationRuntime(animation);
          session.animations.clear();
          if (session.family.name === 'xbf' && session.activeRoutine !== undefined) session.scene.setPose(null);
          session.activeRoutine = routine;
        }
        if (routine != null) {
          const index = session.routines.findIndex(item => item.id === routine);
          if (index < 0) throw new Error(`Unknown snapshot routine: ${routine}`);
          const item = session.routines[index];
          if (session.preview && session.family.name === 'glb' && session.entries[index].tracks.some(track => !/\.(position|quaternion|scale)$/.test(track.name))) {
            throw new Error(`Preview tracing supports rigid transform tracks only: ${item.name}`);
          }
          if (time > item.duration + 1e-9) throw new Error(`Time exceeds routine duration: ${routine}`);
          frameBoundsFor(session, index);
          applyRoutine(session, index, time);
        }
        const result = await captureModel(session.viewport, { job: session.job, outputIndex,
          cameraCache: session.cameraCache, frameBounds: session.frameBounds.get(routine) || null,
          renderOutput: session.preview ? camera => session.preview.renderFrame(camera, { sceneChanged: true }) : null });
        return { image: result.outputs[0].dataUrl, report: { ...result, outputs: result.outputs.map(({ dataUrl, ...output }) => output), routine, time, renderer: session.preview ? 'preview-path-traced' : 'raster', ...(session.preview ? { preview: session.preview.progress } : {}) } };
      } finally { session.busy = false; }
    },
    close,
  };
}
