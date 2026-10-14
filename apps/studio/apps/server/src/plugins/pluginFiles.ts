// @effect-diagnostics nodeBuiltinImport:off -- the plugin readers in @t3tools/plugins take a synchronous folder, and git runs as the person's own binary.
/**
 * Plugin files on disk: a `PluginFolder` over a directory, git clones pinned to
 * a commit, copies out of a clone, and the registry file. Plain Node, wrapped
 * into Effects by the services that call it.
 *
 * Git uses the person's own `git` and credentials (a private repository works
 * when their credential helper does); T3 stores no token. `GIT_TERMINAL_PROMPT=0`
 * turns a missing credential into an error instead of a prompt nobody sees,
 * `GIT_LFS_SKIP_SMUDGE=1` leaves LFS objects as pointers (a plugin is code and
 * skills), and a clone lands in a temporary sibling renamed into place only
 * when complete, so a half clone is never read. A URL, ref or commit comes from a
 * marketplace file, so each follows `--end-of-options` and git never reads one as a flag.
 */
import * as NodeChildProcess from "node:child_process";
import * as NodeCrypto from "node:crypto";
import * as NodeFS from "node:fs";
import * as NodePath from "node:path";
import * as NodeURL from "node:url";

import { type PluginFolder, PluginReadError } from "@t3tools/plugins";

const isDirectory = (target: string) =>
  NodeFS.statSync(target, { throwIfNoEntry: false })?.isDirectory() ?? false;
const isFile = (target: string) =>
  NodeFS.statSync(target, { throwIfNoEntry: false })?.isFile() ?? false;

export const directoryExists = isDirectory;

/** A `PluginFolder` over `root`. */
export function nodeFolder(root: string): PluginFolder {
  const at = (relative: string) => NodePath.join(root, ...relative.split("/"));
  return {
    readJson: (relative) => {
      const file = at(relative);
      if (!isFile(file)) return undefined;
      try {
        return JSON.parse(NodeFS.readFileSync(file, "utf8"));
      } catch (cause) {
        throw new PluginReadError(`${relative} is not valid JSON`, { cause });
      }
    },
    isFile: (relative) => isFile(at(relative)),
    isDirectory: (relative) => isDirectory(at(relative)),
    list: (relative) => {
      try {
        return NodeFS.readdirSync(at(relative));
      } catch {
        return [];
      }
    },
  };
}

/** `relative` resolved inside `root` (following symlinks), or null when it leaves it. */
export function resolveInside(root: string, relative: string): string | null {
  const real = (target: string) => {
    try {
      return NodeFS.realpathSync(target);
    } catch {
      return NodePath.resolve(target);
    }
  };
  const base = real(root);
  const resolved = real(NodePath.resolve(base, relative));
  const back = NodePath.relative(base, resolved);
  return back.startsWith("..") || NodePath.isAbsolute(back) ? null : resolved;
}

export const isInside = (parent: string, child: string) => {
  const back = NodePath.relative(NodePath.resolve(parent), NodePath.resolve(child));
  return back === "" || (!back.startsWith("..") && !NodePath.isAbsolute(back));
};

const GIT_TIMEOUT_MS = 180_000;

/** A git command that failed, with git's own last words (no URL or credential). */
export class GitError extends Error {
  override readonly name = "GitError";
}

function git(args: ReadonlyArray<string>, cwd?: string): Promise<string> {
  return new Promise((resolve, reject) => {
    NodeChildProcess.execFile(
      "git",
      [...args],
      {
        cwd,
        timeout: GIT_TIMEOUT_MS,
        maxBuffer: 8 * 1024 * 1024,
        env: {
          ...process.env,
          GIT_TERMINAL_PROMPT: "0",
          GIT_LFS_SKIP_SMUDGE: "1",
          GIT_ASKPASS: "",
          SSH_ASKPASS: "",
        },
      },
      (error, stdout, stderr) => {
        if (error) {
          const said = String(stderr || "")
            .trim()
            .split("\n")
            .filter(Boolean)
            .slice(-2)
            .join(" ");
          const why =
            (error as NodeJS.ErrnoException).code === "ENOENT"
              ? "git is not installed (T3 Code uses your git to fetch plugins)"
              : error.killed
                ? `git ${args[0]} took longer than ${GIT_TIMEOUT_MS / 1000} s`
                : said || `git ${args[0]} failed`;
          reject(new GitError(why, { cause: error }));
          return;
        }
        resolve(String(stdout).trim());
      },
    );
  });
}

const temporarySibling = (target: string) =>
  NodePath.join(
    NodePath.dirname(target),
    `.${NodePath.basename(target)}.${NodeCrypto.randomBytes(4).toString("hex")}.tmp`,
  );

/**
 * Clone `url` at one commit (`sha`, else the tip of `ref`, else the default
 * branch) into `dir`, replacing it only when the clone is complete. Answers the commit.
 */
