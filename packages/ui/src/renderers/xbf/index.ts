import type { ComponentType } from 'react';
import { defineFileRenderer } from '../../file-viewer/registry.js';
import type { FileRendererProps } from '../../file-viewer/types.js';
import type { LiveViewBinding } from '../kit/shell/liveBinding.js';
import { createCadPreferences, prepareWorkspaceEntry } from '../workspace/index.js';
import type { CadPreferenceSource, PreparedWorkspaceEntry, ViewerCommandSource, WorkspaceClientOption } from '../workspace/index.js';

export type { LiveCameraSnapshot, LiveViewBinding, LiveViewController, LiveViewState } from '../kit/shell/liveBinding.js';

export interface XbfRendererOptions {
  client: WorkspaceClientOption;
  preferences?: CadPreferenceSource;
  /** Host requests. One to select a reference is consumed and declined in words. */
  commands?: ViewerCommandSource;
  /** The mounted view's live command surface. */
  live?: LiveViewBinding<any>;
}
export interface PreparedXbfDocument extends PreparedWorkspaceEntry {
  services: Omit<XbfRendererOptions, 'client'> & { preferences: CadPreferenceSource };
}

/** A cadgen engine document: B-rep parts placed by instances, every face, edge and vertex named. */
export const XBF_FILE = /\.xbf$/i;

/**
 * An `.xbf` is drawn from what the cadgen engine serves of it (its structure and one buffer per
 * part), read by the page from the engine itself. Registers without loading three.js, a viewport
 * or a backend connection.
 */
export function createXbfRenderer({ client, ...options }: XbfRendererOptions) {
  const services = { ...options, preferences: options.preferences || createCadPreferences() };
  return defineFileRenderer<PreparedXbfDocument>({
    id: 'xbf',
    // Above the STEP renderer's, which takes any file the viewer calls CAD: an .xbf is this one's.
    priority: 110,
    matches: (file) => XBF_FILE.test(file.path),
    async prepare(context) {
      const prepared = await prepareWorkspaceEntry(client, context);
      return { ...prepared, data: { ...prepared.data, services } };
    },
    load: () => import('./XbfRenderer.jsx') as Promise<{ default: ComponentType<FileRendererProps<PreparedXbfDocument>> }>
  });
}
