import { describe, expect, it } from "@effect/vitest";
import type { InstalledPlugin, OrchestrationV2TurnItem, PluginView } from "@t3tools/contracts";

import { agentOpenedView, pluginFollowTarget, workspaceRelativePath } from "./pluginFollow.ts";

const openView: PluginView = {
  server: "cad",
  tool: "cad_open",
  title: "CAD",
  resourceUri: "ui://cad/app.html",
  entrypoints: [{ type: "thread" }],
};
const fileView: PluginView = {
  server: "cad",
  tool: "cad_file",
  title: "CAD",
  resourceUri: "ui://cad/app.html",
  entrypoints: [{ type: "file", extensions: ["step", "stl"] }],
};

const plugin = (overrides: Partial<InstalledPlugin> = {}): InstalledPlugin => ({
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
  views: [openView, fileView],
  compatibility: { level: "works", label: "Works", notes: [] },
  updateAvailable: false,
  error: null,
  ...overrides,
});

// Only the fields these functions read; the rest of a turn item is irrelevant here.
const item = (fields: Record<string, unknown>) =>
  ({ status: "completed", ...fields }) as unknown as OrchestrationV2TurnItem;

describe("agentOpenedView", () => {
  it("matches the name Claude and Codex report for a plugin's view tool", () => {
    for (const toolName of [
      "mcp__t3-code__text-to-cad__cad_open",
      "t3-code.text-to-cad__cad_open",
      "text-to-cad__cad_open",
    ]) {
      const target = agentOpenedView([plugin()], toolName, { path: "/work/part.step" });
      expect(target?.choice.view.tool).toBe("cad_open");
      expect(target?.path).toBe("/work/part.step");
      expect(target?.arguments).toEqual({ path: "/work/part.step" });
    }
  });

  it("ignores other tools, lookalike names and disabled plugins", () => {
    expect(agentOpenedView([plugin()], "mcp__t3-code__text-to-cad__cad_show", {})).toBeNull();
    expect(agentOpenedView([plugin()], "mcp__other__xtext-to-cad__cad_open", {})).toBeNull();
    expect(
      agentOpenedView([plugin({ enabled: false })], "text-to-cad__cad_open", { path: "/a.step" }),
    ).toBeNull();
  });
});

describe("pluginFollowTarget", () => {
  it("opens the view an agent called, with its arguments", () => {
    const target = pluginFollowTarget(
      [plugin()],
      item({
        type: "dynamic_tool",
        toolName: "mcp__t3-code__text-to-cad__cad_open",
        input: { path: "/w/a.step" },
      }),
    );
    expect(target).toMatchObject({ kind: "agent", path: "/w/a.step" });
  });

  it("follows the last written file a view claims", () => {
    const target = pluginFollowTarget(
      [plugin()],
      item({
        type: "file_change",
        fileName: "model.py",
        changes: [
          { operation: "add", path: "out/a.step" },
          { operation: "update", path: "out/b.stl" },
          { operation: "update", path: "notes.md" },
        ],
      }),
    );
    expect(target).toMatchObject({ kind: "file", path: "out/b.stl" });
    expect(target?.choice.view.tool).toBe("cad_file");
  });

  it("ignores checkpoints, whose paths are the repository's, not the workspace's", () => {
    const target = pluginFollowTarget(
      [plugin()],
      item({
        type: "checkpoint",
        files: [{ path: "packages/cad/model.step", kind: "modified", additions: 9, deletions: 0 }],
      }),
    );
    expect(target).toBeNull();
  });

  it("skips deletions, unfinished items and files no view claims", () => {
    const plugins = [plugin()];
    expect(
      pluginFollowTarget(
        plugins,
        item({
          type: "file_change",
          fileName: "a.step",
          changes: [{ operation: "delete", path: "a.step" }],
        }),
      ),
    ).toBeNull();
    expect(
      pluginFollowTarget(
        plugins,
        item({ type: "file_change", fileName: "a.step", status: "running" }),
      ),
    ).toBeNull();
    expect(pluginFollowTarget(plugins, item({ type: "file_change", fileName: "a.md" }))).toBeNull();
  });
});

describe("workspaceRelativePath", () => {
  it("keeps relative paths and strips the workspace root from absolute ones", () => {
    expect(workspaceRelativePath("./out/a.step", "/w")).toBe("out/a.step");
    expect(workspaceRelativePath("/w/out/a.step", "/w/")).toBe("out/a.step");
    expect(workspaceRelativePath("/elsewhere/a.step", "/w")).toBeNull();
    expect(workspaceRelativePath("/w2/a.step", "/w")).toBeNull();
    expect(workspaceRelativePath("/w/a.step", null)).toBeNull();
  });
});
