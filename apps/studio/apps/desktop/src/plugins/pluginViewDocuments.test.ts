import { describe, expect, it } from "vite-plus/test";

import {
  makePluginViewDocuments,
  MAX_STAGED_PLUGIN_VIEWS,
  pluginViewContentSecurityPolicy,
  pluginViewNavigationAllowed,
} from "./pluginViewDocuments.ts";

const directives = (policy: string) =>
  Object.fromEntries(
    policy.split("; ").map((directive) => {
      const [name, ...values] = directive.split(" ");
      return [name, values];
    }),
  );

describe("plugin view policy", () => {
  it("allows inline code and blob workers but no network by default", () => {
    const policy = directives(pluginViewContentSecurityPolicy(null));
    expect(policy["script-src"]).toEqual(["'unsafe-inline'", "'wasm-unsafe-eval'", "blob:"]);
    expect(policy["worker-src"]).toEqual(["blob:"]);
    expect(policy["connect-src"]).toEqual(["'none'"]);
    expect(policy["frame-src"]).toEqual(["'none'"]);
    expect(policy["default-src"]).toEqual(["'none'"]);
  });

  it("adds declared domains and drops anything that could widen the policy", () => {
    const policy = directives(
      pluginViewContentSecurityPolicy({
        connectDomains: ["https://api.example.com", "blob:", "'unsafe-eval'", "* 'self'"],
        resourceDomains: ["https://cdn.example.com", 42, "https://x.test; script-src *"],
      }),
    );
    expect(policy["connect-src"]).toEqual(["https://api.example.com", "blob:"]);
    expect(policy["script-src"]).toContain("https://cdn.example.com");
    expect(policy["script-src"]).not.toContain("*");
    expect(policy["img-src"]).toEqual(["data:", "blob:", "https://cdn.example.com"]);
  });

  it("takes a wildcard only in front of a real host", () => {
    const policy = directives(
      pluginViewContentSecurityPolicy({
        connectDomains: [
          "*",
          "https://*",
          "wss://*:443",
          "*.",
          "*.*",
          "https://*.example.com",
          "localhost:*",
        ],
      }),
    );
    expect(policy["connect-src"]).toEqual(["https://*.example.com", "localhost:*"]);
  });
});

describe("staged plugin views", () => {
  it("serves a staged document with its policy until it is released", async () => {
    let next = 0;
    const documents = makePluginViewDocuments(() => `id${next++}`);
    const url = documents.stage("<p>hi</p>", { connectDomains: ["https://a.test"] });
    expect(url).toBe("mcp-app://id0/index.html");
    const response = documents.serve(url);
    expect(response.status).toBe(200);
    expect(await response.text()).toBe("<p>hi</p>");
    expect(response.headers.get("content-security-policy")).toContain("connect-src https://a.test");
    // Any path on the origin is the same document.
    expect(documents.serve("mcp-app://id0/other").status).toBe(200);
    expect(documents.release(url)).toBe("mcp-app://id0");
    expect(documents.serve(url).status).toBe(404);
    expect(documents.release("https://example.com/")).toBeNull();
  });

  it("forgets the oldest documents past the cap", () => {
    let next = 0;
    const documents = makePluginViewDocuments(() => `id${next++}`);
    const first = documents.stage("first", null);
    for (let index = 0; index < MAX_STAGED_PLUGIN_VIEWS; index++) documents.stage("x", null);
    expect(documents.serve(first).status).toBe(404);
    expect(documents.serve(`mcp-app://id${MAX_STAGED_PLUGIN_VIEWS}/index.html`).status).toBe(200);
  });
});

describe("plugin view navigation", () => {
  it("keeps a view frame on its own origin", () => {
    expect(pluginViewNavigationAllowed("mcp-app://a/index.html", "mcp-app://a/next")).toBe(true);
    expect(pluginViewNavigationAllowed("mcp-app://a/index.html", "mcp-app://b/index.html")).toBe(
      false,
    );
    expect(pluginViewNavigationAllowed("mcp-app://a/index.html", "https://example.com")).toBe(
      false,
    );
  });

  it("leaves other frames alone", () => {
    expect(pluginViewNavigationAllowed("https://example.com", "mcp-app://a/index.html")).toBe(true);
    expect(pluginViewNavigationAllowed(undefined, "https://example.com")).toBe(true);
  });
});
