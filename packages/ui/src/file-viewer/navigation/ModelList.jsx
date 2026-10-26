import { LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ScrollArea } from "@text-to-cad/ui/primitives/scroll-area";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { TREE_GLYPH_CLASS, TREE_GLYPH_STROKE, TREE_ROW_INSET_PX, TreeRowLabel, TreeRowSurface } from "@text-to-cad/ui/primitives/tree-row";
import { useI18n } from "@text-to-cad/ui/i18n";
import { ageLabel, lastActivity } from "../../lib/age.js";
import { DocumentGlyph } from "./DocumentGlyph.jsx";

/** The most the Models tab lists: the Home, one click away, has the rest. */
export const MODEL_ROWS = 8;

/** Whether a library row is a document built from code (a model's, a shape's), not a file. */
const built = row => row.kind === "model" || row.kind === "shape";

/**
 * The Models tab's rows: the library's models alone (no file), by last activity (the later of when
 * each was opened and updated), newest first, at most `MODEL_ROWS`.
 *
 * @template {{ path: string, kind?: string, opened?: number | null, updated?: number | null }} Row
 * @param {readonly Row[]} rows
 * @returns {Row[]}
 */
export function modelRows(rows) {
  return rows.map((row, index) => ({ row, index, at: lastActivity(row) ?? 0 })).filter(({ row }) => built(row))
    .sort((a, b) => b.at - a.at || a.index - b.index).slice(0, MODEL_ROWS).map(({ row }) => row);
}

function Note({ children, busy = false }) {
  return <p className="px-3 py-4 text-center text-xs text-muted-foreground" role={busy ? "status" : undefined}>
    {busy ? <LoaderCircle className="mr-1.5 inline size-3 animate-spin align-[-2px]" aria-hidden="true" /> : null}{children}
  </p>;
}

/**
 * The name menu's Models tab, in the explorer's own style (`FolderExplorer`): one compact row per
 * model -- its glyph, its name, and at the right how long ago it was last active, with no verb --
 * the one on screen highlighted. Read from the host's library once per opening. A press
 * opens a document (`onOpen`, as the explorer's does); Up and Down move between the rows.
 *
 * @param {{ recent: { list(): Promise<readonly { path: string, name: string, kind?: string, opened?: number | null, updated?: number | null, missing?: boolean }[]> },
 *   current: string, onOpen(path: string): void }} props
 */
export function ModelList({ recent, current, onOpen }) {
  const { t, locale } = useI18n();
  const [listed, setListed] = useState({ rows: null, error: null });
  const rows = useRef(null);
  useEffect(() => {
    let active = true;
    recent.list().then(
      found => { if (active) setListed({ rows: modelRows(found), error: null }); },
      error => { if (active) setListed({ rows: null, error }); },
    );
    return () => { active = false; };
  }, [recent]);
  const walk = event => {
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    const all = [...(rows.current?.closest("[data-model-list]")?.querySelectorAll("[data-model-row]:not(:disabled)") ?? [])];
    const at = all.indexOf(event.target.closest?.("[data-model-row]"));
    const next = event.key === "ArrowDown" ? all[at + 1] : at > 0 ? all[at - 1] : null;
    event.preventDefault();
    next?.focus();
  };
  const now = Date.now();
  let body;
  if (listed.error) body = <Note>{t("explorer.modelsFailed")}</Note>;
  else if (!listed.rows) body = <Note busy>{t("explorer.loading")}</Note>;
  else if (!listed.rows.length) body = <Note>{t("explorer.noModels")}</Note>;
  else body = listed.rows.map(row => <TooltipHint key={row.path} content={row.name} overflowOnly>
    <TreeRowSurface as="button" type="button" active={row.path === current} className="disabled:opacity-50"
      style={{ paddingLeft: TREE_ROW_INSET_PX }} data-model-row="" data-path={row.path} disabled={row.missing}
      aria-current={row.path === current ? "true" : undefined} onClick={() => onOpen(row.path)}>
      <DocumentGlyph model={built(row)} path={row.path} className={TREE_GLYPH_CLASS} strokeWidth={TREE_GLYPH_STROKE} />
      <TreeRowLabel className="flex-1">{row.name}</TreeRowLabel>
      <span className="ml-2 shrink-0 text-muted-foreground" data-model-age="">{ageLabel(lastActivity(row), now, t, locale)}</span>
    </TreeRowSurface>
  </TooltipHint>);
  return <div className="flex min-h-0 flex-1 flex-col" data-model-list="">
    <ScrollArea className="min-h-0 flex-1" viewportClassName="p-1" viewportRef={rows} viewportProps={{ onKeyDown: walk }}>
      {body}
    </ScrollArea>
  </div>;
}
