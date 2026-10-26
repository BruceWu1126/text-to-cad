import { ArrowLeft, ArrowRight, ChevronDown, Ellipsis } from "lucide-react";
import { useRef, useState } from "react";

import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@text-to-cad/ui/primitives/dropdown-menu";
import { Popover, PopoverContent, PopoverTrigger } from "@text-to-cad/ui/primitives/popover";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@text-to-cad/ui/primitives/tabs";
import { useI18n } from "@text-to-cad/ui/i18n";
import { cn } from "@text-to-cad/ui/utils";

import logo from "../../assets/logo-c.svg";
import wordmark from "../../assets/logo-texttocad.svg";
import { NAVBAR_CONTROLS_CLASS, NAVBAR_ROW_CLASS } from "../../lib/navbarRow.js";
import { FLOATING_SURFACE_CLASS } from "../../lib/floatingSurface.js";
import { useViewerMobile } from "../responsive.js";

import { AppMenu } from "./AppMenu.jsx";
import { DocumentGlyph } from "./DocumentGlyph.jsx";
import { entryMenu } from "./entry-menu.js";
import { FolderExplorer } from "./FolderExplorer.jsx";
import { RecentList } from "./RecentList.jsx";
import { useControlUsage } from "../../usage/controls.js";

// The file menu's item as telemetry names it (`usage/controls.js`).
const FILE_ACTION_CONTROLS = Object.freeze({ "copy-path": "file_copy_path", reveal: "file_reveal" });
// The name menu's tabs as telemetry names them: Files is the explorer.
const TAB_CONTROLS = Object.freeze({ recent: "recent", files: "explorer" });

/**
 * The row above a file and above the host's home in a page's chrome (`FileViewer`'s `chrome: "page"`,
 * the web viewer's): the same row whatever is open, so a person finds each control in the same place.
 * Over the home it has only the logo (its menu without Back to files), Back and Forward, the host's
 * update and its actions. A host that shows the view alone (`chrome: "bare"`: the CAD app's cards and
 * tabs) has no navbar, and Preview hides it in every host, drawing its own bar in its place.
 *
 * Left, in this order: the text-to-cad logo (the TEXTTOCAD wordmark over the home, the C over a
 * document), which opens the app's menu (`AppMenu`: Back to files
 * where the host has a home, then the person's settings, feedback and links); Back and Forward,
 * where the host keeps the view's own history (`history`); the open document's name, after its
 * kind's glyph, which opens the name menu (Recent, the host's library, and Files, the explorer)
 * where the view can show another; and the ⋯ menu of what the host can do with the file.
 *
 * Right, only what a host or a file adds: the host's update button while its install is behind
 * (`update`, the blue `UpdateButton` from `@text-to-cad/ui/update`); the file's own actions (the
 * CAD viewer's one: a dismissed alert's icon, which brings its card back); last, the host's own
 * actions (`actions`: the web viewer's Export). The colour scheme is the app menu's. A view's own
 * controls are never here: Display and Preview end its tool strip.
 *
 * Nothing here is drawn for its own sake: a control appears only where it does something.
 */

/**
 * The left of the row: the logo, the file's name and its ⋯, one kind of control — transparent until
 * the pointer is on it or its menu is open, then the same accent — and none with a tooltip: each
 * says what it is. They sit edge to edge, spaced by their own padding alone: the logo's 24px button,
 * Back, Forward and the ⋯ in 20px ones, the name 4px in from its edges — about 7px between glyphs.
 */
const NAV_ITEM_CLASS = "flex h-6 shrink-0 cursor-pointer items-center rounded-md outline-none transition-colors hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/45 data-[state=open]:bg-accent data-[state=open]:text-accent-foreground";

/**
 * The text-to-cad logo, the app's menu: over the home the TEXTTOCAD wordmark, 20px tall; over a
 * document the C, the size of the row's other icon buttons. A plain swap, with no animation: the
 * one button shows the one or the other.
 */
function LogoMenu({ onHome, menu, home = false }) {
  const { t } = useI18n();
  return <AppMenu onHome={onHome} {...menu} trigger={<button type="button" aria-label={t("navbar.menu")} className={cn(NAV_ITEM_CLASS, "cad-navbar-logo justify-center px-1")}
    data-navbar-menu="" data-logo={home ? "wordmark" : "mark"}>
    {home ? <img src={wordmark} alt="" className="cad-navbar-wordmark" draggable={false} />
      : <img src={logo} alt="" className="cad-navbar-mark" draggable={false} />}
  </button>} />;
}

// Back and Forward: the row's icon buttons, muted as the ⋯ is, and dimmed where there is nowhere to go.
const HISTORY_CLASS = cn(NAV_ITEM_CLASS, "w-5 justify-center text-muted-foreground disabled:cursor-default disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-muted-foreground");

/**
 * Back and Forward through the view's own history, as a browser's are: each disabled where it has
 * nowhere to go. The history is the host's (`viewHistory`), never the page's.
 */
