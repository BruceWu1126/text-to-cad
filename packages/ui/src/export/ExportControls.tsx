import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowDownToLine, X, Check, CircleAlert } from 'lucide-react';
import { ExportRequestError, exportPause, type ExportJob, type ExportRequest, type ExportService } from '@text-to-cad/core/client';
import type { DownloadPort, ExportRecord } from '../host/types.js';
import { Button } from '../primitives/button.jsx';
import { Spinner } from '../primitives/spinner.js';
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger } from '../primitives/dropdown-menu.jsx';
import { TooltipHint } from '../primitives/tooltip.jsx';
import { FLOATING_SURFACE_CLASS } from '../lib/floatingSurface.js';
import { NAVBAR_CONTROL_CLASS } from '../lib/navbarRow.js';
import { cn } from '../lib/utils.js';
import { useI18n } from '../i18n/index.js';
import { useViewerMobile } from '../file-viewer/responsive.js';
import { ExportContext, type ExportView } from './context.js';
import { exportChoices, type ExportChoice } from './formats.js';

const active = (job: ExportJob | null) => !job || ['queued', 'running', 'cancelling'].includes(job.state);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const retryable = (error: unknown) => !(error instanceof ExportRequestError) || error.status >= 500;
// What the toast says is the job's state in the page's language; the server's stage is English.
const STATE_TEXT: Record<ExportJob['state'], string> = {
  queued: 'export.queued', running: 'export.running', cancelling: 'export.cancelling', cancelled: 'export.cancelled',
  failed: 'export.failed', expired: 'export.expired', ready: 'export.downloaded',
};

