import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { fillMarkAt, inkAt, penMark, shapeMarks, textMark, withGroups } from "../../../../drawing/inkGeometry.js";
import { DEFAULT_OVERLAY_DRAWING_COLOR } from "../../../../drawing/toolbar.jsx";
import { useViewerHost } from "../../../../host/context.js";
import { capturePointer } from "../../../../lib/pointerCapture.js";
import { inkOnScreen, moveInk, projectInk } from "./ink3d.js";

// Someone who pressed Draw came to draw: Draw opens on the pen.
export const CAD_DRAWING_DEFAULTS = Object.freeze({ tool: "freedraw", color: DEFAULT_OVERLAY_DRAWING_COLOR });

const SHAPES = new Set(["line", "arrow", "rectangle", "ellipse"]);
const ERASER_RADIUS = 8;
const FONT_PX = Object.freeze({ 1: 16, 2: 20, 4: 28 });
const fontOf = px => `500 ${px}px ui-sans-serif, system-ui, sans-serif`;
const DASH = Object.freeze({ solid: [], dashed: [8, 6], dotted: [1.5, 5] });
const SELECTION_COLOR = "#2979ff";

// A mark's path on the preview canvas: its points, at its width (a pen's, point by point).
function paintMark(context, mark) {
  context.save();
  context.globalAlpha = mark.opacity ?? 1;
  context.strokeStyle = mark.color;
  context.fillStyle = mark.color;
  context.lineCap = "round";
  context.lineJoin = "round";
  if (mark.kind === "area") {
    context.beginPath();
    mark.outline.forEach((point, index) => (index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y)));
    context.closePath();
    context.fill();
  } else if (mark.widths) {
    for (let index = 1; index < mark.points.length; index += 1) {
      context.lineWidth = mark.width * (mark.widths[index - 1] + mark.widths[index]) / 2;
      context.beginPath();
      context.moveTo(mark.points[index - 1].x, mark.points[index - 1].y);
      context.lineTo(mark.points[index].x, mark.points[index].y);
      context.stroke();
    }
  } else {
    context.lineWidth = mark.width;
    context.setLineDash(DASH[mark.dash] || []);
    context.beginPath();
    mark.points.forEach((point, index) => (index ? context.lineTo(point.x, point.y) : context.moveTo(point.x, point.y)));
    context.stroke();
  }
  context.restore();
}
// Ink already in the model, outlined where it falls on the screen: what the eraser is about to take
// (`erasing`), or what Select has picked, moved by the drag in progress.
function paintOutline(context, ink, { delta = { x: 0, y: 0 }, color, dash = [] }) {
  context.save();
  context.strokeStyle = color;
  context.lineWidth = Math.max(2, ink.width + 4);
  context.globalAlpha = 0.45;
  context.lineCap = "round";
  context.lineJoin = "round";
  context.setLineDash(dash);
  context.beginPath();
  const path = ink.closed || ink.kind !== "stroke" ? [...ink.points, ink.points[0]] : ink.points;
  path.forEach((point, index) => (index ? context.lineTo(point.x + delta.x, point.y + delta.y) : context.moveTo(point.x + delta.x, point.y + delta.y)));
  context.stroke();
  context.restore();
}

/**
 * Draw's surface over the viewport: every tool's gesture, its ink previewed as it is made (on a
 * canvas of its own, in screen space) and put into the model as it ends (`ink3d.js`): the pen, the
 * line, the arrow, the rectangle and the ellipse drawn; text written in place; a fill pressed inside
 * ink; the eraser dragged over ink; Select picking ink (Shift adds), dragging it (it lands on what is
 * under it where it is dropped, or in space if it was drawn there), Delete deleting it. Each mark lies
 * on the model or in space, as the session's placement was when it was put down. All of it works on the whole drawing, whatever
 * view each piece was drawn from. The camera is the person's between strokes: the wheel zooms, and
 * the Move view tool hands the pointer to the view. ⌘Z / ⇧⌘Z undo and redo while the surface has the keyboard.
 * Each mark names the view it was put down in (`viewOf`, the session's `noteView`), and a mark moved
 * names the view it was moved in: a prompt carries every view its ink was drawn in.
 *
 * @param {{ drawing: import("../../../../drawing/session.js").DrawingSession, runtimeRef: { current: any } }} props
 */
