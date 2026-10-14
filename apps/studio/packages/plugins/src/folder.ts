/**
 * A plugin or marketplace folder, as the readers in this package see it. The
 * server implements it over the filesystem; tests implement it over a map.
 * Paths are relative to the folder, `/`-separated, and already checked by
 * `insideRelative`.
 */
export interface PluginFolder {
  /** The parsed JSON file, `undefined` when it does not exist. Throws `PluginReadError` when it is not JSON. */
  readonly readJson: (relative: string) => unknown;
  readonly isFile: (relative: string) => boolean;
  readonly isDirectory: (relative: string) => boolean;
  /** The names of a directory's entries, empty when it does not exist. */
  readonly list: (relative: string) => ReadonlyArray<string>;
}

/** A plugin or marketplace that cannot be read, with a sentence a person can act on. */
export class PluginReadError extends Error {
  override readonly name = "PluginReadError";
}

/**
 * A path a manifest names, normalized and kept inside its folder: `./a/../b`
 * is `b`, `.` and `./` are the folder itself (`""`), and anything absolute or
 * climbing out is refused with null. A manifest cannot point at someone else's files.
 */
export function insideRelative(relative: string): string | null {
  if (relative.startsWith("/") || relative.startsWith("\\") || /^[A-Za-z]:/.test(relative)) {
    return null;
  }
  const parts: Array<string> = [];
  for (const part of relative.split(/[\\/]+/)) {
    if (part === "" || part === ".") continue;
    if (part === "..") {
      if (parts.length === 0) return null;
      parts.pop();
      continue;
    }
    parts.push(part);
  }
  return parts.join("/");
}

/** `insideRelative`, throwing the reason instead of answering null. */
export function requireInside(relative: string): string {
  const inside = insideRelative(relative);
  if (inside === null) throw new PluginReadError(`${relative} is outside the plugin folder`);
  return inside;
}

export function joinRelative(...parts: ReadonlyArray<string>): string {
  return parts.filter((part) => part.length > 0).join("/");
}

/** A view of a subfolder, for a plugin that lives inside a marketplace. */
export function subfolder(folder: PluginFolder, relative: string): PluginFolder {
  if (relative === "") return folder;
  const at = (inner: string) => joinRelative(relative, inner);
  return {
    readJson: (inner) => folder.readJson(at(inner)),
    isFile: (inner) => folder.isFile(at(inner)),
    isDirectory: (inner) => folder.isDirectory(at(inner)),
    list: (inner) => folder.list(at(inner)),
  };
}

export const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

export const nonEmptyString = (value: unknown): string | null =>
  typeof value === "string" && value.trim().length > 0 ? value : null;

/** Cut a string to a schema's maximum length. */
export const clip = (text: string, max: number): string =>
  text.length <= max ? text : `${text.slice(0, Math.max(0, max - 1))}…`;
