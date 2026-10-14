/**
 * The catalog: every marketplace's entries merged, the same plugin offered by
 * several marketplaces shown once.
 *
 * Two entries are the same plugin when they come from the same repository
 * folder (a git URL plus a subfolder, or the same folder of one marketplace),
 * and then when they have the same name: Linear in Claude's marketplace and in
 * Codex's is one card. The card lists every marketplace; the first is the one
 * Install uses: the listing that works best here (`LEVEL_ORDER`), and among
 * equals a folder with a Codex manifest (where MCP App views are declared),
 * then any folder, then another repository. What the card shows is gathered
 * from every listing, the primary's first.
 *
 * Whether a plugin works here is read from its folder, without installing it
 * (`compatibilityOf`). An entry in another repository is read on install, and says so.
 */
import {
  PLUGIN_CATALOG_PAGE_LIMIT,
  type PluginCatalogEntry,
  type PluginCompatibility,
  type PluginCompatibilityLevel,
} from "@t3tools/contracts";

import { type PluginFolder, clip, isRecord } from "./folder.ts";
import { keyOf } from "./git.ts";
import {
  describeManifest,
  expandPluginRoot,
  findManifest,
  rawServersOf,
  skillsOf,
} from "./manifest.ts";
import type { MarketplaceEntry } from "./marketplace.ts";

/** The catalogs T3 Code adds by default, in the order it shows them. */
export const DEFAULT_MARKETPLACES = [
  { repository: "anthropics/claude-plugins-official", name: "Claude official" },
  { repository: "openai/plugins", name: "Codex official" },
  { repository: "earthtojake/text-to-cad", name: "text-to-cad" },
] as const;

const OFFICIAL_NAMES: ReadonlyMap<string, string> = new Map(
  DEFAULT_MARKETPLACES.map((market) => [
    keyOf(`https://github.com/${market.repository}`),
    market.name,
  ]),
);

/** The name T3 shows for a catalog: an official one by its own name, others as they call themselves. */
export function catalogName(url: string | null, displayName: string): string {
  return (url === null ? undefined : OFFICIAL_NAMES.get(keyOf(url))) ?? displayName;
}

export function isOfficialCatalog(url: string | null): boolean {
  return url !== null && OFFICIAL_NAMES.has(keyOf(url));
}

/** Plugins a catalog lists for its own testing, never meant for people. */
const HIDDEN = new Set(["fakechat"]);

export interface CatalogMarket {
  /** Stable id, the first half of a catalog key. */
  readonly id: string;
  readonly name: string;
  /** The repository a git marketplace was cloned from, or null for a folder. */
  readonly url: string | null;
  readonly official: boolean;
  /** Each entry, with its folder when it is on disk (inside the marketplace). */
  readonly plugins: ReadonlyArray<{
    readonly entry: MarketplaceEntry;
    readonly folder: PluginFolder | null;
  }>;
}

type Json = Record<string, unknown>;

const safely = <A>(read: () => A, fallback: A): A => {
  try {
    return read();
  } catch {
    return fallback;
  }
};

function manifestOf(folder: PluginFolder | null, inline: Json | null): Json | null {
  if (folder !== null) {
    const found = findManifest(folder);
    const raw =
      found === null ? undefined : safely(() => folder.readJson(found.location), undefined);
    if (isRecord(raw)) return raw;
  }
  return inline;
}

