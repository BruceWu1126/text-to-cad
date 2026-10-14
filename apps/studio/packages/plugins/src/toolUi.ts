/**
 * MCP App views: which of a server's tools render a UI, and where the UI shows.
 *
 * A tool renders a view when its `_meta` names a UI resource: MCP Apps'
 * `ui.resourceUri`, the older flat `ui/resourceUri`, or `openai/outputTemplate`.
 * `_meta["openai/ui"].entrypoints` (or the neutral `_meta.ui.entrypoints`) says
 * where it opens: `global` (a page), `thread` (a thread tab), `file` (how files
 * with those extensions open). A UI tool that names none is a `thread` tool.
 * `ui.visibility: ["app"]` hides a tool from the model: only its view calls it.
 */
import type { PluginView, PluginViewEntrypoint } from "@t3tools/contracts";

import { clip, isRecord } from "./folder.ts";

/** The MIME type an MCP App's UI resource is served as (MCP Apps, SEP-1865). */
export const MCP_APP_MIME = "text/html;profile=mcp-app";
/** The client capability extension that tells a server this host renders MCP Apps. */
export const MCP_UI_EXTENSION = "io.modelcontextprotocol/ui";
/**
 * The client capability extension that tells a server which of Codex's UI
 * entrypoints this host presents. text-to-cad's (`dev.texttocad/tabs`): its
 * server offers tabs only to a host that declares all three.
 */
export const UI_ENTRYPOINTS_EXTENSION = "dev.texttocad/tabs";
export const UI_ENTRYPOINTS = ["global", "thread", "file"] as const;

/** A tool as `tools/list` returns it, as far as this package reads it. */
export interface ListedTool {
  readonly name: string;
  readonly title?: string | undefined;
  readonly description?: string | undefined;
  readonly annotations?: { readonly title?: string | undefined } | undefined;
  readonly _meta?: Readonly<Record<string, unknown>> | undefined;
}

const record = (value: unknown): Readonly<Record<string, unknown>> =>
  isRecord(value) ? value : {};

function readEntrypoint(value: unknown): PluginViewEntrypoint | null {
  if (!isRecord(value)) return null;
  if (value.type === "global" || value.type === "thread") return { type: value.type };
  if (value.type === "file") {
    const extensions = Array.isArray(value.extensions)
      ? value.extensions
          .filter((item): item is string => typeof item === "string")
          .map((item) => item.replace(/^\./, "").toLowerCase())
          .filter((item) => item.length > 0 && item.length <= 32)
          .slice(0, 64)
      : [];
    return { type: "file", extensions };
  }
  return null;
}

/** A tool only its view may call (`ui.visibility: ["app"]`): hidden from agents. */
export function isAppOnly(tool: Pick<ListedTool, "_meta">): boolean {
  const visibility = record(record(tool._meta).ui).visibility;
  return (
    Array.isArray(visibility) &&
    visibility.length > 0 &&
    visibility.every((entry) => entry === "app")
  );
}

/** The view a tool renders, or null for a tool without a UI resource. */
export function readToolUi(server: string, tool: ListedTool): PluginView | null {
  const meta = record(tool._meta);
  const ui = record(meta.ui);
  const openaiUi = record(meta["openai/ui"]);
  const resourceUri = [ui.resourceUri, meta["ui/resourceUri"], meta["openai/outputTemplate"]].find(
    (value): value is string => typeof value === "string" && value.length > 0,
  );
  if (resourceUri === undefined) return null;
  const raw = Array.isArray(openaiUi.entrypoints)
    ? openaiUi.entrypoints
    : Array.isArray(ui.entrypoints)
      ? ui.entrypoints
      : [];
  const entrypoints = raw.flatMap((entry) => {
    const parsed = readEntrypoint(entry);
    return parsed === null ? [] : [parsed];
  });
  return {
    server: clip(server, 128),
    tool: clip(tool.name, 128),
    title: clip(tool.title ?? tool.annotations?.title ?? tool.name, 160),
    resourceUri: clip(resourceUri, 2048),
    // A UI tool that names no place still has one: a tab a thread can open.
    entrypoints: entrypoints.length > 0 ? entrypoints.slice(0, 8) : [{ type: "thread" }],
  };
}

/** The extensions a view opens, lowercased without the dot. */
export function fileExtensionsOf(view: Pick<PluginView, "entrypoints">): Array<string> {
  return view.entrypoints.flatMap((entry) => (entry.type === "file" ? entry.extensions : []));
}

/** Lives in contracts: clients match an agent's tool call to its view by it. */
export { AGENT_TOOL_NAME_MAX, agentToolName } from "@t3tools/contracts";
