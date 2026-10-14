/**
 * Plugins an app built on T3 Code ships with. text-to-cad Studio runs this
 * server from inside a text-to-cad checkout and sets:
 *
 * - `T3CODE_PLUGIN_MARKETPLACE`: a marketplace folder read in place, beside the
 *   default catalogs (the checkout itself, whose plugin is `./`);
 * - `T3CODE_DEFAULT_PLUGINS`: comma-separated plugin names from that folder to
 *   install and enable on first start.
 *
 * Each default is offered once (`plugins/offered.json`), so one the user
 * uninstalls stays uninstalled. Without the variables nothing changes.
 */
import { catalogKey } from "@t3tools/plugins";
import * as Config from "effect/Config";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Option from "effect/Option";
import * as Path from "effect/Path";

import * as ServerConfig from "../config.ts";
import * as PluginFiles from "./pluginFiles.ts";
import * as PluginService from "./PluginService.ts";
import * as PluginStore from "./PluginStore.ts";

const marketplaceFolder = Config.String("T3CODE_PLUGIN_MARKETPLACE").pipe(
  Config.map((value) => value.trim()),
  Config.option,
  Config.map(Option.filter((value) => value.length > 0)),
);

const defaultPluginNames = Config.String("T3CODE_DEFAULT_PLUGINS").pipe(
  Config.map((value) =>
    value
      .split(",")
      .map((name) => name.trim())
      .filter((name) => name.length > 0),
  ),
  Config.withDefault<ReadonlyArray<string>>([]),
);

/** The catalog keys of the plugins to offer, given the folder and names. */
export function defaultPluginKeys(
  folder: string,
  names: ReadonlyArray<string>,
): ReadonlyArray<string> {
  const market = PluginStore.folderMarketplaceId(folder);
  return names.map((name) => catalogKey(market, name));
}

/** The plugin store over the default catalogs plus the bundled folder, when one is set. */
export const storeLayer = Layer.unwrap(
  Effect.gen(function* () {
    const folder = yield* marketplaceFolder;
    return PluginStore.layerWith(
      Option.isNone(folder)
        ? PluginStore.DEFAULT_MARKETPLACE_SOURCES
        : [...PluginStore.DEFAULT_MARKETPLACE_SOURCES, { type: "folder", path: folder.value }],
    );
  }),
);

const readOffered = (file: string): Array<string> => {
  const raw = PluginFiles.readJsonFile(file);
  return Array.isArray(raw) ? raw.filter((key): key is string => typeof key === "string") : [];
};

/** Installs and enables each default plugin the user was not offered before. */
export const offerDefaults = Effect.gen(function* () {
  const folder = yield* marketplaceFolder;
  const names = yield* defaultPluginNames;
  if (Option.isNone(folder) || names.length === 0) return;
  const plugins = yield* PluginService.PluginService;
  const config = yield* ServerConfig.ServerConfig;
  const path = yield* Path.Path;
  const offeredFile = path.join(config.stateDir, "plugins", "offered.json");
  for (const key of defaultPluginKeys(folder.value, names)) {
    if (readOffered(offeredFile).includes(key)) continue;
    // Recorded only once installed: a failure (an unreadable checkout, a clash with the same
    // plugin from another catalog) is tried again at the next start.
    yield* plugins.install(key).pipe(
      Effect.tap((installed) =>
        installed.enabled ? Effect.void : plugins.setEnabled(installed.id, true),
      ),
      Effect.tap(() =>
        Effect.sync(() =>
          PluginFiles.writeJsonFile(offeredFile, [...readOffered(offeredFile), key]),
        ),
      ),
      Effect.catch((error) =>
        Effect.logWarning("bundled plugin was not installed", { key, error: error.message }),
      ),
    );
  }
});

/** Offers the defaults in the background, so startup never waits on a plugin's first listing. */
export const layer = Layer.effectDiscard(Effect.forkScoped(offerDefaults));
