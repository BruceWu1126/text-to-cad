import { describe, expect, it } from "vite-plus/test";

import {
  agentToolName,
  buildCatalog,
  type CatalogMarket,
  compatibilityOf,
  expandPluginRoot,
  fileExtensionsOf,
  filterCatalog,
  insideRelative,
  isAppOnly,
  keyOf,
  parseCatalogKey,
  parseGitRemote,
  type PluginFolder,
  PluginReadError,
  readMarketplace,
  readPlugin,
  readToolUi,
  subfolder,
} from "./index.ts";

/** A folder over a map of relative paths to file contents (objects are JSON). */
function memoryFolder(files: Record<string, string | object>): PluginFolder {
  const entries = new Map(
    Object.entries(files).map(([path, content]) => [
      path,
      typeof content === "string" ? content : JSON.stringify(content),
    ]),
  );
  const isDirectory = (relative: string) =>
    relative === "" || [...entries.keys()].some((path) => path.startsWith(`${relative}/`));
  return {
    readJson: (relative) => {
      const text = entries.get(relative);
      if (text === undefined) return undefined;
      try {
        return JSON.parse(text);
      } catch {
        throw new PluginReadError(`${relative} is not valid JSON`);
      }
    },
    isFile: (relative) => entries.has(relative),
    isDirectory,
    list: (relative) => {
      const prefix = relative === "" ? "" : `${relative}/`;
      return [
        ...new Set(
          [...entries.keys()]
            .filter((path) => path.startsWith(prefix))
            .map((path) => path.slice(prefix.length).split("/")[0]!),
        ),
      ];
    },
  };
}

describe("readPlugin", () => {
  it("reads a Codex manifest with servers in a separate file and skills", () => {
    const read = readPlugin(
      memoryFolder({
        ".codex-plugin/plugin.json": {
          name: "demo",
          version: "1.0.0",
          mcpServers: "./servers.json",
          skills: "./skills/",
          interface: {
            displayName: "Demo",
            defaultPrompt: ["Design a bracket.", "", 7, "Check my part."],
          },
        },
        "servers.json": {
          mcpServers: { main: { command: "node", args: ["${PLUGIN_ROOT}/server.mjs"] } },
        },
        "skills/one/SKILL.md": "---\nname: one\n---\n",
        "skills/not-a-skill/README.md": "",
      }),
    );
    expect(read.format).toBe("codex");
    expect(read.manifest).toMatchObject({ name: "demo", displayName: "Demo", version: "1.0.0" });
    expect(read.manifest.starterPrompts).toEqual(["Design a bracket.", "Check my part."]);
    expect(Object.keys(read.servers)).toEqual(["main"]);
    expect(read.servers.main!.args).toEqual(["${PLUGIN_ROOT}/server.mjs"]);
    expect(read.skillsDir).toBe("skills");
    expect(read.skills).toEqual(["one"]);
  });

  it("reads a Claude Code manifest with inline servers, a .mcp.json beside it, and a bare map", () => {
    expect(
      Object.keys(
        readPlugin(
          memoryFolder({
            ".claude-plugin/plugin.json": { name: "inline", mcpServers: { s: { command: "x" } } },
          }),
        ).servers,
      ),
    ).toEqual(["s"]);
    const beside = readPlugin(
      memoryFolder({
        ".claude-plugin/plugin.json": { name: "beside" },
        ".mcp.json": { mcpServers: { t: { url: "https://example.com/mcp" } } },
      }),
    );
    expect(beside.format).toBe("claude");
    expect(beside.servers.t!.url).toBe("https://example.com/mcp");
    // Linear's plugin: a bare map of servers, no `mcpServers` key.
    const bare = readPlugin(
      memoryFolder({
        ".claude-plugin/plugin.json": { name: "bare" },
        ".mcp.json": { linear: { type: "http", url: "https://mcp.linear.app/mcp" } },
      }),
    );
    expect(bare.servers.linear!.url).toBe("https://mcp.linear.app/mcp");
  });

  it("reads the Agent Plugins standard: a bare plugin.json and the mcp.json beside it", () => {
    const read = readPlugin(
      memoryFolder({
        "plugin.json": { name: "text-to-cad" },
        "mcp.json": {
          mcpServers: { cad: { type: "stdio", command: "uvx", args: ["cadgen", "mcp"] } },
        },
      }),
    );
    expect(read.format).toBe("bare");
    expect(read.servers.cad!.command).toBe("uvx");
    expect(read.servers.cad!.args).toEqual(["cadgen", "mcp"]);
  });

  it("uses a marketplace entry's inline manifest only when the folder has none", () => {
    const read = readPlugin(memoryFolder({ "skills/s/SKILL.md": "" }), {
      name: "loose",
      description: "d",
    });
    expect(read.format).toBe("inline");
    expect(read.skills).toEqual(["s"]);
  });

  it("says why a folder is not a plugin", () => {
    expect(() => readPlugin(memoryFolder({}))).toThrow(/no plugin manifest/);
    expect(() => readPlugin(memoryFolder({ "plugin.json": { name: "has spaces" } }))).toThrow(
      /name must be/,
    );
    expect(() =>
      readPlugin(memoryFolder({ "plugin.json": { name: "n", mcpServers: { s: { args: [] } } } })),
    ).toThrow(/command or a url/);
    expect(() => readPlugin(memoryFolder({ "plugin.json": "{ nope" }))).toThrow(/not valid JSON/);
  });

  it("refuses paths that leave the plugin folder", () => {
    expect(() =>
      readPlugin(
        memoryFolder({ "plugin.json": { name: "escape", mcpServers: "../outside.json" } }),
      ),
    ).toThrow(/outside the plugin folder/);
    expect(insideRelative("../x")).toBeNull();
    expect(insideRelative("/etc/passwd")).toBeNull();
    expect(insideRelative("./a/../b/")).toBe("b");
    expect(insideRelative(".")).toBe("");
  });

  it("expands the plugin root in commands", () => {
    expect(expandPluginRoot("${CLAUDE_PLUGIN_ROOT}/a ${PLUGIN_ROOT}/b", "/r")).toBe("/r/a /r/b");
  });
});

