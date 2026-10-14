import { promptReferenceIds } from "@text-to-cad/core/prompt";
import { referenceToken } from "./quickEditPrompt.js";
import { compactSuffix, hasPickGlyph, pickGlyphNode } from "../../inspector/pickGlyph.jsx";

// Quick Edit's note is plain text with references inline: an editable field whose text is the
// person's and whose chips are references pasted into it, each one atomic (a Backspace takes it
// whole) and read back as the reference it stands for (`/models/base.step#o1.f2`), in its place.

// 16px tall in the note's 20px line, 2px clear above and below it: centred, whatever the
// fonts' baselines (a transform moves it without moving the line).
const CHIP_CLASS = "mx-px inline-flex h-4 translate-y-px select-none items-center whitespace-nowrap rounded-sm bg-accent/80 px-1 align-top text-tiny leading-4 text-foreground";

/**
 * A reference as the note shows it: a chip carrying its token, named as the view names it — a
 * reference into the file on screen by what the renderer calls it (`name`: "cam", or a compact
 * name, "cam ◧3", "cam ◧×4"), into any other by its file and what in it ("hinge.step · o1.e4").
 */
export function noteChip(reference, document = globalThis.document, name = "") {
  const chip = document.createElement("span");
  const token = referenceToken(reference);
  chip.setAttribute("contenteditable", "false");
  chip.dataset.quickEditNoteRef = token;
  chip.className = CHIP_CLASS;
  if (name && typeof name === "object" && hasPickGlyph(name.kind)) {
    const suffix = document.createElement("span");
    suffix.className = "ml-1 inline-flex items-center gap-px text-muted-foreground";
    suffix.append(pickGlyphNode(document, name.kind, "size-2.5"), compactSuffix(name));
    chip.append(name.name || "", suffix);
  } else {
    chip.textContent = (typeof name === "string" && name) || `${reference.resource.path.split("/").pop()} · ${promptReferenceIds(reference).join(", ")}`;
  }
  chip.title = token;
  return chip;
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
 * A paste as the note's nodes: each line that is a reference alone a chip (followed by a space to
 * type on after it), every other line text, the lines' breaks kept.
 */
export function pasteNodes(lines, document = globalThis.document, nameReference = null) {
  const nodes = [];
  lines.forEach((line, index) => {
    if (index) nodes.push(document.createTextNode("\n"));
    if (typeof line === "string") { if (line) nodes.push(document.createTextNode(line)); }
    else nodes.push(noteChip(line, document, nameReference?.(line) || ""), document.createTextNode(" "));
  });
  return nodes;
}