function HistoryButtons({ viewHistory }) {
  const { t } = useI18n();
  const usage = useControlUsage();
  return <span className="flex shrink-0 items-center" data-navbar-history="">
    <button type="button" aria-label={t("navbar.back")} disabled={!viewHistory.canGoBack} onClick={() => { usage.used("navbar_back"); viewHistory.back(); }} className={HISTORY_CLASS}>
      <ArrowLeft className="size-3.5" aria-hidden="true" />
    </button>
    <button type="button" aria-label={t("navbar.forward")} disabled={!viewHistory.canGoForward} onClick={() => { usage.used("navbar_forward"); viewHistory.forward(); }} className={HISTORY_CLASS}>
      <ArrowRight className="size-3.5" aria-hidden="true" />
    </button>
  </span>;
}

/** The ⋯ after the file's name: what the host can do with the file. A host that can do nothing has none. */
function FileMenu({ path, platform, capabilities, onAction }) {
  const { t } = useI18n();
  const usage = useControlUsage();
  const items = entryMenu(platform, capabilities, t);
  if (!items.length) return null;
  return <DropdownMenu modal={false}>
    <DropdownMenuTrigger asChild><button aria-label={t("navbar.fileActions")} data-testid="file-actions" type="button"
      className={cn(NAV_ITEM_CLASS, "w-5 justify-center text-muted-foreground")}>
      <Ellipsis className="size-3.5" />
    </button></DropdownMenuTrigger>
    <DropdownMenuContent align="start" className="w-56" sideOffset={6}>
      {items.map(item => <DropdownMenuItem key={item.action} onSelect={() => { usage.used(FILE_ACTION_CONTROLS[item.action]); onAction(item.action, path); }}>{item.label}</DropdownMenuItem>)}
    </DropdownMenuContent>
  </DropdownMenu>;
}

/**
 * What the name opens: Recent and Files, two stock tabs on top of the explorer's own style, each
 * there only where the host has it (one alone has no tabs). Recent is the host's library, by last
 * activity, a row opening its document; Files is the explorer (`FolderExplorer`), from the file's
 * folder, or the folder a model was registered from. Built anew on each opening, on Recent over a
 * model and on Files over a file.
 */
function NameMenu({ path, kind, explorer, recent, onPick }) {
  const { t } = useI18n();
  const usage = useControlUsage();
  const [tab, setTab] = useState(() => firstTab(kind, explorer, recent));
  // The tab on screen as last chosen: a press and the focus it brings both ask for the same tab.
  const shownTab = useRef(tab);
  const files = explorer ? <FolderExplorer source={explorer.source} file={path} folder={explorer.folder} onOpen={next => onPick(explorer.onOpen, next)} /> : null;
  const recents = recent ? <RecentList recent={recent} current={path} onOpen={next => { usage.used("recent_open"); onPick(recent.onOpen, next); }} /> : null;
  if (!files || !recents) return files ?? recents;
  // A tab a person turns to is a use of it, as the one the menu opens on is (`FileName`).
  return <Tabs value={tab} onValueChange={next => { if (next !== shownTab.current) usage.used(TAB_CONTROLS[next]); shownTab.current = next; setTab(next); }} className="flex min-h-0 flex-1 flex-col gap-0" data-name-menu="">
    <TabsList className="mx-2 mt-2 h-7 w-auto shrink-0 self-stretch">
      <TabsTrigger value="recent">{t("explorer.recent")}</TabsTrigger>
      <TabsTrigger value="files">{t("explorer.files")}</TabsTrigger>
    </TabsList>
    <TabsContent value="recent" className="flex min-h-0 flex-col">{recents}</TabsContent>
    <TabsContent value="files" className="flex min-h-0 flex-col">{files}</TabsContent>
  </Tabs>;
}

/** The tab the name menu opens on: Recent over a model, Files over a file, or the one there is. */
const firstTab = (kind, explorer, recent) => (!explorer ? "recent" : !recent ? "files" : kind === "model" ? "recent" : "files");

/**
 * The open document's name, after its kind's glyph (a model's code glyph, a file's type icon).
 * Where the view can show another document it is also the name menu's door: the name, transparent
 * until the pointer is on it, opening Recent and Files under it. Where it cannot, it is the name
 * alone, in the same place.
 */
