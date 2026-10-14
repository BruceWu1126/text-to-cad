import { Axis3d, Box, Circle, Eraser, Hand, Minus, PaintBucket, SquareMousePointer, MoveUpRight, Pencil, Redo2, Square, Trash2, Type, Undo2 } from 'lucide-react';
import { useState } from 'react';
import { ToolbarButton } from '@text-to-cad/ui/primitives/toolbar-button';

/**
 * Draw's controls: its tools, the colour, weight and line style of what is drawn next (and of the
 * ink Select has picked), where it is drawn (on the model or in space), undo, redo and clear — all
 * the drawing session's (`session.js`).
 */

/**
 * The drawing editor's tools a host offers. Its lock, image, frame, embed and laser tools are left out.
 * @typedef {'selection' | 'hand' | 'freedraw' | 'line' | 'arrow' | 'rectangle' | 'ellipse' | 'text' | 'fill' | 'eraser'} DrawingTool
 */
// What puts ink down, the pen first: it is what Draw opens on, and the icon of the Draw tool itself.
/** @type {readonly { id: DrawingTool, label: string, Icon: typeof Hand }[]} */
const INK_TOOLS = [
  { id: 'freedraw', label: 'Pen', Icon: Pencil },
  { id: 'line', label: 'Line', Icon: Minus },
  { id: 'arrow', label: 'Arrow', Icon: MoveUpRight },
  { id: 'rectangle', label: 'Rectangle', Icon: Square },
  { id: 'ellipse', label: 'Ellipse', Icon: Circle },
  { id: 'text', label: 'Text', Icon: Type },
  { id: 'fill', label: 'Fill area', Icon: PaintBucket },
  { id: 'eraser', label: 'Eraser', Icon: Eraser },
];
// Selecting and panning come after the ink tools: the tools that draw lead.
/** @type {readonly { id: DrawingTool, label: string, Icon: typeof Hand }[]} */
const HANDLING_TOOLS = [
  { id: 'selection', label: 'Select and move drawings', Icon: SquareMousePointer },
  { id: 'hand', label: 'Move view', Icon: Hand },
];
/** @type {readonly { id: DrawingTool, label: string, Icon: typeof Hand }[]} */
export const DRAWING_TOOLBAR_TOOLS = [...INK_TOOLS, ...HANDLING_TOOLS];
/** @type {readonly DrawingTool[]} */
export const DRAWING_TOOLS = DRAWING_TOOLBAR_TOOLS.map(tool => tool.id);

// Saturated enough to stand out from a shaded model in either theme; black and
// white for the cases a neon cannot cover (a white page, a dark render).
/** @type {readonly { value: string, label: string }[]} */
export const DRAWING_COLORS = [
  { value: '#ff2d55', label: 'Neon red' },
  { value: '#ff7a00', label: 'Neon orange' },
  { value: '#ffe600', label: 'Neon yellow' },
  { value: '#39ff14', label: 'Neon green' },
  { value: '#00e5ff', label: 'Neon cyan' },
  { value: '#2979ff', label: 'Electric blue' },
  { value: '#d500f9', label: 'Neon magenta' },
  { value: '#ffffff', label: 'White' },
  { value: '#111111', label: 'Black' },
];
export const DEFAULT_OVERLAY_DRAWING_COLOR = DRAWING_COLORS[0].value;

// A shape's stroke in px; the pen draws a little heavier at the same weight (`inkGeometry.js`).
/** @type {readonly { value: number, label: string }[]} */
export const DRAWING_STROKE_WIDTHS = [
  { value: 1, label: 'Thin' },
  { value: 2, label: 'Medium' },
  { value: 4, label: 'Bold' },
];
/** @typedef {'solid' | 'dashed' | 'dotted'} DrawingStrokeStyle */
// How a shape's line is stroked; the pen's freehand ink is always solid.
/** @type {readonly { value: DrawingStrokeStyle, label: string }[]} */
export const DRAWING_STROKE_STYLES = [
  { value: 'solid', label: 'Solid' },
  { value: 'dashed', label: 'Dashed' },
  { value: 'dotted', label: 'Dotted' },
];
/** @typedef {'surface' | 'space'} DrawingPlacement */
// Where ink lies: on the model under it (carried with its part), or in space, on a plane facing the
// view through the orbit point, on no part. Only what is drawn next: ink already down stays put.
/** @type {readonly { value: DrawingPlacement, label: string, Icon: typeof Hand }[]} */
export const DRAWING_PLACEMENTS = [
  { value: 'surface', label: 'On model', Icon: Box },
  { value: 'space', label: 'In space', Icon: Axis3d },
];
const placementGlyph = placement => {
  const { Icon } = DRAWING_PLACEMENTS.find(option => option.value === placement) ?? DRAWING_PLACEMENTS[0];
  return <Icon className="size-3" strokeWidth={2} aria-hidden="true" />;
};
// The style as a 2px line across a button, on the diagonal, dashed or dotted as it draws.
const STYLE_DASHES = Object.freeze({ solid: undefined, dashed: '4 2.5', dotted: '0.01 3.5' });
const styleGlyph = style => <svg aria-hidden="true" viewBox="0 0 12 12" className="size-3" fill="none" stroke="currentColor"
  strokeWidth="2" strokeLinecap="round"><path d="M2 10L10 2" strokeDasharray={STYLE_DASHES[style]} /></svg>;
