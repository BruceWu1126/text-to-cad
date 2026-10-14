// @effect-diagnostics nodeBuiltinImport:off -- fixtures are plugin folders and git repositories on disk.
import * as NodeChildProcess from "node:child_process";
import * as NodeFS from "node:fs";
import * as NodeOS from "node:os";
import * as NodePath from "node:path";

import * as NodeServices from "@effect/platform-node/NodeServices";
import { assert, describe, it } from "@effect/vitest";
import { ThreadId } from "@t3tools/contracts";
import * as Effect from "effect/Effect";
import * as Layer from "effect/Layer";
import * as Option from "effect/Option";

import * as ServerConfig from "../config.ts";
import * as ProjectStore from "../orchestration-v2/ProjectStore.ts";
import * as ThreadManagementService from "../orchestration-v2/ThreadManagementService.ts";
import * as PluginService from "./PluginService.ts";
import * as PluginStore from "./PluginStore.ts";

/**
 * A stdio MCP server in plain Node: `show` renders a view (and opens .step
 * files), `sync` is for the view alone, `plain` has no view.
 */
const SERVER = String.raw`
import readline from "node:readline";
const send = (message) => process.stdout.write(JSON.stringify(message) + "\n");
const tools = [
  { name: "show", description: "Show the part", inputSchema: { type: "object" },
    _meta: { ui: { resourceUri: "ui://demo/view" }, "openai/ui": { entrypoints: [{ type: "file", extensions: [".step"] }] } } },
  { name: "sync", description: "View state", inputSchema: { type: "object" }, _meta: { ui: { resourceUri: "ui://demo/view", visibility: ["app"] } } },
  { name: "plain", description: "Echo", inputSchema: { type: "object", properties: { text: { type: "string" } } } },
];
readline.createInterface({ input: process.stdin }).on("line", (line) => {
  const message = JSON.parse(line);
  if (message.id === undefined) return;
  const reply = (result) => send({ jsonrpc: "2.0", id: message.id, result });
  switch (message.method) {
    case "initialize":
      return reply({ protocolVersion: message.params.protocolVersion, capabilities: { tools: {}, resources: {} },
        serverInfo: { name: "demo", version: "1.0.0" },
        instructions: JSON.stringify({ ext: message.params.capabilities.extensions ?? null, cwd: process.cwd(), root: process.env.PLUGIN_ROOT }) });
    case "tools/list": return reply({ tools });
    case "tools/call":
      return reply({ content: [{ type: "text", text: JSON.stringify({ name: message.params.name, arguments: message.params.arguments, meta: message.params._meta ?? null }) }] });
    case "resources/read":
      return reply({ contents: [{ uri: message.params.uri, mimeType: "text/html;profile=mcp-app", text: "<p>demo view</p>",
        _meta: { ui: { csp: { connectDomains: ["https://api.example.com"] } } } }] });
    default:
      return send({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "no such method" } });
  }
});
`;

function write(file: string, content: string | object) {
  NodeFS.mkdirSync(NodePath.dirname(file), { recursive: true });
  NodeFS.writeFileSync(
    file,
    typeof content === "string" ? content : JSON.stringify(content, null, 2),
  );
}

function git(cwd: string, ...args: ReadonlyArray<string>) {
  return NodeChildProcess.execFileSync(
    "git",
    [
      "-c",
      "user.email=t@example.com",
      "-c",
      "user.name=t",
      "-c",
      "init.defaultBranch=main",
      ...args,
    ],
    { cwd, encoding: "utf8" },
  ).trim();
}

