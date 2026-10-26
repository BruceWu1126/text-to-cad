import { useCallback, useEffect, useRef, useState, type ChangeEvent } from "react";
import { Box, LayoutGrid, List, Pin, Search, Trash2, X } from "lucide-react";
import { Button } from "../primitives/button.jsx";
import { Input } from "../primitives/input.jsx";
import { Spinner } from "../primitives/spinner.jsx";
import { useI18n } from "../i18n/index.js";
import type { LibraryLayout } from "../tab-store/tabRecord.js";
import wordmark from "../assets/logo-texttocad.svg";
import { HomeLinks } from "../file-viewer/navigation/NavbarLinks.jsx";
import type { ViewerLinks } from "../host/types.js";
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

export function filterModels<Model extends LibraryModel>(items: readonly Model[], query: string): Model[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  return items.filter(item => words.every(word => item.path.toLowerCase().includes(word)));
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
 * switch, both there with no models yet too; then the models, pinned first, as solid cards (a
 * picture over the name and how long ago it was last opened or updated) or as rows. A card can be pinned (its pin
 * filled); a row can also be removed. It is the library alone: nothing opens a file from here but
 * its cards. It is drawn on the navbar's colour.
 *
 * Nothing waits unseen. While the list is read, placeholder cards (or rows) stand where the models
 * will be. A model being opened shows a spinner over its picture, and the library takes no other
 * open until the host has opened it or failed to. A card on screen that wants a picture
 * (`wantsPicture`) is handed to `picture`, where the viewer draws one out of sight — one card at a
 * time, each once while the page is up — and the list is read again once one is kept.
 */
export function ModelLibrary<Model extends LibraryModel>({ library, layout = "grid", onLayoutChange, picture, links, onError }: {
  library: ModelLibrarySource<Model>;
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
  const [query, setQuery] = useState("");
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
    setError("");
    setOpening(item.path);
    void current.current.open(item).catch(fail).finally(() => setOpening(null));
  };
  const change = (action: "pin" | "unpin" | "remove" | "delete", item: Model) => { void current.current.change(action, item).then(setItems, fail); };
  const all = items ?? [];
  // The host lists pinned models first; a search keeps that order.
  const searchQuery = query;
  const shown = filterModels(all, searchQuery);
  const now = Date.now();
  // A card pins; a row pins, and deletes a model or removes a file.
  const actions = (item: Model, removable: boolean) => <div className="cad-library-actions" data-pinned={item.pinned || undefined}>
    <Button variant="ghost" size="icon-xs" aria-label={t(item.pinned ? "home.unpinAria" : "home.pinAria", { name: item.name })} aria-pressed={item.pinned} onClick={() => change(item.pinned ? "unpin" : "pin", item)}><Pin aria-hidden="true" fill={item.pinned ? "currentColor" : "none"} /></Button>
    {removable ? deletes(item)
      ? <Button variant="ghost" size="icon-xs" aria-label={t("home.deleteAria", { name: item.name })} onClick={() => change("delete", item)}><Trash2 aria-hidden="true" /></Button>
      : <Button variant="ghost" size="icon-xs" aria-label={t("home.removeAria", { name: item.name })} onClick={() => change("remove", item)}><X aria-hidden="true" /></Button> : null}
  </div>;
  const status = (item: Model) => item.missing ? t("home.fileUnavailable") : ageLabel(lastActivity(item), now, t, locale);
  const models = layout === "list"
    ? <ul className="cad-library-list" aria-label={t("home.filesAria")}>{shown.map(item => <li key={item.path} className="cad-library-row" data-missing={item.missing || undefined} data-opening={opening === item.path || undefined}>
      <button type="button" className="cad-library-open" disabled={item.missing} aria-label={t("home.openAria", { name: item.name })} aria-busy={opening === item.path || undefined} onClick={() => open(item)}>
        <Thumbnail item={item} load={load} seen={seen} opening={opening === item.path} />
        <span className="cad-library-name">{item.name}</span>
        <span className="cad-library-status">{status(item)}</span>
      </button>
      {actions(item, true)}
    </li>)}</ul>
    : <ul className="cad-library-grid" aria-label={t("home.filesAria")}>{shown.map(item => <li key={item.path} className="cad-library-card" data-missing={item.missing || undefined} data-opening={opening === item.path || undefined}>
      <button type="button" className="cad-library-open" disabled={item.missing} aria-label={t("home.openAria", { name: item.name })} aria-busy={opening === item.path || undefined} onClick={() => open(item)}>
        <Thumbnail item={item} load={load} seen={seen} opening={opening === item.path} />
        <span className="cad-library-body">
          <span className="cad-library-name">{item.name}</span>
          <span className="cad-library-status">{status(item)}</span>
        </span>
      </button>
      {actions(item, false)}
    </li>)}</ul>;
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
            <Button variant="ghost" size="icon-sm" aria-label={t("home.grid")} aria-pressed={layout === "grid"} onClick={() => onLayoutChange("grid")}><LayoutGrid aria-hidden="true" /></Button>
            <Button variant="ghost" size="icon-sm" aria-label={t("home.list")} aria-pressed={layout === "list"} onClick={() => onLayoutChange("list")}><List aria-hidden="true" /></Button>
          </div> : null}
        </div>
      </div>
      {error ? <div className="cad-library-error" role="alert"><p>{error}</p></div> : null}
      {items === null ? <Placeholders layout={layout} />
        : searchQuery && !shown.length ? <p className="cad-library-empty" role="status">{t("home.noMatchingModels")}</p>
          : !all.length ? <p className="cad-library-empty">{t("home.empty")}</p>
            : models}
    </main>
  </div>;
}