describe("readMarketplace", () => {
  it("reads Codex's marketplace, resolving local sources from its root", () => {
    const market = readMarketplace(
      memoryFolder({
        ".agents/plugins/marketplace.json": {
          name: "mine",
          interface: { displayName: "Mine" },
          plugins: [
            { name: "a", description: "A", source: { source: "local", path: "./plugins/a" } },
            { name: "b", source: "./plugins/b" },
            { name: "remote", source: { source: "github", repo: "x/y" } },
            {
              name: "sub",
              source: {
                source: "git-subdir",
                url: "https://example.com/r.git",
                path: "p/q",
                sha: "abc",
              },
            },
            { name: "npm", source: { source: "npm", package: "x" } },
            { name: "escape", source: "../../elsewhere" },
          ],
        },
      }),
      "fallback",
    );
    expect(market.displayName).toBe("Mine");
    expect(market.plugins.map((plugin) => plugin.name)).toEqual(["a", "b", "remote", "sub", "npm"]);
    expect(market.plugins[0]!.source).toEqual({ type: "local", path: "plugins/a" });
    expect(market.plugins[2]!.source).toMatchObject({
      type: "git",
      url: "https://github.com/x/y.git",
      subdir: null,
    });
    expect(market.plugins[3]!.source).toMatchObject({ type: "git", subdir: "p/q", sha: "abc" });
    expect(market.plugins[4]!.source.type).toBe("unsupported");
  });

  it("reads text-to-cad's layout: a Claude marketplace whose one plugin is the repository root", () => {
    const market = readMarketplace(
      memoryFolder({
        ".claude-plugin/marketplace.json": {
          name: "text-to-cad",
          plugins: [{ name: "cad", source: "./" }],
        },
      }),
      "earthtojake-text-to-cad",
    );
    expect(market.plugins[0]!.source).toEqual({ type: "local", path: "" });
  });

  it("refuses a git source whose URL, ref or commit git would read as an option", () => {
    const market = readMarketplace(
      memoryFolder({
        "marketplace.json": {
          plugins: [
            { name: "url", source: { source: "url", url: "--upload-pack=touch /tmp/x" } },
            { name: "ref", source: { source: "github", repo: "x/y", ref: "--upload-pack=x" } },
            { name: "sha", source: { source: "github", repo: "x/y", sha: "-c" } },
            { name: "fine", source: { source: "github", repo: "x/y", ref: "main" } },
          ],
        },
      }),
      "fallback",
    );
    expect(market.plugins.map((plugin) => plugin.source.type)).toEqual([
      "unsupported",
      "unsupported",
      "unsupported",
      "git",
    ]);
  });
});

