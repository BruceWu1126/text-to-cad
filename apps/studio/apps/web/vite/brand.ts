// @effect-diagnostics nodeBuiltinImport:off - a build plugin that reads a folder of brand files.
/**
 * A fork's identity, from the folder `T3CODE_BRAND_DIR` names (text-to-cad
 * Studio's `brand/`): `brand.json` gives the page title and the loading
 * splash's colors, `theme.css` restyles T3 Code's default look, and the other
 * files replace the favicons and the wordmark. The app's name itself is
 * `VITE_APP_BASE_NAME`. Without the variable the app stays T3 Code.
 */
import * as NodeFS from "node:fs";
import * as NodePath from "node:path";
import type { Plugin } from "vite-plus";

export interface Brand {
  readonly dir: string;
  readonly name: string | null;
  /** The splash index.html paints before the app loads, so it does not flash T3's colors. */
  readonly splash: {
    readonly lightAccent: string;
    readonly darkAccent: string;
    readonly darkBackground: string;
  } | null;
  /** `theme.css`: token overrides for T3's default look. */
  readonly css: string | null;
  /** The mark that stands in for the T3 wordmark, as a data URL. */
  readonly markUrl: string | null;
  /** `splash.svg`: the loading splash's logo, as a data URL (it may animate). */
  readonly splashLogoUrl: string | null;
}

/** Icons a brand may replace, by the path the page asks for them at. */
const ICONS: Record<string, string> = {
  "/favicon.ico": "image/x-icon",
  "/favicon-16x16.png": "image/png",
  "/favicon-32x32.png": "image/png",
  "/apple-touch-icon.png": "image/png",
};

// T3 Code's splash colors in index.html: its accents, light then dark, and its dark background.
const SPLASH_ACCENTS = ['accent: "#4f46e5"', 'accent: "#818cf8"'] as const;
const DARK_BACKGROUND = "#0a0a0a";

const text = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value.trim() : null;

const readIfFile = (file: string) => (NodeFS.existsSync(file) ? NodeFS.readFileSync(file) : null);

export function readBrand(dir: string | undefined): Brand | null {
  if (!dir) return null;
  const file = NodePath.join(dir, "brand.json");
  if (!NodeFS.existsSync(file)) throw new Error(`T3CODE_BRAND_DIR has no brand.json: ${dir}`);
  const raw = JSON.parse(NodeFS.readFileSync(file, "utf8")) as Record<string, unknown>;
  const splash = (raw.splash ?? {}) as Record<string, unknown>;
  const lightAccent = text(splash.lightAccent);
  const darkAccent = text(splash.darkAccent);
  const darkBackground = text(splash.darkBackground);
  const mark = readIfFile(NodePath.join(dir, "mark.svg"));
  const splashLogo = readIfFile(NodePath.join(dir, "splash.svg"));
  return {
    dir,
    name: text(raw.name),
    splash:
      lightAccent && darkAccent && darkBackground
        ? { lightAccent, darkAccent, darkBackground }
        : null,
    css: readIfFile(NodePath.join(dir, "theme.css"))?.toString("utf8") ?? null,
    markUrl: mark === null ? null : `data:image/svg+xml;base64,${mark.toString("base64")}`,
    splashLogoUrl:
      splashLogo === null ? null : `data:image/svg+xml;base64,${splashLogo.toString("base64")}`,
  };
}

export function brandHtml(html: string, brand: Brand): string {
  let out = html;
  if (brand.name !== null) {
    out = out
      .replace(/<title>[^<]*<\/title>/, `<title>${brand.name}</title>`)
      .replace('aria-label="T3 Code splash screen"', `aria-label="${brand.name} splash screen"`)
      .replace('alt="T3 Code"', `alt="${brand.name}"`);
  }
  if (brand.splashLogoUrl !== null) {
    out = out.replace(
      '<img id="boot-shell-logo" src="/apple-touch-icon.png"',
      `<img id="boot-shell-logo" src="${brand.splashLogoUrl}"`,
    );
  }
  if (brand.splash !== null) {
    out = out
      .replace(SPLASH_ACCENTS[0], `accent: "${brand.splash.lightAccent}"`)
      .replace(SPLASH_ACCENTS[1], `accent: "${brand.splash.darkAccent}"`)
      .replaceAll(DARK_BACKGROUND, brand.splash.darkBackground);
  }
  if (brand.css !== null) {
    out = out.replace("</head>", `<style data-brand>${brand.css}</style></head>`);
  }
  return out;
}

const brandIcon = (brand: Brand, urlPath: string): string | null => {
  if (!(urlPath in ICONS)) return null;
  const file = NodePath.join(brand.dir, urlPath.slice(1));
  return NodeFS.existsSync(file) ? file : null;
};

export function brandPlugin(dir = process.env.T3CODE_BRAND_DIR?.trim()): Plugin | null {
  const brand = readBrand(dir);
  if (brand === null) return null;
  return {
    name: "t3code:brand",
    config: () => ({
      define: { "import.meta.env.VITE_APP_MARK_URL": JSON.stringify(brand.markUrl ?? "") },
    }),
    // Ahead of Vite's own static files, so the brand's icons win over public/.
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const urlPath = (req.url ?? "").split("?")[0] ?? "";
        const file = brandIcon(brand, urlPath);
        if (file === null) return next();
        res.setHeader("Content-Type", ICONS[urlPath]!);
        res.end(NodeFS.readFileSync(file));
      });
    },
    transformIndexHtml: (html) => brandHtml(html, brand),
    writeBundle(options) {
      if (!options.dir) return;
      for (const urlPath of Object.keys(ICONS)) {
        const file = brandIcon(brand, urlPath);
        if (file !== null) NodeFS.copyFileSync(file, NodePath.join(options.dir, urlPath.slice(1)));
      }
    },
  };
}
