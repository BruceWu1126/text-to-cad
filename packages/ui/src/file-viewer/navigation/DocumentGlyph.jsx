import { Box } from "lucide-react";

import { FileIcon } from "./icons.jsx";

/**
 * What a document is, as one small glyph, the same wherever the viewer lists documents (the
 * Home's cards, the name menu's Models rows): a model, built from code, is the cube; a file is
 * its type's `File*` icon, as the explorer draws it, so a file reads as a file at a glance.
 *
 * @param {{ model?: boolean, path: string, className?: string, strokeWidth?: number }} props
 */
export function DocumentGlyph({ model = false, path, className, strokeWidth = 1.75 }) {
  return model
    ? <Box className={className} strokeWidth={strokeWidth} aria-hidden="true" data-glyph="model" />
    : <span className="contents" data-glyph="file"><FileIcon className={className} path={path} strokeWidth={strokeWidth} /></span>;
}