function FileName({ path, name: given, kind = "file", explorer, recent }) {
  const [open, setOpen] = useState(false);
  const mobile = useViewerMobile();
  const usage = useControlUsage();
  const name = given || path.split("/").pop() || path;
  const glyph = <DocumentGlyph model={kind === "model"} path={path} className="size-3.5 shrink-0 text-muted-foreground" strokeWidth={1.5} />;
  if (!explorer && !recent) return <span className="flex min-w-0 items-center gap-1 px-1" data-file-name="">{glyph}<span className="truncate">{name}</span></span>;
  // Opened, the explorer's filter has the keyboard, so typing finds a file at once; on Recent the
  // menu holds it, and Down takes it into the rows. A phone keeps its keyboard down, the menu
  // holding focus itself. Nothing else is focused for the person on opening.
  const focusFilter = event => {
    event.preventDefault();
    const content = event.currentTarget;
    (mobile ? content : content.querySelector("input") ?? content).focus({ preventScroll: true });
  };
  const intoRows = event => {
    if (event.key !== "ArrowDown" || event.target !== event.currentTarget) return;
    const first = event.currentTarget.querySelector("[data-recent-row]:not(:disabled), [data-explorer-row]");
    if (!first) return;
    event.preventDefault();
    first.focus();
  };
  const pick = (opener, next) => { setOpen(false); opener(next); };
  // Opened, the tab it opens on is a use of that tab (`usage/controls.js`: `recent`, `explorer`).
  return <Popover open={open} onOpenChange={next => { if (next && !open) usage.used(TAB_CONTROLS[firstTab(kind, explorer, recent)]); setOpen(next); }}>
    <PopoverTrigger asChild>
      <button type="button" className={cn(NAV_ITEM_CLASS, "min-w-0 shrink gap-1 px-1 text-left")} data-file-name="">
        {glyph}
        <span className="truncate">{name}</span>
        {/* The name opens its menu; the chevron says so. A plain name (no menu) has none. */}
        <ChevronDown className="ml-0.5 size-3 shrink-0 text-muted-foreground" aria-hidden="true" />
      </button>
    </PopoverTrigger>
    <PopoverContent align="start" sideOffset={8} data-file-explorer="" onOpenAutoFocus={focusFilter} onKeyDown={intoRows}
      className={cn(FLOATING_SURFACE_CLASS, "flex max-h-[min(28rem,var(--radix-popover-content-available-height))] w-72 max-w-[calc(100vw-1rem)] flex-col overflow-hidden p-0")}>
      {/* Remounted on each opening, so it starts on its first tab, in the open file's folder. */}
      <NameMenu path={path} kind={kind} explorer={explorer} recent={recent} onPick={pick} />
    </PopoverContent>
  </Popover>;
}

/**
 * @param {object} props
 * @param {(() => void) | undefined} [props.onHome] The host's home, where it has one: the menu's Back to files.
 * @param {{ links?: import("../../host/types.js").ViewerLinks, appSettings?: readonly import("../types.js").AppSetting[], colorScheme?: import("../types.js").ColorSchemeSetting, platform?: string } | null} [props.menu]
 *   The rest of the app's menu: the host's links and the person's settings.
 * @param {import("../types.js").ViewerHistory | null} [props.history] The view's own history, where the host keeps one: Back and Forward after the logo.
 * @param {string | null} [props.file] The open file, by its absolute path (a document with no file of its own: its id).
 * @param {string} [props.name] The document's name where its path does not give it (a model's).
 * @param {"model" | "file"} [props.kind] What the open document is: its glyph, and the name menu's first tab.
 * @param {{ source: Pick<import("../types").FileSource, "list" | "search">, folder?: string | null, onOpen(path: string): void } | null} [props.explorer]
 *   Where the host's files can be browsed: the name menu's Files, from `folder` where given.
 * @param {{ list(): Promise<readonly import("../types").RecentDocument[]>, onOpen(path: string): void } | null} [props.recent]
 *   The host's library: the name menu's Recent.
 * @param {{ platform: import("./entry-menu.js").Platform, capabilities: ReadonlySet<import("./entry-menu.js").EntryAction>,
 *   onAction(action: import("./entry-menu.js").EntryAction, path: string): void } | null} [props.fileMenu]
 * @param {import("react").ReactNode} [props.trailing] The file's actions.
 * @param {import("react").ReactNode} [props.actions] Host workflow controls at the right end.
 * @param {import("react").ReactNode} [props.update] The host's update button, first among the controls.
 * @param {string} [props.className]
 */
export function ViewerNavbar({ onHome: goHome, menu = null, history = null, file = null, name, kind = "file", explorer = null, recent = null, fileMenu = null, trailing = null, update = null, actions = null, className }) {
  const { t } = useI18n();
  const usage = useControlUsage();
  const onHome = goHome ? () => { usage.used("home"); goHome(); } : goHome;
  return (
    <header className={cn(NAVBAR_ROW_CLASS, "border-border bg-background text-foreground", className)} data-viewer-navbar="">
      <nav aria-label={t("navbar.viewer")} className="flex min-w-0 flex-1 items-center overflow-hidden text-sm">
        {onHome || menu ? <LogoMenu onHome={onHome} menu={menu} home={!file} /> : null}
        {history ? <HistoryButtons viewHistory={history} /> : null}
        {file ? <span className="flex min-w-0 items-center">
          <FileName path={file} name={name} kind={kind} explorer={explorer} recent={recent} />
          {fileMenu ? <FileMenu path={file} {...fileMenu} /> : null}
        </span> : null}
      </nav>
      <div className={NAVBAR_CONTROLS_CLASS}>
        {update}
        {trailing}
        {actions}
      </div>
    </header>
  );
}
