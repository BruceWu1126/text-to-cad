import { assert, describe, it } from "@effect/vitest";
import { EnvironmentId, ProviderInstanceId, ThreadId } from "@t3tools/contracts";
import type { McpServer } from "effect/ai";
import { McpSchema } from "effect/ai";
import * as Context from "effect/Context";
import * as Deferred from "effect/Deferred";
import * as Effect from "effect/Effect";
import * as Stream from "effect/Stream";

import * as PluginService from "../../../plugins/PluginService.ts";
import * as McpInvocationContext from "../../McpInvocationContext.ts";
import { register } from "./tools.ts";

type Registration = Parameters<McpServer.McpServer["Service"]["addTool"]>[0];

const plugins = PluginService.PluginService.of({
  agentTools: Effect.succeed([
    {
      name: "cad__cad_file",
      pluginId: "cad",
      server: "cad",
      tool: {
        name: "cad_file",
        description: "Open a CAD file",
        inputSchema: { type: "object", properties: { file: { type: "object" } } },
        _meta: { ui: { resourceUri: "ui://cad/file" } },
      },
    },
  ]),
  callAgentTool: (input: { readonly threadId: string; readonly name: string }) =>
    Effect.succeed({ content: [{ type: "text", text: `${input.threadId}:${input.name}` }] }),
  changes: Stream.empty,
} as unknown as PluginService.PluginService["Service"]);

const threadCaller: McpInvocationContext.McpInvocationScope = {
  environmentId: EnvironmentId.make("environment"),
  capabilities: new Set(),
  issuedAt: 0,
  requestNamespace: "test",
  thread: {
    threadId: ThreadId.make("thread-1"),
    providerSessionId: "session",
    providerInstanceId: ProviderInstanceId.make("claude"),
  },
  client: undefined,
};

/** The plugin handler reads no request state; the type asks for it. */
const requestContext = {} as McpSchema.McpRequestContext["Service"];

describe("plugin tools on /mcp", () => {
  it.effect(
    "registers each plugin tool by its namespaced name, without its UI metadata, and runs it as the calling thread",
    () =>
      Effect.gen(function* () {
        const registrations: Array<Registration> = [];
        yield* register((options) => Effect.sync(() => void registrations.push(options))).pipe(
          Effect.provideService(PluginService.PluginService, plugins),
          Effect.scoped,
        );
        const [registration] = registrations;
        assert.strictEqual(registration?.tool.name, "cad__cad_file");
        assert.strictEqual(registration?.tool._meta, undefined);
        const enabled = Context.getUnsafe(
          registration!.annotations as Context.Context<McpSchema.EnabledWhen>,
          McpSchema.EnabledWhen,
        );
        assert.isTrue(enabled({ protocolVersion: "2025-06-18", capabilities: {} }));

        const asThread = (yield* registration!
          .handle({ file: {} })
          .pipe(
            Effect.provideService(McpInvocationContext.McpInvocationContext, threadCaller),
            Effect.provideService(McpSchema.McpRequestContext, requestContext),
          )) as McpSchema.CallToolResult;
        assert.deepEqual(asThread.content, [{ type: "text", text: "thread-1:cad__cad_file" }]);

        const outside = (yield* registration!.handle({}).pipe(
          Effect.provideService(McpInvocationContext.McpInvocationContext, {
            ...threadCaller,
            thread: undefined,
          }),
          Effect.provideService(McpSchema.McpRequestContext, requestContext),
        )) as McpSchema.CallToolResult;
        assert.isTrue(outside.isError);
      }),
  );

  it.effect("registers a tool again when an update changes its definition", () =>
    Effect.gen(function* () {
      const descriptions = ["Open a CAD file", "Open a CAD file", "Open a STEP or STL file"];
      let listing = 0;
      const tool = (description: string) => ({
        name: "cad__cad_file",
        pluginId: "cad",
        server: "cad",
        tool: { name: "cad_file", description, inputSchema: { type: "object" } },
      });
      const registrations: Array<Registration> = [];
      const synced = yield* Deferred.make<void>();
      yield* Effect.gen(function* () {
        yield* register((options) => Effect.sync(() => void registrations.push(options)));
        yield* Deferred.await(synced);
      }).pipe(
        Effect.provideService(
          PluginService.PluginService,
          PluginService.PluginService.of({
            agentTools: Effect.sync(() => [tool(descriptions[listing++] ?? "")]),
            // An unchanged listing, then the update; it ends once both have synced.
            changes: Stream.make(undefined, undefined).pipe(
              Stream.ensuring(Deferred.succeed(synced, undefined)),
            ),
          } as unknown as PluginService.PluginService["Service"]),
        ),
        Effect.scoped,
      );
      assert.deepEqual(
        registrations.map((registration) => registration.tool.description),
        ["Open a CAD file", "Open a STEP or STL file"],
      );
    }),
  );
});
