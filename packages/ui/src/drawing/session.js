import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { sameView } from './views.js';

/**
 * The drawing: one sketch, its ink in the model (`renderers/kit/tools/draw/ink3d.js`'s items), what
 * Draw's tools put down next (`tool`, `color`, `strokeWidth`, `strokeStyle`, `placement`) and its history. It
 * outlives Draw being put down — the ink stays in the model, and the next time Draw is taken up it
 * draws on it — and goes when the person clears it or the model it was drawn on changes (`discard`).
 *
 * @typedef {object} DrawingSession
 * @property {boolean} ready  Always: the toolbar's tools are the session's own.
 * @property {import('./toolbar.jsx').DrawingTool} tool
 * @property {string} color
 * @property {number} strokeWidth
 * @property {import('./toolbar.jsx').DrawingStrokeStyle} strokeStyle
 * @property {import('./toolbar.jsx').DrawingPlacement} placement  Where what is drawn next lies: on
 *   the model, or in space on a plane facing the view.
 * @property {readonly import('../renderers/kit/tools/draw/ink3d.js').InkItem[]} ink
 * @property {readonly string[]} selected  The ink the viewer's Select has picked: what a colour, a weight, a line
 *   style and Delete act on. Draw's tools never pick ink.
 * @property {boolean} canUndo
 * @property {boolean} canRedo
 * @property {boolean} hasContent  There is ink.
 * @property {boolean} hasDrawing  The same: there is a drawing.
 * @property {(tool: import('./toolbar.jsx').DrawingTool) => void} selectTool
 * @property {(color: string) => void} selectColor  What is drawn next, and the ink selected.
 * @property {(width: number) => void} selectStrokeWidth  What is drawn next, and the strokes selected.
 * @property {(style: import('./toolbar.jsx').DrawingStrokeStyle) => void} selectStrokeStyle  What is drawn next, and the strokes selected.
 * @property {(placement: import('./toolbar.jsx').DrawingPlacement) => void} selectPlacement  What is drawn next only: ink already down stays where it lies.
 * @property {(items: readonly import('../renderers/kit/tools/draw/ink3d.js').InkItem[]) => void} add  Ink put down: one step.
 * @property {(ids: readonly string[]) => void} remove  Ink erased or deleted: one step.
 * @property {(items: readonly import('../renderers/kit/tools/draw/ink3d.js').InkItem[]) => void} replace  Ink changed in place (moved, recoloured): one step.
 * @property {(ids: readonly string[]) => void} select
 * @property {() => void} undo
 * @property {() => void} redo
 * @property {() => void} clear  Every piece of ink, as one step to undo.
 * @property {() => void} forget  The same as `clear`.
 * @property {() => void} discard  Every piece of ink and the history with it: nothing to undo.
 * @property {readonly import('./views.js').LabelledView[]} views  The views ink was drawn in, in order
 *   ("view 1", "view 2"…): a mark names its own (`view`).
 * @property {(view: import('./views.js').PromptView | null) => string | undefined} noteView  The label of
 *   the view ink is being put down in: the one drawn in before that is the same (`sameView`), or a new one.
 */

const DEFAULTS = Object.freeze({ tool: 'freedraw', color: '#ff2d55', strokeWidth: 2, strokeStyle: 'solid', placement: 'surface' });
const LIMIT = 200;

/**
 * @param {boolean} [active]  Draw is the tool. Put down, it goes back to its first tool (the pen);
 *   the ink, its selection, the colour, the weight, the line style and the placement stay.
 * @param {{ tool?: string, color?: string, strokeWidth?: number, strokeStyle?: import('./toolbar.jsx').DrawingStrokeStyle,
 *   placement?: import('./toolbar.jsx').DrawingPlacement }} [initial]
 * @returns {DrawingSession}
 */