/** A marketplace folder with the `demo` plugin, and a bare git repository of the same. */
function fixture() {
  const dir = NodeFS.realpathSync(
    NodeFS.mkdtempSync(NodePath.join(NodeOS.tmpdir(), "t3-plugins-")),
  );
  const market = NodePath.join(dir, "market");
  write(NodePath.join(market, ".claude-plugin", "marketplace.json"), {
    name: "demo-market",
    plugins: [{ name: "demo", source: "./demo", description: "A demo plugin" }],
  });
  const plugin = NodePath.join(market, "demo");
  write(NodePath.join(plugin, ".codex-plugin", "plugin.json"), {
    name: "demo",
    version: "1.0.0",
    interface: { displayName: "Demo" },
  });
  write(NodePath.join(plugin, ".mcp.json"), {
    mcpServers: { main: { command: process.execPath, args: ["${PLUGIN_ROOT}/server.mjs"] } },
  });
  write(NodePath.join(plugin, "server.mjs"), SERVER);
  write(NodePath.join(plugin, "skills", "parts", "SKILL.md"), "---\nname: parts\n---\n");
  git(market, "init", "-q");
  git(market, "add", "-A");
  git(market, "commit", "-q", "-m", "first");
  const bare = NodePath.join(dir, "remote", "market.git");
  NodeFS.mkdirSync(NodePath.dirname(bare), { recursive: true });
  NodeChildProcess.execFileSync("git", ["clone", "-q", "--bare", market, bare]);
  const work = NodePath.join(dir, "work");
  write(NodePath.join(work, "parts", "bracket.step"), "ISO-10303-21;");
  return { dir, market, bare, work };
}

const threadId = ThreadId.make("thread-plugins");

/** The thread and project lookups the service makes, answering one thread whose worktree is `work`. */
const layerThreads = (work: string) =>
  Layer.mergeAll(
    Layer.succeed(
      ThreadManagementService.ThreadManagementService,
      // Only getThreadRecords is reached.
      ThreadManagementService.ThreadManagementService.of({
        getThreadRecords: () => Effect.succeed({ thread: { worktreePath: work, projectId: "p" } }),
      } as unknown as ThreadManagementService.ThreadManagementService["Service"]),
    ),
    Layer.succeed(
      ProjectStore.ProjectStoreV2,
      ProjectStore.ProjectStoreV2.of({
        get: () => Effect.succeed(Option.none()),
      } as unknown as ProjectStore.ProjectStoreV2["Service"]),
    ),
  );

const layerFor = (
  sources: ReadonlyArray<PluginStore.MarketplaceSource>,
  base: string,
  work: string,
) =>
  PluginService.layer.pipe(
    Layer.provideMerge(PluginStore.layerWith(sources)),
    Layer.provide(layerThreads(work)),
    Layer.provide(ServerConfig.layerTest(process.cwd(), base)),
  );

