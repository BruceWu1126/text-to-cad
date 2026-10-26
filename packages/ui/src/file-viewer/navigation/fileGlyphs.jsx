import { createElement } from "react";

import { cn } from "@text-to-cad/ui/utils";

/**
 * The viewer's own file glyphs, one per kind of CAD file: lucide's file outline, on its 24-unit
 * grid and in its stroke (1.75 unless the list asks for a lighter line), with what the file holds
 * drawn small inside it, so a file reads as a file at a glance and its kind at a second look. They
 * sit beside lucide's icons (`icons.jsx`, every other file; a model is lucide's `Box`) and take the
 * same props. lucide's own `File*` family has no mesh, drawing or joint, hence these.
 */

// lucide's `file`: the sheet and its folded corner.
const SHEET = "M6 22a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h8a2.4 2.4 0 0 1 1.704.706l3.588 3.588A2.4 2.4 0 0 1 20 8v12a2 2 0 0 1-2 2z";
const FOLD = "M14 2v5a1 1 0 0 0 1 1h5";

/** What each kind holds, as paths in the sheet's lower part (x 7 to 17, y 10.5 to 19.5). */
export const FILE_GLYPH_PATHS = Object.freeze({
  // A solid: a small cube, STEP's and xbf's B-rep.
  solid: ["M12 10.5l4 2.25v4.5L12 19.5l-4-2.25v-4.5z", "M8 12.75l4 2.25 4-2.25", "M12 15v4.5"],
  // A mesh: a triangle cut into four, STL's and 3MF's facets.
  mesh: ["M12 10.5l4.5 8h-9z", "M9.75 14.5h4.5L12 18.5z"],
  // A scene: its three axes, a GLB's.
  scene: ["M8 10.5v8h8", "M8 18.5l4.5-4.5"],
  // A drawing: one flat polyline, a DXF's.
  drawing: ["M7 17.5l3-4.5 3.5 3 3.5-5"],
  // A mechanism: two joints and the link between them, a URDF's, SRDF's or SDF's.
  mechanism: ["M10.7 15.4l2.6-1.8", "M11 16.5a2 2 0 1 1-4 0 2 2 0 0 1 4 0z", "M17 12.5a2 2 0 1 1-4 0 2 2 0 0 1 4 0z"],
});

function glyph(kind) {
  const Glyph = ({ className, strokeWidth = 1.75, ...props }) => createElement("svg", {
    xmlns: "http://www.w3.org/2000/svg", width: 24, height: 24, viewBox: "0 0 24 24", fill: "none",
    stroke: "currentColor", strokeWidth, strokeLinecap: "round", strokeLinejoin: "round",
    "aria-hidden": "true", ...props, className: cn("lucide cad-file-glyph", className), "data-file-glyph": kind,
  }, [SHEET, FOLD, ...FILE_GLYPH_PATHS[kind]].map(d => createElement("path", { key: d, d })));
  Glyph.displayName = `File${kind[0].toUpperCase()}${kind.slice(1)}Glyph`;
  return Glyph;
}

export const FileSolidGlyph = glyph("solid");
export const FileMeshGlyph = glyph("mesh");
export const FileSceneGlyph = glyph("scene");
export const FileDrawingGlyph = glyph("drawing");
export const FileMechanismGlyph = glyph("mechanism");
