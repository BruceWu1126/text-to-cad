import { useEffect, useState } from "react";
import { fetchXbfElementFacts, placeXbfFacts } from "@text-to-cad/core/lib/xbf/xbfDocument.js";
import ToolPanel from "../kit/tools/ToolPanel.jsx";
import { CoordValue, InfoRow, MonoValue, formatNumber } from "../kit/inspector/referenceRows.jsx";

// The picked element, and what the kernel measures of it: asked of the engine when it is picked,
// on the exact shape, never the mesh (FreeCAD's measurements are the shape's). A face says its
// area, centroid, surface type, exact box and check; an edge its length, curve type, class, faces
// and ends; a vertex its position and tolerance. Its name is the element name the document gives
// it, which survives an edit that keeps the feature that made it.

const KIND_LABEL = Object.freeze({ face: "Face", edge: "Edge", vertex: "Vertex" });
const mm = (value, digits = 3) => `${formatNumber(value, digits)} mm`;

function FaceFacts({ facts }) {
  return <>
    <InfoRow label="Area"><MonoValue>{`${formatNumber(facts.area, 3)} mm²`}</MonoValue></InfoRow>
    <InfoRow label="Centroid"><CoordValue vector={facts.centroid} digits={3}/></InfoRow>
    <InfoRow label="Surface"><MonoValue>{facts.surface}</MonoValue></InfoRow>
    {facts.box ? <>
      <InfoRow label="Box min"><CoordValue vector={facts.box.min} digits={3}/></InfoRow>
      <InfoRow label="Box max"><CoordValue vector={facts.box.max} digits={3}/></InfoRow>
    </> : null}
    <InfoRow label="Check"><MonoValue>{facts.check}</MonoValue></InfoRow>
    {facts.meshed === false ? <InfoRow label="Mesh"><MonoValue>not meshed: not drawn</MonoValue></InfoRow> : null}
  </>;
}

function EdgeFacts({ facts }) {
  return <>
    <InfoRow label="Length"><MonoValue>{mm(facts.length)}</MonoValue></InfoRow>
    <InfoRow label="Curve"><MonoValue>{facts.curve}</MonoValue></InfoRow>
    <InfoRow label="Class"><MonoValue>{facts.class}</MonoValue></InfoRow>
    {facts.faces?.length ? <InfoRow label="Faces"><MonoValue>{facts.faces.join(", ")}</MonoValue></InfoRow> : null}
    {(facts.endpoints || []).map((point, at) => <InfoRow key={at} label={at ? "End" : "Start"}><CoordValue vector={point} digits={3}/></InfoRow>)}
  </>;
}

function VertexFacts({ facts }) {
  return <>
    <InfoRow label="Position"><CoordValue vector={facts.position} digits={3}/></InfoRow>
    <InfoRow label="Tolerance"><MonoValue>{mm(facts.tolerance, 6)}</MonoValue></InfoRow>
  </>;
}

/**
 * @param {{ element: { id: string, kind: "face" | "edge" | "vertex", ordinal: number, key: string, name: string,
 *   edgeClass: string, path: string, matrix?: number[] } | null, source: string, version: string, active?: boolean }} props
 *   `element`: the picked element as the scene describes it (`describeElement`), or null (no panel). `source`: the
 *   document's API URL. `version`: the document's, so a rebuild measures it again.
 */
export default function XbfElementSection({ element, source, version, active = true }) {
  const [state, setState] = useState({ id: "", facts: null, error: null });
  const id = element?.id || "";
  useEffect(() => {
    if (!element || !source) return undefined;
    const controller = new AbortController();
    setState({ id: element.id, facts: null, error: null });
    fetchXbfElementFacts(source, element.key, element.kind, element.ordinal, { signal: controller.signal })
      // Measured in the part's own frame; read where this instance places it, as the view shows it.
      .then(facts => setState({ id: element.id, facts: placeXbfFacts(facts, element.matrix), error: null }),
        error => { if (!controller.signal.aborted) setState({ id: element.id, facts: null, error }); });
    return () => controller.abort();
    // The element by its id, the part by its key, again for a new version of the document.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id, element?.key, source, version]);
  if (!element) return null;
  const facts = state.id === element.id ? state.facts : null;
  const error = state.id === element.id ? state.error : null;
  return <ToolPanel id="xbf-element" title={`${KIND_LABEL[element.kind]} ${element.ordinal}`} label="Element" fit="details" hidden={!active}>
    <div className="px-2 pb-1.5 pt-0.5" aria-label="Element facts">
      <InfoRow label="Name"><MonoValue>{element.name || "(no element name)"}</MonoValue></InfoRow>
      <InfoRow label="Part"><MonoValue>{element.path}</MonoValue></InfoRow>
      {element.edgeClass && !facts ? <InfoRow label="Class"><MonoValue>{element.edgeClass}</MonoValue></InfoRow> : null}
      {facts?.kind === "face" ? <FaceFacts facts={facts}/> : facts?.kind === "edge" ? <EdgeFacts facts={facts}/> : facts?.kind === "vertex" ? <VertexFacts facts={facts}/> : null}
      {!facts && !error ? <p role="status" className="py-0.5 text-micro text-muted-foreground">Measuring on the exact shape…</p> : null}
      {error ? <p role="alert" className="py-0.5 text-micro text-destructive">{`The engine could not measure it: ${error.message || error}`}</p> : null}
    </div>
  </ToolPanel>;
}