describe("plugins", () => {
  it.layer(NodeServices.layer)((it) => {
    it.effect(
      "installs from a folder marketplace, lists views, opens one, and hides app-only tools from agents",
      () => {
        const files = fixture();
        return Effect.gen(function* () {
          const plugins = yield* PluginService.PluginService;
          const store = yield* PluginStore.PluginStore;

          const catalog = yield* plugins.catalog({ query: "" });
          assert.deepEqual(
            catalog.entries.map((entry) => [entry.key, entry.compatibility.level]),
            [["local-market/demo", "works"]],
          );
          assert.strictEqual(catalog.catalogs[0]?.state, "ready");

          const installed = yield* plugins.install("local-market/demo");
          assert.strictEqual(installed.id, "demo");
          assert.deepEqual(installed.skills, ["parts"]);
          assert.deepEqual(installed.servers, [
            { name: "main", transport: "stdio", state: "ready", error: null },
          ]);
          assert.deepEqual(
            installed.views.map((view) => [view.tool, view.entrypoints]),
            [
              ["show", [{ type: "file", extensions: ["step"] }]],
              ["sync", [{ type: "thread" }]],
            ],
          );
          const after = yield* plugins.catalog({ query: "demo" });
          assert.strictEqual(after.entries[0]?.installedId, "demo");

          // The environment scope's view: the tool's result and its UI resource.
          const document = yield* plugins.openView({
            threadId: null,
            pluginId: "demo",
            server: "main",
            tool: "show",
            arguments: { part: 1 },
          });
          assert.strictEqual(document.html, "<p>demo view</p>");
          assert.deepEqual(document.csp, { connectDomains: ["https://api.example.com"] });
          assert.deepEqual(document.toolInput, { part: 1 });

          // A file opens against the thread's workspace, never outside it.
          const file = yield* plugins.openView({
            threadId,
            pluginId: "demo",
            server: "main",
            tool: "show",
            arguments: {},
            file: "parts/bracket.step",
          });
          const absolute = NodePath.join(files.work, "parts", "bracket.step");
          assert.deepEqual(file.toolInput, {
            file: { name: "bracket.step", resourceUri: `file://${absolute}` },
          });
          const echoed = JSON.parse(
            (file.toolResult as { content: Array<{ text: string }> }).content[0]!.text,
          );
          assert.deepEqual(echoed.meta, { "openai/resource": { path: absolute } });
          const outside = yield* plugins
            .openView({
              threadId,
              pluginId: "demo",
              server: "main",
              tool: "show",
              arguments: {},
              file: "../market/demo/server.mjs",
            })
            .pipe(Effect.flip);
          assert.strictEqual(outside.reason, "request_failed");

          // A view's own request may call an app-only tool.
          const sync = yield* plugins.request({
            threadId,
            pluginId: "demo",
            server: "main",
            method: "tools/call",
            params: { name: "sync", arguments: {} },
          });
          assert.include(JSON.stringify(sync.result), "sync");

          // Agents see every tool but the app-only one, namespaced by plugin.
          const agentTools = yield* plugins.agentTools;
          assert.deepEqual(
            agentTools.map((tool) => tool.name),
            ["demo__show", "demo__plain"],
          );
          const called = (yield* plugins.callAgentTool({
            threadId,
            name: "demo__plain",
            arguments: { text: "hi" },
          })) as { content: Array<{ text: string }> };
          assert.deepEqual(JSON.parse(called.content[0]!.text).arguments, { text: "hi" });
          const refused = yield* plugins
            .callAgentTool({ threadId, name: "demo__sync", arguments: {} })
            .pipe(Effect.result);
          assert.isTrue(refused._tag === "Failure" && refused.failure.reason === "not_found");

          // Providers load the plugin's skills from its folder.
          const enabled = yield* store.enabledPlugins;
          assert.deepEqual(
            enabled.map((plugin) => plugin.skillsDir),
            [NodePath.join(files.market, "demo", "skills")],
          );

          const off = yield* plugins.setEnabled("demo", false);
          assert.deepEqual([off.enabled, off.views, off.servers[0]?.state], [false, [], "idle"]);
          assert.deepEqual(yield* plugins.agentTools, []);
          const on = yield* plugins.setEnabled("demo", true);
          assert.strictEqual(on.views.length, 2);

          yield* plugins.uninstall("demo");
          assert.deepEqual((yield* plugins.list).plugins, []);
        }).pipe(
          Effect.provide(
            layerFor(
              [{ type: "folder", path: files.market }],
              NodePath.join(files.dir, "t3"),
              files.work,
            ),
          ),
          Effect.ensuring(
            Effect.sync(() => NodeFS.rmSync(files.dir, { recursive: true, force: true })),
          ),
        );
      },
    );

    it.effect(
      "installs from a git marketplace into its own cache at the marketplace's commit",
      () => {
        const files = fixture();
        const base = NodePath.join(files.dir, "t3");
        return Effect.gen(function* () {
          const plugins = yield* PluginService.PluginService;
          const first = yield* plugins.catalog({ query: "" });
          // The first read never waits on the network.
          assert.strictEqual(first.catalogs[0]?.state, "fetching");
          const marketId = first.catalogs[0]!.id;
          const installed = yield* plugins.install(`${marketId}/demo`);
          const commit = git(files.market, "rev-parse", "HEAD");
          assert.strictEqual(installed.commit, commit);
          assert.strictEqual(installed.updateAvailable, false);
          // Cache folder names are shortened, so read the one market folder the install made.
          const cache = NodePath.join(base, "userdata", "plugins", "cache");
          const [marketDir] = NodeFS.readdirSync(cache);
          const root = NodePath.join(cache, marketDir!, "demo", commit.slice(0, 12));
          assert.isTrue(NodeFS.existsSync(NodePath.join(root, "server.mjs")));
          assert.strictEqual(installed.views.length, 2);
          yield* plugins.uninstall("demo");
          assert.isFalse(NodeFS.existsSync(root));
        }).pipe(
          Effect.provide(
            layerFor([{ type: "git", repository: `file://${files.bare}` }], base, files.work),
          ),
          Effect.ensuring(
            Effect.sync(() => NodeFS.rmSync(files.dir, { recursive: true, force: true })),
          ),
        );
      },
    );
  });
});
