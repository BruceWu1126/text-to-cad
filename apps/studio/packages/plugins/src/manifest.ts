/**
 * Reading a plugin folder in the Codex/Claude Code plugin format: its manifest,
 * its MCP servers and its skills.
 *
 * The manifest is looked for where each host keeps it: `.codex-plugin/plugin.json`
 * (Codex), `.claude-plugin/plugin.json` (Claude Code), or a bare `plugin.json`.
 * Servers come from `mcpServers` (a path to a JSON file holding
 * `{ "mcpServers": {...} }` or the bare map, or that map inline), else from a
 * `.mcp.json` in the folder, else the Agent Plugins standard's `mcp.json`; skills from `skills` (a directory of
 * `<name>/SKILL.md`), else `skills/` when it exists.
 */
import {
  type PluginFolder,
  PluginReadError,
  isRecord,
  joinRelative,
  nonEmptyString,
  requireInside,
} from "./folder.ts";

export const MANIFEST_LOCATIONS = [
  { location: ".codex-plugin/plugin.json", format: "codex" },
  { location: ".claude-plugin/plugin.json", format: "claude" },
  { location: "plugin.json", format: "bare" },
] as const;

/** Which host's layout the manifest came in; `inline` is a marketplace entry with `"strict": false`. */
export type ManifestFormat = (typeof MANIFEST_LOCATIONS)[number]["format"] | "inline";

/** One MCP server as a plugin declares it: Codex's keys, and Claude Code's. */
export interface PluginServerConfig {
  readonly command: string | null;
  readonly args: ReadonlyArray<string>;
  readonly env: Readonly<Record<string, string>>;
  readonly cwd: string | null;
  /** Streamable HTTP, instead of a command. */
  readonly url: string | null;
  readonly headers: Readonly<Record<string, string>>;
  readonly startupTimeoutSec: number | null;
  readonly toolTimeoutSec: number | null;
}

export interface PluginManifest {
  readonly name: string;
  readonly version: string | null;
  readonly description: string;
  readonly displayName: string;
  readonly publisher: string | null;
  readonly homepage: string | null;
  readonly category: string | null;
  /** `interface.defaultPrompt`: what the plugin suggests asking first. */
  readonly starterPrompts: ReadonlyArray<string>;
  /** The manifest as written, for what this reader does not name. */
  readonly raw: Readonly<Record<string, unknown>>;
}

export interface ReadPlugin {
  readonly manifest: PluginManifest;
  readonly format: ManifestFormat;
  readonly servers: Readonly<Record<string, PluginServerConfig>>;
  /** The directory of `<name>/SKILL.md` folders, relative to the plugin, or null. */
  readonly skillsDir: string | null;
  readonly skills: ReadonlyArray<string>;
}

const PLUGIN_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]*$/;
const SERVER_NAME = /^[A-Za-z0-9_-]+$/;

/** `${CLAUDE_PLUGIN_ROOT}` and `${PLUGIN_ROOT}` in a command, an argument or a value. */
export function expandPluginRoot(value: string, root: string): string {
  return value.replaceAll("${CLAUDE_PLUGIN_ROOT}", root).replaceAll("${PLUGIN_ROOT}", root);
}

export function findManifest(folder: PluginFolder): (typeof MANIFEST_LOCATIONS)[number] | null {
  return MANIFEST_LOCATIONS.find((candidate) => folder.isFile(candidate.location)) ?? null;
}

const stringRecord = (value: unknown): Record<string, string> =>
  isRecord(value)
    ? Object.fromEntries(
        Object.entries(value).filter(
          (entry): entry is [string, string] => typeof entry[1] === "string",
        ),
      )
    : {};

const positive = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;

function parseServer(name: string, value: unknown): PluginServerConfig {
  if (!SERVER_NAME.test(name)) {
    throw new PluginReadError(
      `MCP server name "${name}" must be letters, digits, dashes and underscores`,
    );
  }
  if (!isRecord(value)) throw new PluginReadError(`MCP server "${name}" is not an object`);
  const command = nonEmptyString(value.command);
  const url = nonEmptyString(value.url);
  if (command === null && url === null) {
    throw new PluginReadError(`MCP server "${name}" needs a command or a url`);
  }
  return {
    command,
    args: Array.isArray(value.args)
      ? value.args.filter((arg): arg is string => typeof arg === "string")
      : [],
    env: stringRecord(value.env),
    cwd: nonEmptyString(value.cwd),
    url,
    headers: stringRecord(value.headers),
    startupTimeoutSec: positive(value.startup_timeout_sec),
    toolTimeoutSec: positive(value.tool_timeout_sec),
  };
}

