// @effect-diagnostics nodeBuiltinImport:off - reads the brand folder and index.html the build plugin reads.
import * as NodeFS from "node:fs";
import * as NodePath from "node:path";
import { describe, expect, it } from "vite-plus/test";

import { brandHtml, readBrand } from "../vite/brand";

const studioBrand = NodePath.join(import.meta.dirname, "..", "..", "..", "brand");
const indexHtml = NodeFS.readFileSync(
  NodePath.join(import.meta.dirname, "..", "index.html"),
  "utf8",
);

describe("brand plugin", () => {
  it("leaves T3 Code alone without a brand folder", () => {
    expect(readBrand(undefined)).toBeNull();
  });

  it("brands index.html with text-to-cad Studio's title, splash and theme", () => {
    const brand = readBrand(studioBrand)!;
    expect(brand.name).toBe("Studio");
    expect(brand.markUrl).toMatch(/^data:image\/svg\+xml;base64,/);

    const html = brandHtml(indexHtml, brand);
    expect(html).toContain("<title>Studio</title>");
    // The splash plays text-to-cad's animated mark under the app's name.
    expect(html).toMatch(
      /<img id="boot-shell-logo" src="data:image\/svg\+xml;base64,[^"]+" alt="Studio"/,
    );
    // The splash would otherwise flash T3's indigo before the app paints.
    expect(html).not.toContain('accent: "#4f46e5"');
    expect(html).not.toContain('accent: "#818cf8"');
    expect(html).toContain('accent: "#2c7197"');
    expect(html).not.toContain("#0a0a0a");
    // text-to-cad's palette, scoped so a theme the user picks keeps its own colors.
    expect(html).toContain("<style data-brand>");
    expect(html).toContain('html.dark:not([data-theme-id]), html.dark[data-theme-id=""]');
    expect(html).toContain("--primary: #2c7197;");
  });

  it("says which folder lacks brand.json", () => {
    expect(() => readBrand(import.meta.dirname)).toThrow(/has no brand\.json/);
  });
});
