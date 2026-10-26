import { useId, type ReactNode } from 'react';
import { UpdateCard, type UpdateNotice as Notice } from '@text-to-cad/ui/update';

/**
 * The file handler's corner: what this host adds to the one view that has no navbar (`chrome: "bare"`,
 * Codex's file handler), at the top-right of the viewport — the column that holds the host's notice
 * and Quick Edit under it (`ViewportTopRight`). Full size first, where the host offers it, then the
 * update notice while this install is behind. Gone in Preview, as the rest of the column is. Only
 * its controls take presses: the model under the rest of its width stays reachable. Every other
 * view carries these in its navbar.
 */
export function CardCorner({ fullSize = null, update = null }: { fullSize?: ReactNode; update?: ReactNode }) {
  if (!fullSize && !update) return null;
  return <div className="pointer-events-none flex w-full flex-col items-end gap-2" data-card-corner="">
    {fullSize ? <div className="pointer-events-auto">{fullSize}</div> : null}
    {update ? <div className="pointer-events-auto w-full">{update}</div> : null}
  </div>;
}

/**
 * A newer text-to-cad, as a notice in the file handler's corner rather than the navbar's blue button: the update
 * card itself (`UpdateCard`: its prompt, Send to agent where the host takes messages, else Copy
 * prompt, and Manual installation), until the person closes it for this view.
 */
export function UpdateNotice({ notice, send, copy, onLink, onClose }: {
  notice: Notice; send?: (prompt: string) => Promise<void>; copy(prompt: string): Promise<void>; onLink(url: string): void; onClose(): void;
}) {
  const id = useId();
  return <section aria-labelledby={`${id}-title`} aria-describedby={`${id}-text`} data-update-notice="">
    <UpdateCard id={id} notice={notice} send={send} copy={copy} onLink={onLink} onClose={onClose} />
  </section>;
}
