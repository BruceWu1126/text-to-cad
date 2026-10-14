/**
 * What plugins this environment has: the marketplaces it reads, what is
 * installed and on, and what each plugin's servers listed when they last ran.
 *
 * Everything lives under `<stateDir>/plugins`, never in `~/.claude` or
 * `~/.codex`: `installed.json` (the registry), `marketplaces/<id>` (shallow
 * clones of git marketplaces, fetched in the background when older than six
 * hours), and `cache/<market>/<plugin>/<commit>` (installed copies, one folder
 * per commit). A plugin from a folder marketplace is used in place.
 *
 * A catalog read never waits on the network: a marketplace being fetched says
 * so (`fetching`) and its plugins arrive on a later read.
 */
import {
  type InstalledPlugin,
  type PluginCatalogEntry,
  type PluginCatalogInput,
  type PluginCatalogResult,
  type PluginCatalogStatus,
  PluginOperationError,
  type PluginOperationErrorReason,
  type PluginServerStatus,
} from "@t3tools/contracts";
import {
  buildCatalog,
  type CatalogMarket,
  catalogKey,
  catalogName,
  clip,
  compatibilityOfFolder,
  DEFAULT_MARKETPLACES,
  filterCatalog,
  isOfficialCatalog,
  isRecord,
  type ListedTool,
  type ManifestFormat,
  type MarketplaceEntry,
  type MarketplaceFile,
  parseCatalogKey,
  parseGitRemote,
  type PluginServerConfig,
  readMarketplace,
  readPlugin,
  type ReadPlugin,
  readToolUi,
  slugOf,
  subfolder,
} from "@t3tools/plugins";
import * as Clock from "effect/Clock";
import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Fiber from "effect/Fiber";
import * as Layer from "effect/Layer";
import * as Option from "effect/Option";
import * as Path from "effect/Path";
import * as PubSub from "effect/PubSub";
import * as Semaphore from "effect/Semaphore";
import * as Stream from "effect/Stream";

import * as ServerConfig from "../config.ts";
import * as PluginFiles from "./pluginFiles.ts";

/** A marketplace T3 reads: a git repository it clones, or a folder used in place. */
export type MarketplaceSource =
  | { readonly type: "git"; readonly repository: string }
  | { readonly type: "folder"; readonly path: string };

export const DEFAULT_MARKETPLACE_SOURCES: ReadonlyArray<MarketplaceSource> =
  DEFAULT_MARKETPLACES.map((market) => ({ type: "git", repository: market.repository }));

/** The id the store gives a folder marketplace, from the folder's name. */
export const folderMarketplaceId = (dir: string) =>
  `local-${slugOf(
    dir
      .replace(/[\\/]+$/, "")
      .split(/[\\/]/)
      .at(-1) ?? dir,
  )}`;

const STALE_AFTER_MS = 6 * 60 * 60 * 1000;

/** One server's last listing, as recorded in the registry. */
export interface ServerListing {
  readonly state: "ready" | "error";
  readonly error: string | null;
  /** The tools as `tools/list` returned them. */
  readonly tools: ReadonlyArray<ListedTool & Readonly<Record<string, unknown>>>;
}

interface InstalledRecord {
  readonly id: string;
  readonly marketplace: string;
  readonly entry: string;
  readonly root: string;
  readonly commit: string | null;
  readonly enabled: boolean;
  readonly installedAt: number;
  readonly servers: Readonly<Record<string, ServerListing>>;
}

interface MarketRecord {
  readonly id: string;
  readonly commit: string | null;
  readonly fetchedAt: number | null;
  readonly error: string | null;
}

interface RegistryFile {
  readonly plugins: ReadonlyArray<InstalledRecord>;
  readonly marketplaces: ReadonlyArray<MarketRecord>;
}

