import {
  WS_METHODS,
  type EnvironmentId,
  type InstalledPlugin,
  type PluginView,
} from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import { Atom, type AtomRegistry } from "effect/reactivity";

import type { EnvironmentRegistry } from "../connection/registry.ts";
import {
  createAtomCommandScheduler,
  createEnvironmentRpcCommand,
  createEnvironmentRpcQueryAtomFamily,
} from "./runtime.ts";

/**
 * Bumped after every install, enable, update and uninstall so the installed
 * list and the catalog's "Installed" marks re-read together.
 */
const pluginsRevision = Atom.family((environmentId: EnvironmentId) =>
  Atom.make(0).pipe(
    Atom.keepAlive,
    Atom.withLabel(`environment-data:plugins:revision:${environmentId}`),
  ),
);

const invalidatePlugins = (
  target: { readonly environmentId: EnvironmentId },
  registry: AtomRegistry.AtomRegistry,
) =>
  Effect.sync(() => registry.update(pluginsRevision(target.environmentId), (value) => value + 1));

/**
 * Plugin catalog, installed plugins, their lifecycle commands, and the two
 * calls a plugin view makes (open its document, then talk to its server).
 * Each client binds them to its own connection runtime.
 */
export function createPluginEnvironmentAtoms<R, E>(
  runtime: Atom.AtomRuntime<EnvironmentRegistry | R, E>,
) {
  const scheduler = createAtomCommandScheduler();
  // Lifecycle changes rewrite one environment's plugin directory; run them in order.
  const serialPerEnvironment = {
    mode: "serial",
    key: ({ environmentId }: { readonly environmentId: string }) => environmentId,
  } as const;
  const lifecycle = { scheduler, concurrency: serialPerEnvironment, onSettled: invalidatePlugins };
  return {
    catalog: createEnvironmentRpcQueryAtomFamily(runtime, {
      label: "environment-data:plugins:catalog",
      tag: WS_METHODS.pluginsCatalog,
      staleTimeMs: 5 * 60_000,
      refreshTrigger: ({ environmentId }) => pluginsRevision(environmentId),
    }),
    list: createEnvironmentRpcQueryAtomFamily(runtime, {
      label: "environment-data:plugins:list",
      tag: WS_METHODS.pluginsList,
      staleTimeMs: 30_000,
      refreshTrigger: ({ environmentId }) => pluginsRevision(environmentId),
    }),
    /** Fetches every catalog again (`refresh: true`), then re-reads the list and catalog queries. */
    refreshCatalog: createEnvironmentRpcCommand(runtime, {
      label: "environment-command:plugins:refresh-catalog",
      tag: WS_METHODS.pluginsCatalog,
      concurrency: {
        mode: "singleFlight",
        key: ({ environmentId }) => environmentId,
      },
      onSettled: invalidatePlugins,
    }),
    install: createEnvironmentRpcCommand(runtime, {
      label: "environment-command:plugins:install",
      tag: WS_METHODS.pluginsInstall,
      ...lifecycle,
    }),
    setEnabled: createEnvironmentRpcCommand(runtime, {
      label: "environment-command:plugins:set-enabled",
      tag: WS_METHODS.pluginsSetEnabled,
      ...lifecycle,
    }),
    uninstall: createEnvironmentRpcCommand(runtime, {
      label: "environment-command:plugins:uninstall",
      tag: WS_METHODS.pluginsUninstall,
      ...lifecycle,
    }),
    update: createEnvironmentRpcCommand(runtime, {
      label: "environment-command:plugins:update",
      tag: WS_METHODS.pluginsUpdate,
      ...lifecycle,
    }),
    openView: createEnvironmentRpcCommand(runtime, {
      label: "environment-command:plugins:open-view",
      tag: WS_METHODS.pluginsOpenView,
    }),
    request: createEnvironmentRpcCommand(runtime, {
      label: "environment-command:plugins:request",
      tag: WS_METHODS.pluginsRequest,
    }),
  };
}

/** A view an enabled plugin offers, with the plugin it belongs to. */
export interface PluginViewChoice {
  readonly pluginId: InstalledPlugin["id"];
  readonly pluginName: string;
  readonly view: PluginView;
}

const fileName = (path: string) =>
  path.slice(Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\")) + 1);

/** The extension of a path, lowercased and without its dot; empty when it has none. */
export function pluginFileExtension(path: string): string {
  const name = fileName(path);
  const dot = name.lastIndexOf(".");
  return dot <= 0 ? "" : name.slice(dot + 1).toLowerCase();
}

const normalizeExtension = (extension: string) => extension.trim().replace(/^\./, "").toLowerCase();

/** Every view of an enabled plugin that can open from `type`. */
export function pluginViewsWithEntrypoint(
  plugins: ReadonlyArray<InstalledPlugin>,
  type: "global" | "thread" | "file",
): PluginViewChoice[] {
  return plugins.flatMap((plugin) =>
    plugin.enabled
      ? plugin.views
          .filter((view) => view.entrypoints.some((entry) => entry.type === type))
          .map((view) => ({ pluginId: plugin.id, pluginName: plugin.displayName, view }))
      : [],
  );
}

/**
 * The views of enabled plugins that claim this file's extension, in install order.
 * A claim matches the whole end of the name, so a compound one (`.harness.yml`)
 * claims only its own files, not every `.yml`.
 */
export function pluginFileHandlers(
  plugins: ReadonlyArray<InstalledPlugin>,
  path: string,
): PluginViewChoice[] {
  const name = fileName(path).toLowerCase();
  const claims = (claimed: string) => {
    const ending = `.${normalizeExtension(claimed)}`;
    return ending.length > 1 && name.length > ending.length && name.endsWith(ending);
  };
  return pluginViewsWithEntrypoint(plugins, "file").filter(({ view }) =>
    view.entrypoints.some((entry) => entry.type === "file" && entry.extensions.some(claims)),
  );
}

/** What enabled plugins suggest asking first, in install order, without repeats. */
export function pluginStarterPrompts(
  plugins: ReadonlyArray<InstalledPlugin>,
  limit: number,
): string[] {
  const prompts = new Set<string>();
  for (const plugin of plugins) {
    if (!plugin.enabled) continue;
    for (const prompt of plugin.starterPrompts ?? []) {
      if (prompts.size >= limit) return [...prompts];
      prompts.add(prompt.trim());
    }
  }
  return [...prompts];
}
