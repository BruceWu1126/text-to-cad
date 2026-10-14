/**
 * Plugins as clients and agents use them: the catalog and lifecycle (over
 * `PluginStore`), views and their requests (over `PluginHost`, in the
 * thread's own scope with the thread's workspace as its root), and the
 * plugin tools agents call through T3's MCP server.
 *
 * Installing or enabling a plugin lists its servers' tools in the background,
 * which fills its views and server status; the call waits a few seconds for
 * that so a quick server's views are there on return, never for a slow first
 * download.
 */
import {
  type InstalledPlugin,
  type PluginCatalogInput,
  type PluginCatalogResult,
  type PluginListResult,
  PluginOperationError,
  type PluginOpenViewInput,
  type PluginRequestInput,
  type PluginRequestResult,
  type PluginViewDocument,
  type ThreadId,
} from "@t3tools/contracts";
import {
  agentToolName,
  clip,
  isAppOnly,
  isRecord,
  type ListedTool,
  readToolUi,
} from "@t3tools/plugins";
import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Fiber from "effect/Fiber";
import * as Layer from "effect/Layer";
import * as Option from "effect/Option";
import * as Path from "effect/Path";
import type * as Stream from "effect/Stream";

import * as ProjectStore from "../orchestration-v2/ProjectStore.ts";
import * as ThreadManagementService from "../orchestration-v2/ThreadManagementService.ts";
import * as PluginFiles from "./pluginFiles.ts";
import * as PluginHost from "./PluginHost.ts";
import * as PluginStore from "./PluginStore.ts";

/** A plugin tool as agents see it on T3's MCP server. */
export interface AgentTool {
  /** `<plugin>__<tool>`, see `agentToolName`. */
  readonly name: string;
  readonly pluginId: string;
  readonly server: string;
  readonly tool: ListedTool & Readonly<Record<string, unknown>>;
}

const MAX_VIEW_BYTES = 32 * 1024 * 1024;
const LISTING_WAIT = "10 seconds";

const fail = (reason: PluginOperationError["reason"], message: string) =>
  new PluginOperationError({ reason, message: clip(message, 400) });

const strings = (value: unknown) =>
  Array.isArray(value)
    ? value
        .filter((item): item is string => typeof item === "string")
        .map((item) => clip(item, 512))
    : undefined;

export class PluginService extends Context.Service<
  PluginService,
  {
    readonly catalog: (
      input: PluginCatalogInput,
    ) => Effect.Effect<PluginCatalogResult, PluginOperationError>;
    readonly list: Effect.Effect<PluginListResult>;
    readonly install: (key: string) => Effect.Effect<InstalledPlugin, PluginOperationError>;
    readonly update: (pluginId: string) => Effect.Effect<InstalledPlugin, PluginOperationError>;
    readonly uninstall: (pluginId: string) => Effect.Effect<void, PluginOperationError>;
    readonly setEnabled: (
      pluginId: string,
      enabled: boolean,
    ) => Effect.Effect<InstalledPlugin, PluginOperationError>;
    /** Call a view's tool and read its UI resource, in the thread's scope (or the environment's). */
    readonly openView: (
      input: PluginOpenViewInput,
    ) => Effect.Effect<PluginViewDocument, PluginOperationError>;
    /** A request a view makes of its own server; app-only tools are callable here. */
    readonly request: (
      input: PluginRequestInput,
    ) => Effect.Effect<PluginRequestResult, PluginOperationError>;
    /** Every enabled plugin tool an agent may call, from the servers' last listing. */
    readonly agentTools: Effect.Effect<ReadonlyArray<AgentTool>>;
    /** Call a plugin tool for an agent in its thread's scope. Answers the server's `CallToolResult`. */
    readonly callAgentTool: (input: {
      readonly threadId: ThreadId;
      readonly name: string;
      readonly arguments: unknown;
    }) => Effect.Effect<unknown, PluginOperationError>;
    /** Anything `agentTools` answers may have changed. */
    readonly changes: Stream.Stream<void>;
  }
