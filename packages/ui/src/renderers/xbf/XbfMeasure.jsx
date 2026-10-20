import { useEffect, useRef } from "react";
import { usePointerPick } from "../kit/tools/select/usePointerPick.js";
import { useMeasureOverlay } from "../kit/tools/measure/useMeasureOverlay.js";

// An engine document's Measure, as a STEP's: it measures between points on the model, snapping
// to no edge or face (its faces and edges are not pickable while it is up), in millimetres, the
// rulers drawn and listed as a STEP's are (`useMeasureOverlay.js`, `measurementChips.jsx`). The
// session is the renderer's (`useMeasureSession`), since Quick Edit reads it before the shell is
// made. The kit's Measure on a scene of triangles (`SceneMeasure.jsx`) snaps to a triangle's
// corners and calls its readings approximate; a STEP's does neither, so this one does neither.

const pointArray = point => (Array.isArray(point) ? point : point?.toArray?.() ?? null);

/** Where a pick lands: the point on the surface under the pointer. */
export function xbfMeasurePick(hit) {
  const point = pointArray(hit?.point);
  if (!Array.isArray(point) || point.length < 3 || !point.every(Number.isFinite)) return null;
  // A free point, as a STEP's Measure takes it (`classifyMeasurePick` with no reference).
  return { point: point.slice(0, 3), snapKind: "free", referenceId: "", reference: null, geometry: null };
}

/**
 * Measure's layer over the viewport: the measurements drawn, and while the tool is in hand
 * (`picking`) a pick per tap and the marker under the resting pointer.
 */
export default function XbfMeasureOverlay({ viewport, scene, measure, picking, preview = false }) {
  const canvasRef = useRef(null);
  const hoverRef = useRef(null);
  const anchorRef = useRef(null);
  useEffect(() => { if (!picking) { hoverRef.current = null; anchorRef.current = null; } }, [picking]);
  useEffect(() => { if (!measure.state?.draft?.anchor) anchorRef.current = null; }, [measure.state]);
  usePointerPick({
    viewport, scene, enabled: picking && !preview,
    sameHit: () => false,
    onPick(hit) {
      const pick = xbfMeasurePick(hit);
      if (!pick) return;
      if (!anchorRef.current) { anchorRef.current = pick; hoverRef.current = pick; } else anchorRef.current = null;
      measure.onPick(pick);
    },
    onHover(hit) {
      const pick = xbfMeasurePick(hit);
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
