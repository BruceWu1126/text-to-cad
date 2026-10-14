import type { PluginViewDocument } from "@t3tools/contracts";
import {
  injectMcpAppCookieBootstrap,
  injectMcpAppCsp,
  injectMcpAppThemeBootstrap,
  readMcpAppCsp,
} from "@t3tools/shared/mcpApp";

/**
 * The desktop app's preload exposes this so a plugin view can load from its
 * own `mcp-app://<id>` origin (apps/desktop/src/plugins/PluginViews.ts).
 * Absent in a browser, where views fall back to an opaque-origin `srcdoc`.
 */
export interface PluginViewsBridge {
  readonly stage: (html: string, csp: unknown) => Promise<string>;
  readonly release: (url: string) => Promise<void>;
}

export function readPluginViewsBridge(): PluginViewsBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as { readonly pluginViewsBridge?: PluginViewsBridge }).pluginViewsBridge;
  return bridge ?? null;
}

/** Real-origin views need scripts, their own storage, forms, popups and downloads. */
export const PLUGIN_VIEW_STAGED_SANDBOX =
  "allow-scripts allow-same-origin allow-forms allow-popups allow-downloads";
/** Never allow-same-origin here: a srcdoc frame would share T3's own origin. */
export const PLUGIN_VIEW_SRCDOC_SANDBOX = "allow-scripts allow-forms";

/** `mcp-app://<id>` for a staged URL: the only window origin its messages go to. */
export function stagedPluginViewOrigin(url: string): string {
  const match = /^([a-z][a-z0-9+.-]*):\/\/([^/?#]+)/i.exec(url);
  return match ? `${match[1]!.toLowerCase()}://${match[2]!.toLowerCase()}` : "*";
}

/** The HTML a staged view is served: the desktop response header carries its policy. */
export function stagedPluginViewHtml(document: PluginViewDocument): string {
  return injectMcpAppThemeBootstrap(document.html);
}

/**
 * The `srcdoc` a browser hosts: the view's policy as a meta tag ahead of any
 * script, plus the theme and cookie shims T3's captured MCP Apps get.
 */
export function srcdocPluginViewHtml(document: PluginViewDocument): string {
  return injectMcpAppCookieBootstrap(
    injectMcpAppThemeBootstrap(injectMcpAppCsp(document.html, readMcpAppCsp(document.csp))),
  );
}