describe("git remotes", () => {
  it("reads owner/repo, URLs and paths, and compares them without .git, case or credentials", () => {
    expect(parseGitRemote("anthropics/claude-plugins-official")).toMatchObject({
      url: "https://github.com/anthropics/claude-plugins-official.git",
      slug: "anthropics-claude-plugins-official",
    });
    expect(keyOf("https://github.com/Figma/MCP-Server-Guide.git")).toBe(
      keyOf("git@github.com:figma/mcp-server-guide"),
    );
    expect(() => parseGitRemote("not a repo")).toThrow(/owner\/repo/);
  });
});

describe("the catalog", () => {
  const market = (
    id: string,
    files: Record<string, string | object>,
    options: { url?: string; official?: boolean } = {},
  ): CatalogMarket => {
    const folder = memoryFolder(files);
    const read = readMarketplace(folder, id);
    return {
      id,
      name: read.displayName,
      url: options.url ?? null,
      official: options.official ?? false,
      plugins: read.plugins.map((entry) => ({
        entry,
        folder:
          entry.source.type === "local" && folder.isDirectory(entry.source.path)
            ? subfolder(folder, entry.source.path)
            : null,
      })),
    };
  };

  it("shows a plugin two marketplaces offer once, preferring the one with a Codex manifest", () => {
    const claude = market("claude", {
      ".claude-plugin/marketplace.json": {
        name: "claude-market",
        plugins: [{ name: "linear", source: "./linear" }],
      },
      "linear/.claude-plugin/plugin.json": { name: "linear" },
      "linear/.mcp.json": { linear: { type: "http", url: "https://mcp.linear.app/mcp" } },
    });
    const codex = market("codex", {
      ".claude-plugin/marketplace.json": {
        name: "codex-market",
        plugins: [{ name: "linear", source: "./linear" }],
      },
      "linear/.codex-plugin/plugin.json": { name: "linear", interface: { displayName: "Linear" } },
      "linear/.mcp.json": { mcpServers: { linear: { url: "https://mcp.linear.app/mcp" } } },
    });
    const catalog = buildCatalog([claude, codex], new Map(), new Set());
    expect(catalog).toHaveLength(1);
    expect(catalog[0]).toMatchObject({
      key: "codex/linear",
      displayName: "Linear",
      compatibility: { level: "signin", label: "May need sign-in" },
    });
    expect(catalog[0]!.marketplaces.map((ref) => ref.id)).toEqual(["codex", "claude"]);
  });

  it("leaves out test fixtures, marks installed plugins, and trusts official catalogs", () => {
    const official = market(
      "anthropics-claude-plugins-official",
      {
        ".claude-plugin/marketplace.json": {
          name: "claude-plugins-official",
          plugins: [
            { name: "fakechat", source: "./fakechat" },
            { name: "keys", source: "./keys" },
          ],
        },
        "fakechat/.claude-plugin/plugin.json": { name: "fakechat" },
        "keys/.claude-plugin/plugin.json": { name: "keys", author: { name: "Keys Inc" } },
        "keys/.mcp.json": { keys: { command: "npx", args: ["-y", "keys-mcp"] } },
      },
      { url: "https://github.com/anthropics/claude-plugins-official.git", official: true },
    );
    const catalog = buildCatalog(
      [official],
      new Map([["anthropics-claude-plugins-official/keys", "keys"]]),
      new Set(["keys"]),
    );
    expect(catalog.map((entry) => entry.name)).toEqual(["keys"]);
    expect(catalog[0]).toMatchObject({
      verified: true,
      installedId: "keys",
      publisher: "Keys Inc",
      compatibility: { level: "works", notes: ["1 MCP server"] },
    });
    expect(filterCatalog(catalog, "KEYS inc").matchCount).toBe(1);
    expect(filterCatalog(catalog, "nothing").entries).toEqual([]);
  });

  it("labels what T3 Code cannot run, and what it reads only on install", () => {
    const codexOnly = memoryFolder({
      ".codex-plugin/plugin.json": { name: "review" },
      ".mcp.json": { mcpServers: { review: { command: "./bin/codex-launcher" } } },
    });
    const local = { source: { type: "local", path: "" }, inline: null } as const;
    expect(compatibilityOf(local, codexOnly)).toMatchObject({
      level: "codex",
      label: "Needs Codex",
    });
    const hooks = memoryFolder({
      ".claude-plugin/plugin.json": { name: "h" },
      "hooks/hooks.json": {},
      "commands/go.md": "",
    });
    // Hooks and commands load through the Claude Agent SDK, so they work with Claude only.
    expect(compatibilityOf(local, hooks)).toMatchObject({
      level: "partly",
      label: "Claude only",
      notes: ["its hooks, slash commands run with Claude only"],
    });
    expect(
      compatibilityOf(
        {
          source: {
            type: "git",
            url: "https://github.com/x/y.git",
            ref: null,
            sha: null,
            subdir: null,
          },
          inline: null,
        },
        null,
      ),
    ).toMatchObject({ level: "unknown", label: "Checked on install" });
    expect(
      compatibilityOf({ source: { type: "unsupported", reason: "npm" }, inline: null }, null).level,
    ).toBe("unavailable");
  });

  it("parses catalog keys", () => {
    expect(parseCatalogKey("earthtojake-text-to-cad/cad")).toEqual({
      marketId: "earthtojake-text-to-cad",
      name: "cad",
    });
    expect(parseCatalogKey("nope")).toBeNull();
  });
});

