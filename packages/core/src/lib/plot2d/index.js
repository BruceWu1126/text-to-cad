/**
 * Plots: a KiCad board drawn from its file, shared by the CAD Viewer's plot pane and the headless
 * snapshot bundle. Payload in (`GET /__cad/plot`, schema 3: geometry per layer), Canvas 2D out in
 * KiCad's default colours, the view maths drawing2d's.
 */
export {
  OPTIONAL_LAYER_KINDS,
  PLOT_HAIRLINE_CSS_PX,
  PLOT_SCHEMA_VERSION,
  drawPlot,
  drawPlotImages,
  fitPlotTransform,
  layoutPlot,
  mirrorPageX,
  pageToScreen,
  rectsOverlap,
  screenToPage,
  viewedLayers,
  visiblePageRect
} from "./plot.js";
export { HOLE_COLORS, VIA_COLORS, itemColor, layerColor } from "./theme.js";