export default function InkOverlay({ drawing, runtimeRef, viewOf = null }) {
  const { platform } = useViewerHost().environment;
  const root = useRef(null);
  const canvas = useRef(null);
  const gesture = useRef(null);
  // Marks just put into the model, still previewed until the scene has drawn them.
  const landing = useRef(null);
  const [text, setText] = useState(null);
  const textRef = useRef(text);
  textRef.current = text;
  const drawingRef = useRef(drawing);
  drawingRef.current = drawing;
  const style = { color: drawing.color, width: drawing.strokeWidth, dash: drawing.strokeStyle };
  const styleRef = useRef(style);
  styleRef.current = style;

  const size = () => ({ width: root.current?.clientWidth || 0, height: root.current?.clientHeight || 0 });
  const point = event => {
    const rect = root.current.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  };
  const onScreen = () => inkOnScreen(runtimeRef.current, drawingRef.current.ink, size());
  const viewOfRef = useRef(viewOf);
  viewOfRef.current = viewOf;
  // The view ink is going down in, as the session labels it.
  const viewNow = () => drawingRef.current.noteView?.(viewOfRef.current?.() ?? null);
  // Each mark put down lies where the session's placement says: on the model, or in space.
  const put = marks => {
    const runtime = runtimeRef.current, session = drawingRef.current;
    if (!runtime || !marks.length) return;
    const placement = session.placement ?? "surface", view = viewNow();
    session.add(projectInk(runtime, marks.map(mark => ({ ...mark, placement, ...(view ? { view } : {}) })), size()));
  };
  // A mark drawn stays on the preview until the frame after the one that draws it in the model,
  // so it never blinks out between the two.
  const land = marks => {
    put(marks);
    landing.current = marks;
    globalThis.requestAnimationFrame?.(() => globalThis.requestAnimationFrame?.(() => {
      if (landing.current !== marks) return;
      landing.current = null;
      paint();
    }));
  };

  // The preview: the gesture in progress, and the selection's outline where it is now.
  const paint = useCallback(() => {
    const element = canvas.current, host = root.current, runtime = runtimeRef.current;
    if (!element || !host) return;
    const ratio = globalThis.devicePixelRatio || 1;
    const width = host.clientWidth, height = host.clientHeight;
    if (element.width !== Math.round(width * ratio) || element.height !== Math.round(height * ratio)) {
      element.width = Math.round(width * ratio);
      element.height = Math.round(height * ratio);
    }
    const context = element.getContext("2d");
    if (!context) return;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    const current = gesture.current, session = drawingRef.current;
    if (runtime && session.selected.length) {
      const picked = new Set(session.selected);
      const delta = current?.kind === "move" ? current.delta : undefined;
      for (const ink of inkOnScreen(runtime, session.ink.filter(item => picked.has(item.id)), { width, height })) {
        paintOutline(context, ink, { delta, color: SELECTION_COLOR, dash: [6, 4] });
      }
    }
    for (const mark of landing.current || []) paintMark(context, mark);
    if (!current) return;
    if (current.kind === "pen") {
      const mark = penMark(current.points, styleRef.current, current.pressures);
      if (mark) paintMark(context, mark);
    } else if (current.kind === "shape") {
      for (const mark of shapeMarks(current.tool, current.from, current.to, styleRef.current)) paintMark(context, mark);
    } else if (current.kind === "erase") {
      const erasing = new Set(withGroups([...current.hits], session.ink));
      for (const ink of current.screen.filter(item => erasing.has(item.id))) paintOutline(context, ink, { color: "#ffffff" });
      const last = current.path[current.path.length - 1];
      context.save();
      context.strokeStyle = "rgba(255,255,255,0.8)";
      context.lineWidth = 1;
      context.beginPath();
      context.arc(last.x, last.y, ERASER_RADIUS, 0, Math.PI * 2);
      context.stroke();
      context.restore();
    }
  }, [runtimeRef]);
  useLayoutEffect(() => { paint(); }, [paint, drawing.ink, drawing.selected]);
  // The camera moves (the wheel, Move view): the selection's outline goes with what it outlines.
  useEffect(() => {
    const controls = runtimeRef.current?.controls;
    if (!controls?.addEventListener) return undefined;
    controls.addEventListener("change", paint);
    return () => controls.removeEventListener("change", paint);
  }, [paint, runtimeRef]);

  // The text being written closes: kept (put down) or dropped (Escape). Closed by a key or a press on
  // the surface, the keyboard goes back to the surface, for ⌘Z; closed by focus going elsewhere, it
  // stays there.
  const closeText = (keep, { refocus = true } = {}) => {
    const current = textRef.current;
    textRef.current = null;
    setText(null);
    if (keep) commitText(current);
    if (refocus) root.current?.focus({ preventScroll: true });
  };
  // Written text, measured as it is drawn, put down as a text mark where it was written.
  const commitText = current => {
    if (!current?.value.trim()) return;
    const { fontPx } = current;
    const lines = current.value.trim().split("\n");
    const measure = globalThis.document?.createElement("canvas")?.getContext("2d");
    if (measure) measure.font = fontOf(fontPx);
    const width = Math.max(...lines.map(line => measure?.measureText(line).width ?? line.length * fontPx * 0.55));
    const mark = textMark(current.value, current.at, { width, height: lines.length * fontPx * 1.25 }, { color: current.color, fontPx });
    if (mark) put([mark]);
  };

  const onPointerDown = event => {
    if (event.button !== 0) return;
    const session = drawingRef.current, at = point(event);
    if (textRef.current) { closeText(true); return; }
    root.current.focus({ preventScroll: true });
    capturePointer(event.currentTarget, event.pointerId);
    const pressure = event.pointerType === "pen" ? event.pressure : null;
    if (session.tool === "freedraw") gesture.current = { kind: "pen", points: [at], pressures: pressure == null ? null : [pressure] };
    else if (SHAPES.has(session.tool)) gesture.current = { kind: "shape", tool: session.tool, from: at, to: at };
    else if (session.tool === "eraser") {
      const screen = onScreen();
      gesture.current = { kind: "erase", screen, path: [at], hits: new Set(inkAt(at, screen, ERASER_RADIUS).map(ink => ink.id)) };
    } else if (session.tool === "selection") {
      const [hit] = inkAt(at, onScreen());
      if (!hit) { session.select([]); gesture.current = null; }
      else {
        const picked = event.shiftKey
          ? (session.selected.includes(hit.id) ? session.selected.filter(id => id !== hit.id) : [...session.selected, hit.id])
          : (session.selected.includes(hit.id) ? session.selected : [hit.id]);
        const grouped = withGroups(picked, session.ink);
        session.select(grouped);
        gesture.current = grouped.includes(hit.id) ? { kind: "move", from: at, delta: { x: 0, y: 0 }, ids: grouped } : null;
      }
    } else if (session.tool === "fill") {
      const mark = fillMarkAt(at, onScreen(), session.color);
      if (mark) put([mark]);
    } else if (session.tool === "text") {
      setText({ at, value: "", color: session.color, fontPx: FONT_PX[session.strokeWidth] ?? 20 });
    }
    paint();
  };
  const onPointerMove = event => {
    const current = gesture.current;
    if (!current) return;
    const at = point(event);
    if (current.kind === "pen") {
      current.points.push(at);
      if (current.pressures) current.pressures.push(event.pressure);
    } else if (current.kind === "shape") current.to = at;
    else if (current.kind === "erase") {
      current.path.push(at);
      for (const ink of inkAt(at, current.screen, ERASER_RADIUS)) current.hits.add(ink.id);
    } else if (current.kind === "move") current.delta = { x: at.x - current.from.x, y: at.y - current.from.y };
    paint();
  };
  const onPointerUp = () => {
    const current = gesture.current, session = drawingRef.current, runtime = runtimeRef.current;
    gesture.current = null;
    if (current?.kind === "pen") { const mark = penMark(current.points, styleRef.current, current.pressures); if (mark) land([mark]); }
    else if (current?.kind === "shape") land(shapeMarks(current.tool, current.from, current.to, styleRef.current));
    else if (current?.kind === "erase") session.remove(withGroups([...current.hits], session.ink));
    else if (current?.kind === "move" && runtime && Math.hypot(current.delta.x, current.delta.y) > 1) {
      const ids = new Set(current.ids);
      const view = viewNow();
      session.replace(moveInk(runtime, session.ink.filter(item => ids.has(item.id)), current.delta, size())
        .map(item => (view ? { ...item, view } : item)));
    }
    paint();
  };
  // The surface takes the keyboard itself, on the press: the mousedown after it would focus the
  // surface again and blur the text just opened there, closing it before a key is typed.
  const onMouseDown = event => { if (event.target === event.currentTarget) event.preventDefault(); };
  const onKeyDown = event => {
    const session = drawingRef.current;
    const mod = platform === "darwin" ? event.metaKey : event.ctrlKey;
    if (mod && event.key.toLowerCase() === "z") { event.preventDefault(); (event.shiftKey ? session.redo : session.undo)(); }
    else if ((event.key === "Delete" || event.key === "Backspace") && session.selected.length) { event.preventDefault(); session.remove(session.selected); }
    else if (event.key === "Escape" && session.selected.length) { event.preventDefault(); session.select([]); }
  };
  // The wheel is the camera's, as it is outside Draw.
  const onWheel = event => {
    const target = runtimeRef.current?.renderer?.domElement;
    target?.dispatchEvent(new WheelEvent("wheel", { deltaX: event.deltaX, deltaY: event.deltaY, deltaZ: event.deltaZ, deltaMode: event.deltaMode,
      clientX: event.clientX, clientY: event.clientY, ctrlKey: event.ctrlKey, shiftKey: event.shiftKey, metaKey: event.metaKey, bubbles: true, cancelable: true }));
  };

  const moving = drawing.tool === "hand";
  return <div ref={root} tabIndex={-1} data-cad-drawing-overlay="" data-ink-tool={drawing.tool}
    className="absolute inset-0 z-10 outline-none"
    style={{ pointerEvents: moving ? "none" : "auto", cursor: drawing.tool === "selection" ? "default" : drawing.tool === "text" ? "text" : "crosshair", touchAction: "none" }}
    onPointerDown={onPointerDown} onPointerMove={onPointerMove} onPointerUp={onPointerUp} onPointerCancel={onPointerUp}
    onMouseDown={onMouseDown} onKeyDown={onKeyDown} onWheel={onWheel}>
    <canvas ref={canvas} className="pointer-events-none absolute inset-0 h-full w-full" />
    {text ? <textarea autoFocus aria-label="Text" value={text.value} rows={1}
      onChange={event => setText(current => current && { ...current, value: event.target.value })}
      onKeyDown={event => {
        event.stopPropagation();
        if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); closeText(true); }
        else if (event.key === "Escape") { event.preventDefault(); closeText(false); }
      }}
      onBlur={() => closeText(true, { refocus: false })} onPointerDown={event => event.stopPropagation()}
      className="absolute resize-none overflow-hidden border-0 bg-transparent p-0 outline-none [field-sizing:content]"
      style={{ left: text.at.x, top: text.at.y, color: text.color, font: fontOf(text.fontPx), lineHeight: 1.25, minWidth: "1ch" }} /> : null}
  </div>;
}
