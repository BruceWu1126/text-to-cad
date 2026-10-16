import { createPromptContext, formatPromptMessage, formatPromptReference, parsePromptReference, referencePart, resolveAttachmentPaths, textPart } from "@text-to-cad/core/prompt";
import { normalizeMeasurementMarks } from "@text-to-cad/core/lib/viewer/measureDimension.js";

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
 * write one — is that reference (into any file); a line that is a measurement as its chip copies it
 * (a snapshot job's `measurements` entry) is `{ measurement }`; a paste that is the annotation as its
 * chip copies it (its annotation JSON, whole) is `[{ annotation }]`; every other line stays text. A
 * reference within a sentence is the sentence's. A line that is what one of the box's chips copied
 * (`copied`: what it copied, to the chip — an annotation's or a measurement's file path) is
 * `{ chip, token }`.
 *
 * @param {string} pasted
 * @returns {Array<string | import("@text-to-cad/core/prompt").PromptReference | { measurement: object } | { annotation: object }>}
 */
export function pastedLines(pasted, copied = null) {
  const text = String(pasted || "");
  // The annotation, as its chip copies it: its annotation file's JSON, over several lines.
  const whole = parsedJson(text);
  if (whole && !Array.isArray(whole) && Array.isArray(whole.marks) && whole.parts && typeof whole.parts === "object") return [{ annotation: whole }];
  return text.split(/\r?\n/).map(line => (copied?.has(line.trim()) ? { chip: copied.get(line.trim()), token: line.trim() } : null)
    ?? parsePromptReference(line) ?? pastedMeasurement(line) ?? line);
}

const parsedJson = text => {
  const trimmed = text.trim();
  if (!trimmed.startsWith("{")) return null;
  try { return JSON.parse(trimmed); } catch { return null; }
};
// A measurement, as its chip copies it: a snapshot job's `measurements` entry.
const pastedMeasurement = line => {
  const value = parsedJson(line);
  if (!value) return null;
  try { return { measurement: normalizeMeasurementMarks([value])[0] }; } catch { return null; }
};

/** A reference as one line of text, the way Copy References writes it: what tells two apart. */
export const referenceToken = reference => formatPromptReference(reference);

/** The selection a Quick Edit attaches: what is picked in the file, not the file itself, which it always attaches. */
export const quickEditSelection = references => references.filter(reference => reference.target.kind !== "whole-resource");

/**
 * A Quick Edit as one prompt context: what the person wrote, the file on screen (always), the
 * selection in it (`references`, in the prompt grammar), and — once the host has made them
 * (`files`, which may still be rendering) — a picture of each view it carries (`views`: the one on
 * screen as it went, and each its annotation was drawn in), those views as a snapshot job file
 * (`cadgen snapshot --job` renders them again), and the annotation as an annotation file (`annotation`). Each
 * file says where it was saved (`path`); its bytes are read (`read`) only by a host that carries
 * them. Built at the press, from what is live then.
 *
 * @param {{ resource: object, references?: object[], text: string, views?: { label: string }[],
 *   files?: Promise<{ job: string, annotation: string | null, images: { label: string, path: string }[] }> | null,
 *   read?: (path: string) => Promise<Blob>, annotation?: boolean }} quickEdit
 */
export function createQuickEditContext({ resource, references = [], text, views = [], files = null, read = null, annotation = false }) {
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
    if (annotation) file("annotation", "Annotation", `${name}.annotation.json`, "application/json", result => result.annotation);
  }
  return createPromptContext(parts);
}

/**
 * A Quick Edit as the person copies it: its references by their files' absolute paths, and its
 * pictures, views and annotation by the paths they were saved at, since text carries no file.
 * @returns {Promise<string>}
 */
export async function copiedQuickEdit(context) {
  const paths = await resolveAttachmentPaths(context);
  return formatPromptMessage(context, { attachmentPath: part => paths.get(part.id) });
}