export function useDrawingSession(active = true, initial = {}) {
  const start = useMemo(() => ({ ...DEFAULTS, ...Object.fromEntries(Object.entries(initial).filter(([, value]) => value != null)) }),
    [initial.tool, initial.color, initial.strokeWidth, initial.strokeStyle, initial.placement]);
  const [tool, setTool] = useState(start.tool);
  const [color, setColor] = useState(start.color);
  const [strokeWidth, setStrokeWidth] = useState(start.strokeWidth);
  const [strokeStyle, setStrokeStyle] = useState(start.strokeStyle);
  const [placement, setPlacement] = useState(start.placement);
  // The ink, and the inks it was before and after each step: undo and redo move between them.
  const [history, setHistory] = useState(() => ({ past: [], ink: [], future: [] }));
  const [selected, setSelected] = useState(/** @type {string[]} */ ([]));
  // The views ink was drawn in. Not a step to undo: a label a mark names stays, whatever is undone.
  const viewsRef = useRef(/** @type {import('./views.js').LabelledView[]} */ ([]));
  const [views, setViews] = useState(viewsRef.current);
  const noteView = useCallback(view => {
    if (!view) return undefined;
    const known = viewsRef.current.findLast(drawn => sameView(drawn, view));
    if (known) return known.label;
    const label = `view ${viewsRef.current.length + 1}`;
    viewsRef.current = [...viewsRef.current, { ...view, label }];
    setViews(viewsRef.current);
    return label;
  }, []);
  useEffect(() => {
    if (!active) setTool(start.tool);
  }, [active, start.tool]);
  const step = useCallback(change => setHistory(current => {
    const ink = change(current.ink);
    return ink === current.ink ? current : { past: [...current.past, current.ink].slice(-LIMIT), ink, future: [] };
  }), []);
  const add = useCallback(items => { if (items.length) step(ink => [...ink, ...items]); }, [step]);
  const remove = useCallback(ids => {
    const gone = new Set(ids);
    if (!gone.size) return;
    step(ink => (ink.some(item => gone.has(item.id)) ? ink.filter(item => !gone.has(item.id)) : ink));
    setSelected(current => current.filter(id => !gone.has(id)));
  }, [step]);
  const replace = useCallback(items => {
    const next = new Map(items.map(item => [item.id, item]));
    if (next.size) step(ink => ink.map(item => next.get(item.id) ?? item));
  }, [step]);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const widthRef = useRef(strokeWidth);
  widthRef.current = strokeWidth;
  // A style chosen with ink selected restyles it, as one step.
  const restyle = useCallback(change => {
    const ids = new Set(selectedRef.current);
    if (ids.size) step(ink => ink.map(item => (ids.has(item.id) ? change(item) : item)));
  }, [step]);
  const actions = useMemo(() => ({
    selectTool: next => setTool(next),
    selectColor: next => { setColor(next); restyle(item => ({ ...item, color: next })); },
    selectStrokeWidth: next => {
      const from = widthRef.current;
      setStrokeWidth(next);
      restyle(item => (item.kind === 'stroke' && item.width ? { ...item, width: (item.width / from) * next } : item));
    },
    selectStrokeStyle: next => { setStrokeStyle(next); restyle(item => (item.kind === 'stroke' && !item.widths ? { ...item, dash: next } : item)); },
    // Not a style: ink already down, selected or not, stays where it lies.
    selectPlacement: next => setPlacement(next),
    select: ids => setSelected([...ids]),
    undo: () => setHistory(current => (current.past.length
      ? { past: current.past.slice(0, -1), ink: current.past[current.past.length - 1], future: [current.ink, ...current.future] } : current)),
    redo: () => setHistory(current => (current.future.length
      ? { past: [...current.past, current.ink], ink: current.future[0], future: current.future.slice(1) } : current)),
    clear: () => { step(ink => (ink.length ? [] : ink)); setSelected([]); },
    discard: () => { setHistory({ past: [], ink: [], future: [] }); setSelected([]); viewsRef.current = []; setViews([]); },
  }), [restyle, step]);
  const hasContent = history.ink.length > 0;
  return { ready: true, tool, color, strokeWidth, strokeStyle, placement, ink: history.ink, selected, views,
    canUndo: history.past.length > 0, canRedo: history.future.length > 0, hasContent, hasDrawing: hasContent,
    add, remove, replace, noteView, forget: actions.clear, ...actions };
}
