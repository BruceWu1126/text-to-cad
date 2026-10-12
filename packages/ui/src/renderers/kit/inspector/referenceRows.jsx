import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";

// Onshape-style axis colour coding for coordinate triples.
const AXES = Object.freeze([
  { key: "X", className: "text-rose-500 dark:text-rose-400" },
  { key: "Y", className: "text-emerald-500 dark:text-emerald-400" },
  { key: "Z", className: "text-sky-500 dark:text-sky-400" }
]);

export function formatNumber(value, digits = 2) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "—";
  }
  return numeric.toLocaleString(undefined, { maximumFractionDigits: digits });
}

// One line a row: the label at the left, as wide as its words, and the value at the
// right, taking the rest, so a short value never wraps under a wide empty label
// column. Sized for the tool stack's narrowest width: a value too long for what is
// left wraps between words, and breaks inside one only when the word alone is
// wider than its room (a long id). Compact (2px above and below), and in the
// panel's one face and size: labels and values alike are the UI font at
// `text-tiny`, numbers kept in tabular figures.
export function InfoRow({ label, children, title }) {
  return (
    <TooltipHint content={title}><div className="flex items-baseline justify-between gap-3 py-0.5" data-info-row="">
      <span className="shrink-0 text-tiny text-muted-foreground">{label}</span>
      <div className="min-w-0 text-right text-tiny text-sidebar-foreground [overflow-wrap:break-word]">{children}</div>
    </div></TooltipHint>
  );
}

/** A number, an id or a colour: the UI font, with tabular figures so a column of them lines up. */
export function MonoValue({ children }) {
  return <span className="tabular-nums">{children}</span>;
}

export function CoordValue({ vector, digits = 2 }) {
  const values = Array.isArray(vector) ? vector : [];
  return (
    <span className="inline-flex flex-wrap items-baseline justify-end gap-x-3 gap-y-0.5 tabular-nums">
      {AXES.map((axis, index) => (
        <span key={axis.key} className="inline-flex items-baseline gap-1">
          <span className={axis.className}>{axis.key}</span>
          <span>{formatNumber(values[index], digits)}</span>
        </span>
      ))}
    </span>
  );
}