/** An enabled plugin, as the host runs it and providers load it. */
export interface EnabledPlugin {
  readonly id: string;
  readonly root: string;
  readonly format: ManifestFormat;
  readonly servers: Readonly<Record<string, PluginServerConfig>>;
  /** Absolute directory of `<name>/SKILL.md` folders, or null. */
  readonly skillsDir: string | null;
  readonly listings: Readonly<Record<string, ServerListing>>;
}

interface Market {
  readonly id: string;
  readonly source: string;
  readonly url: string | null;
  /** The folder, or where the clone is. */
  readonly dir: string;
}

const str = (value: unknown): string | null => (typeof value === "string" ? value : null);
const num = (value: unknown): number | null => (typeof value === "number" ? value : null);

/** The registry file, tolerating anything a person or an older build left in it. */
function decodeRegistry(raw: unknown): RegistryFile {
  const file = isRecord(raw) ? raw : {};
  const plugins = Array.isArray(file.plugins)
    ? file.plugins.flatMap((item): Array<InstalledRecord> => {
        if (!isRecord(item)) return [];
        const id = str(item.id);
        const root = str(item.root);
        if (id === null || root === null) return [];
        const servers = isRecord(item.servers)
          ? Object.fromEntries(
              Object.entries(item.servers).flatMap(
                ([name, listing]): Array<[string, ServerListing]> =>
                  isRecord(listing)
                    ? [
                        [
                          name,
                          {
                            state: listing.state === "error" ? "error" : "ready",
                            error: str(listing.error),
                            tools: Array.isArray(listing.tools)
                              ? listing.tools.filter(
                                  (tool): tool is ListedTool & Record<string, unknown> =>
                                    isRecord(tool) && typeof tool.name === "string",
                                )
                              : [],
                          },
                        ],
                      ]
                    : [],
              ),
            )
          : {};
        return [
          {
            id,
            marketplace: str(item.marketplace) ?? "",
            entry: str(item.entry) ?? id,
            root,
            commit: str(item.commit),
            enabled: item.enabled !== false,
            installedAt: num(item.installedAt) ?? 0,
            servers,
          },
        ];
      })
    : [];
  const marketplaces = Array.isArray(file.marketplaces)
    ? file.marketplaces.flatMap((item): Array<MarketRecord> => {
        if (!isRecord(item) || str(item.id) === null) return [];
        return [
          {
            id: str(item.id)!,
            commit: str(item.commit),
            fetchedAt: num(item.fetchedAt),
            error: str(item.error),
          },
        ];
      })
    : [];
  return { plugins, marketplaces };
}

const fail = (reason: PluginOperationErrorReason, message: string) =>
  new PluginOperationError({ reason, message: clip(message, 400) });

const sentence = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

export class PluginStore extends Context.Service<
  PluginStore,
  {
    readonly catalog: (
      input: PluginCatalogInput,
    ) => Effect.Effect<PluginCatalogResult, PluginOperationError>;
    readonly list: Effect.Effect<ReadonlyArray<InstalledPlugin>>;
    readonly get: (id: string) => Effect.Effect<InstalledPlugin, PluginOperationError>;
    /** Install a catalog entry; answers the installed plugin's id. */
    readonly install: (key: string) => Effect.Effect<string, PluginOperationError>;
    /** Install a plugin again from its marketplace as it is now. */
    readonly update: (id: string) => Effect.Effect<string, PluginOperationError>;
    readonly uninstall: (id: string) => Effect.Effect<void, PluginOperationError>;
    readonly setEnabled: (
      id: string,
      enabled: boolean,
    ) => Effect.Effect<void, PluginOperationError>;
    readonly enabledPlugins: Effect.Effect<ReadonlyArray<EnabledPlugin>>;
    /** A server is starting to list its tools; `list` shows it as starting until its listing lands. */
    readonly markStarting: (id: string, server: string) => Effect.Effect<void>;
    readonly recordListing: (
      id: string,
      server: string,
      listing: ServerListing,
    ) => Effect.Effect<void>;
    /** Anything `list` or `enabledPlugins` shows changed. */
    readonly changes: Stream.Stream<void>;
  }
