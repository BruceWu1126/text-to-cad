import { useMemo } from "react";
import { createBoardIndex } from "@text-to-cad/core/lib/board2d/boardIndex.js";
import { boardLoadAlert, useBoardPayload } from "./useBoardPayload.js";

/**
 * The board on screen: its plot (`useBoardPayload`), its sheet, its index (its parts, pads and
 * nets, when the payload carries one: with it, the board has the tools a person points with), and
 * the load's alert. A board drawn layer by layer can be seen from either side, with any of its
 * layers: its Display.
 *
 * @param {{ workspace: ReturnType<typeof import("../workspace/useWorkspaceDocument.js").useWorkspaceDocument>, path: string }} options
 */
export function useBoardDocument({ workspace, path }) {
  const file = workspace.entry?.file || path;
  const payload = useBoardPayload({ client: workspace.client, file, revision: workspace.resource.revision });
  const { plot } = payload;
  const sheet = plot?.layout.sheets[0] ?? null;
  const board = plot?.board ?? null;
  const index = useMemo(() => (board && sheet ? createBoardIndex(board, { x: sheet.x, y: sheet.y }) : null), [board, sheet]);
  const layered = Array.isArray(sheet?.layers) && sheet.layers.length > 0;

  // With a board on screen, a failure to read the file again leaves that board to use.
  const shown = Boolean(plot);
  const alert = useMemo(() => {
    if (workspace.catalogError && !shown) {
      return {
        severity: "error", kind: "status", title: "Couldn’t open the board",
        message: "The viewer couldn’t retrieve this file’s information.",
        recovery: "Try again. If this continues, check that the viewer is running.",
        details: String(workspace.catalogError), reload: true
      };
    }
    const failed = boardLoadAlert(workspace.modelKey || file, payload.error);
    return failed && shown ? { ...failed, blocking: false, message: `${failed.message} The existing board remains visible.` } : failed;
  }, [workspace.catalogError, workspace.modelKey, file, payload.error, shown]);

  return { file, plot, index, sheet, layered, shown, alert, loading: payload.loading, updating: payload.updating };
}
