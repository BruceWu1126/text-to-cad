// The Render Studio's view: a viewport of its own, mounted over the editor's while the Studio is
// open and unmounted as it closes.
//
// Its canvas, scene, camera, look and view-update coordinator are its own; only the GPU device is
// shared (`kit/viewport/gpuDevice.js`), so the model's buffers and compiled programs are uploaded
// once. The editor's viewport stays mounted underneath and is never told the Studio exists:
// entering and leaving change nothing of it, and what shows when the Studio closes is the editor's
// own last frame, untouched.
//
// The scene is the renderer's (`studio.scene`): built for the Studio by its family's own builder
// over the editor's geometry, posed and played by the Studio's own pose and routine. What drives
// it from inside the viewport, if anything does (a renderer's own layers), is
// `studio.overlay(viewport, drawn)`, handed the Render scene the Studio draws.
import { useCallback } from "react";
import ShellViewport from "./ShellViewport.jsx";
import { useAppliedViewSettings } from "../view-settings/useAppliedViewSettings.js";

/**
 * @param {{ studio: { scene: object | null, overlay?: ((viewport: object, drawn: object) => import("react").ReactNode) | null,
 *   runtimeLifecycle?: object | null, onCameraSettled?: (() => void) | null },
 *   desired: object, modelKey: string, sceneScaleMode: string, viewRef: import("react").RefObject<object>,
 *   orbit?: boolean, orbitSpeed?: number, onPresented?: (presented: boolean) => void }} props
 */
export default function StudioView({ studio, desired, modelKey, sceneScaleMode, viewRef, orbit = false, orbitSpeed = 1, onPresented = null }) {
  const studioKey = `${modelKey}#studio`;
  const update = useAppliedViewSettings(desired, studioKey, viewRef, null);
  // The file's Render as the Studio draws it (`studioViewScene.js`), applied by its own coordinator.
  const drawn = update.scene;
  const overlay = studio.overlay;
  const children = useCallback(viewport => overlay?.(viewport, drawn) ?? null, [overlay, drawn]);
  const presentationChange = useCallback(state => onPresented?.(state.key === studioKey && !state.preparing), [onPresented, studioKey]);
  return <ShellViewport
    ref={viewRef}
    studio
    scene={studio.scene}
    modelKey={modelKey}
    presentationKey={studioKey}
    sceneScaleMode={sceneScaleMode}
    projection={drawn.camera.projection}
    focalLength={drawn.camera.focalLength}
    themeSettings={drawn.theme}
    displaySettings={drawn.display}
    appearance={drawn.appearance}
    receiveShadows={drawn.view.lighting.enabled}
    renderMode={drawn.render.enabled}
    renderConfiguration={drawn.render.configuration}
    quality={drawn.quality}
    orbit={orbit}
    orbitSpeed={orbitSpeed}
    viewCube={false}
    viewUpdate={update}
    onPresentationChange={presentationChange}
    onCameraSettled={studio.onCameraSettled || null}
    runtimeLifecycle={studio.runtimeLifecycle || null}
    preserveInteractionPixelRatio
  >{children}</ShellViewport>;
}
