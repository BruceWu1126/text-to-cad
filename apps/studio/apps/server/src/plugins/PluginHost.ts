/**
 * T3 as an MCP host for plugin servers: one client per (scope, plugin,
 * server). A thread's scope is its id, so each thread has its own server
 * processes the way a Codex thread does; the `environment` scope serves views
 * opened outside a thread and the tool listing. Agents never start a plugin's
 * server themselves: T3's MCP server forwards their calls here, so an agent's
 * `cad_file` and the CAD tab's requests reach the same process.
 *
 * The client declares MCP Apps (`io.modelcontextprotocol/ui`) and the three
 * Codex UI entrypoints (`dev.texttocad/tabs`), and answers `roots/list` with
 * the thread's workspace. Codex starts a plugin's servers in the plugin's
 * folder (its launchers are relative); Claude Code starts them in the project
 * and plugins reach their files through `${CLAUDE_PLUGIN_ROOT}`.
 */
import { PluginOperationError, type PluginRequestInput } from "@t3tools/contracts";
import {
  expandPluginRoot,
  MCP_APP_MIME,
  MCP_UI_EXTENSION,
  type PluginServerConfig,
  UI_ENTRYPOINTS,
  UI_ENTRYPOINTS_EXTENSION,
} from "@t3tools/plugins";
import * as HostProcess from "@t3tools/shared/HostProcess";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StdioClientTransport } from "@modelcontextprotocol/sdk/client/stdio.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";
import {
  CallToolResultSchema,
  GetPromptResultSchema,
  ListPromptsResultSchema,
  ListResourcesResultSchema,
  ListResourceTemplatesResultSchema,
  ListRootsRequestSchema,
  ListToolsResultSchema,
  ReadResourceResultSchema,
} from "@modelcontextprotocol/sdk/types.js";
import * as Clock from "effect/Clock";
import * as Context from "effect/Context";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Path from "effect/Path";
import * as Schedule from "effect/Schedule";

import * as PluginFiles from "./pluginFiles.ts";
import * as PluginStore from "./PluginStore.ts";

export type ForwardedMethod = PluginRequestInput["method"];

const RESULT_SCHEMAS = {
  "tools/list": ListToolsResultSchema,
  "tools/call": CallToolResultSchema,
  "resources/list": ListResourcesResultSchema,
  "resources/templates/list": ListResourceTemplatesResultSchema,
  "resources/read": ReadResourceResultSchema,
  "prompts/list": ListPromptsResultSchema,
  "prompts/get": GetPromptResultSchema,
} as const;

/** A list the server has no capability for answers empty instead of failing. */
const EMPTY_LISTS: Partial<Record<ForwardedMethod, unknown>> = {
  "tools/list": { tools: [] },
  "resources/list": { resources: [] },
  "resources/templates/list": { resourceTemplates: [] },
  "prompts/list": { prompts: [] },
};

/** The scope of views opened outside a thread, and of the tool listing. */
export const ENVIRONMENT_SCOPE = "environment";

const DEFAULT_STARTUP_SECONDS = 30;
/** A server a package runner starts downloads on its first run, which takes longer. */
const RUNNER_STARTUP_SECONDS = 180;
const PACKAGE_RUNNERS = /(^|[\\/])(npx|uvx|bunx|pnpx|pipx|uv|pnpm|bun|yarn)(\.cmd|\.exe)?$/;
const DEFAULT_TOOL_SECONDS = 120;
const STDERR_LINES = 20;
/** A thread's processes close after this long without a request. */
const THREAD_IDLE_MS = 20 * 60 * 1000;

interface Connection {
  readonly client: Client;
  readonly signature: string;
  readonly stderr: Array<string>;
  roots: ReadonlyArray<string>;
  closed: boolean;
  lastUsed: number;
}

const sentence = (cause: unknown) => (cause instanceof Error ? cause.message : String(cause));

const requestFailed = (reason: "server_failed" | "request_failed", message: string) =>
  new PluginOperationError({ reason, message: message.slice(0, 400) });

/**
 * The live entry at `key`, or one `start`ed in place of a missing, failed or
 * stale one (closed first). The replacement is registered before anything is
 * awaited, so callers racing on one stale entry share a single restart instead
 * of each starting a process and orphaning all but the last.
 */
export async function liveOrRestart<T>(
  entries: Map<string, Promise<T>>,
  key: string,
  isLive: (entry: T) => boolean,
  close: (stale: T) => Promise<void>,
  start: () => Promise<T>,
): Promise<T> {
  let stale: T | null = null;
  for (;;) {
    const pending = entries.get(key);
    if (pending === undefined) break;
    const existing = await pending.catch(() => null);
    // Another caller replaced it while this one waited: judge theirs instead.
    if (entries.get(key) !== pending) continue;
    if (existing !== null && isLive(existing)) return existing;
    stale = existing;
    break;
  }
  const next = (async () => {
    if (stale !== null) await close(stale).catch(() => undefined);
    return start();
  })();
  entries.set(key, next);
  next.catch(() => {
    if (entries.get(key) === next) entries.delete(key);
  });
  return next;
}

