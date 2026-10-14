/**
 * Marketplaces: Codex's `.agents/plugins/marketplace.json` and Claude Code's
 * `.claude-plugin/marketplace.json` (or a bare `marketplace.json`) at the root
 * of a folder or repository. Each entry's `source` says where the plugin is:
 * a folder inside the marketplace (`"./plugins/x"`, `{source: "local"}`), or
 * another git repository (`github`, `url`/`git`, `git-subdir`). npm and pip
 * sources are listed with why they cannot be installed.
 */
import {
  type PluginFolder,
  PluginReadError,
  insideRelative,
  isRecord,
  nonEmptyString,
} from "./folder.ts";

export const MARKETPLACE_LOCATIONS = [
  ".agents/plugins/marketplace.json",
  ".claude-plugin/marketplace.json",
  "marketplace.json",
] as const;

export type MarketplaceSource =
  /** A folder inside the marketplace, relative to its root ("" is the root itself). */
  | { readonly type: "local"; readonly path: string }
  | {
      readonly type: "git";
      readonly url: string;
      readonly ref: string | null;
      readonly sha: string | null;
      /** The plugin's folder inside that repository, or null for its root. */
      readonly subdir: string | null;
    }
  | { readonly type: "unsupported"; readonly reason: string };

export interface MarketplaceEntry {
  readonly name: string;
  readonly description: string;
  readonly category: string | null;
  readonly version: string | null;
  readonly homepage: string | null;
  readonly source: MarketplaceSource;
  /** The entry itself, as the manifest of a folder that has none (`"strict": false`), or null. */
  readonly inline: Readonly<Record<string, unknown>> | null;
}

export interface MarketplaceFile {
  readonly location: string;
  readonly name: string;
  readonly displayName: string;
  readonly plugins: ReadonlyArray<MarketplaceEntry>;
}

const githubUrl = (repo: string | null) =>
  repo === null ? null : `https://github.com/${repo}.git`;

/** One entry's `source`, as Codex and Claude Code write it; null when it is not one. */
export function parseSource(source: unknown): MarketplaceSource | null {
  if (typeof source === "string") {
    const path = insideRelative(source);
    return path === null ? null : { type: "local", path };
  }
  if (!isRecord(source)) return null;
  const kind = nonEmptyString(source.source);
  if (kind === "local") {
    const relative = nonEmptyString(source.path);
    const path = relative === null ? null : insideRelative(relative);
    return path === null ? null : { type: "local", path };
  }
  const git = (url: string | null, subdir: string | null): MarketplaceSource | null => {
    if (url === null) return null;
    const ref = nonEmptyString(source.ref);
    const sha = nonEmptyString(source.sha);
    // git would read a leading dash as one of its own options.
    if ([url, ref, sha].some((value) => value?.startsWith("-"))) {
      return { type: "unsupported", reason: "its git source names a URL or ref git cannot take" };
    }
    return { type: "git", url, ref, sha, subdir };
  };
  if (kind === "url" || kind === "git") return git(nonEmptyString(source.url), null);
  if (kind === "github") return git(githubUrl(nonEmptyString(source.repo)), null);
  if (kind === "git-subdir") {
    const relative = nonEmptyString(source.path);
    const subdir = relative === null ? null : insideRelative(relative);
    if (relative !== null && subdir === null) return null;
    return git(
      nonEmptyString(source.url) ?? githubUrl(nonEmptyString(source.repo)),
      subdir || null,
    );
  }
  return {
    type: "unsupported",
    reason: `plugins from a "${kind ?? "unknown"}" source cannot be installed here yet`,
  };
}

/** Read the marketplace at the root of `folder`. Throws `PluginReadError` when there is none. */
export function readMarketplace(folder: PluginFolder, fallbackName: string): MarketplaceFile {
  const location = MARKETPLACE_LOCATIONS.find((candidate) => folder.isFile(candidate));
  if (location === undefined) {
    throw new PluginReadError(`no marketplace (looked for ${MARKETPLACE_LOCATIONS.join(", ")})`);
  }
  const raw = folder.readJson(location);
  if (!isRecord(raw) || !Array.isArray(raw.plugins)) {
    throw new PluginReadError(`${location} lists no plugins`);
  }
  const plugins = raw.plugins.flatMap((item: unknown): Array<MarketplaceEntry> => {
    if (!isRecord(item) || typeof item.name !== "string") return [];
    const source = parseSource(item.source);
    if (source === null) return [];
    const { source: _source, strict, ...rest } = item;
    return [
      {
        name: item.name,
        description: nonEmptyString(item.description) ?? "",
        category: nonEmptyString(item.category),
        version: nonEmptyString(item.version),
        homepage: nonEmptyString(item.homepage),
        source,
        inline: strict === false ? rest : null,
      },
    ];
  });
  const name = nonEmptyString(raw.name) ?? fallbackName;
  const face = isRecord(raw.interface) ? raw.interface : {};
  return { location, name, displayName: nonEmptyString(face.displayName) ?? name, plugins };
}
