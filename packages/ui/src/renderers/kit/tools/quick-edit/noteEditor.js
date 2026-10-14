import { promptReferenceIds } from "@text-to-cad/core/prompt";
import { referenceToken } from "./quickEditPrompt.js";
import { compactSuffix, hasPickGlyph, pickGlyphNode } from "../../inspector/pickGlyph.jsx";

// Quick Edit's note is plain text with references inline: an editable field whose text is the
// person's and whose chips are references pasted into it, each one atomic (a Backspace takes it
// whole) and read back as the reference it stands for (`/models/base.step#o1.f2`), in its place.

// 16px tall in the note's 20px line, 2px clear above and below it: centred, whatever the
// fonts' baselines (a transform moves it without moving the line). The chips' own look
// (`ReferenceChips.jsx`): their background, their glyph first, then their words.
const CHIP_CLASS = "mx-px inline-flex h-4 translate-y-px select-none items-center gap-1 whitespace-nowrap rounded-sm bg-accent/70 px-1.5 align-top text-tiny leading-4 text-foreground";
const GLYPH_CLASS = "size-2.5 shrink-0";

/**
 * A pill in the note: atomic (a Backspace takes it whole) and read back as `token`, in its place;
 * shown as its chip is — its glyph (`kind`, coloured by `color`, else muted), then `text`, then
 * `suffix` (a pick's number or count), muted.
 */
function notePill(document, { token, kind = "", color = null, text, suffix = "" }) {
  const chip = document.createElement("span");
  chip.setAttribute("contenteditable", "false");
  chip.dataset.quickEditNoteRef = token;
  chip.className = CHIP_CLASS;
  if (hasPickGlyph(kind)) chip.append(pickGlyphNode(document, kind, color ? GLYPH_CLASS : `${GLYPH_CLASS} text-muted-foreground`, color));
  if (text) chip.append(text);
  if (suffix) {
    const muted = document.createElement("span");
    muted.className = "text-muted-foreground";
    muted.textContent = suffix;
    chip.append(muted);
  }
  chip.title = token;
  return chip;
}

/**
 * A reference as the note shows it: a pill carrying its token, named as the view names it — a
 * reference into the file on screen by what the renderer calls it (`name`: a part "cam", or a pick
 * by its part, kind and number, `{ name: "cam", kind: "face", number: "3" }`), into any other by its
 * file and what in it ("hinge.step · o1.e4").
 */
export function noteChip(reference, document = globalThis.document, name = "") {
  const token = referenceToken(reference);
  if (name && typeof name === "object") {
    return notePill(document, { token, kind: name.kind, text: name.name || "", suffix: compactSuffix(name) });
  }
  const text = (typeof name === "string" && name) || `${reference.resource.path.split("/").pop()} · ${promptReferenceIds(reference).join(", ")}`;
  return notePill(document, { token, text });
}

/** A measurement (a snapshot job's `measurements` entry, as its chip copies it) as the note shows it. */
export function noteMeasurement(mark, document = globalThis.document) {
  return notePill(document, { token: JSON.stringify(mark), kind: "ruler", color: mark.color, text: mark.label });
}

/** A drawing (its sketch file's JSON, as its chip copies it) as the note shows it. */
export function noteDrawing(sketch, document = globalThis.document) {
  return notePill(document, { token: JSON.stringify(sketch), kind: "pencil", text: "Drawing" });
}

/** The note as text: its words as written, each chip its reference's token, line breaks kept. */
export function noteText(root) {
  if (!root) return "";
  let text = "";
  const walk = node => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) text += child.nodeValue;
      else if (child.nodeType !== 1) continue;
      else if (child.dataset?.quickEditNoteRef) text += child.dataset.quickEditNoteRef;
      else if (child.tagName === "BR") text += "\n";
      else {
        // A block an editor wraps a line in starts a new line.
        if ((child.tagName === "DIV" || child.tagName === "P") && text && !text.endsWith("\n")) text += "\n";
        walk(child);
      }
    }
  };
  walk(root);
  return text.replace(/ /g, " ");
}

/** Nodes put in at the caret (or at the end, where the caret is not in the note), the caret after them. */
export function insertAtCaret(root, nodes) {
  const document = root.ownerDocument;
  const selection = document.getSelection?.();
  let range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  if (!range || !root.contains(range.commonAncestorContainer)) {
    range = document.createRange();
    range.selectNodeContents(root);
    range.collapse(false);
  }
  range.deleteContents();
  const fragment = document.createDocumentFragment();
  nodes.forEach(node => fragment.append(node));
  const last = fragment.lastChild;
  range.insertNode(fragment);
  if (last && selection) {
    const after = document.createRange();
    after.setStartAfter(last);
    after.collapse(true);
    selection.removeAllRanges();
    selection.addRange(after);
  }
}

/**
 * A paste as the note's nodes: each line that is a chip's copy alone (`pastedLines`: a reference, a
 * measurement, the drawing) its pill, followed by a space to type on after it; every other line
 * text, the lines' breaks kept.
 */
export function pasteNodes(lines, document = globalThis.document, nameReference = null) {
  const nodes = [];
  lines.forEach((line, index) => {
    if (index) nodes.push(document.createTextNode("\n"));
    if (typeof line === "string") { if (line) nodes.push(document.createTextNode(line)); return; }
    const pill = line.measurement ? noteMeasurement(line.measurement, document)
      : line.drawing ? noteDrawing(line.drawing, document)
        : noteChip(line, document, nameReference?.(line) || "");
    nodes.push(pill, document.createTextNode(" "));
  });
  return nodes;
}
