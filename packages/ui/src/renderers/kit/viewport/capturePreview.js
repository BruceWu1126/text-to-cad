import { canvasToBlob } from "@text-to-cad/core/lib/viewer/screenshotCapture.js";

// Capture the picture Preview displays now, including its optional traced layer. Copy in the
// same task as the draw: either GPU may recycle its drawing buffer after presentation.
export function capturePreview(runtime, { backgroundColor = "", createCanvas = () => document.createElement("canvas") } = {}) {
  runtime.drawCurrentFrame();
  const tracing = runtime.progressivePreview;
  const visible = tracing?.visible === true;
  const traced = tracing?.captureCanvas;
  // A readback can discover device loss. Refresh raster synchronously before copying its
  // buffer if the layer we intended to capture fell back during that readback.
  if (visible && !traced) runtime.drawCurrentFrame();
  const source = traced || runtime.renderer.drawingCanvas || runtime.renderer.domElement;
  const canvas = createCanvas();
  // Quality can cap traced pixels below the raster buffer. Keep capture dimensions stable,
  // scaling the same picture the browser stretches across the viewport.
  canvas.width = runtime.renderer.domElement.width;
  canvas.height = runtime.renderer.domElement.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Screenshot capture failed");
  if (backgroundColor) {
    context.fillStyle = backgroundColor;
    context.fillRect(0, 0, canvas.width, canvas.height);
  }
  context.drawImage(source, 0, 0, canvas.width, canvas.height);
  return canvasToBlob(canvas);
}
