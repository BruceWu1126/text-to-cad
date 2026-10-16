import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowUp, Check, Copy, ListPlus, X } from "lucide-react";
import { Button } from "@text-to-cad/ui/primitives/button";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { cn } from "@text-to-cad/ui/utils";
import { promptReferenceIds } from "@text-to-cad/core/prompt";
import { usePromptDestination, useViewerHost } from "../../../../host/context.js";
import { FLOATING_SURFACE_CLASS } from "../../../../lib/floatingSurface.js";
import { capturePointer } from "../../../../lib/pointerCapture.js";
import ResizeGrip from "../ResizeGrip.jsx";
import { TOOL_PANEL_BUTTON_CLASS } from "../ToolPanel.jsx";
import { copiedQuickEdit, createQuickEditContext, pastedLines, quickEditSelection } from "./quickEditPrompt.js";
import { insertAtCaret, markSelectedPills, noteText, pasteNodes, selectPillAt } from "./noteEditor.js";
import ReferenceChips from "./ReferenceChips.jsx";

const EMPTY = Object.freeze([]);
// A press of the person's in this view leads the selection it made; an agent's selection (the live
// controller's `select`) follows none, and opens the box without taking their keyboard.
const PICK_MS = 1500;
// The box's size, as the person drags its corner: its width, and the note's height. A box opens
// 15rem wide, its note growing with what is written.
export const QUICK_EDIT_LIMITS = Object.freeze({ minWidth: 208, maxWidth: 640, minHeight: 56, maxHeight: 320 });
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const failure = result => (result && (result.status === "failed" || result.status === "cancelled") ? result.message || "The prompt did not go." : "");

/** Each selected reference by its ids, one a line, as the prompt grammar names them (`promptReferenceIds`). */
export function quickEditReferenceIds(selection) {
  return selection.flatMap(promptReferenceIds);
}

/**
 * Quick Edit: a person's note about the file on screen, handed to their agent, in a box at the
 * viewer's top-right that is there only while it has something to say about: nothing shows
 * otherwise. While the note is empty the box follows what it would carry: it opens when something
 * is picked or annotated, and closes when that goes. A written note keeps it open, whatever is
 * picked, until it goes or its X clears it. The X clears everything it would carry too — the
 * selection, as a press on the background would, and the annotation (`onClear`) — and so does a note
 * queued or sent. It takes the keyboard for the person — each pick of theirs, or an annotation's first
 * stroke once the pen lifts — never for an agent's selection.
 *
 * Under its header, what goes with the note is chips: first "Annotation" while Annotate has ink (`annotation`:
 * a click copies the annotation as an annotation file's JSON, its X wipes the ink), then each measurement
 * (`measurements.chips`: its colour's ruler and its reading; its X deletes it, resting on it lights
 * it; the note's views carry them all, drawn over each picture), then the selected references,
 * as the renderer describes them (`chips`,
 * `ReferenceChips.jsx`: a few, then "+N more"; each a card of what it is, its picks lit while the
 * pointer is on it, its X dropping them). A renderer that describes none has its references
 * counted in the header instead, their ids on hover (`references`, the selection in the prompt
 * grammar, is what goes either way). The note is plain text, and a reference pasted into it (a
 * line that is one and nothing else, as Copy References writes it, into this file or any other) is
 * a chip in it, where it was pasted: one piece a Backspace takes whole, which the note carries as
 * that reference's text, in its place (`noteEditor.js`). The file always goes and is not named. The buttons, bottom-right, are the ones this
 * host can carry out, the rightmost primary and pressed by Enter (Shift+Enter is a new line): Copy
 * Prompt always — the note as text to paste into an agent's prompt box, its references by their
 * files' absolute paths, and the files the host makes for it (`prompt`, `host.promptFiles`) by
 * theirs: a picture of each view it carries — the one on screen as it goes, and each the annotation
 * was drawn in — those views as a snapshot job file, and the annotation as an annotation file. Every way
 * it goes carries the same; Queue where the destination is a composer (the host's context for the next
 * message); Send where the host can post a message now (`promptContext.send`). Queue and Send
 * clear and close the box once the note has gone; Copy Prompt keeps it all (nothing has gone yet),
 * its button showing a tick for a moment. A failure keeps the note and says why. Its corner resizes it for
 * as long as it is open: the size is the box's own, written to it a frame at a time, so a drag
 * renders nothing, and it goes with the box — the next one opens at the default size.
 *
 * @param {{ resource: import("@text-to-cad/core/prompt").ResourceRef,
 *   references?: readonly import("@text-to-cad/core/prompt").PromptReference[],
 *   chips?: readonly import("./ReferenceChips.jsx").ReferenceChip[] | null,
 *   annotation?: { present: boolean, clear?: () => void } | null,
 *   measurements?: { chips: readonly import("./ReferenceChips.jsx").ReferenceChip[] } | null,
 *   prompt?: { views(): { label: string }[], files(views: { label: string }[]): Promise<object> | null,
 *     read(path: string): Promise<Blob>, annotation(): object | null } | null,
 *   nameReference?: ((reference: import("@text-to-cad/core/prompt").PromptReference) => string | import("../../inspector/pickGlyph.jsx").CompactName) | null,
 *   onCopy?: () => boolean, onEscape?: () => unknown, onClear?: () => void, disabled?: boolean,
 *   hidden?: boolean, className?: string, style?: import("react").CSSProperties }} props
 *   `nameReference`: what the renderer calls a reference into the file on screen, for its chip in
 *   the note ("cam"); "" where it has no name for it, which then reads as its file and ids.
 *   `onCopy`: the viewer's own copy (⌘C / Ctrl+C: the selection's references, or the annotation),
 *   which the key still reaches from the box while none of the note is selected. `onEscape`: the
 *   viewer's own Escape (clearing the selection), which an empty box passes on as it closes; a box
 *   with a note keeps it, and Escape only hands the keyboard back to the view. `hidden`: out of
 *   sight with all it holds (the view is loading).
 */
