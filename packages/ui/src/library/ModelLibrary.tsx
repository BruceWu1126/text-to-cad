import { useCallback, useEffect, useRef, useState, type ChangeEvent, type ReactNode } from "react";
import { Box, ChevronRight, Ellipsis, LayoutGrid, List, Pin, Search } from "lucide-react";
import { Button } from "../primitives/button.jsx";
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle } from "../primitives/alert-dialog.jsx";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "../primitives/dropdown-menu.jsx";
import { Input } from "../primitives/input.jsx";
import { Spinner } from "../primitives/spinner.jsx";
import { useI18n } from "../i18n/index.js";
import type { LibraryLayout } from "../tab-store/tabRecord.js";
import wordmark from "../assets/logo-texttocad.svg";
import { HomeLinks } from "../file-viewer/navigation/NavbarLinks.jsx";
import { DocumentGlyph } from "../file-viewer/navigation/DocumentGlyph.jsx";
import { entryMenu } from "../file-viewer/navigation/entry-menu.js";
import type { EntryAction } from "../file-viewer/navigation/index.js";
import type { FileActions } from "../file-viewer/types.js";
import type { ViewerLinks } from "../host/types.js";
import { useControlUsage } from "../usage/controls.js";
import { ageLabel, lastActivity } from "../lib/age.js";

/** A model someone opened: kept by the host, which also says where it is shown from. */
export interface LibraryModel {
  /** The model's identity in the host's library; the absolute path where there is one. */
  path: string;
  name: string;
  /** Where it is, as the person reads it. */
  folder: string;
  /** When a page last opened it, in seconds; null for a model no page has opened. */
  opened: number | null;
  /** When it last changed, in seconds: a file's time on disk (null once it is gone), a model's run. */
  updated: number | null;
  pinned: boolean;
  /** Gone from where it was: listed, but not openable. */
  missing: boolean;
  /** A picture's name, for `thumbnail()`. */
  thumbnail: string | null;
  /** When the picture was taken, in seconds; null without one. A document updated since has an old one. */
  pictured: number | null;
  /**
   * What it is: a document built from code, a model's or a shape's (`model`, `shape`), which a row
   * deletes -- the engine's copy and its entry; or a CAD file on disk (`file`, and when the host does
   * not say), which a row removes from the list, the file untouched.
   */
  kind?: "file" | "model" | "shape";
  /** A model's or a shape's: the script its run registered (a shape's home), its card's Copy path and Reveal. */
  script?: string | null;
}

/** Whether a row deletes the model (a document built from code) rather than removing a file from the list. */
export const deletes = (model: LibraryModel) => model.kind === "model" || model.kind === "shape";

/** A host's library of models: read, changed and opened through the host. */
export interface ModelLibrarySource<Model extends LibraryModel = LibraryModel> {
  /** Pinned first, then by last activity (`lastActivity`), newest first. */
  list(): Promise<readonly Model[]>;
  /** `delete` for a model's row (`deletes`), `remove` for a file's. */
  change(action: "pin" | "unpin" | "remove" | "delete", model: Model): Promise<readonly Model[]>;
  /** An image URL for a model's thumbnail, or null. */
  thumbnail(name: string): Promise<string | null>;
  open(model: Model): Promise<void>;
}

/** A card wants a picture: it has none, or it was updated (a file written, a model run) since it was taken. */
export const wantsPicture = (model: LibraryModel) => !model.missing && (!model.thumbnail
  || (model.updated !== null && model.pictured !== null && model.updated > model.pictured));

/** Whether every word of `query` is in the model's name or where it is (a file's path, a model's script). */
const matches = (item: LibraryModel, words: readonly string[]) => {
  const text = `${item.name}\n${item.path}\n${item.script ?? ""}`.toLowerCase();
  return words.every(word => text.includes(word));
};
const wordsOf = (query: string) => query.trim().toLowerCase().split(/\s+/).filter(Boolean);

export function filterModels<Model extends LibraryModel>(items: readonly Model[], query: string): Model[] {
  const words = wordsOf(query);
  return items.filter(item => matches(item, words));
}

/** What the type chips above the list show: everything, the documents built from code, or the files. */
export type LibraryKind = "all" | "models" | "files";
const ofKind = (item: LibraryModel, kind: LibraryKind) => kind === "all" || (kind === "models") === deletes(item);

