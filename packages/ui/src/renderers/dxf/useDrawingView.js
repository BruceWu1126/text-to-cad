/**
 * The canvas a drawing is painted on, and the three things a person does to it:
 * drag to pan, wheel or pinch to zoom about the pointer, double-click to fit —
 * and, for the tool in hand, a tap (a press that lets go without dragging) and
 * the pointer resting over the drawing (`onTap`, `onHover`), in the pane's CSS
 * pixels. Those are every flat view's (`kit/viewport/useFlatCanvasView.js`); this
 * is the drawing painted under them.
 *
 * Painting is on demand: one `requestAnimationFrame` is scheduled when
 * something changed, and an idle drawing draws nothing at all. A resize is the
 * exception: resizing the canvas wipes it, so the pane's observer paints at once.
 * What the tools draw over the drawing (a highlight, ink) is painted in the same
 * frame, after it (`overlay`), so a capture of the canvas holds it too.
 */
import { useCallback, useEffect, useRef } from "react";
import { clearSurface, drawDrawing, fitTransform } from "@text-to-cad/core/lib/drawing2d/index.js";
import { useFlatCanvasView } from "../kit/viewport/useFlatCanvasView.js";
import { readDrawingThemeColors } from "./themeColors.js";

const NO_LAYERS = new Set();

/**
 * @param {object} options
 * @param {object|null} options.drawing  A prepared drawing, or null while it loads.
 * @param {{ scale: number, offsetX: number, offsetY: number }|null} options.restored
 *   The view this file was left at, when it was left anywhere but the fit.
 * @param {"light"|"dark"} options.colorScheme
 * @param {(transform: object|null) => void} options.onViewMoved  Called after the person moves the
 *   view, with the transform to remember. Never called for a fit.
 * @param {ReadonlySet<string>} [options.hiddenLayers]  Layers not drawn.
 * @param {(ctx: CanvasRenderingContext2D, view: { transform: object, pixelRatio: number, width: number, height: number }) => void} [options.overlay]
 *   Painted over the drawing in each frame.
 * @param {(point: { x: number, y: number }, event: PointerEvent) => void} [options.onTap]
 * @param {(point: { x: number, y: number } | null, event: PointerEvent | null) => void} [options.onHover]
 *   A pointer resting over the canvas with nothing pressed (null: it left).
 */
export function useDrawingView({ drawing, restored = null, colorScheme = "light", onViewMoved, hiddenLayers = NO_LAYERS,
  overlay = null, onTap = null, onHover = null }) {
  const drawingRef = useRef(drawing);
  drawingRef.current = drawing;
  const hiddenRef = useRef(hiddenLayers);
  hiddenRef.current = hiddenLayers;
  const overlayRef = useRef(overlay);
  overlayRef.current = overlay;

  const paint = useCallback((ctx, { width, height, pixelRatio, transform, element, colorScheme: scheme }) => {
    const { background, foreground } = readDrawingThemeColors(element, scheme);
    clearSurface(ctx, { width, height, pixelRatio, background });
    const drawable = drawingRef.current;
    if (drawable && transform) {
      drawDrawing(ctx, drawable, { transform, foreground, pixelRatio, hiddenLayers: hiddenRef.current });
      overlayRef.current?.(ctx, { transform, pixelRatio, width, height });
    }
  }, []);
  const view = useFlatCanvasView({ content: drawing, bounds: drawing?.bounds ?? null, restored, colorScheme, onViewMoved, paint, onTap, onHover });
  const { containerRef, schemeRef, requestPaint, encode } = view;

  // A layer shown or hidden: the same view, without it.
  useEffect(() => { requestPaint(); }, [hiddenLayers, requestPaint]);

  /**
   * The drawing on its own, for a library card: fitted whole to `width` × `height`, in the theme's
   * pens on its background, painted on a canvas of its own — the pane's view is left as it is.
   */
  const thumbnail = useCallback(({ width, height }) => new Promise((resolve, reject) => {
    const drawable = drawingRef.current;
    if (!drawable?.bounds) { reject(new Error("This drawing has nothing to picture.")); return; }
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.round(width));
    canvas.height = Math.max(1, Math.round(height));
    const context = canvas.getContext("2d");
    if (!context) { reject(new Error("The browser cannot draw the drawing's picture.")); return; }
    const { background, foreground } = readDrawingThemeColors(containerRef.current, schemeRef.current);
    clearSurface(context, { width: canvas.width, height: canvas.height, background });
    drawDrawing(context, drawable, { transform: fitTransform(drawable.bounds, canvas.width, canvas.height), foreground });
    canvas.toBlob((blob) => {
      if (blob) resolve(blob);
      else reject(new Error("The browser could not encode this drawing as a PNG."));
    }, "image/png");
  }), [containerRef, schemeRef]);

  /** The framed picture as a PNG, background included: it is painted into the canvas. */
  const capture = useCallback(() => encode("drawing"), [encode]);

  return { containerRef, canvasRef: view.canvasRef, dragging: view.dragging, fit: view.fit, zoomBy: view.zoomBy, capture, thumbnail,
    transformRef: view.transformRef, sizeRef: view.sizeRef, requestPaint };
}