/** The raw server map a manifest, `.mcp.json` or `mcp.json` declares, without validating each server. */
export function rawServersOf(
  folder: PluginFolder | null,
  manifest: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  let raw: unknown;
  if (typeof manifest.mcpServers === "string") {
    if (folder === null) return {};
    raw = folder.readJson(requireInside(manifest.mcpServers));
  } else if (isRecord(manifest.mcpServers)) {
    raw = manifest.mcpServers;
  } else if (folder?.isFile(".mcp.json")) {
    raw = folder.readJson(".mcp.json");
  } else if (folder?.isFile("mcp.json")) {
    // The Agent Plugins standard's server config (agent-plugins.org), beside its bare plugin.json.
    raw = folder.readJson("mcp.json");
  }
  if (!isRecord(raw)) return {};
  // Claude Code also accepts a bare map of servers, without the `mcpServers` key (Linear's plugin).
  const map = "mcpServers" in raw ? raw.mcpServers : raw;
  return isRecord(map) ? map : {};
}

/** The skills directory a manifest names (or `skills/`), and the skills in it. */
export function skillsOf(
  folder: PluginFolder,
  manifest: Readonly<Record<string, unknown>>,
): { readonly dir: string | null; readonly names: ReadonlyArray<string> } {
  const dir = typeof manifest.skills === "string" ? requireInside(manifest.skills) : "skills";
  if (!folder.isDirectory(dir)) return { dir: null, names: [] };
  const names = folder
    .list(dir)
    .filter((name) => folder.isFile(joinRelative(dir, name, "SKILL.md")))
    .toSorted();
  return { dir, names };
}

/** What the manifest says about the plugin, for cards and lists. */
export function describeManifest(
  raw: Readonly<Record<string, unknown>>,
  fallbackName: string,
): PluginManifest {
  const face = isRecord(raw.interface) ? raw.interface : {};
  const author = raw.author;
  const authorName =
    typeof author === "string"
      ? nonEmptyString(author)
      : isRecord(author)
        ? nonEmptyString(author.name)
        : null;
  const name = nonEmptyString(raw.name) ?? fallbackName;
  const prompts = Array.isArray(face.defaultPrompt) ? face.defaultPrompt : [face.defaultPrompt];
  return {
    name,
    version: nonEmptyString(raw.version),
    description: nonEmptyString(face.shortDescription) ?? nonEmptyString(raw.description) ?? "",
    displayName: nonEmptyString(face.displayName) ?? name,
    publisher: nonEmptyString(face.developerName) ?? authorName,
    homepage: nonEmptyString(face.websiteURL) ?? nonEmptyString(raw.homepage),
    category: nonEmptyString(face.category) ?? nonEmptyString(raw.category),
    starterPrompts: prompts.flatMap((prompt) => nonEmptyString(prompt) ?? []).slice(0, 6),
    raw,
  };
}

/**
 * Read a plugin folder. Throws `PluginReadError` with a sentence when it is not
 * one. `inline` is the manifest a marketplace entry carries for a folder that
 * has none of its own (Claude Code's `"strict": false`): used only when no
 * manifest is found.
 */
export function readPlugin(
  folder: PluginFolder,
  inline?: Readonly<Record<string, unknown>> | null,
): ReadPlugin {
  const found = findManifest(folder);
  if (found === null && !inline) {
    throw new PluginReadError(
      `no plugin manifest (looked for ${MANIFEST_LOCATIONS.map((item) => item.location).join(", ")})`,
    );
  }
  const raw = found === null ? inline : folder.readJson(found.location);
  if (!isRecord(raw)) throw new PluginReadError("the plugin manifest is not a JSON object");
  if (typeof raw.name !== "string" || !PLUGIN_NAME.test(raw.name)) {
    throw new PluginReadError(
      "the manifest's name must be letters, digits, dots, dashes and underscores",
    );
  }
  const servers = Object.fromEntries(
    Object.entries(rawServersOf(folder, raw)).map(([name, value]) => [
      name,
      parseServer(name, value),
    ]),
  );
  const skills = skillsOf(folder, raw);
  return {
    manifest: describeManifest(raw, raw.name),
    format: found?.format ?? "inline",
    servers,
    skillsDir: skills.dir,
    skills: skills.names,
  };
}