// The weight as a dot, 3px, 5px or 7px across, whatever it draws on the model: a dot, so it never
// reads as a line style.
const strokeGlyph = width => {
  const size = width === 1 ? 3 : width === 2 ? 5 : 7;
  return <span aria-hidden="true" className="block rounded-full bg-current" style={{ width: `${size}px`, height: `${size}px` }} />;
};

// Two rows of seven by default, the width of the block fixed by that count; a
// container narrower than it wraps the same buttons into more rows.
const SURFACE = 'pointer-events-auto flex w-[calc(7*1.5rem+6*0.125rem+0.5rem+2px)] max-w-full flex-wrap gap-0.5 rounded-md border border-border bg-background p-1 text-foreground shadow-sm';
const swatch = color => ({ backgroundColor: color, boxShadow: 'inset 0 0 0 1px color-mix(in oklab, currentColor 35%, transparent)' });

/**
 * `layout="panel"`: the controls of a panel as wide as its column — the tools, wrapping to the
 * width they are given, then a rule, then the settings on a row of their own: colour, stroke width,
 * line style, placement, undo, redo and clear. Both rows share one column grid, so their buttons line up.
 * @param {{ drawing: import('./session.js').DrawingSession, className?: string, layout?: 'toolbar' | 'panel' }} props
 */