describe("readToolUi", () => {
  it("reads MCP Apps' resourceUri, visibility and Codex's entrypoints", () => {
    const tool = {
      name: "view",
      _meta: {
        ui: { resourceUri: "ui://x", visibility: ["app"] },
        "openai/ui": {
          entrypoints: [
            { type: "global" },
            { type: "file", extensions: [".STEP", "stp"] },
            { type: "nonsense" },
          ],
        },
      },
    };
    const view = readToolUi("s", tool)!;
    expect(view).toMatchObject({ server: "s", tool: "view", title: "view", resourceUri: "ui://x" });
    expect(view.entrypoints.map((entry) => entry.type)).toEqual(["global", "file"]);
    expect(fileExtensionsOf(view)).toEqual(["step", "stp"]);
    expect(isAppOnly(tool)).toBe(true);
    expect(isAppOnly({ _meta: { ui: { visibility: ["app", "model"] } } })).toBe(false);
  });

  it("accepts the older flat key, defaults to a thread tool, and skips tools without a UI", () => {
    expect(
      readToolUi("s", { name: "t", _meta: { "ui/resourceUri": "ui://y" } })!.entrypoints,
    ).toEqual([{ type: "thread" }]);
    expect(readToolUi("s", { name: "plain" })).toBeNull();
  });
});

describe("agentToolName", () => {
  it("namespaces by plugin and stays within what providers accept", () => {
    expect(agentToolName("cad", "cad_file")).toBe("cad__cad_file");
    expect(agentToolName("a.b", "c d")).toBe("a_b__c_d");
    const long = agentToolName(
      "a-very-long-plugin-name-indeed",
      "and_an_even_longer_tool_name_here",
    );
    expect(long).toHaveLength(50);
    expect(long).not.toBe(
      agentToolName("a-very-long-plugin-name-indeed", "and_an_even_longer_tool_name_herf"),
    );
  });
});