>()("t3/plugins/PluginService") {}

const make = Effect.gen(function* () {
  const store = yield* PluginStore.PluginStore;
  const host = yield* PluginHost.PluginHost;
  const threads = yield* ThreadManagementService.ThreadManagementService;
  const projects = yield* ProjectStore.ProjectStoreV2;
  const path = yield* Path.Path;
  const scope = yield* Effect.scope;

  /** The thread's workspace: its worktree, else its project's root. */
  const workspaceOf = (threadId: ThreadId) =>
    Effect.gen(function* () {
      const records = yield* threads.getThreadRecords(threadId, []);
      if (records.thread.worktreePath !== null) return records.thread.worktreePath;
      const project = yield* projects.get(records.thread.projectId);
      if (Option.isNone(project)) return yield* Effect.fail("missing project");
      return project.value.workspaceRoot;
    }).pipe(Effect.mapError(() => fail("thread_not_found", "that thread is gone")));

  const rootsOf = (threadId: ThreadId | null) =>
    threadId === null
      ? Effect.succeed([])
      : workspaceOf(threadId).pipe(Effect.map((root) => [root]));

  const scopeOf = (threadId: ThreadId | null) => threadId ?? PluginHost.ENVIRONMENT_SCOPE;

  /** List the plugin's tools in the background, waiting a little for a quick server. */
  const relist = (pluginId: string) =>
    Effect.gen(function* () {
      yield* host.closePlugin(pluginId);
      const fiber = yield* host.listPlugin(pluginId).pipe(Effect.forkIn(scope));
      yield* Fiber.join(fiber).pipe(Effect.timeout(LISTING_WAIT), Effect.ignore);
      return yield* store.get(pluginId);
    });

  const listedTool = (pluginId: string, server: string, tool: string) =>
    store.enabledPlugins.pipe(
      Effect.map(
        (plugins) =>
          plugins
            .find((plugin) => plugin.id === pluginId)
            ?.listings[server]?.tools.find((item) => item.name === tool) ?? null,
      ),
    );

  const openView = Effect.fn("PluginService.openView")(function* (input: PluginOpenViewInput) {
    const roots = yield* rootsOf(input.threadId);
    let toolInput: Record<string, unknown> = { ...input.arguments };
    let meta: Record<string, unknown> | undefined;
    if (input.file !== undefined) {
      const root = roots[0];
      if (root === undefined) return yield* fail("request_failed", "opening a file needs a thread");
      const absolute = PluginFiles.resolveInside(root, input.file);
      if (absolute === null)
        return yield* fail("request_failed", `${input.file} is outside the thread's workspace`);
      toolInput = {
        ...toolInput,
        file: { name: path.basename(absolute), resourceUri: PluginFiles.fileUrl(absolute) },
      };
      meta = { "openai/resource": { path: absolute } };
    }
    const call = (method: PluginHost.ForwardedMethod, params: unknown) =>
      host.request({
        scope: scopeOf(input.threadId),
        pluginId: input.pluginId,
        server: input.server,
        method,
        params,
        roots,
      });
    let listed = yield* listedTool(input.pluginId, input.server, input.tool);
    if (listed === null) {
      yield* host.listPlugin(input.pluginId);
      listed = yield* listedTool(input.pluginId, input.server, input.tool);
    }
    const view = listed === null ? null : readToolUi(input.server, listed);
    if (view === null) {
      return yield* fail("not_found", `${input.pluginId} has no view called ${input.tool}`);
    }
    const toolResult = yield* call("tools/call", {
      name: input.tool,
      arguments: toolInput,
      ...(meta === undefined ? {} : { _meta: meta }),
    });
    const resource = yield* call("resources/read", { uri: view.resourceUri });
    const contents =
      isRecord(resource) && Array.isArray(resource.contents) ? resource.contents : [];
    const first: unknown = contents[0];
    if (!isRecord(first))
      return yield* fail("request_failed", `${view.resourceUri} has no content`);
    const html =
      typeof first.text === "string"
        ? first.text
        : typeof first.blob === "string"
          ? Buffer.from(first.blob, "base64").toString("utf8")
          : null;
    if (html === null)
      return yield* fail("request_failed", `${view.resourceUri} is not a document`);
    if (html.length > MAX_VIEW_BYTES)
      return yield* fail("request_failed", `${view.resourceUri} is too large to show`);
    const ui = isRecord(first._meta) && isRecord(first._meta.ui) ? first._meta.ui : {};
    const csp = isRecord(ui.csp) ? ui.csp : null;
    const connectDomains = strings(csp?.connectDomains);
    const resourceDomains = strings(csp?.resourceDomains);
    const frameDomains = strings(csp?.frameDomains);
    return {
      html,
      csp:
        csp === null
          ? null
          : {
              ...(connectDomains === undefined ? {} : { connectDomains }),
              ...(resourceDomains === undefined ? {} : { resourceDomains }),
              ...(frameDomains === undefined ? {} : { frameDomains }),
            },
      toolInput,
      toolResult,
    };
  });

  const agentTools = store.enabledPlugins.pipe(
    Effect.map((plugins) => {
      const seen = new Set<string>();
      return plugins.flatMap((plugin) =>
        Object.entries(plugin.listings).flatMap(([server, listing]) =>
          listing.tools.flatMap((tool): Array<AgentTool> => {
            if (plugin.servers[server] === undefined || isAppOnly(tool)) return [];
            const name = agentToolName(plugin.id, tool.name);
            if (seen.has(name)) return [];
            seen.add(name);
            return [{ name, pluginId: plugin.id, server, tool }];
          }),
        ),
      );
    }),
  );

  const callAgentTool: PluginService["Service"]["callAgentTool"] = (input) =>
    Effect.gen(function* () {
      const tool = (yield* agentTools).find((item) => item.name === input.name);
      if (tool === undefined)
        return yield* fail("not_found", `no enabled plugin has the tool ${input.name}`);
      const roots = yield* rootsOf(input.threadId);
      return yield* host.request({
        scope: input.threadId,
        pluginId: tool.pluginId,
        server: tool.server,
        method: "tools/call",
        params: {
          name: tool.tool.name,
          arguments: isRecord(input.arguments) ? input.arguments : {},
        },
        roots,
      });
    });

  return PluginService.of({
    catalog: store.catalog,
    list: store.list.pipe(Effect.map((plugins) => ({ plugins }))),
    install: (key) => store.install(key).pipe(Effect.flatMap(relist)),
    // Stop the servers before the update replaces the folder they run from.
    update: (pluginId) =>
      host
        .closePlugin(pluginId)
        .pipe(Effect.andThen(store.update(pluginId)), Effect.flatMap(relist)),
    uninstall: (pluginId) =>
      host.closePlugin(pluginId).pipe(Effect.andThen(store.uninstall(pluginId))),
    setEnabled: (pluginId, enabled) =>
      store
        .setEnabled(pluginId, enabled)
        .pipe(
          Effect.andThen(
            enabled
              ? relist(pluginId)
              : host.closePlugin(pluginId).pipe(Effect.andThen(store.get(pluginId))),
          ),
        ),
    openView,
    request: (input) =>
      rootsOf(input.threadId).pipe(
        Effect.flatMap((roots) =>
          host.request({
            scope: scopeOf(input.threadId),
            pluginId: input.pluginId,
            server: input.server,
            method: input.method,
            params: input.params,
            roots,
          }),
        ),
        Effect.map((result) => ({ result })),
      ),
    agentTools,
    callAgentTool,
    changes: store.changes,
  });
});

export const layer = Layer.effect(PluginService, make).pipe(Layer.provide(PluginHost.layer));
