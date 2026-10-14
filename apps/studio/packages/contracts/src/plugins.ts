import * as Schema from "effect/Schema";

import { ThreadId, TrimmedNonEmptyString, TrimmedString } from "./baseSchemas.ts";

/**
 * Plugins in the Codex/Claude plugin format: a folder holding a manifest
 * (`.codex-plugin/plugin.json` or `.claude-plugin/plugin.json`), MCP servers,
 * skills and MCP App views. The environment installs them into its own state
 * directory (never into `~/.claude` or `~/.codex`), runs their servers once per
 * thread, and hands their tools to every provider through T3's MCP server.
 */

const Text = (max: number) => Schema.String.check(Schema.isMaxLength(max));
const NullableText = (max: number) => Schema.NullOr(Text(max));

/** `<plugin name>` as installed, unique per environment. */
export const PluginId = TrimmedNonEmptyString.check(
  Schema.isMaxLength(128),
  Schema.isPattern(/^[A-Za-z0-9][A-Za-z0-9._-]*$/),
);
export type PluginId = typeof PluginId.Type;

/** A catalog row: `<marketplace>/<plugin name>` for the source Install will use. */
export const PluginCatalogKey = TrimmedNonEmptyString.check(Schema.isMaxLength(300));
export type PluginCatalogKey = typeof PluginCatalogKey.Type;

export const PluginCompatibilityLevel = Schema.Literals([
  "works",
  "signin",
  "partly",
  "codex",
  "unknown",
  "unavailable",
]);
export type PluginCompatibilityLevel = typeof PluginCompatibilityLevel.Type;

export const PluginCompatibility = Schema.Struct({
  level: PluginCompatibilityLevel,
  /** Short label for a card: "Works", "Partly", "Checked on install"… */
  label: Text(40),
  /** One line each: what works here and what is ignored. */
  notes: Schema.Array(Text(200)).check(Schema.isMaxLength(12)),
});
export type PluginCompatibility = typeof PluginCompatibility.Type;

export const PluginMarketplaceRef = Schema.Struct({
  id: Text(160),
  name: Text(160),
  official: Schema.Boolean,
});
export type PluginMarketplaceRef = typeof PluginMarketplaceRef.Type;

export const PluginCatalogEntry = Schema.Struct({
  key: PluginCatalogKey,
  name: Text(128),
  displayName: Text(160),
  description: Text(400),
  category: NullableText(80),
  publisher: NullableText(160),
  homepage: NullableText(2048),
  version: NullableText(64),
  /** Every marketplace that lists this plugin; the first is the one Install uses. */
  marketplaces: Schema.Array(PluginMarketplaceRef).check(Schema.isMaxLength(16)),
  verified: Schema.Boolean,
  compatibility: PluginCompatibility,
  /** Set when this plugin is installed. */
  installedId: Schema.NullOr(PluginId),
});
export type PluginCatalogEntry = typeof PluginCatalogEntry.Type;

export const PluginCatalogStatus = Schema.Struct({
  id: Text(160),
  name: Text(160),
  source: Text(2048),
  state: Schema.Literals(["ready", "fetching", "error"]),
  pluginCount: Schema.Number,
  error: NullableText(400),
});
export type PluginCatalogStatus = typeof PluginCatalogStatus.Type;

export const PLUGIN_CATALOG_PAGE_LIMIT = 200;

export const PluginCatalogInput = Schema.Struct({
  query: TrimmedString.check(Schema.isMaxLength(120)),
  /** Fetch every catalog again instead of using the cached copies. */
  refresh: Schema.optionalKey(Schema.Boolean),
});
export type PluginCatalogInput = typeof PluginCatalogInput.Type;

export const PluginCatalogResult = Schema.Struct({
  entries: Schema.Array(PluginCatalogEntry).check(Schema.isMaxLength(PLUGIN_CATALOG_PAGE_LIMIT)),
  matchCount: Schema.Number,
  catalogs: Schema.Array(PluginCatalogStatus).check(Schema.isMaxLength(32)),
});
export type PluginCatalogResult = typeof PluginCatalogResult.Type;

/** Where a view can be opened from, as the plugin's tool declares it. */
export const PluginViewEntrypoint = Schema.Union([
  Schema.Struct({ type: Schema.Literal("global") }),
  Schema.Struct({ type: Schema.Literal("thread") }),
  Schema.Struct({
    type: Schema.Literal("file"),
    extensions: Schema.Array(Text(32)).check(Schema.isMaxLength(64)),
  }),
]);
export type PluginViewEntrypoint = typeof PluginViewEntrypoint.Type;

/** A tool whose result is an MCP App view (`_meta.ui.resourceUri`). */
export const PluginView = Schema.Struct({
  server: Text(128),
  tool: Text(128),
  title: Text(160),
  resourceUri: Text(2048),
  entrypoints: Schema.Array(PluginViewEntrypoint).check(Schema.isMaxLength(8)),
});
export type PluginView = typeof PluginView.Type;

export const PluginServerStatus = Schema.Struct({
  name: Text(128),
  transport: Schema.Literals(["stdio", "http"]),
  /** Tools the server listed when it last started, or an error. */
  state: Schema.Literals(["idle", "starting", "ready", "error"]),
  error: NullableText(400),
});
export type PluginServerStatus = typeof PluginServerStatus.Type;