/** Each toast owns one job subscription and hands its file to the browser once; siblings never cancel each other. */
function ExportToast({ attempt, service, downloads, reveal, onDelivered, onRequest, onRemove, onActivity, onExportAgain }: {
  attempt: ExportRecord; service: ExportService; downloads: DownloadPort; reveal: number;
  onDelivered(): void; onRequest(request: ExportRequest): void; onRemove(): void; onActivity(id: string, busy: boolean): void; onExportAgain(): void;
}) {
  const { t } = useI18n();
  // A phone's buttons are its 36px touch tiles (`docs/settings-ui.md`, Mobile); a desktop's the menu's.
  const mobile = useViewerMobile();
  const button = mobile ? { size: 'default' as const, className: 'px-3 text-tiny' } : { size: 'xs' as const, className: 'text-tiny' };
  const [job, setJob] = useState<ExportJob | null>(null);
  const [error, setError] = useState('');
  const [fatal, setFatal] = useState(false);
  const [connection, setConnection] = useState('');
  const [hidden, setHidden] = useState(false);
  const [now, setNow] = useState(Date.now());
  const cancelWanted = useRef(false);
  // A file is handed to the browser once by itself; again only when the person asks.
  const delivered = useRef(attempt.delivered);
  const latestJob = useRef(job); latestJob.current = job;
  const latest = useRef({ downloads, onDelivered, onRequest }); latest.current = { downloads, onDelivered, onRequest };
  // The request as it was handed over (a new one is a new toast: they are keyed by its id); one a
  // menu item made learns its session's epoch below, once, before it is submitted.
  const asked = useRef(attempt.request);
  useEffect(() => { setHidden(false); }, [reveal]);
  const expired = job?.state === 'expired' || (job?.state === 'ready' && now / 1000 >= job.expiresAt);
  useEffect(() => {
    if (job?.state === 'ready' && !expired) {
      const timer = setTimeout(() => setNow(Date.now()), Math.max(0, job.expiresAt * 1000 - Date.now()) + 50);
      return () => clearTimeout(timer);
    }
    if (!active(job) || fatal) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [job, expired, fatal]);

  const deliver = useCallback((ready: ExportJob) => {
    delivered.current = true;
    // The server's name for the file: the one its Content-Disposition gives every browser.
    latest.current.downloads.save(service.fileUrl(ready.id), ready.filename || attempt.filename);
    latest.current.onDelivered();
  }, [service, attempt.filename]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    (async () => {
      let found: ExportJob | null = null;
      let failures = 0;
      for (;;) {
        try {
          if (!asked.current.epoch) {
            // The request names the session it is made in, and the page keeps it so: after a reload,
            // a server that has restarted since refuses it rather than running it again.
            const epoch = await service.epoch(signal);
            if (signal.aborted) return;
            asked.current = { ...asked.current, epoch };
            latest.current.onRequest(asked.current);
          }
          found = found ? await service.status(found.id, found.sequence, signal) : await service.create(asked.current, signal);
          if (signal.aborted) return;
          failures = 0; setConnection(''); setJob(found);
          if (cancelWanted.current && active(found) && found.state !== 'cancelling') {
            found = await service.cancel(found.id);
            if (signal.aborted) return;
            setJob(found);
          }
          if (!active(found)) {
            if (found.state !== 'ready') return;
            if (!delivered.current && !cancelWanted.current) deliver(found);
            // Ready, its status waits for the file to go: fetched whole by the browser, or past its
            // window (`cadgen/viewer/exports.py`). Then Download again becomes Export again.
          }
        } catch (failure) {
          if (signal.aborted) return;
          if (!retryable(failure)) { setError(message(failure)); setFatal(true); setConnection(''); return; }
          setConnection(t('export.reconnecting'));
          await exportPause(Math.min(1000 * 2 ** failures++, 10000), signal);
        }
      }
    })().catch(() => {});
    return () => controller.abort();
  }, [service, deliver, t]);

  const cancel = async () => {
    cancelWanted.current = true;
    setConnection(t('export.cancelRequested'));
    const found = latestJob.current;
    if (!found || !active(found)) return;
    try { setJob(await service.cancel(found.id)); setConnection(''); }
    catch (failure) { setError(t('export.cancelError', { message: message(failure) })); }
  };
  // In the press itself, so the browser counts it as the person's own download.
  const again = () => { if (job?.state === 'ready' && !expired) deliver(job); };
  const remove = async () => {
    if (job && !fatal) {
      try { await service.release(job.id); } catch (failure) {
        if (!(failure instanceof ExportRequestError && failure.status === 404)) { setError(message(failure)); return; }
      }
    }
    onRemove();
  };
  const busy = active(job) && !fatal;
  useEffect(() => { onActivity(attempt.request.requestId, busy); }, [onActivity, attempt.request.requestId, busy]);
  if (hidden) return null;
  const failure = error || job?.error;
  const title = !job ? (failure ? t('export.failed') : t('export.submitting'))
    : t(job.fetched ? 'export.fetched' : expired ? 'export.expired' : STATE_TEXT[job.state]);
  const seconds = Math.max(0, Math.floor(((job?.finishedAt || now / 1000) - (job?.createdAt || now / 1000))));
  const ready = job?.state === 'ready' && !expired;
  return <section className="rounded-lg border border-border bg-popover p-3 text-tiny text-popover-foreground shadow-lg" aria-label={t('export.toast', { name: job?.filename || attempt.filename })} data-export-toast="">
    <div className="flex items-start gap-2">
      {failure ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" /> : busy ? <Spinner className="mt-0.5 size-4 shrink-0" />
        : ready ? <Check className="mt-0.5 size-4 shrink-0" /> : <ArrowDownToLine className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
      <div className="min-w-0 flex-1"><p className="truncate">{job?.filename || attempt.filename}</p><p className="mt-1 text-muted-foreground" role="status" aria-live="polite">{title}{busy ? ` · ${seconds}s` : ''}</p></div>
      <Button variant="ghost" size={mobile ? 'icon' : 'icon-xs'} aria-label={t('export.dismiss')} onClick={() => setHidden(true)}><X /></Button>
    </div>
    {connection ? <p className="mt-2">{connection}</p> : null}
    {failure ? <p className="mt-2 break-words text-destructive" role="alert">{failure}</p> : null}
    {ready ? <p className="mt-2 text-muted-foreground">{t('export.until', { time: new Date(job.expiresAt * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) })}</p> : null}
    <div className="mt-2 flex justify-end gap-2">
      {busy ? <Button {...button} variant="outline" disabled={job?.state === 'cancelling'} onClick={() => { void cancel(); }}>{t('export.cancel')}</Button> : null}
      {ready ? <Button {...button} onClick={again}>{t('export.again')}</Button> : null}
      {expired ? <Button {...button} onClick={onExportAgain}>{t('export.exportAgain')}</Button> : null}
      {!busy ? <Button {...button} variant="ghost" onClick={() => { void remove(); }}>{t('export.remove')}</Button> : null}
    </div>
  </section>;
}

/** Where the toasts stand: under the navbar at the view's top-right on a desktop; at a phone's
 * bottom, the view's width less its margins, where a thumb reaches them. Rendered inside the
 * viewer, so it reads the viewer's own width (`useViewerMobile`). */
function ToastStack({ label, children }: { label: string; children: ReactNode }) {
  const mobile = useViewerMobile();
  return <div className={`pointer-events-none absolute inset-x-2 z-40 flex max-h-[calc(100%-1rem)] flex-col overflow-y-auto ${mobile ? 'bottom-2 items-stretch' : 'top-2 items-end'}`}
    aria-label={label} data-export-stack={mobile ? 'bottom' : 'top'}>
    <div className={`pointer-events-auto flex max-w-full flex-col gap-2 ${mobile ? 'w-full' : 'w-80'}`}>{children}</div>
  </div>;
}

export function ExportControls({ service, downloads, file, children }: {
  service?: ExportService; downloads?: DownloadPort; file: string;
  children(slots: { navbar: ReactNode; overlay: ReactNode }): ReactNode;
}) {
  const { t } = useI18n();
  const [source, setSource] = useState<ExportView | null>(null);
  const [open, setOpen] = useState(false);
  const [reveal, setReveal] = useState(0);
  const [activity, setActivity] = useState<Record<string, boolean>>({});
  const reportActivity = useCallback((id: string, busy: boolean) => setActivity(old => old[id] === busy ? old : { ...old, [id]: busy }), []);
  const [attempts, setAttempts] = useState<ExportRecord[]>(() => downloads?.restore?.() || []);
  useEffect(() => { downloads?.remember?.(attempts); }, [attempts, downloads]);
  const displayed = source?.file === file ? source : null;
  // What the file on screen saves as, known from its kind: the menu has nothing to wait for.
  const choices = displayed ? exportChoices(displayed) : [];
  const begin = (choice: ExportChoice) => {
    if (!displayed) return;
    // The document as its code built it, at the revision on screen: never a selection or a pose.
    // Its toast asks the session's epoch and submits it at once.
    const request: ExportRequest = { requestId: crypto.randomUUID(), epoch: '', source: { doc: displayed.doc, revision: displayed.revision }, format: choice.format };
    const stem = (displayed.name.split('::').pop() || 'model').replace(/\.[^.]+$/, '').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0, 100);
    setAttempts(old => [...old, { request, filename: (stem || 'model') + choice.extension, delivered: false }]);
    setReveal(n => n + 1);
  };
  const enabled = Boolean(service && downloads);
  const label = t('export.button');
  // A dropdown of the formats, the file menu's own: a choice closes it and starts that export.
  // After them, Show exports while there are any; last, muted, what an export holds.
  const navbar = enabled && (choices.length || attempts.length) ? <DropdownMenu modal={false} open={open} onOpenChange={setOpen}>
    <TooltipHint content={label} side="bottom" disabled={open}>
      <DropdownMenuTrigger asChild><Button type="button" variant="ghost" size="icon-xs" aria-label={label} className={NAVBAR_CONTROL_CLASS} data-export-menu="">
        {attempts.some(row => activity[row.request.requestId]) ? <Spinner className="size-3.5" /> : <ArrowDownToLine className="size-3.5" />}
      </Button></DropdownMenuTrigger>
    </TooltipHint>
    {/* Closes with no exit animation, so a quick second press always reaches the button. */}
    <DropdownMenuContent align="end" sideOffset={6} collisionPadding={8} aria-label={label}
      className={cn(FLOATING_SURFACE_CLASS, 'w-44 data-[state=closed]:animate-none!')}>
      {choices.map(choice => <DropdownMenuItem key={choice.format} onSelect={() => begin(choice)} data-export-format={choice.format}>{choice.label}</DropdownMenuItem>)}
      {attempts.length ? <>
        {choices.length ? <DropdownMenuSeparator /> : null}
        <DropdownMenuItem onSelect={() => setReveal(n => n + 1)}>{t('export.show', { count: attempts.length })}</DropdownMenuItem>
      </> : null}
      {choices.length ? <>
        <DropdownMenuSeparator />
        <DropdownMenuLabel className="text-muted-foreground" data-export-note="">
          {t(choices.some(choice => choice.animations) ? 'export.noteAnimated' : 'export.note')}
        </DropdownMenuLabel>
      </> : null}
    </DropdownMenuContent>
  </DropdownMenu> : null;
  const overlay = enabled && attempts.length ? <ToastStack label={t('export.jobs')}>
    {attempts.map(attempt => <ExportToast key={attempt.request.requestId} attempt={attempt} service={service!} downloads={downloads!} reveal={reveal} onActivity={reportActivity}
      onDelivered={() => setAttempts(old => old.map(row => row.request.requestId === attempt.request.requestId ? { ...row, delivered: true } : row))}
      // The request with its session's epoch, kept so that a reload reconnects to its job by it.
      onRequest={request => setAttempts(old => old.map(row => row.request.requestId === request.requestId ? { ...row, request } : row))}
      onRemove={() => setAttempts(old => old.filter(row => row.request.requestId !== attempt.request.requestId))}
      // The file is gone (downloaded, or past its window): the same export run again, in its place.
      onExportAgain={() => setAttempts(old => old.map(row => row.request.requestId === attempt.request.requestId
        ? { request: { ...row.request, requestId: crypto.randomUUID() }, filename: row.filename, delivered: false } : row))} />)}
  </ToastStack> : null;
  return <ExportContext.Provider value={enabled ? setSource : null}>{children({ navbar, overlay })}</ExportContext.Provider>;
}
