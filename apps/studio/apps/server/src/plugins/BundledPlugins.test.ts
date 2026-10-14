// @effect-diagnostics nodeBuiltinImport:off -- fixtures are plugin folders on disk.
import * as NodeFS from "node:fs";
import * as NodeOS from "node:os";
import * as NodePath from "node:path";

import * as NodeServices from "@effect/platform-node/NodeServices";
import { assert, describe, it } from "@effect/vitest";
import * as ConfigProvider from "effect/ConfigProvider";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Option from "effect/Option";

import * as ServerConfig from "../config.ts";
import * as ProjectStore from "../orchestration-v2/ProjectStore.ts";
import * as ThreadManagementService from "../orchestration-v2/ThreadManagementService.ts";
import * as BundledPlugins from "./BundledPlugins.ts";
import * as PluginService from "./PluginService.ts";

function write(file: string, content: object) {
  NodeFS.mkdirSync(NodePath.dirname(file), { recursive: true });
  NodeFS.writeFileSync(file, JSON.stringify(content, null, 2));
}

/** A checkout shaped like text-to-cad: the repository is its own marketplace and plugin (`./`). */
function checkout() {
  const dir = NodeFS.realpathSync(
    NodeFS.mkdtempSync(NodePath.join(NodeOS.tmpdir(), "t3-bundled-")),
  );
  const repo = NodePath.join(dir, "text-to-cad");
  write(NodePath.join(repo, ".claude-plugin", "marketplace.json"), {
    name: "earthtojake",
    plugins: [{ name: "text-to-cad", source: "./", description: "Design 3D models" }],
  });
  write(NodePath.join(repo, ".codex-plugin", "plugin.json"), {
    name: "text-to-cad",
    version: "1.0.0",
    skills: "./skills/",
  });
  NodeFS.mkdirSync(NodePath.join(repo, "skills", "cad"), { recursive: true });
  NodeFS.writeFileSync(NodePath.join(repo, "skills", "cad", "SKILL.md"), "---\nname: cad\n---\n");
  return { dir, repo };
}

const layerThreads = Layer.mergeAll(
  Layer.succeed(
    ThreadManagementService.ThreadManagementService,
    ThreadManagementService.ThreadManagementService.of(
      {} as unknown as ThreadManagementService.ThreadManagementService["Service"],
    ),
  ),
  Layer.succeed(
    ProjectStore.ProjectStoreV2,
    ProjectStore.ProjectStoreV2.of({
      get: () => Effect.succeed(Option.none()),
    } as unknown as ProjectStore.ProjectStoreV2["Service"]),
  ),
);

const layerFor = (env: Record<string, string>, base: string) => {
  const config = ConfigProvider.layer(ConfigProvider.fromEnv({ env }));
  return PluginService.layer.pipe(
    Layer.provideMerge(BundledPlugins.storeLayer.pipe(Layer.provide(config))),
    Layer.provide(layerThreads),
    Layer.provideMerge(ServerConfig.layerTest(process.cwd(), base)),
    Layer.provideMerge(config),
  );
};

describe("bundled plugins", () => {
  it.layer(NodeServices.layer)((it) => {
    it.effect("installs a checkout's plugin once, and an uninstall sticks", () => {
      const { dir, repo } = checkout();
      const base = NodePath.join(dir, "home");
      const env = {
        T3CODE_PLUGIN_MARKETPLACE: repo,
        T3CODE_DEFAULT_PLUGINS: "text-to-cad, missing",
      };
      const listIds = Effect.gen(function* () {
        const plugins = yield* PluginService.PluginService;
        return (yield* plugins.list).plugins.map((plugin) => [plugin.id, plugin.enabled]);
      });
      return Effect.gen(function* () {
        assert.deepEqual(BundledPlugins.defaultPluginKeys(repo, ["text-to-cad"]), [
          "local-text-to-cad/text-to-cad",
        ]);

        // First start: installed and on.
        yield* Effect.gen(function* () {
          yield* BundledPlugins.offerDefaults;
          assert.deepEqual(yield* listIds, [["text-to-cad", true]]);
          // A plugin that failed to install is not recorded, so the next start tries again.
          assert.deepEqual(
            JSON.parse(
              NodeFS.readFileSync(
                NodePath.join(
                  (yield* ServerConfig.ServerConfig).stateDir,
                  "plugins",
                  "offered.json",
                ),
                "utf8",
              ),
            ),
            ["local-text-to-cad/text-to-cad"],
          );
          const plugins = yield* PluginService.PluginService;
          yield* plugins.uninstall("text-to-cad");
        }).pipe(Effect.provide(layerFor(env, base)));

        // Next start: the user's uninstall stands.
        yield* Effect.gen(function* () {
          yield* BundledPlugins.offerDefaults;
          assert.deepEqual(yield* listIds, []);
        }).pipe(Effect.provide(layerFor(env, base)));

        // Without the variables nothing is offered.
        yield* Effect.gen(function* () {
          yield* BundledPlugins.offerDefaults;
          assert.deepEqual(yield* listIds, []);
        }).pipe(Effect.provide(layerFor({}, NodePath.join(dir, "other-home"))));
      }).pipe(
        Effect.ensuring(Effect.sync(() => NodeFS.rmSync(dir, { recursive: true, force: true }))),
      );
    });
  });
});