>()("t3/plugins/PluginStore") {}

const makeWith = (sources: ReadonlyArray<MarketplaceSource>) =>
  Effect.gen(function* () {
    const config = yield* ServerConfig.ServerConfig;
    const path = yield* Path.Path;
    const scope = yield* Effect.scope;
    const dataDir = path.join(config.stateDir, "plugins");
    const registryFile = path.join(dataDir, "installed.json");
    const cacheDir = path.join(dataDir, "cache");

    const markets: ReadonlyArray<Market> = sources.map((source) => {
      if (source.type === "folder") {
        return {
          id: folderMarketplaceId(source.path),
          source: source.path,
          url: null,
          dir: source.path,
        };
      }
      const remote = parseGitRemote(source.repository);
      return {
        id: remote.slug,
        source: remote.url,
        url: remote.url,
        dir: path.join(dataDir, "marketplaces", remote.slug),
      };
    });

    let registry = decodeRegistry(PluginFiles.readJsonFile(registryFile));
    const writes = yield* Semaphore.make(1);
    const changesPubSub = yield* PubSub.unbounded<void>();
    const publish = PubSub.publish(changesPubSub, undefined).pipe(Effect.asVoid);
    const starting = new Set<string>();
    const fetching = new Map<string, Fiber.Fiber<void>>();
    const updateCache = new Map<string, boolean>();
    let catalogCache: {
      readonly signature: string;
      readonly entries: Array<PluginCatalogEntry>;
    } | null = null;

    /** Change the registry and write it, one change at a time. */
    const modify = (change: (current: RegistryFile) => RegistryFile) =>
      writes.withPermit(
        Effect.sync(() => {
          registry = change(registry);
          PluginFiles.writeJsonFile(registryFile, registry);
        }),
      );

    const marketRecord = (id: string) => registry.marketplaces.find((item) => item.id === id);
    const isGit = (market: Market) => market.url !== null;
    const cloned = (market: Market) =>
      !isGit(market) ||
      (marketRecord(market.id)?.commit != null && PluginFiles.directoryExists(market.dir));

    const readMarket = (market: Market): MarketplaceFile | null => {
      if (!cloned(market)) return null;
      try {
        return readMarketplace(PluginFiles.nodeFolder(market.dir), market.id);
      } catch {
        return null;
      }
    };

    /** Clone the marketplace, or move it to its latest commit. Never fails: the error is recorded. */
    const fetchMarket = (market: Market) =>
      Effect.gen(function* () {
        if (market.url === null) return;
        const url = market.url;
        const fetched = yield* Effect.tryPromise({
          try: () =>
            PluginFiles.directoryExists(path.join(market.dir, ".git"))
              ? PluginFiles.fetchLatest(market.dir)
              : PluginFiles.shallowClone(url, market.dir),
          catch: sentence,
        }).pipe(Effect.result);
        const now = yield* Clock.currentTimeMillis;
        yield* modify((current) => {
          const previous = current.marketplaces.find((item) => item.id === market.id);
          const next: MarketRecord =
            fetched._tag === "Success"
              ? { id: market.id, commit: fetched.success, fetchedAt: now, error: null }
              : {
                  id: market.id,
                  commit: previous?.commit ?? null,
                  fetchedAt: previous?.fetchedAt ?? null,
                  error: fetched.failure,
                };
          return {
            ...current,
            marketplaces: [...current.marketplaces.filter((item) => item.id !== market.id), next],
          };
        });
        if (fetched._tag === "Failure") {
          yield* Effect.logWarning("plugin marketplace fetch failed", { marketplace: market.id });
        }
      });

    /** The marketplace's fetch: the one in flight, or a new one in the background. */
    const startFetch = (market: Market) =>
      Effect.gen(function* () {
        const existing = fetching.get(market.id);
        if (existing !== undefined) return existing;
        const fiber = yield* fetchMarket(market).pipe(
          Effect.ensuring(
            Effect.sync(() => fetching.delete(market.id)).pipe(Effect.andThen(publish)),
          ),
          Effect.forkIn(scope),
        );
        fetching.set(market.id, fiber);
        return fiber;
      });

    const awaitFetch = (market: Market) =>
      isGit(market) ? startFetch(market).pipe(Effect.flatMap(Fiber.join)) : Effect.void;

    const readInstalled = (record: InstalledRecord): ReadPlugin | Error => {
      try {
        return readPlugin(PluginFiles.nodeFolder(record.root));
      } catch (cause) {
        return cause instanceof Error ? cause : new Error(String(cause));
      }
    };

    /** Whether the marketplace now offers something newer than what is installed. */
    const updateAvailable = (record: InstalledRecord, read: ReadPlugin | Error): boolean => {
      const market = markets.find((item) => item.id === record.marketplace);
      const marketCommit = market === undefined ? null : (marketRecord(market.id)?.commit ?? null);
      if (
        market === undefined ||
        !isGit(market) ||
        record.commit === null ||
        marketCommit === null
      ) {
        return false;
      }
      const cacheKey = `${record.id}:${record.commit}:${marketCommit}`;
      const cached = updateCache.get(cacheKey);
      if (cached !== undefined) return cached;
      const entry = readMarket(market)?.plugins.find((item) => item.name === record.entry);
      let available = false;
      if (entry?.source.type === "git") {
        available = entry.source.sha !== null && entry.source.sha !== record.commit;
      } else if (entry?.source.type === "local" && marketCommit !== record.commit) {
        const latestDir = path.join(market.dir, entry.source.path);
        try {
          const latest = readPlugin(PluginFiles.nodeFolder(latestDir), entry.inline);
          const installed = read instanceof Error ? null : read.manifest.version;
          available =
            latest.manifest.version !== null && installed !== null
              ? latest.manifest.version !== installed
              : PluginFiles.fingerprint(latestDir) !== PluginFiles.fingerprint(record.root);
        } catch {
          available = false;
        }
      }
      updateCache.set(cacheKey, available);
      return available;
    };

    const toInstalled = (record: InstalledRecord): InstalledPlugin => {
      const read = readInstalled(record);
      const market = markets.find((item) => item.id === record.marketplace);
      const servers: Array<PluginServerStatus> =
        read instanceof Error
          ? []
          : Object.entries(read.servers).map(([name, server]) => {
              const listing = record.servers[name];
              return {
                name: clip(name, 128),
                transport: server.url === null ? "stdio" : "http",
                state: !record.enabled
                  ? "idle"
                  : starting.has(`${record.id}/${name}`)
                    ? "starting"
                    : (listing?.state ?? "idle"),
                error: listing?.error == null ? null : clip(listing.error, 400),
              };
            });
      const views =
        read instanceof Error || !record.enabled
          ? []
          : Object.keys(read.servers).flatMap((name) =>
              (record.servers[name]?.tools ?? []).flatMap((tool) => {
                const view = readToolUi(name, tool);
                return view === null ? [] : [view];
              }),
            );
      const manifest = read instanceof Error ? null : read.manifest;
      const serverError = servers.find((server) => server.error !== null)?.error ?? null;
      return {
        id: record.id,
        displayName: clip(manifest?.displayName ?? record.id, 160),
        description: clip(manifest?.description ?? "", 400),
        version: manifest?.version == null ? null : clip(manifest.version, 64),
        publisher: manifest?.publisher == null ? null : clip(manifest.publisher, 160),
        enabled: record.enabled,
        marketplace:
          market === undefined
            ? null
            : clip(catalogName(market.url, readMarket(market)?.displayName ?? market.id), 160),
        commit: record.commit === null ? null : clip(record.commit, 64),
        skills: (read instanceof Error ? [] : read.skills)
          .slice(0, 200)
          .map((skill) => clip(skill, 128)),
        servers: servers.slice(0, 32),
        views: views.slice(0, 64),
        compatibility: compatibilityOfFolder(PluginFiles.nodeFolder(record.root)),
        updateAvailable: updateAvailable(record, read),
        starterPrompts: (manifest?.starterPrompts ?? []).map((prompt) => clip(prompt, 300)),
        error: read instanceof Error ? clip(read.message, 400) : serverError,
      };
    };

    const find = (id: string) =>
      Effect.suspend(() => {
        const record = registry.plugins.find((item) => item.id === id);
        return record === undefined
          ? Effect.fail(fail("not_found", `no plugin "${id}" is installed`))
          : Effect.succeed(record);
      });

    const catalog = Effect.fn("PluginStore.catalog")(function* (input: PluginCatalogInput) {
      const now = yield* Clock.currentTimeMillis;
      for (const market of markets) {
        if (!isGit(market)) continue;
        const record = marketRecord(market.id);
        const stale =
          !cloned(market) || record?.fetchedAt == null || record.fetchedAt < now - STALE_AFTER_MS;
        if (input.refresh === true || stale) yield* startFetch(market);
      }
      const read = markets.map((market) => ({ market, file: readMarket(market) }));
      const statuses: Array<PluginCatalogStatus> = read.map(({ market, file }) => {
        const record = marketRecord(market.id);
        const error =
          record?.error ?? (file === null && cloned(market) ? "no marketplace found" : null);
        return {
          id: clip(market.id, 160),
          name: clip(catalogName(market.url, file?.displayName ?? market.id), 160),
          source: clip(market.source, 2048),
          state: fetching.has(market.id)
            ? "fetching"
            : error !== null && file === null
              ? "error"
              : "ready",
          pluginCount: file?.plugins.length ?? 0,
          error: error === null ? null : clip(error, 400),
        };
      });
      const installed = new Map(
        registry.plugins.map((record) => [catalogKey(record.marketplace, record.entry), record.id]),
      );
      const signature = [
        ...read.map(
          ({ market, file }) =>
            `${market.id}:${marketRecord(market.id)?.commit ?? ""}:${file !== null}`,
        ),
        ...[...installed.entries()].map(([key, id]) => `${key}=${id}`),
      ].join("|");
      if (catalogCache?.signature !== signature) {
        const catalogMarkets: Array<CatalogMarket> = read.flatMap(({ market, file }) => {
          if (file === null) return [];
          const folder = PluginFiles.nodeFolder(market.dir);
          return [
            {
              id: market.id,
              name: catalogName(market.url, file.displayName),
              url: market.url,
              official: isOfficialCatalog(market.url),
              plugins: file.plugins.map((entry) => ({
                entry,
                folder:
                  entry.source.type === "local" && folder.isDirectory(entry.source.path)
                    ? subfolder(folder, entry.source.path)
                    : null,
              })),
            },
          ];
        });
        catalogCache = {
          signature,
          entries: buildCatalog(
            catalogMarkets,
            installed,
            new Set(registry.plugins.map((record) => record.id)),
          ),
        };
      }
      const page = filterCatalog(catalogCache.entries, input.query);
      return {
        entries: page.entries,
        matchCount: page.matchCount,
        catalogs: statuses.slice(0, 32),
      };
    });

    /** Put the entry's plugin where it will run from: in place, or a cached copy at one commit. */
    const materialize = (market: Market, entry: MarketplaceEntry) =>
      Effect.tryPromise({
        try: async (): Promise<{ readonly root: string; readonly commit: string | null }> => {
          const clean = (text: string) =>
            text.replace(/[^A-Za-z0-9._-]+/g, "-").slice(0, 60) || "plugin";
          const parent = path.join(cacheDir, clean(market.id), clean(entry.name));
          if (entry.source.type === "git") {
            const incoming = path.join(parent, ".incoming");
            const commit = await PluginFiles.shallowClone(entry.source.url, incoming, {
              ref: entry.source.ref,
              sha: entry.source.sha,
            });
            const target = path.join(parent, commit.slice(0, 12));
            PluginFiles.moveFolder(incoming, target);
            const root =
              entry.source.subdir === null
                ? target
                : PluginFiles.resolveInside(target, entry.source.subdir);
            if (root === null) throw new Error(`${entry.source.subdir} is outside the repository`);
            return { root, commit };
          }
          if (entry.source.type === "local") {
            const from = path.join(market.dir, entry.source.path);
            if (!isGit(market)) return { root: from, commit: null };
            const commit = marketRecord(market.id)?.commit ?? null;
            const root = path.join(parent, (commit ?? "latest").slice(0, 12));
            PluginFiles.copyPluginFolder(from, root);
            return { root, commit };
          }
          throw new Error(entry.source.reason);
        },
        catch: (cause) => fail("install_failed", sentence(cause)),
      });

    const install = Effect.fn("PluginStore.install")(function* (key: string) {
      const parsed = parseCatalogKey(key);
      const market =
        parsed === null ? undefined : markets.find((item) => item.id === parsed.marketId);
      if (parsed === null || market === undefined) {
        return yield* fail("not_found", `no marketplace has the plugin ${key}`);
      }
      if (!cloned(market)) yield* awaitFetch(market);
      const file = readMarket(market);
      if (file === null) {
        return yield* fail("catalog_unavailable", `the ${market.id} marketplace could not be read`);
      }
      const entry = file.plugins.find((item) => item.name === parsed.name);
      if (entry === undefined)
        return yield* fail("not_found", `${file.displayName} has no plugin named "${parsed.name}"`);
      if (entry.source.type === "unsupported")
        return yield* fail("install_failed", entry.source.reason);
      const placed = yield* materialize(market, entry);
      const inline =
        entry.inline === null
          ? null
          : { name: entry.name, description: entry.description, ...entry.inline };
      const read = yield* Effect.try({
        try: () => {
          const folder = PluginFiles.nodeFolder(placed.root);
          if (inline !== null && isGit(market) && readPluginSafely(folder) === null) {
            PluginFiles.writeInlineManifest(placed.root, inline);
          }
          return readPlugin(folder, inline);
        },
        catch: (cause) => fail("manifest_invalid", sentence(cause)),
      });
      const id = read.manifest.name;
      const clash = registry.plugins.find((item) => item.id === id);
      if (clash !== undefined && (clash.marketplace !== market.id || clash.entry !== entry.name)) {
        return yield* fail(
          "install_failed",
          `a different plugin named "${id}" is already installed; uninstall it first`,
        );
      }
      const now = yield* Clock.currentTimeMillis;
      yield* modify((current) => ({
        ...current,
        plugins: [
          ...current.plugins.filter((item) => item.id !== id),
          {
            id,
            marketplace: market.id,
            entry: entry.name,
            root: placed.root,
            commit: placed.commit,
            enabled: clash?.enabled ?? true,
            installedAt: clash?.installedAt ?? now,
            servers: {},
          },
        ],
      }));
      yield* publish;
      return id;
    });

    const removeCachedCopy = (root: string) =>
      Effect.sync(() => {
        if (PluginFiles.isInside(cacheDir, root) && root !== cacheDir)
          PluginFiles.removeFolder(root);
      });

    const update = Effect.fn("PluginStore.update")(function* (id: string) {
      const record = yield* find(id);
      const market = markets.find((item) => item.id === record.marketplace);
      if (market === undefined)
        return yield* fail("not_found", `${id}'s marketplace is no longer listed`);
      yield* awaitFetch(market);
      const installedId = yield* install(catalogKey(market.id, record.entry));
      const next = registry.plugins.find((item) => item.id === installedId);
      if (next !== undefined && next.root !== record.root) yield* removeCachedCopy(record.root);
      return installedId;
    });

    const uninstall = Effect.fn("PluginStore.uninstall")(function* (id: string) {
      const record = yield* find(id);
      yield* modify((current) => ({
        ...current,
        plugins: current.plugins.filter((item) => item.id !== id),
      }));
      yield* removeCachedCopy(record.root);
      yield* publish;
    });

    const setEnabled = Effect.fn("PluginStore.setEnabled")(function* (
      id: string,
      enabled: boolean,
    ) {
      yield* find(id);
      yield* modify((current) => ({
        ...current,
        plugins: current.plugins.map((item) => (item.id === id ? { ...item, enabled } : item)),
      }));
      yield* publish;
    });

    const enabledPlugins = Effect.sync(() =>
      registry.plugins.flatMap((record): Array<EnabledPlugin> => {
        if (!record.enabled) return [];
        const read = readInstalled(record);
        if (read instanceof Error) return [];
        return [
          {
            id: record.id,
            root: record.root,
            format: read.format,
            servers: read.servers,
            skillsDir: read.skillsDir === null ? null : path.join(record.root, read.skillsDir),
            listings: record.servers,
          },
        ];
      }),
    );

    const markStarting = (id: string, server: string) =>
      Effect.sync(() => starting.add(`${id}/${server}`)).pipe(Effect.andThen(publish));

    const recordListing = (id: string, server: string, listing: ServerListing) =>
      Effect.gen(function* () {
        starting.delete(`${id}/${server}`);
        yield* modify((current) => ({
          ...current,
          plugins: current.plugins.map((item) =>
            item.id === id ? { ...item, servers: { ...item.servers, [server]: listing } } : item,
          ),
        }));
        yield* publish;
      });

    return PluginStore.of({
      catalog,
      list: Effect.sync(() => registry.plugins.map(toInstalled)),
      get: (id) => find(id).pipe(Effect.map(toInstalled)),
      install,
      update,
      uninstall,
      setEnabled,
      enabledPlugins,
      markStarting,
      recordListing,
      changes: Stream.fromPubSub(changesPubSub),
    });
  });