export async function shallowClone(
  url: string,
  dir: string,
  options: { readonly ref?: string | null; readonly sha?: string | null } = {},
): Promise<string> {
  NodeFS.mkdirSync(NodePath.dirname(dir), { recursive: true });
  const temporary = temporarySibling(dir);
  try {
    await git(["init", "-q", temporary]);
    await git(["remote", "add", "--end-of-options", "origin", url], temporary);
    try {
      await git(
        [
          "fetch",
          "-q",
          "--depth",
          "1",
          "--end-of-options",
          "origin",
          options.sha ?? options.ref ?? "HEAD",
        ],
        temporary,
      );
    } catch (error) {
      // A server that will not serve a commit by its id: fetch the ref and look for it there.
      if (!options.sha) throw error;
      await git(
        ["fetch", "-q", "--depth", "50", "--end-of-options", "origin", options.ref ?? "HEAD"],
        temporary,
      );
    }
    // checkout takes no --end-of-options with --detach, so it gets the resolved commit id.
    const commit = await git(
      ["rev-parse", "--verify", "--end-of-options", `${options.sha ?? "FETCH_HEAD"}^{commit}`],
      temporary,
    );
    await git(["checkout", "-q", "--detach", commit], temporary);
    NodeFS.rmSync(dir, { recursive: true, force: true });
    NodeFS.renameSync(temporary, dir);
    return commit;
  } finally {
    NodeFS.rmSync(temporary, { recursive: true, force: true });
  }
}

/** Move a clone to the latest commit of its default branch. Answers the commit. */
export async function fetchLatest(dir: string): Promise<string> {
  await git(["fetch", "-q", "--depth", "1", "origin", "HEAD"], dir);
  await git(["checkout", "-q", "--detach", "FETCH_HEAD"], dir);
  return git(["rev-parse", "HEAD"], dir);
}

/** Copy a plugin folder out of a clone: no `.git`, no installed dependencies, symlinks dereferenced. */
export function copyPluginFolder(from: string, to: string): void {
  NodeFS.mkdirSync(NodePath.dirname(to), { recursive: true });
  const temporary = temporarySibling(to);
  try {
    NodeFS.cpSync(from, temporary, {
      recursive: true,
      dereference: true,
      filter: (source) =>
        ![".git", "node_modules", ".venv", "__pycache__", ".DS_Store"].includes(
          NodePath.basename(source),
        ),
    });
    NodeFS.rmSync(to, { recursive: true, force: true });
    NodeFS.renameSync(temporary, to);
  } finally {
    NodeFS.rmSync(temporary, { recursive: true, force: true });
  }
}

/** Write a manifest into a copied plugin that had none (a `"strict": false` marketplace entry). */
export function writeInlineManifest(
  root: string,
  manifest: Readonly<Record<string, unknown>>,
): void {
  const file = NodePath.join(root, ".claude-plugin", "plugin.json");
  NodeFS.mkdirSync(NodePath.dirname(file), { recursive: true });
  NodeFS.writeFileSync(file, `${JSON.stringify(manifest, null, 2)}\n`);
}

export function removeFolder(target: string): void {
  NodeFS.rmSync(target, { recursive: true, force: true });
}

/** A folder's files and sizes, for "did it change" when no version says. */
export function fingerprint(dir: string): string {
  const parts: Array<string> = [];
  const walk = (folder: string, prefix: string) => {
    for (const entry of NodeFS.readdirSync(folder, { withFileTypes: true }).toSorted((a, b) =>
      a.name.localeCompare(b.name),
    )) {
      if ([".git", "node_modules", ".claude-plugin"].includes(entry.name)) continue;
      const full = NodePath.join(folder, entry.name);
      if (entry.isDirectory()) walk(full, `${prefix}${entry.name}/`);
      else parts.push(`${prefix}${entry.name}:${NodeFS.statSync(full).size}`);
    }
  };
  try {
    walk(dir, "");
  } catch {
    return "";
  }
  return parts.join("|");
}

/** The JSON file, or `undefined` when it is missing. A file that does not parse is set aside as `.bad`. */
export function readJsonFile(file: string): unknown {
  if (!isFile(file)) return undefined;
  try {
    return JSON.parse(NodeFS.readFileSync(file, "utf8"));
  } catch {
    try {
      NodeFS.renameSync(file, `${file}.bad`);
    } catch {
      // Nothing to keep.
    }
    return undefined;
  }
}

export function writeJsonFile(file: string, value: unknown): void {
  NodeFS.mkdirSync(NodePath.dirname(file), { recursive: true });
  const temporary = `${file}.${process.pid}.tmp`;
  NodeFS.writeFileSync(temporary, `${JSON.stringify(value, null, 2)}\n`);
  NodeFS.renameSync(temporary, file);
}

/** Rename `from` to `to`, replacing what was there. */
export function moveFolder(from: string, to: string): void {
  NodeFS.rmSync(to, { recursive: true, force: true });
  NodeFS.renameSync(from, to);
}

export const fileUrl = (target: string) => NodeURL.pathToFileURL(target).href;

/** What a server is started from: a changed config restarts its processes. */
export const serverSignature = (root: string, config: unknown) =>
  `${root}\u0000${JSON.stringify(config)}`;
