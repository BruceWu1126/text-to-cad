/**
 * `mcp-app://`: where a plugin's MCP App view is served from on desktop.
 *
 * A plugin view arrives as one HTML string. In an opaque-origin frame (srcdoc,
 * as the web client hosts it) module and `blob:` workers and storage fail, and
 * text-to-cad's CAD viewer starts its model workers that way. Served from its
 * own scheme instead, every staged document is its own origin,
 * `mcp-app://<random id>`, so the renderer frame keeps `allow-same-origin`
 * and that origin is real: same-origin with itself only, never with T3's
 * renderer or another view. The frame cannot navigate off its origin
 * (`pluginViewNavigationAllowed`), its storage is cleared on release, and it
 * talks to the host by `postMessage` alone.
 *
 * This file holds the Electron-free rules; `PluginViews.ts` wires them up.
 */
export const PLUGIN_VIEW_SCHEME = "mcp-app";

/** Documents the renderer may hold at once; one that never releases (a reload) cannot grow this. */
export const MAX_STAGED_PLUGIN_VIEWS = 64;

/** A host source with a real host: `*` only as a leading `*.` label, never alone. */
const DOMAIN =
  /^(?:https?:\/\/|wss?:\/\/)?(?:\*\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*(?::\d+|:\*)?(?:\/[^\s;,'"]*)?$/;
/** Scheme sources a view may name: pages that build workers and models from `blob:`/`data:` URLs fetch them. */
const SCHEMES = new Set(["data:", "blob:"]);

function domains(list: unknown): string[] {
  return Array.isArray(list)
    ? list
        .filter(
          (entry): entry is string =>
            typeof entry === "string" && (SCHEMES.has(entry) || DOMAIN.test(entry)),
        )
        .slice(0, 32)
    : [];
}

/**
 * The response policy for one view, from its resource's `_meta.ui.csp`: no
 * network unless it names domains, scripts and styles inline or from its
 * `resourceDomains`, and workers from `blob:`.
 */
export function pluginViewContentSecurityPolicy(csp: unknown): string {
  const declared = (csp !== null && typeof csp === "object" ? csp : {}) as Record<string, unknown>;
  const connect = domains(declared.connectDomains);
  const resources = domains(declared.resourceDomains);
  const frames = domains(declared.frameDomains);
  const join = (...parts: string[]) => parts.join(" ");
  return [
    "default-src 'none'",
    `script-src ${join("'unsafe-inline'", "'wasm-unsafe-eval'", "blob:", ...resources)}`,
    `style-src ${join("'unsafe-inline'", ...resources)}`,
    `img-src ${join("data:", "blob:", ...resources)}`,
    `font-src ${join("data:", ...resources)}`,
    `media-src ${join("data:", "blob:", ...resources)}`,
    `connect-src ${connect.length > 0 ? connect.join(" ") : "'none'"}`,
    `frame-src ${frames.length > 0 ? frames.join(" ") : "'none'"}`,
    "worker-src blob:",
    "object-src 'none'",
    "base-uri 'none'",
    "form-action 'none'",
  ].join("; ");
}

export interface StagedPluginView {
  readonly html: string;
  readonly policy: string;
}

/** The view a staged URL names, its origin, or null for anything that is not one of ours. */
export function pluginViewOrigin(url: string | undefined): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === `${PLUGIN_VIEW_SCHEME}:` && parsed.hostname !== ""
      ? `${PLUGIN_VIEW_SCHEME}://${parsed.hostname}`
      : null;
  } catch {
    return null;
  }
}

/** Documents staged for the renderer, keyed by the random host of their URL. */
const randomId = () =>
  Array.from(globalThis.crypto.getRandomValues(new Uint8Array(12)), (byte) =>
    byte.toString(16).padStart(2, "0"),
  ).join("");

export function makePluginViewDocuments(random: () => string = randomId) {
  const staged = new Map<string, StagedPluginView>();
  return {
    /** Stages a document and returns its one-off URL. */
    stage: (html: string, csp: unknown): string => {
      const id = random();
      staged.set(id, { html, policy: pluginViewContentSecurityPolicy(csp) });
      while (staged.size > MAX_STAGED_PLUGIN_VIEWS) {
        const oldest = staged.keys().next().value;
        if (oldest === undefined) break;
        staged.delete(oldest);
      }
      return `${PLUGIN_VIEW_SCHEME}://${id}/index.html`;
    },
    /** Drops a staged document; returns the origin whose storage should be cleared. */
    release: (url: string): string | null => {
      const origin = pluginViewOrigin(url);
      if (origin === null) return null;
      staged.delete(new URL(url).hostname);
      return origin;
    },
    /** The response for a request on the scheme. */
    serve: (url: string): Response => {
      const origin = pluginViewOrigin(url);
      const document = origin === null ? undefined : staged.get(new URL(url).hostname);
      if (document === undefined) {
        return new Response("This view was closed. Reopen it.", {
          status: 404,
          headers: { "content-type": "text/plain; charset=utf-8" },
        });
      }
      return new Response(document.html, {
        headers: {
          "content-type": "text/html; charset=utf-8",
          "content-security-policy": document.policy,
          "cache-control": "no-store",
        },
      });
    },
  };
}

/**
 * Whether a frame showing `from` may go to `to`. A view's frame stays on its
 * own origin (its pages, its reloads); it never becomes another view or the
 * web. Frames that are not views are not this rule's business.
 */
export function pluginViewNavigationAllowed(from: string | undefined, to: string): boolean {
  const own = pluginViewOrigin(from);
  if (own === null) return true;
  return pluginViewOrigin(to) === own;
}