/** A command only Codex supplies: a launcher the plugin does not carry, or Codex itself. */
function codexOnly(folder: PluginFolder, server: Json): boolean {
  const command = typeof server.command === "string" ? expandPluginRoot(server.command, ".") : "";
  const args = Array.isArray(server.args)
    ? server.args.filter((arg): arg is string => typeof arg === "string")
    : [];
  if (
    /(^|\/)codex(\.exe)?$/.test(command) ||
    [command, ...args].some((part) => /\.codex\/|CODEX_HOME|codex-app/.test(part))
  ) {
    return true;
  }
  if (command.startsWith("./") || command.startsWith("../")) {
    const relative = command.replace(/^\.\//, "");
    return command.startsWith("../") || !folder.isFile(relative);
  }
  return false;
}

const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

const compatibility = (
  level: PluginCompatibilityLevel,
  label: string,
  note: string,
): PluginCompatibility => ({ level, label, notes: [clip(note, 200)] });

/**
 * Whether a plugin will work in T3 Code, read from its folder (or the entry's
 * inline manifest) without installing it. Works: MCP servers that run here and
 * skills. May need sign-in: a remote server. Partly: parts T3 ignores (language
 * servers, ChatGPT apps). Hooks, slash commands, subagents and output styles load
 * for Claude only, through the Agent SDK's `plugins` option, so they are noted.
 * Needs Codex: nothing but launchers Codex supplies.
 */
export function compatibilityOf(
  entry: Pick<MarketplaceEntry, "source" | "inline">,
  folder: PluginFolder | null,
): PluginCompatibility {
  if (entry.source.type === "unsupported") {
    return compatibility("unavailable", "Can't install", entry.source.reason);
  }
  if (folder === null && entry.inline === null) {
    const host =
      entry.source.type === "git"
        ? keyOf(entry.source.url)
            .replace(/^https:\/\//, "")
            .split("/")[0]
        : undefined;
    return compatibility(
      "unknown",
      "Checked on install",
      host
        ? `T3 Code reads this plugin from ${host} when you install it`
        : "T3 Code reads this plugin when you install it",
    );
  }
  const manifest = manifestOf(folder, entry.inline) ?? {};
  const servers = safely(() => rawServersOf(folder, manifest), {});
  const skills = folder === null ? 0 : safely(() => skillsOf(folder, manifest).names.length, 0);

  const remote: Array<string> = [];
  const codex: Array<string> = [];
  let local = 0;
  for (const [name, server] of Object.entries(servers)) {
    if (!isRecord(server)) continue;
    if (typeof server.url === "string") {
      remote.push(safely(() => new URL(server.url as string).host, name));
    } else if (folder !== null && codexOnly(folder, server)) codex.push(name);
    else if (typeof server.command === "string") local += 1;
  }
  const ignored: Array<string> = [];
  const claudeOnly: Array<string> = [];
  const has = (key: string, dir?: string, file?: string) =>
    manifest[key] !== undefined ||
    (folder !== null &&
      ((dir !== undefined && folder.isDirectory(dir)) ||
        (file !== undefined && folder.isFile(file))));
  if (has("hooks", undefined, "hooks/hooks.json") || folder?.isFile("hooks.json"))
    claudeOnly.push("hooks");
  if (has("commands", "commands")) claudeOnly.push("slash commands");
  if (has("agents", "agents")) claudeOnly.push("subagents");
  if (manifest.lspServers !== undefined) ignored.push("a language server");
  if (has("outputStyles", "output-styles")) claudeOnly.push("output styles");
  const appsFile = folder?.isFile(".app.json")
    ? safely(() => folder.readJson(".app.json"), undefined)
    : undefined;
  const apps = isRecord(appsFile) && isRecord(appsFile.apps) ? Object.keys(appsFile.apps) : [];
  if (apps.length > 0) ignored.push("ChatGPT apps");

  const usable = local + remote.length + skills;
  const claudeNote =
    claudeOnly.length > 0 ? `; its ${claudeOnly.join(", ")} run with Claude only` : "";
  const verb = usable === 1 ? "works" : "work";
  const hosts = [...new Set(remote)].join(", ");
  const parts = [
    local > 0 ? plural(local, "MCP server") : null,
    remote.length > 0 ? `a server at ${hosts}` : null,
    skills > 0 ? plural(skills, "skill") : null,
  ]
    .filter(Boolean)
    .join(", ");
  if (codex.length > 0 && usable === 0) {
    return compatibility(
      "codex",
      "Needs Codex",
      `its ${codex.join(", ")} server runs a launcher only Codex supplies`,
    );
  }
  if (ignored.length > 0 || codex.length > 0) {
    const skipped = [
      ...ignored,
      ...(codex.length > 0 ? [`the ${codex.join(", ")} server (Codex only)`] : []),
    ];
    if (usable === 0) {
      const chatgpt = apps.length > 0 && ignored.length === 1;
      return compatibility(
        chatgpt ? "codex" : "partly",
        chatgpt ? "Needs ChatGPT" : "Partly",
        `T3 Code does not run its ${skipped.join(", ")}`,
      );
    }
    return compatibility(
      "partly",
      "Partly",
      `${parts} ${verb} here; T3 Code does not run its ${skipped.join(", ")}${claudeNote}`,
    );
  }
  if (remote.length > 0) {
    return compatibility(
      "signin",
      "May need sign-in",
      `${parts}; you sign in to ${hosts} if it asks${claudeNote}`,
    );
  }
  if (usable === 0 && claudeOnly.length > 0)
    return compatibility(
      "partly",
      "Claude only",
      `its ${claudeOnly.join(", ")} run with Claude only`,
    );
  if (usable === 0)
    return compatibility("partly", "Partly", "it has no MCP servers or skills T3 Code can use");
  return compatibility("works", "Works", `${parts}${claudeNote}`);
}

/** Works first, then what works with a sign-in or in part, then what is read on install, then what will not run. */
const LEVEL_ORDER: Record<PluginCompatibilityLevel, number> = {
  works: 0,
  signin: 1,
  partly: 2,
  unknown: 3,
  codex: 4,
  unavailable: 5,
};

/** A repository page, not a product's site. */
const isRepoPage = (url: string) =>
  /^https?:\/\/(www\.)?(github|gitlab)\.com\/[^/]+\/[^/]+/i.test(url);

const first = <T>(values: ReadonlyArray<T | null | undefined>): T | null =>
  values.find((value): value is T => value !== null && value !== undefined && value !== "") ?? null;

interface Listing {
  readonly market: CatalogMarket;
  readonly entry: MarketplaceEntry;
  readonly folder: PluginFolder | null;
  readonly compatibility: PluginCompatibility;
  readonly rank: number;
}

function sourceKey(market: CatalogMarket, entry: MarketplaceEntry): string {
  if (entry.source.type === "git") return `${keyOf(entry.source.url)}#${entry.source.subdir ?? ""}`;
  if (entry.source.type === "local") {
    return market.url === null
      ? `${market.id}#${entry.source.path}`
      : `${keyOf(market.url)}#${entry.source.path}`;
  }
  return `${market.id}#${entry.name}`;
}

/** The catalog key Install takes: `<marketplace id>/<plugin name>`. */
export const catalogKey = (marketId: string, name: string) => `${marketId}/${name}`;

/** A catalog key's halves, or null when it is not one. */
export function parseCatalogKey(
  key: string,
): { readonly marketId: string; readonly name: string } | null {
  const slash = key.lastIndexOf("/");
  if (slash <= 0 || slash === key.length - 1) return null;
  return { marketId: key.slice(0, slash), name: key.slice(slash + 1) };
}

/**
 * The merged list. `installed` maps catalog keys (`<market>/<entry>`) to the
 * installed plugin's id; `installedIds` catches a plugin installed under its own name.
 */
export function buildCatalog(
  markets: ReadonlyArray<CatalogMarket>,
  installed: ReadonlyMap<string, string>,
  installedIds: ReadonlySet<string>,
): Array<PluginCatalogEntry> {
  type Group = { names: Set<string>; items: Array<Listing> };
  const bySource = new Map<string, Group>();
  for (const market of markets) {
    for (const { entry, folder } of market.plugins) {
      if (HIDDEN.has(entry.name.toLowerCase())) continue;
      const key = sourceKey(market, entry);
      const group = bySource.get(key) ?? { names: new Set<string>(), items: [] };
      const codexManifest = folder?.isFile(".codex-plugin/plugin.json") ?? false;
      group.items.push({
        market,
        entry,
        folder,
        compatibility: compatibilityOf(entry, folder),
        rank: codexManifest ? 0 : folder !== null ? 1 : 2,
      });
      group.names.add(entry.name.toLowerCase());
      bySource.set(key, group);
    }
  }
  // Then by name: the same plugin listed by two marketplaces from two folders.
  const byName = new Map<string, Group>();
  const merged: Array<Group> = [];
  for (const group of bySource.values()) {
    const existing = [...group.names].map((name) => byName.get(name)).find(Boolean);
    if (existing) {
      existing.items.push(...group.items);
      for (const name of group.names) {
        existing.names.add(name);
        byName.set(name, existing);
      }
    } else {
      merged.push(group);
      for (const name of group.names) byName.set(name, group);
    }
  }
  return merged
    .map((group): PluginCatalogEntry => {
      const items = group.items.toSorted(
        (a, b) =>
          LEVEL_ORDER[a.compatibility.level] - LEVEL_ORDER[b.compatibility.level] ||
          a.rank - b.rank,
      );
      const primary = items[0]!;
      const faces = items.map((item) => {
        const manifest = manifestOf(item.folder, item.entry.inline);
        return manifest === null ? null : describeManifest(manifest, item.entry.name);
      });
      const homepages = items.map((item) => item.entry.homepage);
      const sites = [
        ...faces
          .map((face) => face?.homepage ?? null)
          .filter((url) => url !== null && !isRepoPage(url)),
        ...homepages.filter((url) => url !== null && !isRepoPage(url)),
        ...homepages,
      ];
      const keys = items.map((item) => catalogKey(item.market.id, item.entry.name));
      const installedId =
        first(keys.map((key) => installed.get(key))) ??
        (installedIds.has(primary.entry.name) ? primary.entry.name : null);
      const marketplaces = items
        .map((item) => ({
          id: item.market.id,
          name: clip(item.market.name, 160),
          official: item.market.official,
        }))
        .filter((ref, index, all) => all.findIndex((other) => other.id === ref.id) === index)
        .slice(0, 16);
      const category = first(items.map((item) => item.entry.category));
      const publisher = first(faces.map((face) => face?.publisher));
      const version = first([
        primary.entry.version,
        ...faces.map((face) => face?.version),
        ...items.map((item) => item.entry.version),
      ]);
      const homepage = first(sites);
      return {
        key: keys[0]!,
        name: clip(primary.entry.name, 128),
        displayName: clip(
          first(
            faces.map((face) => (face?.displayName === face?.name ? null : face?.displayName)),
          ) ?? primary.entry.name,
          160,
        ),
        description: clip(
          first([
            primary.entry.description,
            faces[0]?.description,
            ...items.map((item) => item.entry.description),
            ...faces.map((face) => face?.description),
          ]) ?? "",
          400,
        ),
        category: category === null ? null : clip(category, 80),
        publisher: publisher === null ? null : clip(publisher, 160),
        homepage: homepage === null ? null : clip(homepage, 2048),
        version: version === null ? null : clip(version, 64),
        marketplaces,
        verified: items.some((item) => item.market.official),
        compatibility: primary.compatibility,
        installedId,
      };
    })
    .toSorted(
      (a, b) =>
        LEVEL_ORDER[a.compatibility.level] - LEVEL_ORDER[b.compatibility.level] ||
        a.displayName.localeCompare(b.displayName, undefined, { sensitivity: "base" }),
    );
}

/** The entries matching a search, at most one page of them, and how many matched. */
export function filterCatalog(
  entries: ReadonlyArray<PluginCatalogEntry>,
  query: string,
): { readonly entries: ReadonlyArray<PluginCatalogEntry>; readonly matchCount: number } {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  const matches =
    words.length === 0
      ? entries
      : entries.filter((entry) => {
          const text = [
            entry.name,
            entry.displayName,
            entry.description,
            entry.publisher ?? "",
            entry.category ?? "",
          ]
            .join(" ")
            .toLowerCase();
          return words.every((word) => text.includes(word));
        });
  return { entries: matches.slice(0, PLUGIN_CATALOG_PAGE_LIMIT), matchCount: matches.length };
}

/** Exposed for the installed list: a plugin's compatibility from its own folder. */
export const compatibilityOfFolder = (folder: PluginFolder): PluginCompatibility =>
  compatibilityOf({ source: { type: "local", path: "" }, inline: null }, folder);
