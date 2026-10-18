import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import {
  DARK_PICTURE_ATTRIBUTE, FILE_SIDEBAR_SURFACE_CLASS, FLOATING_CHROME_SURFACE_CLASS, FLOATING_SURFACE_CLASS
} from "./lib/floatingSurface.js";

// The viewer's chrome follows docs/settings-ui.md: type from the token scale (text-micro,
// text-tiny, text-xs, text-ui), and layout from the ONE viewer breakpoint (useViewerMobile)
// or a container query — never the window's `sm:`/`md:` breakpoints. Document content
// (markdown, code) keeps its own content styles and is not chrome.
const root = fileURLToPath(new URL(".", import.meta.url));
const CHROME = ["file-viewer", "host", "primitives", "loading", "annotation", "library", "cad-viewer", "renderers/kit", "renderers/glb", "renderers/mesh", "renderers/dxf", "renderers/board",
  "renderers/workspace"];
const sources = dir => readdirSync(dir).flatMap(name => {
  const path = join(dir, name);
  if (statSync(path).isDirectory()) return name === "__fixtures__" ? [] : sources(path);
  return /\.(jsx?|tsx?)$/.test(name) && !/\.test\./.test(name) ? [path] : [];
});
const WINDOW_BREAKPOINT = /(^|[\s"'`])(sm|md|lg|xl|2xl):[\w[!-]/;
const PIXEL_FONT_SIZE = /\btext-\[\d+(\.\d+)?px\]/;

test("viewer chrome uses the type scale and the viewer breakpoint, never window breakpoints or pixel font sizes", () => {
  const found = [];
  for (const dir of CHROME) for (const file of sources(join(root, dir))) {
    readFileSync(file, "utf8").split("\n").forEach((line, index) => {
      if (WINDOW_BREAKPOINT.test(line) || PIXEL_FONT_SIZE.test(line)) found.push(`${relative(root, file)}:${index + 1}: ${line.trim().slice(0, 120)}`);
    });
  }
  assert.deepEqual(found, []);
});

// Every scroll region of the chrome is the one ScrollArea primitive (`primitives/scroll-area.jsx`):
// thin overlay bars in the theme's colours, never the platform's scrollbar. The home (the model
// library) is a page, not chrome: it scrolls as a page does, with the platform's own scrollbar.
const NATIVE_SCROLLER = /\boverflow(?:-[xy])?-(?:auto|scroll)\b/;
const PAGES = ["library/ModelLibrary.tsx"];
const SCROLL_REGIONS = ["renderers/kit/tools/ToolPanel.jsx", "file-viewer/navigation/FolderExplorer.jsx", "primitives/dropdown-menu.jsx",
  "renderers/kit/status/ViewerAlertCard.jsx"];

test("every scroll region in the viewer's chrome is the ScrollArea primitive, never a native scroller", () => {
  const found = [];
  for (const dir of [...CHROME, "renderers/step", "renderers/robot"]) for (const file of sources(join(root, dir))) {
    if (PAGES.includes(relative(root, file))) continue;
    readFileSync(file, "utf8").split("\n").forEach((line, index) => {
      if (NATIVE_SCROLLER.test(line)) found.push(`${relative(root, file)}:${index + 1}: ${line.trim().slice(0, 120)}`);
    });
  }
  assert.deepEqual(found, []);
  for (const file of SCROLL_REGIONS) assert.match(readFileSync(join(root, file), "utf8"), /<ScrollArea\b/, `${file} scrolls in a ScrollArea`);
});

// The floating surfaces are defined once (`lib/floatingSurface.js`) and are what main draws
// everywhere. The one addition is the tone under a view that declares its picture dark whatever
// the theme, in the light theme alone; no chrome writes a backdrop of its own, and the board is
// the one view that declares a dark picture.
const SURFACES = [
  [FLOATING_SURFACE_CLASS, "border border-border bg-background/75 text-foreground shadow-sm backdrop-blur-md", "12px"],
  [FILE_SIDEBAR_SURFACE_CLASS, "border border-border bg-background/55 text-foreground shadow-sm backdrop-blur-[4px]", "4px"],
  [FLOATING_CHROME_SURFACE_CLASS, "border border-border bg-background/30 text-foreground shadow-sm backdrop-blur-[1px]", "1px"],
];
const BACKDROP = /\bbackdrop-(?:blur|brightness|contrast|grayscale|hue-rotate|invert|opacity|saturate|sepia)\b|\[-?(?:webkit-)?backdrop-filter:/;

test("the floating surfaces are main's; only a view that declares a dark picture tones them, in the light theme, and only the board declares one", () => {
  for (const [surface, base, blur] of SURFACES) {
    assert.equal(surface, `${base} [[${DARK_PICTURE_ATTRIBUTE}]_&:not(.dark_*)]:[backdrop-filter:blur(${blur})_contrast(0.5)_brightness(1.333)]`);
  }
  const found = [];
  for (const dir of [...CHROME, "renderers/step", "renderers/robot"]) for (const file of sources(join(root, dir))) {
    const path = relative(root, file);
    readFileSync(file, "utf8").split("\n").forEach((line, index) => {
      const declares = /DARK_PICTURE_ATTRIBUTE|data-cad-dark-picture/.test(line) && !path.startsWith("renderers/board/");
      if (BACKDROP.test(line) || declares) found.push(`${path}:${index + 1}: ${line.trim().slice(0, 120)}`);
    });
  }
  assert.deepEqual(found, []);
});
