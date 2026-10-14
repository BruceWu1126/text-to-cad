import { describe, expect, it } from "@effect/vitest";
import {
  AuthOrchestrationOperateScope,
  AuthProvidersManageScope,
  EnvironmentId,
  WS_METHODS,
  type AuthEnvironmentScope,
  type AuthSessionState,
  type InstalledPlugin,
  type PluginView,
} from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import { Atom, AtomRegistry, AsyncResult } from "effect/reactivity";
import { vi } from "vite-plus/test";

import { EnvironmentRegistry } from "../connection/registry.ts";
import { createCommandPermissions } from "./commandPermissions.ts";
import {
  pluginFileExtension,
  pluginFileHandlers,
  pluginStarterPrompts,
  pluginViewsWithEntrypoint,
} from "./plugins.ts";

vi.mock("./session.ts", () => ({
  createEnvironmentSessionAtoms: () => ({ sessionStateAtom: sessions }),
}));
const sessions = Atom.family((_id: EnvironmentId) =>
  Atom.make<AsyncResult.AsyncResult<AuthSessionState, string>>(AsyncResult.initial()),
);

const view = (overrides: Partial<PluginView>): PluginView => ({
  server: "cad",
  tool: "open_cad",
  title: "CAD",
  resourceUri: "ui://cad/index.html",
  entrypoints: [],
  ...overrides,
});

const plugin = (overrides: Partial<InstalledPlugin>): InstalledPlugin => ({
  id: "text-to-cad",
  displayName: "text-to-cad",
  description: "",
  version: null,
  publisher: null,
  enabled: true,
  marketplace: null,
  commit: null,
  skills: [],
  servers: [],
  views: [],
  compatibility: { level: "works", label: "Works", notes: [] },
  updateAvailable: false,
  error: null,
  ...overrides,
});

describe("plugin view matching", () => {
  it("reads the extension of the last path segment only", () => {
    expect(pluginFileExtension("models/bracket.STEP")).toBe("step");
    expect(pluginFileExtension("dir.v2/README")).toBe("");
    expect(pluginFileExtension(".gitignore")).toBe("");
    expect(pluginFileExtension("C:\\cad\\part.stp")).toBe("stp");
  });

  it("matches claimed extensions with or without a dot, in any case", () => {
    const cad = view({ entrypoints: [{ type: "file", extensions: [".STEP", "stp"] }] });
    const plugins = [plugin({ views: [cad] })];
    expect(pluginFileHandlers(plugins, "a/part.step").map((choice) => choice.view)).toEqual([cad]);
    expect(pluginFileHandlers(plugins, "a/part.STP")).toHaveLength(1);
    expect(pluginFileHandlers(plugins, "a/part.stl")).toEqual([]);
    expect(pluginFileHandlers(plugins, "a/step")).toEqual([]);
  });

  it("matches a compound extension by the name's whole ending", () => {
    // text-to-cad's WireViz harnesses: `.harness.yml`, not every `.yml`.
    const cad = view({
      entrypoints: [{ type: "file", extensions: [".kicad_pcb", ".harness.yml"] }],
    });
    const plugins = [plugin({ views: [cad] })];
    expect(pluginFileHandlers(plugins, "hat/hat.kicad_pcb")).toHaveLength(1);
    expect(pluginFileHandlers(plugins, "loom/main.Harness.YML")).toHaveLength(1);
    expect(pluginFileHandlers(plugins, "ci/workflow.yml")).toEqual([]);
    expect(pluginFileHandlers(plugins, "loom/.harness.yml")).toEqual([]);
  });

  it("leaves out disabled plugins and other entrypoints", () => {
    const fileView = view({ entrypoints: [{ type: "file", extensions: ["step"] }] });
    const threadView = view({ tool: "workbench", entrypoints: [{ type: "thread" }] });
    const plugins = [
      plugin({ id: "off", enabled: false, views: [fileView] }),
      plugin({ id: "on", views: [fileView, threadView] }),
    ];
    expect(pluginFileHandlers(plugins, "x.step").map((choice) => choice.pluginId)).toEqual(["on"]);
    expect(pluginViewsWithEntrypoint(plugins, "thread").map((choice) => choice.view.tool)).toEqual([
      "workbench",
    ]);
    expect(pluginViewsWithEntrypoint(plugins, "global")).toEqual([]);
  });
});

describe("pluginStarterPrompts", () => {
  it("takes enabled plugins' prompts in order, once each, up to the limit", () => {
    const plugins = [
      plugin({ id: "off", enabled: false, starterPrompts: ["Hidden"] }),
      plugin({ id: "cad", starterPrompts: ["Design a bracket", "Check my part"] }),
      plugin({ id: "old" }),
      plugin({ id: "pcb", starterPrompts: ["Design a bracket", "Route my board"] }),
    ];
    expect(pluginStarterPrompts(plugins, 3)).toEqual([
      "Design a bracket",
      "Check my part",
      "Route my board",
    ]);
    expect(pluginStarterPrompts(plugins, 1)).toEqual(["Design a bracket"]);
  });
});

describe("plugin command permissions", () => {
  const env = EnvironmentId.make("target");
  const runtime = Atom.runtime(
    Layer.succeed(EnvironmentRegistry, {
      run: (_id: EnvironmentId, effect: Effect.Effect<unknown>) => effect,
    } as unknown as EnvironmentRegistry["Service"]),
  );
  const grant = (scope: AuthEnvironmentScope): AuthSessionState => ({
    authenticated: true,
    auth: {
      policy: "remote-reachable",
      bootstrapMethods: [],
      sessionMethods: [],
      sessionCookieName: "test",
    },
    scopes: [scope],
    permissions: [scope],
  });

  it.effect("installs, toggles, updates and uninstalls only with the provider grant", () =>
    Effect.scoped(
      Effect.gen(function* () {
        const registry = AtomRegistry.make();
        registry.mount(sessions(env));
        yield* Effect.addFinalizer(() => Effect.sync(() => registry.dispose()));
        const guarded = [
          WS_METHODS.pluginsInstall,
          WS_METHODS.pluginsSetEnabled,
          WS_METHODS.pluginsUpdate,
          WS_METHODS.pluginsUninstall,
        ].map((tag) => createCommandPermissions(runtime, tag));
        registry.set(sessions(env), AsyncResult.success(grant(AuthOrchestrationOperateScope)));
        for (const permissions of guarded) {
          expect(registry.get(permissions.permissionAtom(env))).toBe(false);
          expect((yield* permissions.authorize(registry, env).pipe(Effect.flip))._tag).toBe(
            "EnvironmentAuthorizationError",
          );
        }
        registry.set(sessions(env), AsyncResult.success(grant(AuthProvidersManageScope)));
        for (const permissions of guarded) {
          expect(registry.get(permissions.permissionAtom(env))).toBe(true);
        }
      }),
    ),
  );
});