export function DrawingToolbar({ drawing, className = '', layout = 'toolbar' }) {
  // Which of the pickers is open under the buttons, if any: the colours, the weights, the line styles or the placements.
  const [choosing, setChoosing] = useState(/** @type {'' | 'color' | 'width' | 'style' | 'placement'} */ (''));
  const choosingColor = choosing === 'color';
  const disabled = !drawing.ready;
  // No tooltips: over a canvas they cover the ink being pointed at. Every button keeps its accessible name.
  const toolButton = ({ id, label, Icon }) => <ToolbarButton key={id} tooltip={false} label={label} disabled={disabled}
    active={!disabled && drawing.tool === id} aria-pressed={!disabled && drawing.tool === id} onClick={() => drawing.selectTool(id)}>
    <Icon className="size-3" strokeWidth={2} aria-hidden="true" />
  </ToolbarButton>;
  const panel = layout === 'panel';
  const settings = <>
    <ToolbarButton tooltip={false} label="Color" disabled={disabled} active={choosingColor} aria-expanded={choosingColor} onClick={() => setChoosing(open => open === 'color' ? '' : 'color')}>
      <span className="size-3 rounded-full" style={swatch(drawing.color)} aria-hidden="true" />
    </ToolbarButton>
    <ToolbarButton tooltip={false} label="Stroke width" disabled={disabled} active={choosing === 'width'} aria-expanded={choosing === 'width'} onClick={() => setChoosing(open => open === 'width' ? '' : 'width')}>
      {strokeGlyph(drawing.strokeWidth ?? 2)}
    </ToolbarButton>
    <ToolbarButton tooltip={false} label="Line style" disabled={disabled} active={choosing === 'style'} aria-expanded={choosing === 'style'} onClick={() => setChoosing(open => open === 'style' ? '' : 'style')}>
      {styleGlyph(drawing.strokeStyle ?? 'solid')}
    </ToolbarButton>
    <ToolbarButton tooltip={false} label="Placement" disabled={disabled} active={choosing === 'placement'} aria-expanded={choosing === 'placement'} onClick={() => setChoosing(open => open === 'placement' ? '' : 'placement')}>
      {placementGlyph(drawing.placement ?? 'surface')}
    </ToolbarButton>
    <ToolbarButton tooltip={false} label="Undo" disabled={disabled || !drawing.canUndo} onClick={() => drawing.undo()}><Undo2 className="size-3" strokeWidth={2} aria-hidden="true" /></ToolbarButton>
    <ToolbarButton tooltip={false} label="Redo" disabled={disabled || !drawing.canRedo} onClick={() => drawing.redo()}><Redo2 className="size-3" strokeWidth={2} aria-hidden="true" /></ToolbarButton>
    <ToolbarButton tooltip={false} label="Clear drawing" disabled={disabled || !drawing.hasContent} onClick={() => drawing.clear()}><Trash2 className="size-3" strokeWidth={2} aria-hidden="true" /></ToolbarButton>
  </>;
  const palette = choosingColor && !disabled ? <div role="radiogroup" aria-label="Drawing color"
    className={panel ? "mt-1 flex flex-wrap gap-0.5" : SURFACE}>
    {DRAWING_COLORS.map(({ value, label }) => <ToolbarButton key={value} tooltip={false} label={label} role="radio"
      aria-checked={drawing.color.toLowerCase() === value} active={drawing.color.toLowerCase() === value}
      onClick={() => { drawing.selectColor(value); setChoosing(''); }}>
      <span className="size-3.5 rounded-full" style={swatch(value)} aria-hidden="true" />
    </ToolbarButton>)}
  </div> : choosing === 'width' && !disabled ? <div role="radiogroup" aria-label="Stroke width"
    className={panel ? "mt-1 flex flex-wrap gap-0.5" : SURFACE}>
    {DRAWING_STROKE_WIDTHS.map(({ value, label }) => <ToolbarButton key={value} tooltip={false} label={label} role="radio"
      aria-checked={(drawing.strokeWidth ?? 2) === value} active={(drawing.strokeWidth ?? 2) === value}
      onClick={() => { drawing.selectStrokeWidth?.(value); setChoosing(''); }}>
      {strokeGlyph(value)}
    </ToolbarButton>)}
  </div> : choosing === 'style' && !disabled ? <div role="radiogroup" aria-label="Line style"
    className={panel ? "mt-1 flex flex-wrap gap-0.5" : SURFACE}>
    {DRAWING_STROKE_STYLES.map(({ value, label }) => <ToolbarButton key={value} tooltip={false} label={label} role="radio"
      aria-checked={(drawing.strokeStyle ?? 'solid') === value} active={(drawing.strokeStyle ?? 'solid') === value}
      onClick={() => { drawing.selectStrokeStyle?.(value); setChoosing(''); }}>
      {styleGlyph(value)}
    </ToolbarButton>)}
  </div> : choosing === 'placement' && !disabled ? <div role="radiogroup" aria-label="Placement"
    className={panel ? "mt-1 flex flex-wrap gap-0.5" : SURFACE}>
    {DRAWING_PLACEMENTS.map(({ value, label }) => <ToolbarButton key={value} tooltip={false} label={label} role="radio"
      aria-checked={(drawing.placement ?? 'surface') === value} active={(drawing.placement ?? 'surface') === value}
      onClick={() => { drawing.selectPlacement?.(value); setChoosing(''); }}>
      {placementGlyph(value)}
    </ToolbarButton>)}
  </div> : null;
  if (panel) {
    // A grid of 24px columns spread across the width, so however wide the panel is the buttons
    // wrap into even columns, the same columns in both rows.
    const grid = "grid min-w-0 grid-cols-[repeat(auto-fill,1.5rem)] justify-between gap-0.5";
    return <div className={`text-to-cad-drawing-toolbar ${className}`} data-drawing-controls="">
      <div role="group" aria-label="Drawing tools" className={grid}>{DRAWING_TOOLBAR_TOOLS.map(toolButton)}</div>
      {/* The rule runs the panel's full width, through its inset. */}
      <div role="separator" aria-orientation="horizontal" className="-mx-1 my-1 h-px bg-border" data-drawing-rule="" />
      <div role="group" aria-label="Drawing settings" className={grid}>{settings}</div>
      {palette}
    </div>;
  }
  return <div className={`text-to-cad-drawing-toolbar flex max-w-full flex-col items-end gap-1 ${className}`}>
    <div role="group" aria-label="Drawing tools" className={SURFACE}>
      {DRAWING_TOOLBAR_TOOLS.map(toolButton)}
      {settings}
    </div>
    {palette}
  </div>;
}
