import { canvasToBlob } from "@text-to-cad/core/lib/viewer/screenshotCapture.js";

// Capture only the traced image currently presented by Preview.
export function capturePreview(runtime, { backgroundColor = "", createCanvas = () => document.createElement("canvas") } = {}) {
  runtime.drawCurrentFrame();
  const tracing = runtime.progressivePreview;
  const source = tracing?.captureCanvas;
  if (!source) throw new Error("Preview image is not ready");
  const canvas = createCanvas();
  // Quality can cap traced pixels below the raster buffer. Keep capture dimensions stable,
  // fitting the same complete picture centered in the viewport, as the overlay
  // does while a resized view prepares its replacement. Never stretch a stale aspect.
  canvas.width = runtime.renderer.domElement.width;
  canvas.height = runtime.renderer.domElement.height;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Screenshot capture failed");
  if (backgroundColor) {
    context.fillStyle = backgroundColor;
    context.fillRect(0, 0, canvas.width, canvas.height);
  }
  const scale = Math.min(canvas.width / source.width, canvas.height / source.height);
  const width = source.width * scale, height = source.height * scale;
  context.drawImage(source, (canvas.width - width) / 2, (canvas.height - height) / 2, width, height);
  return canvasToBlob(canvas);
}