export default function QuickEdit({ resource, references = EMPTY, chips = null, annotation = null, measurements = null, prompt = null, nameReference = null, onCopy, onEscape, onClear, disabled = false,
  hidden = false, className, style }) {
  const host = useViewerHost();
  const destination = usePromptDestination();
  const [text, setText] = useState("");
  const [pending, setPending] = useState("");
  const [error, setError] = useState("");
  const field = useRef(null);
  const noteBox = useRef(null);
  const box = useRef(null);
  const root = useRef(null);
  const selection = useMemo(() => quickEditSelection(references), [references]);
  const referenceIds = useMemo(() => quickEditReferenceIds(selection), [selection]);
  const annotated = Boolean(annotation?.present);
  const measured = measurements?.chips?.length || 0;
  const attached = referenceIds.length > 0 || annotated || measured > 0;
  const written = Boolean(text.trim());
  const open = written || attached;

  // The person's presses: a stroke is under way while one is down, and a pick follows one in this view.
  const pressed = useRef(false);
  const lastPress = useRef(-Infinity);
  useEffect(() => {
    const down = () => { pressed.current = true; };
    // A primary press: a secondary one opens the viewport's menu, which marks what it is about
    // without being a pick to talk about — and the menu keeps the keyboard.
    const up = event => {
      pressed.current = false;
      const view = root.current?.closest("[data-slot=cad-file-view]");
      if (event.button === 0 && view && event.target instanceof Node && view.contains(event.target)) lastPress.current = performance.now();
    };
    window.addEventListener("pointerdown", down, true);
    window.addEventListener("pointerup", up, true);
    window.addEventListener("pointercancel", up, true);
    return () => {
      window.removeEventListener("pointerdown", down, true);
      window.removeEventListener("pointerup", up, true);
      window.removeEventListener("pointercancel", up, true);
    };
  }, []);

  // Opening for the person takes the keyboard: now, and again once the press that opened it has
  // finished, since a view that keeps its own keys focuses itself as a pick lands. A field the
  // person has moved to since keeps it.
  const takeKeyboard = useCallback(() => {
    const focus = () => {
      const note = field.current;
      const active = document.activeElement;
      const typing = active && active !== note && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable);
      if (note && active !== note && !typing) note.focus({ preventScroll: true });
    };
    focus();
    setTimeout(focus, 0);
  }, []);
  const referenceKey = referenceIds.join("\n");
  const seen = useRef({ referenceKey, annotated, measured });
  useEffect(() => {
    const before = seen.current;
    seen.current = { referenceKey, annotated, measured };
    const picked = referenceIds.length > 0 && referenceKey !== before.referenceKey;
    // Ink and a measurement are only ever the person's; a selection is theirs when it follows their press.
    const inked = (annotated && !before.annotated) || measured > before.measured;
    if (!picked && !inked) return undefined;
    if (!inked && performance.now() - lastPress.current >= PICK_MS) return undefined;
    // An annotation opens it mid-stroke; the keyboard waits for the pen to lift.
    if (pressed.current) {
      const lifted = () => takeKeyboard();
      window.addEventListener("pointerup", lifted, { once: true });
      return () => window.removeEventListener("pointerup", lifted);
    }
    takeKeyboard();
    return undefined;
  }, [referenceKey, annotated, measured, takeKeyboard]);

  // A pill the selection takes in is highlighted as its text is: it shows no selection of its own.
  useEffect(() => {
    if (!open) return undefined;
    const mark = () => markSelectedPills(field.current);
    document.addEventListener("selectionchange", mark);
    return () => document.removeEventListener("selectionchange", mark);
  }, [open]);

  const canQueue = destination.kind === "composer" && destination.available;
  const canSend = typeof host.promptContext.send === "function";
  const ready = written && !pending && !disabled;

  // The note, and everything it would carry: the box goes with them.
  const clearAll = () => { setText(""); field.current?.replaceChildren(); setError(""); onClear?.(); };
  // A copied prompt has gone nowhere yet: the box keeps it, and its button says it was copied.
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef(0);
  useEffect(() => () => clearTimeout(copiedTimer.current), []);
  const showCopied = () => {
    clearTimeout(copiedTimer.current);
    setCopied(true);
    copiedTimer.current = setTimeout(() => setCopied(false), 1600);
  };
  // Everything happens inside the press: the views are read as they are, and the host binds its
  // destination (and the clipboard its write) before their files are made.
  const run = action => {
    if (!ready) return;
    setError("");
    let outcome;
    try {
      const views = prompt?.views() ?? [];
      const files = views.length ? prompt.files(views) : null;
      void files?.catch(() => {});
      const context = createQuickEditContext({ resource, references: selection, text, views, files, read: prompt?.read, annotation: annotated });
      if (action === "send") outcome = host.promptContext.send(context);
      else if (action === "queue") outcome = host.promptContext.deliver(context);
      else outcome = host.clipboard.writeText(copiedQuickEdit(context)).then(() => ({ status: "copied" }));
    } catch (caught) { outcome = Promise.reject(caught); }
    setPending(action);
    Promise.resolve(outcome).then(result => {
      const message = failure(result);
      if (message) throw new Error(message);
      try { host.usage?.used("quickEdit"); } catch { /* counting a use never fails it */ }
      if (action === "copy") showCopied();
      else clearAll();
    }).catch(caught => setError(caught instanceof Error ? caught.message : String(caught)))
      .finally(() => setPending(""));
  };
  const actions = [
    { id: "copy", label: "Copy Prompt", hint: "Copy prompt", Icon: Copy },
    canQueue ? { id: "queue", label: "Queue", hint: "Add to your next message", Icon: ListPlus } : null,
    canSend ? { id: "send", label: "Send", hint: "Send now", Icon: ArrowUp } : null,
  ].filter(Boolean);
  const primary = actions.at(-1).id;

  // What is written, read back from the field: its text, each chip its reference. An emptied field
  // is emptied of what an editor leaves behind (a stray line break), so its placeholder shows.
  const readNote = note => {
    const value = noteText(note);
    if (!value.trim() && note.childNodes.length && !note.querySelector("[data-quick-edit-note-ref]")) note.replaceChildren();
    setText(value);
    setError("");
  };
  // A paste is the note's to place: plain text, and each line that is a reference a chip, at the caret.
  const onPaste = event => {
    event.preventDefault();
    const note = event.currentTarget;
    insertAtCaret(note, pasteNodes(pastedLines(event.clipboardData?.getData("text/plain"), copiedChips.current), note.ownerDocument, nameReference));
    readNote(note);
  };

  // A click on a pill selects it whole, as a double-click does a word: a Backspace takes it.
  const onNoteMouseDown = event => {
    if (event.button !== 0 || event.shiftKey) return;
    const note = event.currentTarget;
    if (!event.target?.closest?.("[data-quick-edit-note-ref]")) return;
    event.preventDefault();
    note.focus({ preventScroll: true });
    selectPillAt(note, event.target);
  };

  const onKeyDown = event => {
    const target = event.currentTarget;
    const copyKey = event.key.toLowerCase() === "c" && (event.metaKey || event.ctrlKey) && !event.altKey && !event.shiftKey;
    if (copyKey && (target.ownerDocument.getSelection?.()?.isCollapsed ?? true)) {
      if (onCopy?.()) event.preventDefault();
    } else if (event.key === "Enter" && !event.nativeEvent.isComposing) {
      event.preventDefault();
      // Shift+Enter is a new line, as plain text has it.
      if (event.shiftKey) { insertAtCaret(target, [target.ownerDocument.createTextNode("\n")]); readNote(target); }
      else run(primary);
    } else if (event.key === "Escape") {
      // Back to the model: the viewer's own keys work again. An empty box goes, and the key is the
      // viewer's too; a note is kept, box and all.
      event.preventDefault();
      target.blur();
      target.closest("[data-cad-surface]")?.focus({ preventScroll: true });
      if (!written) onEscape?.();
    }
  };

  // The corner, bottom-left since the box hangs from the top-right: dragging it out grows the box
  // leftward and its note downward. The size is written to the two elements, once a frame, and kept
  // nowhere else: nothing renders under the pointer, and a box that closes takes its size with it.
  const startResize = event => {
    const section = box.current, note = noteBox.current, text = field.current;
    if (event.button !== 0 || !section || !note || !text) return;
    event.preventDefault();
    const handle = event.currentTarget;
    const start = { x: event.clientX, y: event.clientY, width: section.offsetWidth, height: note.offsetHeight };
    capturePointer(handle, event.pointerId);
    let next = null, frame = 0;
    const draw = () => {
      frame = 0;
      section.style.width = `${next.width}px`;
      note.style.height = `${next.height}px`;
      note.style.maxHeight = "none";
      // The field fills the region it scrolls in, so a press anywhere in it writes.
      text.style.minHeight = `${Math.max(0, next.height - 2)}px`;
    };
    const move = moved => {
      next = {
        width: clamp(Math.round(start.width + start.x - moved.clientX), QUICK_EDIT_LIMITS.minWidth, QUICK_EDIT_LIMITS.maxWidth),
        height: clamp(Math.round(start.height + moved.clientY - start.y), QUICK_EDIT_LIMITS.minHeight, QUICK_EDIT_LIMITS.maxHeight),
      };
      frame ||= requestAnimationFrame(draw);
    };
    const end = () => {
      handle.removeEventListener("pointermove", move);
      handle.removeEventListener("pointerup", end);
      handle.removeEventListener("pointercancel", end);
      // The last move, if its frame has not come yet.
      if (frame) { cancelAnimationFrame(frame); draw(); }
    };
    handle.addEventListener("pointermove", move);
    handle.addEventListener("pointerup", end);
    handle.addEventListener("pointercancel", end);
  };

  // Chips name the references; with none described, the header counts them. The annotation is a chip of
  // its own, first: "Annotation", copied by a click as its annotation file's path (its JSON where the host keeps
  // no prompt files), wiped by its X.
  const referenceCount = chips ? 0 : referenceIds.length;
  const annotationOf = prompt?.annotation, wipe = annotation?.clear, saveFile = prompt?.save;
  const annotationChip = useMemo(() => (annotated ? {
    key: "annotation", label: "Annotation", glyph: "pencil",
    ...(annotationOf ? { copy: () => (saveFile ? saveFile("annotation", annotationOf()) : JSON.stringify(annotationOf(), null, 2)) } : {}), ...(wipe ? { onRemove: wipe } : {})
  } : null), [annotated, annotationOf, wipe, saveFile]);
  // A chip for each measurement too, in its colour (`measurements`: the renderer's). They stand in the
  // order they came — picked, drawn or measured — whatever their kind: each keeps the place it first
  // took, and one that goes and comes back takes a new one at the end.
  const order = useRef(new Map());
  const copiedChips = useRef(new Map());
  const shownChips = useMemo(() => {
    const all = [annotationChip, ...(measurements?.chips || EMPTY), ...(chips || EMPTY)].filter(Boolean);
    const seen = order.current;
    const keys = new Set(all.map(chip => chip.key));
    for (const key of [...seen.keys()]) if (!keys.has(key)) seen.delete(key);
    for (const chip of all) if (!seen.has(chip.key)) seen.set(chip.key, seen.size ? Math.max(...seen.values()) + 1 : 0);
    // What each copies is kept as it goes, so a paste of it into the note is its pill (`pastedLines`).
    return all.sort((a, b) => seen.get(a.key) - seen.get(b.key)).map(chip => (typeof chip.copy === "function" ? { ...chip, copy: async () => {
      const value = await chip.copy();
      if (value) copiedChips.current.set(String(value).trim(), chip);
      return value;
    } } : chip));
  }, [annotationChip, measurements?.chips, chips]);
  return <div ref={root} className={cn("pointer-events-none flex-col items-end", hidden ? "hidden" : "flex", className)} style={style} data-quick-edit="">
    {open ? <section ref={box} aria-label="Quick Edit" data-quick-edit-box=""
      // A narrow viewer (a phone) gets it across its whole width, over the tool stack rather than beside it.
      // It opens in 150ms, animate-in's own: a `duration-*` class would also ease every width a drag
      // writes (the transition is of `all`), and the box would trail its corner.
      className={cn("pointer-events-auto relative flex w-[min(15rem,100%)] max-w-full origin-top-right flex-col gap-2 rounded-lg p-2.5 text-tiny animate-in fade-in-0 zoom-in-95 @max-md/cad-viewport:!w-full",
        FLOATING_SURFACE_CLASS)}>
      <div className="flex min-h-5 min-w-0 items-center gap-2">
        <h3 className="shrink-0 pl-0.5 text-xs font-medium leading-5 text-foreground">Quick Edit</h3>
        {/* What goes with the note, as it is now; the file always does. */}
        <p className="flex min-w-0 flex-1 items-center gap-1.5 truncate text-tiny text-muted-foreground" data-quick-edit-attachments="">
          {referenceCount ? <TooltipHint content={<span className="block whitespace-pre-line text-left">{referenceIds.join("\n")}</span>}>
            <span tabIndex={0} className="rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/45" data-quick-edit-chip="references">
              {referenceCount} {referenceCount === 1 ? "ref" : "refs"}
            </span>
          </TooltipHint> : null}
        </p>
        <button type="button" aria-label="Close Quick Edit" className={TOOL_PANEL_BUTTON_CLASS} onClick={clearAll}>
          <X className="size-3" aria-hidden="true" />
        </button>
      </div>
      {shownChips.length ? <ReferenceChips chips={shownChips} className="-mt-0.5" /> : null}
      {/* Plain text with references inline. It grows with what is written, up to 10rem, until a drag of
          the corner gives it a height, and scrolls in the chrome's own ScrollArea past that; its
          placeholder shows while it is empty. */}
      <ScrollArea ref={noteBox} data-quick-edit-note-box=""
        className="max-h-40 min-h-14 w-full rounded-md border border-input bg-background/80 transition-[border-color,box-shadow] focus-within:border-blue-500 focus-within:ring-2 focus-within:ring-blue-500/20 dark:focus-within:border-blue-400 dark:focus-within:ring-blue-400/20">
        <div ref={field} role="textbox" aria-multiline="true" aria-label="Describe your changes" tabIndex={0}
          contentEditable="plaintext-only" suppressContentEditableWarning data-placeholder="Describe your changes" data-quick-edit-note=""
          className="min-h-[3.375rem] w-full whitespace-pre-wrap break-words px-2 py-1.5 text-xs leading-5 text-foreground outline-none empty:before:pointer-events-none empty:before:text-muted-foreground empty:before:content-[attr(data-placeholder)]"
          onInput={event => readNote(event.currentTarget)} onKeyDown={onKeyDown} onPaste={onPaste} onMouseDown={onNoteMouseDown} />
      </ScrollArea>
      {error ? <p role="alert" className="text-tiny text-destructive">{error}</p> : null}
      {/* Buttons of the ordinary shape, all solid: the primary in its colour, the others secondary.
          Copy Prompt alone (a host that takes no message) says so in words, its icon first. */}
      <div className="flex items-center justify-end gap-1.5">
        {actions.length === 1 ? <Button type="button" variant="default" size="sm" className="h-7 gap-1.5 px-2.5 text-xs"
          aria-label={copied ? "Prompt copied" : undefined} aria-busy={pending === "copy" || undefined} disabled={!ready} onClick={() => run("copy")} data-quick-edit-action="copy">
          {copied ? <Check className="size-3.5" aria-hidden="true" /> : <Copy className="size-3.5" aria-hidden="true" />}
          {copied ? "Copied" : "Copy Prompt"}
        </Button> : actions.map(({ id, label, hint, Icon }) => <TooltipHint key={id} content={hint} side="bottom">
          <Button type="button" variant={id === primary ? "default" : "secondary"} size="icon-sm" className="size-7"
            aria-label={id === "copy" && copied ? "Prompt copied" : label} disabled={!ready} aria-busy={pending === id || undefined} onClick={() => run(id)} data-quick-edit-action={id}>
            {id === "copy" && copied ? <Check className="size-3.5" aria-hidden="true" /> : <Icon className="size-3.5" aria-hidden="true" />}
          </Button>
        </TooltipHint>)}
      </div>
      {/* The grip every resizable box over the viewport has (`ResizeGrip.jsx`), in this one's bottom-left. */}
      <ResizeGrip corner="bottom-left" aria-hidden="true" onPointerDown={startResize} data-quick-edit-resize="" className="@max-md/cad-viewport:hidden" />
    </section> : null}
  </div>;
}
