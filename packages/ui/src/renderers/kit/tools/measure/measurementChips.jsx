import { useMemo } from "react";
import { measureLabelText, measureSeriesColor, measurementMark } from "@text-to-cad/core/lib/viewer/measureDimension.js";
import { useViewerHost } from "../../../../host/context.js";
import MeasurementCard from "./MeasurementCard.jsx";

/**
 * Measure's results as Quick Edit has them (`useRendererShell`'s `measurements`), in any 3D file:
 * each its colour's ruler and its reading — a mesh's said to be approximate — its X deleting it,
 * resting on it lighting it, its card the full reading; and the marks a note's views are drawn
 * with (`measureDimension.js`'s `measurementMark`, as the viewer draws them). A click copies one as a
 * snapshot takes it: the path of a file holding it (`--measurements` reads it), where the host saves
 * prompt files, else the job's `measurements` entry itself. None while nothing is measured.
 *
 * @param {ReturnType<typeof import("./useMeasureSession.js").useMeasureSession>} measure
 * @param {{ path?: string } | null} resource  The file on screen.
 */
export function useQuickEditMeasurements(measure, resource) {
  const host = useViewerHost();
  const items = measure.measurements;
  const { onDelete, hover, clear } = measure;
  const name = String(resource?.path || "").split("/").pop();
  const chips = useMemo(() => items.map((item, index) => {
    const reading = measureLabelText(item.measurement);
    const mark = measurementMark(item);
    return { key: `measure-${item.id}`, label: `Measurement ${index + 1}: ${reading}${item.measurement?.approximate ? " (approximate)" : ""}`, text: reading,
      copy: () => (host.promptFiles?.save ? host.promptFiles.save({ name, kind: "measurement", data: mark }) : JSON.stringify(mark)),
      glyph: "ruler", glyphColor: measureSeriesColor(item.colorIndex),
      card: <MeasurementCard item={item} />,
      onRemove: () => onDelete(item.id), onHover: on => hover(on ? item.id : "") };
  }), [items, onDelete, hover, host.promptFiles, name]);
  return useMemo(() => (items.length ? { chips, clear, marks: () => items.map(measurementMark).filter(Boolean) } : null), [items, chips, clear]);
}