function readPluginSafely(folder: ReturnType<typeof PluginFiles.nodeFolder>): ReadPlugin | null {
  try {
    return readPlugin(folder);
  } catch {
    return null;
  }
}

/** The store over the given marketplaces (tests use folders and local repositories). */
export const layerWith = (sources: ReadonlyArray<MarketplaceSource>) =>
  Layer.effect(PluginStore, makeWith(sources));

export const layer = layerWith(DEFAULT_MARKETPLACE_SOURCES);

/**
 * The Agent SDK's `plugins` option for a Claude session: every enabled
 * plugin's folder, for its skills (and commands, agents, hooks). Its MCP
 * servers are skipped: agents reach them through T3's own MCP server, so the
 * CAD tab and the agent share one process.
 */
export const withClaudePlugins = <
  O extends { readonly plugins?: ReadonlyArray<unknown> | undefined },
>(
  store: Option.Option<PluginStore["Service"]>,
  options: O,
): Effect.Effect<O> =>
  Option.isNone(store)
    ? Effect.succeed(options)
    : store.value.enabledPlugins.pipe(
        Effect.map((plugins) =>
          plugins.length === 0
            ? options
            : {
                ...options,
                plugins: [
                  ...(options.plugins ?? []),
                  ...plugins.map((plugin) => ({
                    type: "local" as const,
                    path: plugin.root,
                    skipMcpDiscovery: true,
                  })),
                ],
              },
        ),
      );

/** Codex's `skills/extraRoots/set`: the skills folders of every enabled plugin. */
export const codexSkillRoots = (
  store: Option.Option<PluginStore["Service"]>,
): Effect.Effect<ReadonlyArray<string>> =>
  Option.isNone(store)
    ? Effect.succeed([])
    : store.value.enabledPlugins.pipe(
        Effect.map((plugins) =>
          plugins.flatMap((plugin) => (plugin.skillsDir === null ? [] : [plugin.skillsDir])),
        ),
      );
