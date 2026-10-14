import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyMeasureRulerDelete,
  applyMeasureRulerHover,
  applyMeasureRulerPick,
  cancelMeasureRulerDraft,
  measureFilterAccepts,
  measureRulerStateForChange
} from "./measureRulerState.js";

const NO_MEASUREMENTS = Object.freeze([]);
const sameMeasurements = (a, b) => a.length === b.length && a.every((item, index) => item.id === b[index].id);

/**
 * The Measure tool's session in a STEP: what its snapping filter is, the measurement being
 * drawn and the ones taken, and which row is highlighted. The viewport feeds it picks and
 * hovers; the panel reads the measurements; the toolbar reads and sets the filter.
 *
 * Measurements outlive the tool (they are cleared on purpose, or with the file's geometry);
 * a draft does not — putting the tool down drops it. The filter goes back to "all" for a new
 * file. The highlighted row follows a new measurement, and otherwise stays where the person
 * put it until that row is gone; only a change to the LIST moves it, so a draft's hover ticks
 * never fight a row click. Under Select, measurements are hovered and picked as anything is
 * (`hovered`/`hover`, `selected`/`select`), haloed in the view, and Delete deletes them
 * (`removeSelected`). Each measurement taken or deleted, and a clear, is a step in the file view's
 * one history (`history`), which Undo and Redo walk whatever tool took the step; a new file's
 * measurements start a history of their own.
 *
 * @param {{ fileKey: string, revision: string, picking: boolean,
 *   history?: import("../../kit/shell/useViewerHistory.js").ViewerHistory | null }} options
 *   `picking`: the tool is in hand and can pick now.
 */
export function useStepMeasure({ fileKey, revision, picking, history = null }) {
  const [filter, setFilter] = useState("all");
  useEffect(() => setFilter("all"), [fileKey]);
  const [state, setState] = useState(null);
  const [activeId, setActiveId] = useState("");
  const onPick = useCallback((pick) => {
    if (measureFilterAccepts(filter, pick?.snapKind)) setState((current) => applyMeasureRulerPick(current, pick));
  }, [filter]);
  const onHoverPoint = useCallback((hover) => {
    setState((current) => applyMeasureRulerHover(current, measureFilterAccepts(filter, hover?.snapKind) ? hover : null));
  }, [filter]);
  const onDelete = useCallback((id) => setState((current) => applyMeasureRulerDelete(current, id)), []);
  const cancelDraft = useCallback(() => setState((current) => cancelMeasureRulerDraft(current)), []);
  const clear = useCallback(() => { setState(null); setActiveId(""); }, []);
  const measurements = state?.measurements || NO_MEASUREMENTS;
  // The list as it was last seen, to record each change to it as a step; a change Undo or Redo
  // made, or a new file's, is not one.
  const historyRef = useRef(history);
  historyRef.current = history;
  const seen = useRef(measurements);
  const unrecorded = useRef(false);
  const restore = useCallback((list) => {
    if (sameMeasurements(seen.current, list)) return;
    unrecorded.current = true;
    setState((current) => ({ ...(current || {}), measurements: list }));
  }, []);
  useEffect(() => {
    const before = seen.current;
    seen.current = measurements;
    if (sameMeasurements(before, measurements)) return;
    if (unrecorded.current) { unrecorded.current = false; return; }
    historyRef.current?.record({ source: "measure", undo: () => restore(before), redo: () => restore(measurements) });
  }, [measurements, restore]);
  useEffect(() => {
    if (seen.current.length) unrecorded.current = true;
    historyRef.current?.forget("measure");
    setState((current) => measureRulerStateForChange(current, { entryChanged: true }));
  }, [fileKey, revision]);
  useEffect(() => {
    if (!picking) setState((current) => measureRulerStateForChange(current, { toolActive: false }));
  }, [picking]);
  const countRef = useRef(0);
  useEffect(() => {
    const count = measurements.length;
    const grew = count > countRef.current;
    countRef.current = count;
    const newest = count ? measurements[count - 1].id : "";
    setActiveId((current) => (!grew && current && measurements.some((item) => item.id === current) ? current : newest));
  }, [measurements]);
  // What Select has picked: the measurements Delete deletes. A measurement gone takes its pick with it.
  const [selected, setSelected] = useState(NO_MEASUREMENTS);
  useEffect(() => {
    setSelected((current) => (current.every((id) => measurements.some((item) => item.id === id)) ? current
      : current.filter((id) => measurements.some((item) => item.id === id))));
  }, [measurements]);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const removeSelected = useCallback(() => {
    const ids = selectedRef.current;
    if (!ids.length) return false;
    setState((current) => ids.reduce((next, id) => applyMeasureRulerDelete(next, id), current));
    setSelected(NO_MEASUREMENTS);
    return true;
  }, []);
  // What the pointer rests on under Select: lit as a part is, until it moves off or the measurement goes.
  const [hovered, setHovered] = useState("");
  useEffect(() => {
    setHovered((current) => (current && !measurements.some((item) => item.id === current) ? "" : current));
  }, [measurements]);
  return { filter, setFilter, state, measurements, activeId, setActiveId, drafting: Boolean(state?.draft?.anchor),
    onPick, onHoverPoint, onDelete, cancelDraft, clear, selected, select: setSelected, removeSelected, hovered, hover: setHovered };
}
