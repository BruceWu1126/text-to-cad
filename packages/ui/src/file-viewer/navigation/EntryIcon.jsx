import { LoaderCircle } from "lucide-react";

import { cn } from "@text-to-cad/ui/utils";
import {
  ENTRY_ICON_KIND,
  entryIconKind
} from "./entryIconKind.js";
import { FileDrawingGlyph, FileMechanismGlyph, FileMeshGlyph, FileSceneGlyph, FileSolidGlyph } from "./fileGlyphs.jsx";

// One icon table for every surface that lists files — the explorer, the
// Home, the name menu and the navbar — so a file cannot read as one thing in
// one place and something else in another. Each is the viewer's own file glyph
// (`fileGlyphs.jsx`): lucide's file outline with what it holds drawn inside, so
// a file reads as a file at a glance (a model, built from code, is lucide's
// plain cube: `DocumentGlyph`).
const ENTRY_ICON_COMPONENTS = {
  [ENTRY_ICON_KIND.LOADING]: LoaderCircle,
  // STEP and xbf, the B-rep formats: a solid.
  [ENTRY_ICON_KIND.STEP]: FileSolidGlyph,
  // STL and 3MF: facets.
  [ENTRY_ICON_KIND.STL_MESH]: FileMeshGlyph,
  [ENTRY_ICON_KIND.THREE_MF_MESH]: FileMeshGlyph,
  // A GLB is a scene: its axes.
  [ENTRY_ICON_KIND.GLB_MESH]: FileSceneGlyph,
  // A DXF is a flat drawing.
  [ENTRY_ICON_KIND.DXF]: FileDrawingGlyph,
  // A robot description (URDF, SRDF, SDF): joints and a link.
  [ENTRY_ICON_KIND.ROBOT]: FileMechanismGlyph
};

export function entryIconComponent(entry, sourceFormat, status) {
  return ENTRY_ICON_COMPONENTS[entryIconKind(entry, { sourceFormat, status })] || FileSolidGlyph;
}

// A file's icon says what it IS — nothing more. How the file came to exist (a
// model script ran, or someone dropped a foreign document in the directory) is
// not a property of the file the viewer shows, so no badge encodes it.
export default function EntryIcon({
  entry,
  sourceFormat = "",
  status = {},
  className,
  spinning = false,
  // Only the shared chrome sets this, so a CAD glyph in a file list is drawn
  // at the same weight as the lucide icons beside it (`shell/icons.jsx`).
  strokeWidth
}) {
  const Icon = entryIconComponent(entry, sourceFormat, status);
  return (
    <Icon
      className={cn(className, spinning && "animate-spin")}
      strokeWidth={strokeWidth}
      aria-hidden="true"
    />
  );
}
