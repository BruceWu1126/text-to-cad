import { Axis3d, Box, Circle, Eraser, MoveUpRight, Pencil, Redo2, Slash, Square, Trash2, Type, Undo2 } from 'lucide-react';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger
} from '@text-to-cad/ui/primitives/dropdown-menu';
import { ToolbarButton } from '@text-to-cad/ui/primitives/toolbar-button';
import { cn } from '@text-to-cad/ui/utils';
import { FLOATING_SURFACE_CLASS } from '../lib/floatingSurface.js';

/**
 * Draw's panel (`DrawingToolbar`): its tools — the pen, each shape, the eraser — its history and
 * Clear, and the colour, weight and line style of what is drawn next and where it is drawn (on
 * the model or in space), all the drawing session's (`session.js`). The same colour, weight and line
 * style dropdowns (`InkStylePickers`) restyle the ink Select has picked, on its selection's bar.
 */

/**
 * The drawing editor's tools a host offers. Its lock, image, frame, embed and laser tools are left out.
 * @typedef {'freedraw' | 'line' | 'arrow' | 'rectangle' | 'ellipse' | 'text' | 'eraser'} DrawingTool
 */
/** @typedef {{ id: DrawingTool, label: string, Icon: typeof Pencil }} DrawingToolEntry */
// The pen, what Draw opens on, and the eraser.
const PEN = { id: 'freedraw', label: 'Pen', Icon: Pencil };
const ERASER = { id: 'eraser', label: 'Eraser', Icon: Eraser };
// The shapes, the line first; text with them, a mark put down in place.
/** @type {readonly DrawingToolEntry[]} */
export const DRAWING_SHAPE_TOOLS = [
  { id: 'line', label: 'Line', Icon: Slash },
  { id: 'arrow', label: 'Arrow', Icon: MoveUpRight },
  { id: 'rectangle', label: 'Rectangle', Icon: Square },
  { id: 'ellipse', label: 'Ellipse', Icon: Circle },
  { id: 'text', label: 'Text', Icon: Type },
];
/** @type {readonly DrawingTool[]} */
export const DRAWING_TOOLS = [PEN, ...DRAWING_SHAPE_TOOLS, ERASER].map(tool => tool.id);

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
/** @type {readonly { value: DrawingPlacement, label: string, Icon: typeof Pencil }[]} */
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
const swatch = color => ({ backgroundColor: color, boxShadow: 'inset 0 0 0 1px color-mix(in oklab, currentColor 35%, transparent)' });
const ROW = 'flex min-w-0 flex-wrap items-center gap-0.5';

/**
 * One of a mark's styles as a dropdown: its button shows the value (no chevron), and opens
 * an ordinary menu of the choices, each its glyph and its name; one chosen closes it.
 */
