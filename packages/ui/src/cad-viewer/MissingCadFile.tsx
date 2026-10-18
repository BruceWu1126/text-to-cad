import { useEffect, useRef, type ReactNode } from 'react';
import { FileX } from 'lucide-react';
import type { CadEntry, CadWorkspaceService } from '@text-to-cad/core/client';
import { EmptyState } from '../file-viewer/navigation/index.js';
import { ViewerLoadingOverlay } from '../file-viewer/presentation.js';
import ViewerAlertCard from '../renderers/kit/status/ViewerAlertCard.jsx';
import { useEditingPreview } from '../renderers/step/components/workbench/hooks/useEditingPreview.js';
import { editingBuildActive } from '../renderers/step/workbench/editingPreview.js';
import { buildViewerEditAlert } from '../renderers/step/workbench/viewerAlerts.js';

/** A first save can fail before the requested file exists. Its status is still the output's
 * artifact-only build feed; no catalog entry or saved document is invented to show it. */
export function MissingCadFile({ file, client, entry, reload, action }: {
  file: string; client: CadWorkspaceService; entry: CadEntry | null; reload?: () => void; action?: ReactNode;
}) {
  const { state } = useEditingPreview(file, { client, enabled: /\.st(?:ep|p)$/i.test(file) });
  const active = editingBuildActive(state);
  const saved = !state.superseded && (active || state.state === 'done') ? state.saved?.documentHash || '' : '';
  const reloaded = useRef('');
  useEffect(() => {
    // The saved event prompts the catalog read; reopen only once that read confirms these
    // exact bytes at this path. A stale entry or another file's late reply cannot do it.
    const identity = `${file}:${saved}`;
    if (saved && entry?.file === file && entry.documentHash === saved && reload && reloaded.current !== identity) {
      reloaded.current = identity;
      reload();
    }
  }, [file, saved, entry, reload]);
  const alert = buildViewerEditAlert(state, false);
  if (alert) return <div className="relative h-full"><ViewerAlertCard alert={{ ...alert, reload: Boolean(reload) }} hasContent={false} file={file} onReload={reload} /></div>;
  if (active || state.saving || saved) return <div className="relative h-full"><ViewerLoadingOverlay viewerLoading /></div>;
  return <EmptyState icon={FileX} title="File does not exist" description={file} tone="warn" action={action} />;
}