/** One card of the Home: a document, and the other models of its script folded under it. */
export interface LibraryCard<Model extends LibraryModel = LibraryModel> {
  item: Model;
  folded: readonly Model[];
}

const stem = (path: string) => (path.split(/[\\/]/).pop() ?? "").replace(/\.[^.]*$/, "");

/**
 * The model a script's card is named for: the one named as its script is (juno.py's juno), else
 * the one its runs built last (an assembly finishes after the parts it calls), else the first.
 * Read from the records alone, never the script.
 */
export function mainModel<Model extends LibraryModel>(models: readonly Model[]): Model {
  const named = models.find(model => model.script && model.name === stem(model.script));
  return named ?? models.reduce((best, model) => ((model.updated ?? 0) > (best.updated ?? 0) ? model : best), models[0]);
}

/**
 * The library as cards, in its order: a script that declares several models is one card, at the
 * place of the first of them, named for its main model (`mainModel`) with the others folded under
 * it; every other document is a card of its own.
 */
export function libraryCards<Model extends LibraryModel>(items: readonly Model[]): LibraryCard<Model>[] {
  const scripts = new Map<string, Model[]>();
  for (const item of items) {
    if (item.kind !== "model" || !item.script) continue;
    const group = scripts.get(item.script) ?? [];
    group.push(item);
    scripts.set(item.script, group);
  }
  const placed = new Set<string>();
  const cards: LibraryCard<Model>[] = [];
  for (const item of items) {
    const group = item.kind === "model" && item.script ? scripts.get(item.script) : undefined;
    if (!group) { cards.push({ item, folded: [] }); continue; }
    if (placed.has(item.script!)) continue;
    placed.add(item.script!);
    const main = mainModel(group);
    cards.push({ item: main, folded: group.filter(model => model !== main) });
  }
  return cards;
}

/** The cards a search and a type chip leave: a card stays when its model or one folded under it matches. */
export function shownCards<Model extends LibraryModel>(cards: readonly LibraryCard<Model>[], query: string, kind: LibraryKind = "all"): LibraryCard<Model>[] {
  const words = wordsOf(query);
  return cards.filter(card => ofKind(card.item, kind) && [card.item, ...card.folded].some(item => matches(item, words)));
}

const message = (failure: unknown) => failure instanceof Error ? failure.message : String(failure);
/** How often the home reads its list again while it is up. */
const LIST_MS = 2_000;
// The chrome's one scroll region (`primitives/scroll-area.jsx`), as this page uses it.

// A card's picture, read once the card is on screen; and a card on screen that wants one (`seen`).
function Thumbnail<Model extends LibraryModel>({ item, load, seen, opening = false }: { item: Model; load(name: string): Promise<string | null>; seen(item: Model): void; opening?: boolean }) {
  const { t } = useI18n();
  const element = useRef<HTMLSpanElement>(null);
  const [image, setImage] = useState<string | null>(null);
  const latest = useRef({ item, seen });
  latest.current = { item, seen };
  useEffect(() => {
    let active = true;
    // A new picture replaces the old one once it has loaded, never through the placeholder.
    if (!item.thumbnail || item.missing) setImage(null);
    if (item.missing || !element.current) return;
    const name = item.thumbnail;
    const show = () => {
      if (name) void load(name).then(value => { if (active) setImage(value); }).catch(() => {});
      latest.current.seen(latest.current.item);
    };
    if (typeof IntersectionObserver === "undefined") { show(); return () => { active = false; }; }
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      observer.disconnect();
      show();
    });
    observer.observe(element.current);
    return () => { active = false; observer.disconnect(); };
  }, [item.path, item.thumbnail, item.missing, item.updated, load]);
  return <span className="cad-library-thumbnail" ref={element}>
    {image ? <img src={image} alt="" /> : <Box strokeWidth={1} aria-hidden="true" />}
    {opening ? <span className="cad-library-opening"><Spinner aria-label={t("home.openingAria", { name: item.name })} /></span> : null}
  </span>;
}

