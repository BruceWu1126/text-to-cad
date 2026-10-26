import assert from "node:assert/strict";
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

// The viewer's chrome follows docs/settings-ui.md: type from the token scale (text-micro,
// text-tiny, text-xs, text-ui), and layout from the ONE viewer breakpoint (useViewerMobile)
// or a container query — never the window's `sm:`/`md:` breakpoints. Document content
// (markdown, code) keeps its own content styles and is not chrome.
const root = fileURLToPath(new URL(".", import.meta.url));
const CHROME = ["file-viewer", "host", "primitives", "loading", "annotation", "library", "cad-viewer", "renderers/kit", "renderers/glb", "renderers/mesh", "renderers/dxf",
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
  for (const dir of [...CHROME, "renderers/xbf", "renderers/robot"]) for (const file of sources(join(root, dir))) {
    if (PAGES.includes(relative(root, file))) continue;
    readFileSync(file, "utf8").split("\n").forEach((line, index) => {
      if (NATIVE_SCROLLER.test(line)) found.push(`${relative(root, file)}:${index + 1}: ${line.trim().slice(0, 120)}`);
    });
  }
  assert.deepEqual(found, []);
  for (const file of SCROLL_REGIONS) assert.match(readFileSync(join(root, file), "utf8"), /<ScrollArea\b/, `${file} scrolls in a ScrollArea`);
});

// The Home (`library/`) is one type scale (docs/settings-ui.md): every text text-ui, 13px -- the
// heading, the type filter, the search, the names, the table, its header included -- but a tile's
// age, text-tiny and muted; a tile's and a row's glyph 16px.
test("the Home is one type scale: text-ui throughout but a tile's age, and 16px glyphs", () => {
  const css = readFileSync(join(root, "styles/globals.css"), "utf8");
  const sizes = [...css.matchAll(/([^{}]*\.cad-library[^{}]*)\{([^}]*)\}/g)].flatMap(([, selector, body]) => {
    const size = body.match(/font-size:\s*([^;]+);/)?.[1].trim();
    return size ? [[selector.trim().replace(/\s+/g, " "), size]] : [];
  });
  assert.deepEqual(sizes, [
    [".cad-library-heading", "var(--text-ui)"],
    [".cad-library-status", "var(--text-tiny, 0.6875rem)"],
    [".cad-library-columns", "var(--text-ui)"],
    [".cad-library-row .cad-library-status", "var(--text-ui)"],
  ]);
  assert.match(css, /\.cad-library-glyph \{[^}]*width: 16px; height: 16px;/);
  const page = readFileSync(join(root, "library/ModelLibrary.tsx"), "utf8");
  assert.deepEqual([...page.matchAll(/\btext-(micro|tiny|xs)\b/g)].map(([name]) => name), []);
  assert.match(page, /className="cad-library h-full overflow-y-auto text-ui"/);
});

// The logo stands on the page's gutter (docs/settings-ui.md): the navbar's 12px start and the logo
// button's 4px padding put its mark 16px in, the Home grid's left edge, over the home and a page.
test("the navbar's logo and the Home's grid share the 16px gutter", () => {
  const css = readFileSync(join(root, "styles/globals.css"), "utf8");
  assert.match(css, /\.cad-library-content \{[^}]*margin: 0; padding: 16px 16px /);
  const navbar = readFileSync(join(root, "file-viewer/navigation/ViewerNavbar.jsx"), "utf8");
  assert.match(navbar, /cn\(NAVBAR_ROW_CLASS, "pl-3 /);
  assert.match(navbar, /"cad-navbar-logo justify-start px-1"/);
});
