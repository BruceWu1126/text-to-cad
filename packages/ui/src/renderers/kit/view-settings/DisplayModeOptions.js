import { useId } from "react";

// The display modes' icons, one family: the same sphere, drawn as each mode draws a model — shaded
// (Solid), shaded and lit, its highlights on top (Render), see-through with its hidden lines
// (X-ray), its visible lines alone (Hidden line), every line (Wireframe).
const SPHERE = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.7", strokeLinecap: "round", strokeLinejoin: "round" };
// Solid's shading, a hard-edged unlit side: the outline's right half, back up a terminator that
// bulges a little to the right — a drawing's shading.
const Shade = ({ cy = 12, r = 9 }) => <path d={`M12 ${cy - r}a${r} ${r} 0 0 1 0 ${2 * r}a${r * 0.15} ${r} 0 0 0 0-${2 * r}Z`}
  fill="currentColor" fillOpacity="0.45" stroke="none" />;

// Render's: lit from the upper left, fading into shadow toward the lower right — a radial blend
// over the whole sphere, a photograph's. Its gradient's id is the icon's own, so two on a page never
// share one.
function SoftShade({ cy = 12, r = 9 }) {
  const id = `sphere-shade-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`;
  return <>
    <defs>
      <radialGradient id={id} cx={12 - r * 0.45} cy={cy - r * 0.45} r={r * 1.65} gradientUnits="userSpaceOnUse">
        <stop offset="0" stopColor="currentColor" stopOpacity="0" />
        <stop offset="0.45" stopColor="currentColor" stopOpacity="0.12" />
        <stop offset="1" stopColor="currentColor" stopOpacity="0.75" />
      </radialGradient>
    </defs>
    <circle cx="12" cy={cy} r={r} fill={`url(#${id})`} stroke="none" />
  </>;
}

export function SolidModeIcon(props) {
  return <svg {...SPHERE} {...props}><Shade /><circle cx="12" cy="12" r="9" /></svg>;
}

export function RenderModeIcon(props) {
  return <svg {...SPHERE} {...props}>
    <SoftShade /><circle cx="12" cy="12" r="9" />
    <circle cx="8.5" cy="8" r="1.5" fill="currentColor" stroke="none" />
    <circle cx="11.5" cy="6.2" r="0.8" fill="currentColor" stroke="none" />
  </svg>;
}

export function XRayModeIcon(props) {
  return <svg {...SPHERE} {...props}>
    <circle cx="12" cy="12" r="9" fill="currentColor" fillOpacity="0.12" />
    <path d="M3 12a9 3.5 0 0 0 18 0" />
    <path d="M3 12a9 3.5 0 0 1 18 0" strokeDasharray="1.5 2" strokeOpacity="0.55" />
  </svg>;
}

export function HiddenLineModeIcon(props) {
  return <svg {...SPHERE} {...props}><circle cx="12" cy="12" r="9" /><path d="M3 12a9 3.5 0 0 0 18 0M12 3a3.5 9 0 0 0 0 18" /></svg>;
}

export function WireframeModeIcon(props) {
  return <svg {...SPHERE} {...props}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18M12 3a3.5 9 0 0 0 0 18M12 3a3.5 9 0 0 1 0 18M3 12a9 3.5 0 0 0 18 0" />
  </svg>;
}

export const DISPLAY_MODE_OPTIONS = Object.freeze([
  Object.freeze({ value: "solid", label: "Solid", title: "Solid surfaces with visible CAD edges", Icon: SolidModeIcon }),
  Object.freeze({ value: "render", label: "Render", title: "Photographic materials, lighting and backdrop", Icon: RenderModeIcon }),
  Object.freeze({ value: "xray", label: "X-ray", title: "Transparent surfaces with visible and hidden CAD edges", Icon: XRayModeIcon }),
  Object.freeze({ value: "hidden-line", label: "Hidden line", title: "Visible contours with obscured edges removed", Icon: HiddenLineModeIcon }),
  Object.freeze({ value: "wireframe", label: "Wireframe", title: "All CAD edges through the model", Icon: WireframeModeIcon })
]);
