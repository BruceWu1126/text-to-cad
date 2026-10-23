import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { ArrowDownToLine, ChevronDown, X, Check, CircleAlert } from 'lucide-react';
import { ExportRequestError, exportPause, type ExportJob, type ExportOption, type ExportRequest, type ExportService, type ExportSource } from '@text-to-cad/core/client';
import type { DownloadDestination, DownloadPort } from '../host/types.js';
import { Button } from '../primitives/button.jsx';
import { Spinner } from '../primitives/spinner.js';
import { Popover, PopoverContent, PopoverTrigger } from '../primitives/popover.jsx';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../primitives/select.jsx';
import { useI18n } from '../i18n/index.js';
import { ExportContext, type ExportView } from './context.js';

type Attempt = { request: ExportRequest; filename: string; destination?: DownloadDestination };
const active = (job: ExportJob | null) => !job || ['queued', 'running', 'cancelling'].includes(job.state);
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const retryable = (error: unknown) => !(error instanceof ExportRequestError) || error.status >= 500;
const extension = (request: ExportRequest) => (request.format === 'threemf' ? '.3mf' : request.format === 'original' ? '.xbf' : `.${request.format}`);
const size = (bytes: number) => bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

/** Each toast owns one job subscription and one local writer; siblings never cancel each other. */
function ExportToast({ attempt, service, downloads, reveal, onRemove, onActivity }: {
  attempt: Attempt; service: ExportService; downloads: DownloadPort; reveal: number; onRemove(): void; onActivity(id: string, busy: boolean): void;
}) {
  const { t } = useI18n();
  const [job, setJob] = useState<ExportJob | null>(null);
  const [error, setError] = useState('');
  const [fatal, setFatal] = useState(false);
  const [connection, setConnection] = useState('');
  const [delivery, setDelivery] = useState<'idle' | 'choosing' | 'saving' | 'saved' | 'failed'>('idle');
  const [bytes, setBytes] = useState(0);
  const [hidden, setHidden] = useState(false);
  const [now, setNow] = useState(Date.now());
  const [localName, setLocalName] = useState(attempt.filename);
  const cancelWanted = useRef(false);
  const transfer = useRef<AbortController | null>(null);
  const destination = useRef(attempt.destination);
  const latestJob = useRef(job); latestJob.current = job;
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; transfer.current?.abort(); }; }, []);
  useEffect(() => { setHidden(false); }, [reveal]);
  useEffect(() => {
    if ((!active(job) || fatal) && delivery !== 'saving') return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [job?.state, delivery, fatal]);

  const save = useCallback(async (ready: ExportJob, chosen: DownloadDestination) => {
    if (transfer.current) return;
    const controller = new AbortController(); transfer.current = controller;
    setError(''); setBytes(0); setDelivery('saving'); setLocalName(chosen.name);
    try {
      const response = await service.download(ready.id, controller.signal);
      await chosen.write(response, { signal: controller.signal, progress: value => { if (mounted.current) setBytes(value); } });
      if (mounted.current) setDelivery('saved');
    } catch (failure) {
      if (mounted.current) { if (failure instanceof ExportRequestError && failure.status === 410) setJob({ ...ready, state: 'expired', stage: 'Export expired' }); setDelivery('failed'); setError(controller.signal.aborted ? t('export.saveCancelled') : message(failure)); }
    } finally { transfer.current = null; }
  }, [service, t]);

  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    (async () => {
      let found: ExportJob | null = null;
      let failures = 0;
      for (;;) {
        try {
          found = found ? await service.status(found.id, found.sequence, signal) : await service.create(attempt.request, signal);
          if (signal.aborted) return;
          failures = 0; setConnection(''); setJob(found);
          if (cancelWanted.current && active(found) && found.state !== 'cancelling') {
            found = await service.cancel(found.id);
            if (signal.aborted) return;
            setJob(found);
          }
          if (!active(found)) {
            if (found.state === 'ready' && destination.current && !cancelWanted.current) {
              const chosen = destination.current; destination.current = undefined;
              void save(found, chosen);
            }
            return;
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
  }, [service, attempt.request, save, t]);

  const cancel = async () => {
    if (transfer.current) { transfer.current.abort(); return; }
    cancelWanted.current = true;
    setConnection(t('export.cancelRequested'));
    const found = latestJob.current;
    if (!found || !active(found)) return;
    try { setJob(await service.cancel(found.id)); setConnection(''); }
    catch (failure) { setError(t('export.cancelError', { message: message(failure) })); }
  };
  const saveAgain = async () => {
    if (!job) return;
    setDelivery('choosing');
    try {
      const chosen = await downloads.choose(localName, extension(attempt.request));
      if (!mounted.current) return;
      if (chosen) await save(job, chosen); else setDelivery('idle');
    } catch (failure) { setDelivery('failed'); setError(message(failure)); }
  };
  const remove = async () => {
    if (job && !fatal) {
      try { await service.release(job.id); } catch (failure) {
        if (!(failure instanceof ExportRequestError && failure.status === 404)) { setError(message(failure)); return; }
      }
    }
    onRemove();
  };
  const busy = active(job) && !fatal;
  const working = busy || delivery === 'saving' || delivery === 'choosing';
  useEffect(() => { onActivity(attempt.request.requestId, working); }, [onActivity, attempt.request.requestId, working]);
  if (hidden) return null;
  const failure = error || job?.error;
  const title = delivery === 'saved' ? t('export.saved') : delivery === 'saving' ? t('export.saving')
    : delivery === 'choosing' ? t('export.chooseLocal') : job?.stage || (error ? t('export.unavailable') : t('export.submitting'));
  const seconds = Math.max(0, Math.floor(((job?.finishedAt || now / 1000) - (job?.createdAt || now / 1000))));
  return <section className="rounded-lg border border-border bg-popover p-3 text-popover-foreground shadow-lg" aria-label={t('export.toast', { name: localName })} data-export-toast="">
    <div className="flex items-start gap-2">
      {failure ? <CircleAlert className="mt-0.5 size-4 shrink-0 text-destructive" /> : working ? <Spinner className="mt-0.5 size-4 shrink-0" />
        : delivery === 'saved' ? <Check className="mt-0.5 size-4 shrink-0" /> : <ArrowDownToLine className="mt-0.5 size-4 shrink-0" />}
      <div className="min-w-0 flex-1"><p className="truncate text-xs">{localName}</p><p className="mt-1 text-tiny text-muted-foreground" role="status" aria-live="polite">{title}{busy ? ` · ${seconds}s` : ''}</p></div>
      <Button variant="ghost" size="icon-xs" aria-label={t('export.dismiss')} onClick={() => setHidden(true)}><X /></Button>
    </div>
    {delivery === 'saving' ? <div className="mt-2"><progress className="h-1 w-full" value={bytes} max={job?.bytes || 1} aria-label={t('export.progress')} /><p className="text-tiny text-muted-foreground">{t('export.bytes', { done: size(bytes), total: size(job?.bytes || 0) })}</p></div> : null}
    {connection ? <p className="mt-2 text-tiny">{connection}</p> : null}
    {failure ? <p className="mt-2 break-words text-tiny text-destructive" role="alert">{failure}</p> : null}
    {job?.state === 'ready' && delivery !== 'saving' ? <p className="mt-2 text-tiny text-muted-foreground">{t('export.until', { time: new Date(job.expiresAt * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) })}</p> : null}
    <div className="mt-2 flex justify-end gap-2">
      {(active(job) && !fatal) || delivery === 'saving' ? <Button size="xs" variant="outline" disabled={job?.state === 'cancelling' && delivery !== 'saving'} onClick={() => { void cancel(); }}>{t('export.cancel')}</Button> : null}
      {job?.state === 'ready' && delivery !== 'saving' && delivery !== 'choosing' ? <Button size="xs" onClick={() => { void saveAgain(); }}>{delivery === 'failed' ? t('export.retry') : delivery === 'saved' ? t('export.again') : t('export.save')}</Button> : null}
      {(!active(job) || fatal) && delivery !== 'saving' ? <Button size="xs" variant="ghost" onClick={() => { void remove(); }}>{t('export.remove')}</Button> : null}
    </div>
  </section>;
}

export function ExportControls({ service, downloads, file, children }: {
  service?: ExportService; downloads?: DownloadPort; file: string;
  children(slots: { navbar: ReactNode; overlay: ReactNode }): ReactNode;
}) {
  const { t } = useI18n();
  const [source, setSource] = useState<ExportView | null>(null);
  const [snapshot, setSnapshot] = useState<ExportView | null>(null);
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState('whole');
  const [position, setPosition] = useState('authored');
  const [format, setFormat] = useState('step');
  const [options, setOptions] = useState<{ epoch: string; formats: ExportOption[] } | null>(null);
  const [error, setError] = useState('');
  const [choosing, setChoosing] = useState(false);
  const [reveal, setReveal] = useState(0);
  const [activity, setActivity] = useState<Record<string, boolean>>({});
  const reportActivity = useCallback((id: string, busy: boolean) => setActivity(old => old[id] === busy ? old : { ...old, [id]: busy }), []);
  const [attempts, setAttempts] = useState<Attempt[]>(() => downloads?.restore?.() || []);
  useEffect(() => { downloads?.remember?.(attempts.map(({ request, filename }) => ({ request, filename }))); }, [attempts, downloads]);
  const spec = useMemo<ExportSource | null>(() => snapshot ? { doc: snapshot.doc, revision: snapshot.revision,
    part: scope === 'selection' ? snapshot.part : null, pose: position === 'current' ? snapshot.pose : null } : null,
  [snapshot, scope, position]);
  useEffect(() => {
    setOptions(null); setError('');
    if (!open || !spec || !service || downloads?.unavailable) return;
    const controller = new AbortController();
    service.options(spec, controller.signal).then(value => { if (!controller.signal.aborted) setOptions(value); }, failure => { if (!controller.signal.aborted) setError(message(failure)); });
    return () => controller.abort();
  }, [open, spec, service, downloads?.unavailable]);
  const selected = options?.formats.find(row => row.format === format);
  useEffect(() => {
    if (options && (!selected || selected.reason)) setFormat(options.formats.find(row => !row.reason)?.format || 'step');
  }, [options, selected]);
  const displayed = source?.file === file ? source : null;
  const changeOpen = (next: boolean) => {
    if (next) { setSnapshot(displayed); setScope('whole'); setPosition('authored'); setReveal(n => n + 1); }
    setOpen(next);
  };
  const begin = async () => {
    if (!downloads || !selected || selected.reason || !spec || !snapshot || !options) return;
    // Capture the exact displayed revision/options before the native chooser yields.
    const request: ExportRequest = { requestId: crypto.randomUUID(), epoch: options.epoch, source: spec, format: selected.format };
    const stem = (snapshot.name.split('::').pop() || 'model').replace(/\.[^.]+$/, '').replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').slice(0, 100);
    const name = (stem || 'model') + (spec.part ? '-part' : '') + selected.extension;
    setChoosing(true); setError('');
    try {
      const destination = await downloads.choose(name, selected.extension);
      if (!destination) return;
      setAttempts(old => [...old, { request, filename: destination.name, destination }]);
      setReveal(n => n + 1); setOpen(false);
    } catch (failure) { setError(message(failure)); }
    finally { setChoosing(false); }
  };
  const enabled = Boolean(service && downloads);
  const navbar = enabled && (displayed || attempts.length) ? <Popover open={open} onOpenChange={changeOpen}>
    <PopoverTrigger asChild><Button variant="ghost" size="xs" aria-label={t('export.button')} className="text-muted-foreground"><>{attempts.some(row => activity[row.request.requestId]) ? <Spinner className="size-3.5" /> : <ArrowDownToLine className="size-3.5" />}</>{t('export.button')}{attempts.length ? <span className="text-tiny">{attempts.length}</span> : null}<ChevronDown className="size-3" /></Button></PopoverTrigger>
    <PopoverContent align="end" className="w-72 space-y-3 p-3 text-xs" aria-label={t('export.heading')}>
      <p>{t('export.heading')}</p>
      {downloads?.unavailable ? <p className="text-tiny text-muted-foreground">{downloads.unavailable}</p> : snapshot ? <>
        <label className="block space-y-1"><span>{t('export.scope')}</span><Select value={scope} onValueChange={setScope}><SelectTrigger size="sm" aria-label={t('export.scope')}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="whole">{t('export.whole')}</SelectItem><SelectItem value="selection" disabled={!snapshot.part}>{t('export.selection')}</SelectItem></SelectContent></Select></label>
        <label className="block space-y-1"><span>{t('export.position')}</span><Select value={position} onValueChange={setPosition}><SelectTrigger size="sm" aria-label={t('export.position')}><SelectValue /></SelectTrigger><SelectContent><SelectItem value="authored">{t('export.authored')}</SelectItem><SelectItem value="current" disabled={!snapshot.pose || snapshot.preview}>{t('export.current')}</SelectItem></SelectContent></Select></label>
        <p className="text-tiny text-muted-foreground">{snapshot.preview ? t('export.preview') + ' ' : ''}{t('export.geometry')}</p>
        {options ? <label className="block space-y-1"><span>{t('export.format')}</span><Select value={format} onValueChange={setFormat}><SelectTrigger size="sm" aria-label={t('export.format')}><SelectValue /></SelectTrigger><SelectContent>{options.formats.filter(row => !row.reason).map(row => <SelectItem key={row.format} value={row.format}>{row.format === 'original' ? t('export.original') : row.label}</SelectItem>)}</SelectContent></Select></label>
          : !error ? <p role="status" className="flex items-center gap-2 text-tiny"><Spinner className="size-3" />{t('export.checking')}</p> : null}
        <Button className="w-full" size="sm" disabled={!selected || Boolean(selected.reason) || choosing} onClick={() => { void begin(); }}>{choosing ? t('export.choosing') : t('export.start')}</Button>
      </> : <p className="text-tiny text-muted-foreground">{t('export.open')}</p>}
      {error ? <p role="alert" className="text-tiny text-destructive">{error}</p> : null}
      {attempts.length ? <Button size="xs" variant="outline" onClick={() => { setReveal(n => n + 1); setOpen(false); }}>{t('export.show', { count: attempts.length })}</Button> : null}
    </PopoverContent>
  </Popover> : null;
  const overlay = enabled && attempts.length ? <div className="pointer-events-none absolute inset-x-2 top-2 z-40 flex max-h-[calc(100%-1rem)] flex-col items-end overflow-y-auto" aria-label={t('export.jobs')}><div className="pointer-events-auto flex w-80 max-w-full flex-col gap-2">
    {attempts.map(attempt => <ExportToast key={attempt.request.requestId} attempt={attempt} service={service!} downloads={downloads!} reveal={reveal} onActivity={reportActivity}
      onRemove={() => setAttempts(old => old.filter(row => row !== attempt))} />)}
  </div></div> : null;
  return <ExportContext.Provider value={enabled ? setSource : null}>{children({ navbar, overlay })}</ExportContext.Provider>;
}
