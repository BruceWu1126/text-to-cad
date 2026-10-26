/** A line over the view, the model still under it: what stopped working, and what to do about it. */
export function Banner({ message }: { message: string }) {
  // Below the navbar row, so it never sits over its controls.
  return <div className="pointer-events-none absolute inset-x-0 top-12 z-50 flex justify-center px-3">
    <div className="pointer-events-auto max-w-md rounded-md border bg-background/95 px-3 py-2 text-ui text-xs text-foreground shadow-sm" role="alert">{message}</div>
  </div>;
}

/** A whole-page message: why CAD cannot show anything, and what to do about it. */
export default function Notice({ title, message, details }: { title: string; message: string; details?: string }) {
  return <div className="cad-notice text-ui" role="alert">
    <div className="flex max-w-md flex-col items-start gap-3">
      <h1 className="text-base font-medium">{title}</h1>
      <p className="text-muted-foreground">{message}</p>
      {details && <details className="w-full text-tiny text-muted-foreground"><summary className="cursor-pointer">Details</summary><pre className="mt-2 whitespace-pre-wrap break-all select-text">{details}</pre></details>}
    </div>
  </div>;
}

/**
 * What a card whose chat carries none of its calls to the engine says (`host/channel.ts`), and
 * nothing else: no home, no library, nothing the host said. `page` is the CAD Viewer's page of what
 * the card was asked to show, from its launch, where the engine gave one; `open` follows it through
 * the host, where the host opens links, and returns false where it does not (the link then opens as
 * an ordinary one, in a new window).
 */
export function Unreachable({ page, model, open }: { page: string | null; model: boolean; open?: (url: string) => boolean }) {
  const what = model ? 'the model in the CAD Viewer' : 'the CAD Viewer';
  const message = page
    ? `This chat can't connect the CAD viewer to its engine. Open ${what} instead.`
    : `This chat can't connect the CAD viewer to its engine. Ask your agent to open ${what} instead.`;
  return <div className="cad-notice text-ui" role="alert" data-cad-unreachable="">
    <div className="flex max-w-md flex-col items-start gap-3">
      <p className="text-foreground">{message}</p>
      {page ? <a href={page} target="_blank" rel="noopener noreferrer"
        className="rounded-md border px-3 py-1.5 text-xs font-medium text-foreground hover:bg-muted"
        onClick={event => { if (open?.(page)) event.preventDefault(); }}>Open in CAD Viewer</a> : null}
    </div>
  </div>;
}
