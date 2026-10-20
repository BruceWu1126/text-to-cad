import { createToolModes } from "../kit/tools/toolModes.js";

// An engine document's tools: Select picks its faces, edges and vertices (and, from the tree, its
// parts), Annotate draws over it, Clip cuts it. There is no Measure tool of triangles: what a
// picked element measures is the engine's, on the exact shape (the Element panel).
export const XBF_TOOL = Object.freeze({ SELECT: "select", ANNOTATE: "annotate", CLIP: "clip" });

export const XBF_TOOL_MODES = createToolModes({
  defaultMode: XBF_TOOL.SELECT,
  modes: { [XBF_TOOL.SELECT]: {}, [XBF_TOOL.ANNOTATE]: {}, [XBF_TOOL.CLIP]: {} }
});

/** What a host command an engine document cannot carry out is told. */
export const XBF_DECLINED_LIVE_COMMANDS = Object.freeze({
  select: "An engine document's elements are picked by a person in the viewport under Select (each named as the document names it); a command cannot select them. Control the camera and display settings instead."
});
