// The display modes' icons, one family: the same sphere, drawn as each mode draws a model — shaded
// (Solid), see-through with its hidden lines (X-ray), its visible lines alone (Hidden line), every
// line (Wireframe). Render is no display mode: it is the Studio's (`RendererShell.jsx`).
const SPHERE = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: "1.7", strokeLinecap: "round", strokeLinejoin: "round" };
// Solid's shading, a hard-edged unlit side: the outline's right half, back up a terminator that
// bulges a little to the right — a drawing's shading.
const Shade = ({ cy = 12, r = 9 }) => <path d={`M12 ${cy - r}a${r} ${r} 0 0 1 0 ${2 * r}a${r * 0.15} ${r} 0 0 0 0-${2 * r}Z`}
  fill="currentColor" fillOpacity="0.45" stroke="none" />;

export function SolidModeIcon(props) {
  return <svg {...SPHERE} {...props}><Shade /><circle cx="12" cy="12" r="9" /></svg>;
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
  Object.freeze({ value: "xray", label: "X-ray", title: "Transparent surfaces with visible and hidden CAD edges", Icon: XRayModeIcon }),
  Object.freeze({ value: "hidden-line", label: "Hidden line", title: "Visible contours with obscured edges removed", Icon: HiddenLineModeIcon }),
  Object.freeze({ value: "wireframe", label: "Wireframe", title: "All CAD edges through the model", Icon: WireframeModeIcon })
]);