export class PluginHost extends Context.Service<
  PluginHost,
  {
    /** One MCP request in a scope, as the server answered it. Starts the server on first use. */
    readonly request: (input: {
      readonly scope: string;
      readonly pluginId: string;
      readonly server: string;
      readonly method: ForwardedMethod;
      readonly params: unknown;
      /** The workspace directories the server sees as its roots. */
      readonly roots: ReadonlyArray<string>;
    }) => Effect.Effect<unknown, PluginOperationError>;
    /** List an enabled plugin's servers' tools in the environment scope and record them. Never fails. */
    readonly listPlugin: (pluginId: string) => Effect.Effect<void>;
    readonly closePlugin: (pluginId: string) => Effect.Effect<void>;
  }
>()("t3/plugins/PluginHost") {}

const make = Effect.gen(function* () {
  const store = yield* PluginStore.PluginStore;
  const path = yield* Path.Path;
  const environment = yield* HostProcess.Environment;
  const home = yield* HostProcess.HomeDirectory;
  const connections = new Map<string, Promise<Connection>>();

  const keyOf = (scope: string, pluginId: string, server: string) =>
    `${scope}\u0000${pluginId}/${server}`;

  const connect = async (
    plugin: PluginStore.EnabledPlugin,
    name: string,
    config: PluginServerConfig,
    roots: ReadonlyArray<string>,
    signature: string,
    now: number,
  ): Promise<Connection> => {
    const client = new Client(
      { name: "T3 Code", version: "1.0.0" },
      {
        capabilities: {
          roots: { listChanged: false },
          extensions: {
            [MCP_UI_EXTENSION]: { mimeTypes: [MCP_APP_MIME] },
            [UI_ENTRYPOINTS_EXTENSION]: { entrypoints: [...UI_ENTRYPOINTS] },
          },
        },
      },
    );
    const connection: Connection = {
      client,
      signature,
      stderr: [],
      roots,
      closed: false,
      lastUsed: now,
    };
    client.setRequestHandler(ListRootsRequestSchema, async () => ({
      roots: connection.roots.map((root) => ({
        uri: PluginFiles.fileUrl(root),
        name: path.basename(root),
      })),
    }));
    client.onclose = () => {
      connection.closed = true;
    };
    const timeout =
      (config.startupTimeoutSec ??
        (config.command !== null && PACKAGE_RUNNERS.test(config.command)
          ? RUNNER_STARTUP_SECONDS
          : DEFAULT_STARTUP_SECONDS)) * 1000;
    if (config.url !== null) {
      const transport = new StreamableHTTPClientTransport(new URL(config.url), {
        requestInit: { headers: { ...config.headers } },
      });
      try {
        // The SDK's transport declares optional members without `| undefined`, which
        // exactOptionalPropertyTypes rejects; it is the SDK's own transport.
        await client.connect(transport as Parameters<Client["connect"]>[0], { timeout });
      } catch (error) {
        await client.close().catch(() => undefined);
        throw new Error(`${name} did not connect: ${sentence(error)}`, { cause: error });
      }
      return connection;
    }
    const cwd =
      config.cwd !== null
        ? PluginFiles.resolveInside(plugin.root, config.cwd)
        : plugin.format === "codex"
          ? plugin.root
          : (roots[0] ?? home);
    if (cwd === null) throw new Error(`${name}'s cwd is outside the plugin folder`);
    const env: Record<string, string> = {};
    for (const [key, value] of Object.entries(environment))
      if (value !== undefined) env[key] = value;
    for (const [key, value] of Object.entries(config.env))
      env[key] = expandPluginRoot(value, plugin.root);
    env.PLUGIN_ROOT = plugin.root;
    env.CLAUDE_PLUGIN_ROOT = plugin.root;
    const transport = new StdioClientTransport({
      command: expandPluginRoot(config.command ?? "", plugin.root),
      args: config.args.map((arg) => expandPluginRoot(arg, plugin.root)),
      env,
      cwd,
      stderr: "pipe",
    });
    transport.stderr?.on("data", (chunk: Buffer) => {
      connection.stderr.push(...chunk.toString("utf8").split(/\r?\n/).filter(Boolean));
      if (connection.stderr.length > STDERR_LINES) {
        connection.stderr.splice(0, connection.stderr.length - STDERR_LINES);
      }
    });
    try {
      await client.connect(transport, { timeout });
    } catch (error) {
      await client.close().catch(() => undefined);
      const tail = connection.stderr.slice(-5).join("\n");
      throw new Error(`${name} did not start: ${sentence(error)}${tail ? `\n${tail}` : ""}`, {
        cause: error,
      });
    }
    return connection;
  };

  const closeWhere = (match: (scope: string, serverKey: string) => boolean) =>
    Effect.promise(async () => {
      const closing: Array<Promise<unknown>> = [];
      for (const [key, pending] of connections) {
        const [scope = "", serverKey = ""] = key.split("\u0000");
        if (!match(scope, serverKey)) continue;
        connections.delete(key);
        closing.push(
          pending.then((connection) => connection.client.close()).catch(() => undefined),
        );
      }
      await Promise.all(closing);
    });

  /** The live connection for a scope, started on first use; a changed config restarts it. */
  const connection = (
    scope: string,
    pluginId: string,
    server: string,
    roots: ReadonlyArray<string>,
  ) =>
    Effect.gen(function* () {
      const plugins = yield* store.enabledPlugins;
      const plugin = plugins.find((item) => item.id === pluginId);
      const config = plugin?.servers[server];
      if (plugin === undefined || config === undefined) {
        return yield* requestFailed(
          "server_failed",
          `no enabled plugin "${pluginId}" has an MCP server "${server}"`,
        );
      }
      const now = yield* Clock.currentTimeMillis;
      const key = keyOf(scope, pluginId, server);
      const signature = PluginFiles.serverSignature(plugin.root, config);
      return yield* Effect.tryPromise({
        try: async () => {
          const live = await liveOrRestart(
            connections,
            key,
            (existing) => !existing.closed && existing.signature === signature,
            (stale) => stale.client.close(),
            () => connect(plugin, server, config, roots, signature, now),
          );
          if (roots.length > 0) live.roots = roots;
          live.lastUsed = now;
          return { connection: live, config };
        },
        catch: (cause) => requestFailed("server_failed", sentence(cause)),
      });
    });

  const request: PluginHost["Service"]["request"] = (input) =>
    Effect.gen(function* () {
      const { connection: live, config } = yield* connection(
        input.scope,
        input.pluginId,
        input.server,
        input.roots,
      );
      const family = input.method.split("/")[0] as "tools" | "resources" | "prompts";
      const capabilities = live.client.getServerCapabilities() ?? {};
      const empty = EMPTY_LISTS[input.method];
      if (!capabilities[family] && empty !== undefined) return empty;
      const timeout = (config.toolTimeoutSec ?? DEFAULT_TOOL_SECONDS) * 1000;
      return yield* Effect.tryPromise({
        try: (signal) =>
          live.client.request(
            { method: input.method, params: (input.params ?? {}) as Record<string, unknown> },
            RESULT_SCHEMAS[input.method],
            { timeout, signal },
          ),
        catch: (cause) => requestFailed("request_failed", sentence(cause)),
      });
    }).pipe(Effect.withSpan("PluginHost.request", { attributes: { method: input.method } }));

  const listServer = (pluginId: string, server: string) =>
    Effect.gen(function* () {
      yield* store.markStarting(pluginId, server);
      const tools: Array<Record<string, unknown> & { readonly name: string }> = [];
      const listed = yield* Effect.gen(function* () {
        let cursor: string | undefined;
        do {
          const page = (yield* request({
            scope: ENVIRONMENT_SCOPE,
            pluginId,
            server,
            method: "tools/list",
            params: cursor === undefined ? {} : { cursor },
            roots: [],
          })) as {
            readonly tools: ReadonlyArray<Record<string, unknown> & { readonly name: string }>;
            readonly nextCursor?: string;
          };
          tools.push(...page.tools);
          cursor = page.nextCursor;
        } while (cursor !== undefined);
      }).pipe(Effect.result);
      yield* store.recordListing(
        pluginId,
        server,
        listed._tag === "Success"
          ? { state: "ready", error: null, tools }
          : { state: "error", error: listed.failure.message, tools: [] },
      );
    });

  const listPlugin = (pluginId: string) =>
    Effect.gen(function* () {
      const plugin = (yield* store.enabledPlugins).find((item) => item.id === pluginId);
      if (plugin === undefined) return;
      yield* Effect.forEach(Object.keys(plugin.servers), (server) => listServer(pluginId, server), {
        concurrency: "unbounded",
        discard: true,
      });
    });

  // A thread's processes go when it has been idle a while; the environment's stay.
  yield* Effect.gen(function* () {
    const now = yield* Clock.currentTimeMillis;
    const idle = new Set<string>();
    for (const [key, pending] of connections) {
      const live = yield* Effect.promise(() => pending.catch(() => null));
      if (
        !key.startsWith(`${ENVIRONMENT_SCOPE}\u0000`) &&
        (live === null || now - live.lastUsed > THREAD_IDLE_MS)
      ) {
        idle.add(key);
      }
    }
    if (idle.size > 0)
      yield* closeWhere((scope, serverKey) => idle.has(`${scope}\u0000${serverKey}`));
  }).pipe(Effect.repeat(Schedule.spaced("1 minute")), Effect.forkScoped);
  yield* Effect.addFinalizer(() => closeWhere(() => true));

  return PluginHost.of({
    request,
    listPlugin,
    closePlugin: (pluginId) => closeWhere((_, serverKey) => serverKey.startsWith(`${pluginId}/`)),
  });
});

export const layer = Layer.effect(PluginHost, make);
