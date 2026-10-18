import { useEffect, useRef } from "react";
import * as THREE from "three";
import { Ruler, RulerDimensionLine } from "lucide-react";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { projectWorldPointToClient } from "@text-to-cad/core/lib/viewer/measureRuler.js";
import { boundsMeasurePicks, meshMeasurePick } from "@text-to-cad/core/lib/viewer/meshMeasure.js";
import { boundsInUnits } from "@text-to-cad/core/lib/viewer/sceneUnits.js";
import SubToolbar, { SubToolbarValue } from "../SubToolbar.jsx";
import { usePointerPick } from "../select/usePointerPick.js";
import { useMeasureOverlay } from "./useMeasureOverlay.js";

// Measure on a scene of triangles: points snapped to the triangles under the pointer
// (`meshMeasure.js`), lengths in the file's unit (the scene's `units`) and approximate, drawn and
// listed as exact measurements are (`useMeasureOverlay.js`, `measurementChips.jsx`). The session
// is the renderer's (`useMeasureSession`), since Quick Edit reads it before the shell is made.

/** The tool id a family's Measure takes on the strip. */
export const SCENE_MEASURE_TOOL = "measure";

/** Measure's strip icon: the ruler. */
export function MeasureIcon(props) {
  return <Ruler strokeWidth={2} {...props} />;
}

const pointArray = point => (Array.isArray(point) ? point : point?.toArray?.() ?? null);

/**
 * Measure's layer over the viewport: the measurements drawn, and while the tool is in hand
 * (`picking`) a pick per tap and a snap marker under the resting pointer. Draws nothing for a
 * scene that cannot be picked.
 *
 * @param {{ viewport: object, scene: object | null, measure: ReturnType<typeof import("./useMeasureSession.js").useMeasureSession>,
 *   picking: boolean, preview?: boolean }} props
 */
export function SceneMeasureOverlay({ viewport, scene, measure, picking, preview = false }) {
  const canvasRef = useRef(null);
  const hoverRef = useRef(null);
  const anchorRef = useRef(null);
  useEffect(() => { if (!picking) { hoverRef.current = null; anchorRef.current = null; } }, [picking]);
  useEffect(() => { if (!measure.state?.draft?.anchor) anchorRef.current = null; }, [measure.state]);
  const snap = (hit, pointer) => {
    const runtime = viewport.runtimeRef.current;
    if (!hit || !pointer || !runtime?.camera || !scene?.units) return null;
    const rect = { left: 0, top: 0, width: pointer.width, height: pointer.height };
    return meshMeasurePick({ ...hit, point: pointArray(hit.point) }, pointer, point => projectWorldPointToClient(point, runtime.camera, rect), scene.units);
  };
  usePointerPick({
    viewport, scene, enabled: picking && !preview,
    // Every pointer move snaps anew: the corner or side nearest it changes within one triangle.
    sameHit: () => false,
    onPick(hit, _modifiers, pointer) {
      const pick = snap(hit, pointer);
      if (!pick) return;
      // The first end holds the snap marker until the second is taken.
      if (!anchorRef.current) { anchorRef.current = pick; hoverRef.current = pick; } else anchorRef.current = null;
      measure.onPick(pick);
    },
    onHover(hit, pointer) {
      const pick = snap(hit, pointer);
      hoverRef.current = pick || anchorRef.current;
      measure.onHoverPoint(pick);
    }
  });
  useMeasureOverlay({
    measureCanvasRef: canvasRef, measureState: measure.state, activeMeasurementId: measure.activeId,
    selectedMeasurementIds: measure.selected, hoveredMeasurementId: measure.hovered, measureHoverRef: hoverRef,
    measureModeActive: picking, runtimeRef: viewport.runtimeRef, mountRef: viewport.mountRef, preview,
    viewerReadyTick: viewport.viewerReadyTick
  });
  return <canvas ref={canvasRef} className="absolute inset-0 z-10 h-full w-full touch-none" style={{ pointerEvents: "none" }} aria-hidden="true" data-scene-measure="" />;
}

/** What `scene` draws now, as one box in its own coordinates; null for nothing drawn. */
function drawnBounds(scene) {
  const box = new THREE.Box3();
  scene?.object3D?.updateMatrixWorld(true);
  scene?.object3D?.traverse((object) => {
    // A cut's stencil and a highlight's ghost are hung under the mesh they decorate.
    if (object.isMesh && object.visible && !object.parent?.isMesh) box.expandByObject(object, false);
  });
  return box.isEmpty() ? null : { min: box.min.toArray(), max: box.max.toArray() };
}

const SIZE_DECIMALS = Object.freeze({ m: 3, cm: 1, mm: 1, "µm": 0, in: 2, ft: 3 });

/**
 * Measure's row under the strip: the model's overall size as drawn now (its box, in the file's
 * unit, approximate), and a button that adds that size to the measurements — the box's three
 * edges, X, Y and Z — so a note can carry it.
 *
 * @param {{ scene: object | null, measure: ReturnType<typeof import("./useMeasureSession.js").useMeasureSession>, disabled?: boolean }} props
 */
export function MeasureSizeBar({ scene, measure, disabled = false }) {
  const units = scene?.units;
  const bounds = units ? drawnBounds(scene) : null;
  const size = bounds ? boundsInUnits(bounds, units) : null;
  const digits = SIZE_DECIMALS[units?.unit] ?? 2;
  const text = size ? `≈ ${[0, 1, 2].map(axis => (size.max[axis] - size.min[axis]).toFixed(digits)).join(" × ")}${units.unit ? ` ${units.unit}` : ""}` : "";
  const addSize = () => {
    measure.cancelDraft();
    for (const [start, end] of boundsMeasurePicks(bounds, units)) { measure.onPick(start); measure.onPick(end); }
  };
  return <SubToolbar label="Measure controls" data-model-tool="measure">
    <span className="shrink-0 pl-1.5 pr-1 text-tiny text-foreground">Size</span>
    <SubToolbarValue data-measure-size="">{text}</SubToolbarValue>
    <ToolbarButton label="Measure the size" disabled={disabled || !bounds} onClick={addSize}>
      <RulerDimensionLine className="size-3" strokeWidth={2} aria-hidden="true" />
    </ToolbarButton>
  </SubToolbar>;
}
