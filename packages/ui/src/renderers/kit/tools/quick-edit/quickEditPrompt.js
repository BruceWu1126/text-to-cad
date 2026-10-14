import { createPromptContext, formatPromptMessage, formatPromptReference, parsePromptReference, referencePart, resolveAttachmentPaths, textPart } from "@text-to-cad/core/prompt";

const stem = path => String(path || "").split("/").pop().replace(/\.[^.]+$/, "") || "view";
const stemOf = resource => stem(resource.kind === "url" ? new URL(resource.url).pathname : resource.path);
const slug = label => String(label).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "view";
/** A view's picture as a prompt titles it: "Current view", "View 1". */
export const viewTitle = label => (label === "current" ? "Current view" : String(label).replace(/^view\b/, "View"));
// Awaited the first time something reads it: a file is fetched only by a host that carries its bytes.
const lazy = start => {
  let started = null;
  return { then: (resolve, reject) => (started ??= Promise.resolve().then(start)).then(resolve, reject) };
};

/**
 * A paste into the note, line by line: each line that is a reference and nothing else — a file's
 * absolute path and the ids in it, `/models/base.step#o1.f2`, as Copy References and a chip's click
 * write one — is that reference (into any file); every other line stays text. A reference within a
 * sentence is the sentence's.
 *
 * @param {string} pasted
 * @returns {Array<string | import("@text-to-cad/core/prompt").PromptReference>}
 */
export function pastedLines(pasted) {
  return String(pasted || "").split(/\r?\n/).map(line => parsePromptReference(line) ?? line);
}

/** A reference as one line of text, the way Copy References writes it: what tells two apart. */
export const referenceToken = reference => formatPromptReference(reference);

/** The selection a Quick Edit attaches: what is picked in the file, not the file itself, which it always attaches. */
export const quickEditSelection = references => references.filter(reference => reference.target.kind !== "whole-resource");

/**
 * A Quick Edit as one prompt context: what the person wrote, the file on screen (always), the
 * selection in it (`references`, in the prompt grammar), and — once the host has made them
 * (`files`, which may still be rendering) — a picture of each view it carries (`views`: the one on
 * screen as it went, and each its drawing was drawn in), those views as a snapshot job file
 * (`cadgen snapshot --job` renders them again), and the drawing as a sketch file (`sketch`). Each
 * file says where it was saved (`path`); its bytes are read (`read`) only by a host that carries
 * them. Built at the press, from what is live then.
 *
 * @param {{ resource: object, references?: object[], text: string, views?: { label: string }[],
 *   files?: Promise<{ job: string, sketch: string | null, images: { label: string, path: string }[] }> | null,
 *   read?: (path: string) => Promise<Blob>, sketch?: boolean }} quickEdit
 */
export function createQuickEditContext({ resource, references = [], text, views = [], files = null, read = null, sketch = false }) {
  const parts = [
    textPart(String(text || "").trim(), "text"),
    referencePart({ resource: { ...resource }, target: { kind: "whole-resource" } }, "file"),
    ...quickEditSelection(references).map((reference, index) => referencePart({ ...reference, resource: { ...reference.resource } }, `reference-${index}`)),
  ];
  if (files && views.length) {
    const name = stemOf(resource);
    const made = pick => { const path = files.then(pick); path.catch(() => {}); return path; };
    const file = (id, label, fileName, mimeType, pick) => {
      const path = made(pick);
      parts.push({ id, kind: "attachment", label, name: fileName, mimeType, path, content: lazy(() => path.then(read)), about: ["file"] });
    };
    views.forEach((view, index) => file(`view-${index}`, viewTitle(view.label), `${name}-${slug(view.label)}.png`, "image/png", result => result.images[index].path));
    file("views", "Views (cadgen snapshot --job)", `${name}.job.json`, "application/json", result => result.job);
    if (sketch) file("sketch", "Sketch", `${name}.sketch.json`, "application/json", result => result.sketch);
  }
  return createPromptContext(parts);
}

/**
 * A Quick Edit as the person copies it: its references by their files' absolute paths, and its
 * pictures, views and sketch by the paths they were saved at, since text carries no file.
 * @returns {Promise<string>}
 */
export async function copiedQuickEdit(context) {
  const paths = await resolveAttachmentPaths(context);
  return formatPromptMessage(context, { attachmentPath: part => paths.get(part.id) });
}
