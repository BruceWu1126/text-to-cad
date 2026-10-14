// @effect-diagnostics nodeBuiltinImport:off -- fixtures are git repositories on disk.
import * as NodeChildProcess from "node:child_process";
import * as NodeFS from "node:fs";
import * as NodeOS from "node:os";
import * as NodePath from "node:path";

import { afterEach, describe, expect, it } from "@effect/vitest";

import { shallowClone } from "./pluginFiles.ts";

const made: Array<string> = [];
afterEach(() => {
  for (const dir of made.splice(0)) NodeFS.rmSync(dir, { recursive: true, force: true });
});

/** A repository with one commit; answers its folder and that commit. */
function repository() {
  const dir = NodeFS.mkdtempSync(NodePath.join(NodeOS.tmpdir(), "t3-plugin-files-"));
  made.push(dir);
  const repo = NodePath.join(dir, "repo");
  NodeFS.mkdirSync(repo);
  NodeFS.writeFileSync(NodePath.join(repo, "plugin.json"), "{}");
  const git = (...args: ReadonlyArray<string>) =>
    NodeChildProcess.execFileSync(
      "git",
      ["-c", "user.email=t@example.com", "-c", "user.name=t", ...args],
      { cwd: repo, encoding: "utf8" },
    ).trim();
  git("init", "-q");
  git("add", "-A");
  git("commit", "-q", "-m", "first");
  return { dir, url: `file://${repo}`, commit: git("rev-parse", "HEAD") };
}

describe("shallowClone", () => {
  it("clones a commit by its short id", async () => {
    const { dir, url, commit } = repository();
    const target = NodePath.join(dir, "clone");
    expect(await shallowClone(url, target, { sha: commit.slice(0, 12) })).toBe(commit);
    expect(NodeFS.existsSync(NodePath.join(target, "plugin.json"))).toBe(true);
  });

  it("never reads a ref or commit as one of git's options", async () => {
    const { dir, url } = repository();
    const marker = NodePath.join(dir, "ran");
    const option = `--upload-pack=touch ${marker}`;
    await expect(shallowClone(url, NodePath.join(dir, "a"), { ref: option })).rejects.toThrow();
    await expect(shallowClone(url, NodePath.join(dir, "b"), { sha: option })).rejects.toThrow();
    expect(NodeFS.existsSync(marker)).toBe(false);
  });
});
