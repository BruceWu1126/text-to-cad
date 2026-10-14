/**
 * Naming git repositories: what a person or a marketplace writes (`owner/repo`,
 * an https or ssh URL, a local path) as the URL git is given, a folder name for
 * its clone, and the key two mentions of one repository compare equal by.
 */
import { PluginReadError } from "./folder.ts";

export interface GitRemote {
  readonly url: string;
  /** A stable folder name: `owner-repo` for GitHub, else a cleaned host and path. */
  readonly slug: string;
  /** The URL without credentials, `.git` or a trailing slash, lowercased. */
  readonly key: string;
}

export function keyOf(url: string): string {
  let text = url.trim();
  const scp = /^[\w.-]+@([\w.-]+):(.*)$/.exec(text);
  if (scp) text = `https://${scp[1]}/${scp[2]}`;
  try {
    const parsed = new URL(text);
    if (parsed.protocol !== "file:") text = `https://${parsed.host}${parsed.pathname}`;
  } catch {
    // A path.
  }
  return text
    .replace(/\/+$/, "")
    .replace(/\.git$/, "")
    .toLowerCase();
}

export function slugOf(url: string): string {
  const key = keyOf(url);
  const github = /^https:\/\/github\.com\/([^/]+)\/([^/]+)$/.exec(key);
  const raw = github ? `${github[1]}-${github[2]}` : key.replace(/^(https|file):\/\//, "");
  return (
    raw
      .replace(/[^a-z0-9._-]+/g, "-")
      .replace(/^[-.]+|-+$/g, "")
      .slice(0, 80) || "repository"
  );
}

/** `owner/repo` (GitHub), an https/ssh/git/file URL, or an absolute path to a repository. */
export function parseGitRemote(input: string): GitRemote {
  const text = input.trim();
  let url: string;
  if (/^[A-Za-z0-9_.-]+\/[A-Za-z0-9_.-]+$/.test(text)) {
    url = `https://github.com/${text.replace(/\.git$/, "")}.git`;
  } else if (
    /^(https?|ssh|git|file):\/\//.test(text) ||
    /^[\w.-]+@[\w.-]+:/.test(text) ||
    text.startsWith("/")
  ) {
    url = text;
  } else {
    throw new PluginReadError(`"${text}" is not owner/repo or a git URL`);
  }
  return { url, slug: slugOf(url), key: keyOf(url) };
}