function StyleMenu({ label, options, value, glyph, onChange, disabled }) {
  return <DropdownMenu modal={false}>
    <DropdownMenuTrigger asChild>
      <ToolbarButton tooltip={false} label={label} disabled={disabled}>{glyph(value)}</ToolbarButton>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="start" sideOffset={4} collisionPadding={8} aria-label={label}
      className={cn(FLOATING_SURFACE_CLASS, 'w-40 data-[state=closed]:animate-none!')}>
      <DropdownMenuRadioGroup value={String(value)} onValueChange={next => onChange(options.find(option => String(option.value) === next).value)}>
        {options.map(option => <DropdownMenuRadioItem key={option.value} value={String(option.value)} aria-label={option.label}>
          <span className="grid size-3.5 place-items-center">{glyph(option.value)}</span>{option.label}
        </DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
}
const colorGlyph = color => <span className="size-3 rounded-full" style={swatch(color)} aria-hidden="true" />;

/**
 * The colour, weight and line style dropdowns (`StyleMenu`) — the ink picked, on its selection's bar. Each is left out where it has nothing to set (`width`/`dash`
 * null: no stroke picked); `placement` only where Draw puts ink down. `children` follow them on
 * their row.
 *
 * @param {{ color: string, width?: number | null, dash?: DrawingStrokeStyle | null, placement?: DrawingPlacement | null,
 *   onColor(value: string): void, onWidth?(value: number): void, onDash?(value: DrawingStrokeStyle): void,
 *   onPlacement?(value: DrawingPlacement): void, disabled?: boolean, label: string, className?: string,
 *   children?: import('react').ReactNode }} props
 */
export function InkStylePickers({ color, width = null, dash = null, placement = null, onColor, onWidth, onDash, onPlacement,
  disabled = false, label, className = ROW, children = null }) {
  return <div role="group" aria-label={label} className={className}>
    <StyleMenu label="Color" options={DRAWING_COLORS} value={color.toLowerCase()} glyph={colorGlyph} onChange={onColor} disabled={disabled} />
    {width == null ? null : <StyleMenu label="Stroke width" options={DRAWING_STROKE_WIDTHS} value={width} glyph={strokeGlyph}
      onChange={value => onWidth?.(value)} disabled={disabled} />}
    {dash == null ? null : <StyleMenu label="Line style" options={DRAWING_STROKE_STYLES} value={dash} glyph={styleGlyph}
      onChange={value => onDash?.(value)} disabled={disabled} />}
    {placement == null ? null : <StyleMenu label="Placement" options={DRAWING_PLACEMENTS} value={placement} glyph={placementGlyph}
      onChange={value => onPlacement?.(value)} disabled={disabled} />}
    {children}
  </div>;
}

const icon = Icon => <Icon className="size-3" strokeWidth={2} aria-hidden="true" />;
// Draw's tools are laid on a grid of six 24px columns spread across the panel's width, so its two
// rows line up, column for column: six tools, then the eraser at the left and the history and
// Clear in the last three columns.
const GRID = 'grid min-w-0 grid-cols-[repeat(6,1.5rem)] justify-between gap-y-0.5';

/**
 * Draw's panel, under the strip while Draw is the tool: its tools, every one a button — the pen,
 * the line, the arrow, the rectangle, the ellipse, text, then the eraser — and at the end of their
 * rows Undo, Redo (the drawing's history, `history`) and Clear; then a rule, then what is drawn
 * next: its colour, weight, line style and placement dropdowns.
 * @param {{ drawing: import('./session.js').DrawingSession,
 *   history: { canUndo: boolean, canRedo: boolean, undo(): void, redo(): void } }} props
 */
export function DrawingToolbar({ drawing, history }) {
  const disabled = !drawing.ready;
  const toolButton = ({ id, label, Icon }) => <ToolbarButton key={id} tooltip={false} label={label} disabled={disabled}
    active={!disabled && drawing.tool === id} aria-pressed={!disabled && drawing.tool === id} onClick={() => drawing.selectTool(id)}>
    {icon(Icon)}
  </ToolbarButton>;
  return <div className="text-to-cad-drawing-toolbar p-1" data-drawing-controls="">
    <div role="group" aria-label="Drawing tools" className={GRID}>
      {[PEN, ...DRAWING_SHAPE_TOOLS, ERASER].map(toolButton)}
      <ToolbarButton tooltip={false} label="Undo" className="col-start-4" disabled={disabled || !history.canUndo} onClick={() => history.undo()}>{icon(Undo2)}</ToolbarButton>
      <ToolbarButton tooltip={false} label="Redo" disabled={disabled || !history.canRedo} onClick={() => history.redo()}>{icon(Redo2)}</ToolbarButton>
      <ToolbarButton tooltip={false} label="Clear drawing" disabled={disabled || !drawing.hasContent} onClick={() => drawing.clear()}>{icon(Trash2)}</ToolbarButton>
    </div>
    {/* The rule runs the panel's full width, through its inset. */}
    <div role="separator" aria-orientation="horizontal" className="-mx-1 my-1 h-px bg-border" data-drawing-rule="" />
    <InkStylePickers label="Drawing settings" disabled={disabled} color={drawing.color} width={drawing.strokeWidth ?? 2}
      dash={drawing.strokeStyle ?? 'solid'} placement={drawing.placement ?? 'surface'} onColor={drawing.selectColor}
      onWidth={drawing.selectStrokeWidth} onDash={drawing.selectStrokeStyle} onPlacement={drawing.selectPlacement} />
  </div>;
}