export const InstalledPlugin = Schema.Struct({
  id: PluginId,
  displayName: Text(160),
  description: Text(400),
  version: NullableText(64),
  publisher: NullableText(160),
  enabled: Schema.Boolean,
  marketplace: NullableText(160),
  /** Commit the installed copy was cloned at, when it came from git. */
  commit: NullableText(64),
  skills: Schema.Array(Text(128)).check(Schema.isMaxLength(200)),
  servers: Schema.Array(PluginServerStatus).check(Schema.isMaxLength(32)),
  views: Schema.Array(PluginView).check(Schema.isMaxLength(64)),
  compatibility: PluginCompatibility,
  updateAvailable: Schema.Boolean,
  /** Prompts the plugin suggests starting with (Codex's `interface.defaultPrompt`). */
  starterPrompts: Schema.optional(Schema.Array(Text(300)).check(Schema.isMaxLength(6))),
  /** A manifest that no longer reads, or a server that failed to start. */
  error: NullableText(400),
});
export type InstalledPlugin = typeof InstalledPlugin.Type;

/**
 * The name an agent sees a plugin tool by on T3's MCP server: `<plugin>__<tool>`,
 * kept to what every provider accepts. Claude prefixes `mcp__t3-code__` (14
 * characters) and caps the whole name at 64, so this stays within 50; a longer
 * one keeps its start and ends in a hash of the full name.
 */
export const AGENT_TOOL_NAME_MAX = 50;

export function agentToolName(pluginId: string, tool: string): string {
  const full = `${pluginId}__${tool}`.replace(/[^A-Za-z0-9_-]/g, "_");
  if (full.length <= AGENT_TOOL_NAME_MAX) return full;
  let hash = 0x811c9dc5;
  for (let index = 0; index < full.length; index += 1) {
    hash ^= full.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  const suffix = hash.toString(16).padStart(8, "0");
  return `${full.slice(0, AGENT_TOOL_NAME_MAX - suffix.length - 1)}_${suffix}`;
}

export const PluginListResult = Schema.Struct({
  plugins: Schema.Array(InstalledPlugin).check(Schema.isMaxLength(500)),
});
export type PluginListResult = typeof PluginListResult.Type;

export const PluginInstallInput = Schema.Struct({ key: PluginCatalogKey });
export type PluginInstallInput = typeof PluginInstallInput.Type;

export const PluginIdInput = Schema.Struct({ pluginId: PluginId });
export type PluginIdInput = typeof PluginIdInput.Type;

export const PluginSetEnabledInput = Schema.Struct({
  pluginId: PluginId,
  enabled: Schema.Boolean,
});
export type PluginSetEnabledInput = typeof PluginSetEnabledInput.Type;

/** A view runs against one thread's server process, or the environment's own scope. */
export const PluginScope = Schema.NullOr(ThreadId);

export const PluginOpenViewInput = Schema.Struct({
  threadId: PluginScope,
  pluginId: PluginId,
  server: Text(128),
  tool: Text(128),
  arguments: Schema.Record(Schema.String, Schema.Unknown),
  /** Opening a file: its path relative to the thread's workspace. */
  file: Schema.optionalKey(Text(4096)),
});
export type PluginOpenViewInput = typeof PluginOpenViewInput.Type;

export const PluginViewDocument = Schema.Struct({
  /** Apps inline their bundle: text-to-cad's CAD view is about 7 MB. */
  html: Schema.String.check(Schema.isMaxLength(32 * 1024 * 1024)),
  /** `_meta.ui.csp` from the resource, passed through for the host to apply. */
  csp: Schema.NullOr(
    Schema.Struct({
      connectDomains: Schema.optionalKey(Schema.Array(Text(512))),
      resourceDomains: Schema.optionalKey(Schema.Array(Text(512))),
      frameDomains: Schema.optionalKey(Schema.Array(Text(512))),
    }),
  ),
  toolInput: Schema.Record(Schema.String, Schema.Unknown),
  /** The MCP `CallToolResult` the view is given on initialize. */
  toolResult: Schema.Unknown,
});
export type PluginViewDocument = typeof PluginViewDocument.Type;

/** A JSON-RPC request a view makes to its own server (tools/call, resources/read…). */
export const PluginRequestInput = Schema.Struct({
  threadId: PluginScope,
  pluginId: PluginId,
  server: Text(128),
  method: Schema.Literals([
    "tools/call",
    "tools/list",
    "resources/read",
    "resources/list",
    "resources/templates/list",
    "prompts/list",
    "prompts/get",
  ]),
  params: Schema.Unknown,
});
export type PluginRequestInput = typeof PluginRequestInput.Type;

export const PluginRequestResult = Schema.Struct({ result: Schema.Unknown });
export type PluginRequestResult = typeof PluginRequestResult.Type;

export const PluginOperationErrorReason = Schema.Literals([
  "not_found",
  "install_failed",
  "manifest_invalid",
  "server_failed",
  "request_failed",
  "thread_not_found",
  "catalog_unavailable",
]);
export type PluginOperationErrorReason = typeof PluginOperationErrorReason.Type;

export class PluginOperationError extends Schema.TaggedError<PluginOperationError>()(
  "PluginOperationError",
  {
    reason: PluginOperationErrorReason,
    message: Schema.String,
  },
) {}
