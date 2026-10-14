import {
  agentToolName,
  type InstalledPlugin,
  type OrchestrationV2TurnItem,
} from "@t3tools/contracts";

import { type PluginViewChoice, pluginFileHandlers } from "./plugins.ts";

/**
 * A plugin view a settled turn item asks to show beside its thread:
 * - `agent`: the agent called a view's tool itself (text-to-cad's `cad_open`),
 *   so the view opens with the call's arguments, as Codex opens its tab.
 * - `file`: a file edit wrote a file an enabled view claims (a `.step`), so
 *   the view follows the agent's work.
 */
export type PluginFollowTarget =
  | {
      readonly kind: "agent";
      readonly choice: PluginViewChoice;
      readonly arguments: Record<string, unknown>;
      /** The `path` argument, when the call names one. */
      readonly path: string | null;
    }
  | { readonly kind: "file"; readonly choice: PluginViewChoice; readonly path: string };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

/**
 * True when a provider's tool name is T3's MCP name for this tool. Claude
 * reports `mcp__t3-code__<name>`, Codex `t3-code.<name>`.
 */
function namesAgentTool(toolName: string, name: string): boolean {
  if (toolName === name) return true;
  if (!toolName.endsWith(name)) return false;
  const separator = toolName.slice(0, -name.length);
  return separator.endsWith("__") || separator.endsWith(".");
}

/** The view an agent's call to a plugin tool renders, or null for any other call. */
export function agentOpenedView(
  plugins: ReadonlyArray<InstalledPlugin>,
  toolName: string,
  input: unknown,
): Extract<PluginFollowTarget, { kind: "agent" }> | null {
  for (const plugin of plugins) {
    if (!plugin.enabled) continue;
    for (const view of plugin.views) {
      if (!namesAgentTool(toolName, agentToolName(plugin.id, view.tool))) continue;
      const args = isRecord(input) ? input : {};
      return {
        kind: "agent",
        choice: { pluginId: plugin.id, pluginName: plugin.displayName, view },
        arguments: args,
        path: typeof args.path === "string" && args.path.length > 0 ? args.path : null,
      };
    }
  }
  return null;
}

/**
 * The files a file edit wrote, deletions left out. Checkpoints are not read:
 * their paths are relative to the repository, not the workspace, and they
 * report every file as modified, deletions included. A model a shell command
 * builds reaches the view through the agent's own `cad_open` or `cad_show`.
 */
function writtenPaths(item: OrchestrationV2TurnItem): ReadonlyArray<string> {
  if (item.type !== "file_change") return [];
  const changes = item.changes ?? [];
  if (changes.length === 0) return [item.fileName];
  return changes.filter((change) => !/^del/i.test(change.operation)).map((change) => change.path);
}

/** What a settled item asks to show, or null. The last claimed file wins. */
export function pluginFollowTarget(
  plugins: ReadonlyArray<InstalledPlugin>,
  item: OrchestrationV2TurnItem,
): PluginFollowTarget | null {
  if (item.status !== "completed") return null;
  if (item.type === "dynamic_tool") {
    return item.toolName === null ? null : agentOpenedView(plugins, item.toolName, item.input);
  }
  const paths = writtenPaths(item);
  for (let index = paths.length - 1; index >= 0; index -= 1) {
    const path = paths[index]!;
    const choice = pluginFileHandlers(plugins, path)[0];
    if (choice !== undefined) return { kind: "file", choice, path };
  }
  return null;
}

/**
 * A path as the workspace sees it: relative paths stay, absolute ones inside
 * the root lose it, and absolute ones elsewhere are null (a view opens
 * workspace files only).
 */
export function workspaceRelativePath(path: string, root: string | null): string | null {
  const normalized = path.replaceAll("\\", "/");
  if (!normalized.startsWith("/") && !/^[A-Za-z]:\//.test(normalized)) {
    return normalized.replace(/^\.\//, "");
  }
  if (root === null) return null;
  const base = root.replaceAll("\\", "/").replace(/\/+$/, "");
  return normalized.startsWith(`${base}/`) ? normalized.slice(base.length + 1) : null;
}