/** Where the models will be while the list is read: cards (or rows) in their own places, pulsing. */
function Placeholders({ layout }: { layout: LibraryLayout }) {
  const { t } = useI18n();
  const status = <li className="sr-only" role="status">{t("home.loadingFiles")}</li>;
  return layout === "list"
    ? <ul className="cad-library-list" aria-label={t("home.filesAria")} aria-busy="true" data-loading="">{status}{[0, 1, 2].map(index => <li key={index} className="cad-library-row" aria-hidden="true">
      <span className="cad-library-open"><span className="cad-library-thumbnail" /><span className="cad-library-placeholder" /></span>
    </li>)}</ul>
    : <ul className="cad-library-grid" aria-label={t("home.filesAria")} aria-busy="true" data-loading="">{status}{[0, 1, 2, 3].map(index => <li key={index} className="cad-library-card" aria-hidden="true">
      <span className="cad-library-open">
        <span className="cad-library-thumbnail" />
        <span className="cad-library-body"><span className="cad-library-placeholder" /><span className="cad-library-placeholder" data-short="" /></span>
      </span>
    </li>)}</ul>;
}

/**
 * The host's home: the models opened before, from every view, to open again. The viewer's navbar
 * is over it (its logo's app menu, the host's update and Full size, as over a file). The TEXTTOCAD
 * wordmark is centred at its top over its byline, then the host's links — GitHub, Discord and X,
 * as icon links, which the app menu also holds — then "Recent" with its search and its grid/list
 * switch, both there with no models yet too; then, where models and files are both listed, the
 * type chips (All, Models, Files); then the documents, pinned first, as solid cards (a picture over
 * the kind's glyph, the name and how long ago it was last opened or updated, with no verb) or as
 * rows. A script that declares several models is one card, named for its main model, the others
 * folded under it ("and 27 more"), opened in place. Each card has one ⋯ menu: Open, Copy path and
 * Reveal where the host can, Pin or Unpin, then Delete, which asks first (an AlertDialog saying what
 * goes and what stays on disk) and takes the card off the list at once; a pinned card shows its pin. It is the
 * library alone: nothing opens a file from here but its cards. It is drawn on the navbar's colour.
 *
 * Nothing waits unseen. While the list is read, placeholder cards (or rows) stand where the models
 * will be. A model being opened shows a spinner over its picture, and the library takes no other
 * open until the host has opened it or failed to. A card on screen that wants a picture
 * (`wantsPicture`) is handed to `picture`, where the viewer draws one out of sight — one card at a
 * time, each once while the page is up — and the list is read again once one is kept.
 */
export function ModelLibrary<Model extends LibraryModel>({ library, layout = "grid", onLayoutChange, picture, links, fileActions, onError }: {
  library: ModelLibrarySource<Model>;
  /** What the host can do with a file on disk (Copy path, Reveal): each card's menu offers it. */
  fileActions?: FileActions;
  /** Grid or list; the host keeps the choice (the tab's settings). */
  layout?: LibraryLayout;
  onLayoutChange?(layout: LibraryLayout): void;
  /** Draw and keep a model's picture; true once it is kept. */
  picture?(model: Model): Promise<boolean>;
  /** The host's links, under the wordmark: GitHub, Discord and X. */
  links?: ViewerLinks;
  onError?(error: Error): void;
}) {
  const [items, setItems] = useState<readonly Model[] | null>(null);
  const [query, setQueryState] = useState("");
  // What a person does on the home, counted (`usage/controls.js`): a search begun, never what it looks for.
  const usage = useControlUsage();
  const setQuery = (next: string) => { if (!query.trim() && next.trim()) usage.used("home_search"); setQueryState(next); };
  const [error, setError] = useState("");
  const { t, locale } = useI18n();
  // The host's library as it is now: a host may hand a new one each render.
  const current = useRef(library);
  current.current = library;
  const images = useRef(new Map<string, Promise<string | null>>());
  // The same list again is not news: the cards keep their state and are not drawn again.
  const refresh = useCallback(() => current.current.list().then(next =>
    setItems(previous => previous && JSON.stringify(previous) === JSON.stringify(next) ? previous : next)), []);
  // Read on mount, again every couple of seconds while the home is up (a model rebuilt meanwhile
  // is updated later than its picture, and is pictured again), and when the page is shown.
  useEffect(() => {
    void refresh().catch(failure => { setItems([]); setError(message(failure)); });
    const again = () => { void refresh().catch(() => {}); };
    const timer = setInterval(again, LIST_MS);
    document.addEventListener("visibilitychange", again);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", again); };
  }, [refresh]);
  // The cards on screen that want a picture, in the order they came into view, drawn one at a time.
  const draw = useRef(picture);
  draw.current = picture;
  const asked = useRef(new Set<string>());
  const [queue, setQueue] = useState<readonly Model[]>([]);
  // Once per card and update: a file written or a model run again wants its picture again.
  const seen = useCallback((item: Model) => {
    const key = JSON.stringify([item.path, item.updated]);
    if (!draw.current || asked.current.has(key) || !wantsPicture(item)) return;
    asked.current.add(key);
    setQueue(waiting => [...waiting, item]);
  }, []);
  const drawing = useRef(false);
  useEffect(() => {
    const next = queue[0];
    if (!next || drawing.current || !draw.current) return;
    drawing.current = true;
    const done = (kept: boolean) => {
      drawing.current = false;
      setQueue(waiting => waiting.slice(1));
      if (kept) void refresh().catch(() => {});
    };
    draw.current(next).then(done, () => done(false));
  }, [queue, refresh]);
  const load = useCallback((name: string) => {
    let image = images.current.get(name);
    if (!image) {
      image = current.current.thumbnail(name);
      images.current.set(name, image);
    }
    return image;
  }, []);
  const fail = (failure: unknown) => setError(message(failure));
  // The model being opened: the host may take a moment, and a second press must not open twice.
  const [opening, setOpening] = useState<string | null>(null);
  const open = (item: Model) => {
    if (opening) return;
    usage.used("home_open");
    setError("");
    setOpening(item.path);
    void current.current.open(item).catch(fail).finally(() => setOpening(null));
  };
  const change = (action: "pin" | "unpin" | "remove" | "delete", item: Model) => current.current.change(action, item).then(setItems, fail);
  // Delete, once the person says so: a model's (or a shape's) document and its record, every model
  // of a script's card; a file's record alone, the file kept. The card leaves the list at once.
  const [confirming, setConfirming] = useState<LibraryCard<Model> | null>(null);
  const [gone, setGone] = useState<ReadonlySet<string>>(() => new Set());
  const remove = (card: LibraryCard<Model>) => {
    const doomed = [card.item, ...card.folded];
    setGone(previous => new Set([...previous, ...doomed.map(item => item.path)]));
    void doomed.reduce<Promise<unknown>>((before, item) => before.then(() => change(deletes(item) ? "delete" : "remove", item)), Promise.resolve())
      .finally(() => setGone(previous => { const next = new Set(previous); for (const item of doomed) next.delete(item.path); return next; }));
  };
  const all = (items ?? []).filter(item => !gone.has(item.path));
  const [kind, setKind] = useState<LibraryKind>("all");
  // The scripts whose folded models are shown, by script: collapsed until a press opens one.
  const [unfolded, setUnfolded] = useState<ReadonlySet<string>>(() => new Set());
  const unfold = (script: string) => {
    if (!unfolded.has(script)) usage.used("home_fold");
    setUnfolded(previous => {
      const next = new Set(previous);
      if (!next.delete(script)) next.add(script);
      return next;
    });
  };
  // The host lists pinned models first; a search and a chip keep that order.
  const cards = libraryCards(all);
  const shown = shownCards(cards, query, kind);
  // The chips filter only where there is something to tell apart: models and files both listed.
  const kinds = all.some(deletes) && all.some(item => !deletes(item));
  const now = Date.now();
  // What the host can do with a document on disk (the file, a model's script): the file menu's items.
  const fileItems = entryMenu(fileActions?.platform ?? "linux", new Set(Object.keys(fileActions?.perform ?? {}) as EntryAction[]), t);
  const perform = (action: EntryAction, item: Model) => {
    const path = deletes(item) ? item.script : item.path;
    if (!path) return;
    usage.used(action === "reveal" ? "home_reveal" : "home_copy_path");
    void Promise.resolve(fileActions?.perform?.[action]?.({ path, kind: "file" })).catch(fail);
  };
  // A card's one menu: Open, what the host can do with it on disk, Pin or Unpin, then Delete. A
  // pinned card shows its pin beside the menu, whatever the pointer does.
  const actions = (card: LibraryCard<Model>) => { const item = card.item; return <div className="cad-library-actions" data-pinned={item.pinned || undefined}>
    {item.pinned ? <Pin className="cad-library-pinned" role="img" aria-label={t("home.pinned")} fill="currentColor" /> : null}
    <DropdownMenu modal={false}>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-xs" aria-label={t("home.actionsAria", { name: item.name })}><Ellipsis aria-hidden="true" /></Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-40">
        <DropdownMenuItem disabled={item.missing} onSelect={() => open(item)}>{t("home.menuOpen")}</DropdownMenuItem>
        {(deletes(item) ? item.script : item.path) ? fileItems.map(entry => <DropdownMenuItem key={entry.action} onSelect={() => perform(entry.action, item)}>{entry.label}</DropdownMenuItem>) : null}
        <DropdownMenuItem onSelect={() => { usage.used(item.pinned ? "home_unpin" : "home_pin"); void change(item.pinned ? "unpin" : "pin", item); }}>{t(item.pinned ? "home.unpin" : "home.pin")}</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={() => setConfirming(card)}>{t("home.delete")}</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  </div>; };
  // What Delete asks first: what goes, and what stays on disk; its words stay while the dialog leaves.
  const lastAsked = useRef<LibraryCard<Model> | null>(null);
  if (confirming) lastAsked.current = confirming;
  const asking = lastAsked.current;
  const question = asking ? deletes(asking.item) ? {
    title: asking.folded.length ? t("home.deleteScriptTitle", { name: asking.item.name, n: asking.folded.length + 1 }) : t("home.deleteModelTitle", { name: asking.item.name }),
    body: t("home.deleteModelBody"),
  } : { title: t("home.deleteFileTitle", { name: asking.item.name }), body: t("home.deleteFileBody") } : null;
  const confirm = <AlertDialog open={Boolean(confirming)} onOpenChange={next => { if (!next) setConfirming(null); }}>
    <AlertDialogContent>
      <AlertDialogHeader>
        <AlertDialogTitle>{question?.title}</AlertDialogTitle>
        <AlertDialogDescription>{question?.body}</AlertDialogDescription>
      </AlertDialogHeader>
      <AlertDialogFooter>
        <AlertDialogCancel onClick={() => usage.used("home_delete_cancel")}>{t("home.cancel")}</AlertDialogCancel>
        <AlertDialogAction variant="destructive" onClick={() => { if (confirming) { usage.used("home_delete"); remove(confirming); } }}>{t("home.delete")}</AlertDialogAction>
      </AlertDialogFooter>
    </AlertDialogContent>
  </AlertDialog>;
  const status = (item: Model) => item.missing ? t("home.fileUnavailable") : ageLabel(lastActivity(item), now, t, locale);
  const name = (item: Model) => <span className="cad-library-name">
    <DocumentGlyph model={deletes(item)} path={item.path} className="cad-library-glyph" />
    <span className="cad-library-label">{item.name}</span>
  </span>;
  // A script's other models, under its card: "and 27 more", opened in place, each a row that opens it.
  const folded = (card: LibraryCard<Model>): ReactNode => {
    const script = card.item.script;
    if (!card.folded.length || !script) return null;
    const expanded = unfolded.has(script);
    return <div className="cad-library-folded">
      <button type="button" className="cad-library-more" aria-expanded={expanded} onClick={() => unfold(script)}>
        <ChevronRight className="cad-library-chevron" aria-hidden="true" />{t("home.andMore", { n: card.folded.length })}
      </button>
      {expanded ? <ul className="cad-library-children" aria-label={t("home.foldedAria", { name: card.item.name })}>
        {card.folded.map(item => <li key={item.path}>
          <button type="button" className="cad-library-child" disabled={item.missing} aria-label={t("home.openAria", { name: item.name })} onClick={() => open(item)}>
            {name(item)}<span className="cad-library-status">{status(item)}</span>
          </button>
        </li>)}
      </ul> : null}
    </div>;
  };
  const models = layout === "list"
    ? <ul className="cad-library-list" aria-label={t("home.filesAria")}>{shown.map(card => { const item = card.item; return <li key={item.path} className="cad-library-row" data-missing={item.missing || undefined} data-opening={opening === item.path || undefined}>
      <div className="cad-library-main">
        <button type="button" className="cad-library-open" disabled={item.missing} aria-label={t("home.openAria", { name: item.name })} aria-busy={opening === item.path || undefined} onClick={() => open(item)}>
          <Thumbnail item={item} load={load} seen={seen} opening={opening === item.path} />
          {name(item)}
          <span className="cad-library-status">{status(item)}</span>
        </button>
        {actions(card)}
      </div>
      {folded(card)}
    </li>; })}</ul>
    : <ul className="cad-library-grid" aria-label={t("home.filesAria")}>{shown.map(card => { const item = card.item; return <li key={item.path} className="cad-library-card" data-missing={item.missing || undefined} data-opening={opening === item.path || undefined}>
      <div className="cad-library-main">
        <button type="button" className="cad-library-open" disabled={item.missing} aria-label={t("home.openAria", { name: item.name })} aria-busy={opening === item.path || undefined} onClick={() => open(item)}>
          <Thumbnail item={item} load={load} seen={seen} opening={opening === item.path} />
          <span className="cad-library-body">
            {name(item)}
            <span className="cad-library-status">{status(item)}</span>
          </span>
        </button>
        {actions(card)}
      </div>
      {folded(card)}
    </li>; })}</ul>;
  // A page: it scrolls, with the platform's own scrollbar, only when there is more of it than fits.
  return <div className="cad-library h-full overflow-y-auto text-ui" data-library-layout={layout}>
    <main className="cad-library-content" aria-label={t("home.modelsAria")}>
      <img className="cad-library-wordmark" src={wordmark} alt="text-to-cad" />
      <p className="cad-library-byline">{t("home.byline")} <span>{t("home.bylineOpenSource")}</span></p>
      {links ? <nav className="cad-library-links" aria-label={t("home.linksAria")}>
        <HomeLinks links={links} onError={onError} />
      </nav> : null}
      <div className="cad-library-toolbar">
        <h1 className="cad-library-heading">{t("home.recentFiles")}</h1>
        <div className="cad-library-controls">
          {/* There with no models too: the toolbar keeps its shape from the first open to the hundredth. */}
          <div className="cad-library-search">
            <Search aria-hidden="true" />
            <Input className="h-8" type="search" aria-label={t("home.searchAria")} placeholder={t("home.searchPlaceholder")} value={query} onChange={(event: ChangeEvent<HTMLInputElement>) => setQuery(event.target.value)} />
          </div>
          {onLayoutChange ? <div className="cad-library-layout" role="group" aria-label={t("home.layout")}>
            <Button variant="ghost" size="icon-sm" aria-label={t("home.grid")} aria-pressed={layout === "grid"} onClick={() => { usage.used("home_layout_grid"); onLayoutChange("grid"); }}><LayoutGrid aria-hidden="true" /></Button>
            <Button variant="ghost" size="icon-sm" aria-label={t("home.list")} aria-pressed={layout === "list"} onClick={() => { usage.used("home_layout_list"); onLayoutChange("list"); }}><List aria-hidden="true" /></Button>
          </div> : null}
        </div>
      </div>
      {kinds ? <div className="cad-library-kinds" role="group" aria-label={t("home.kindsAria")}>
        {(["all", "models", "files"] as const).map(value => <Button key={value} variant={kind === value ? "secondary" : "ghost"} size="xs"
          aria-pressed={kind === value} onClick={() => { if (kind !== value) usage.used(`home_chip_${value}`); setKind(value); }}>{t(`home.kind.${value}`)}</Button>)}
      </div> : null}
      {error ? <div className="cad-library-error" role="alert"><p>{error}</p></div> : null}
      {items === null ? <Placeholders layout={layout} />
        : (query || kind !== "all") && all.length && !shown.length ? <p className="cad-library-empty" role="status">{t("home.noMatchingModels")}</p>
          : !all.length ? <p className="cad-library-empty">{t("home.empty")}</p>
            : models}
      {confirm}
    </main>
  </div>;
}
