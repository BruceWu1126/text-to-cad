import { useLayoutEffect, useRef } from "react";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_CHROME_SURFACE_CLASS } from "../../lib/floatingSurface.js";
import { VIEWPORT_BOTTOM_CENTER } from "../kit/shell/viewportLayout.js";
import { camTime } from "./camResult.js";

// A result that is played has a playbar on that line in preview: the readout steps up above it, as FEA's colour bar does.
const PLAYBAR_CLEARANCE = "3rem";

/** What the readout says at a moment: the operation under way ("2 Pocket"), and the job's time there over its whole ("1 min 32 s / 3 min 38 s"). */
export function readoutWords(cam, at, operation) {
  const position = operation ? cam.operations.indexOf(operation) : -1;
  return { operation: operation ? `${position + 1} ${operation.word}` : "", time: `${camTime(Math.min(at, cam.duration))} / ${camTime(cam.duration)}` };
}

/**
 * Where a CAM result's job is, over the bottom of its viewport: the operation under way and the job's time over its
 * whole, then, while the playbar plays it faster than real time, by how much ("10× speed"). Bottom-centre on the line
 * the playbar uses, or one playbar's height above it while the playbar is there (`raised`: in preview). The playbar
 * writes it every frame through `readoutRef.current(words)`, never through React; it takes no pointer, and is not read
 * out as it changes (a screen reader has the playbar's time and the Time row's value).
 */
export default function CamReadout({ readoutRef, words, raised = false }) {
  const operationRef = useRef(null);
  const timeRef = useRef(null);
  const paceRef = useRef(null);
  useLayoutEffect(() => {
    const write = (next) => {
      if (!operationRef.current) return;
      if (operationRef.current.textContent !== next.operation) operationRef.current.textContent = next.operation;
      if (timeRef.current.textContent !== next.time) timeRef.current.textContent = next.time;
      const pace = next.pace || "";
      if (paceRef.current.textContent !== pace) paceRef.current.textContent = pace;
      paceRef.current.style.display = pace ? "" : "none";
    };
    readoutRef.current = write;
    write(words);
    return () => { if (readoutRef.current === write) readoutRef.current = null; };
  }, [readoutRef, words]);
  return (
    <div className="pointer-events-none absolute inset-x-0 z-20 flex justify-center px-3" aria-hidden="true" data-cam-readout=""
      style={{ bottom: raised ? `calc(${VIEWPORT_BOTTOM_CENTER} + ${PLAYBAR_CLEARANCE})` : VIEWPORT_BOTTOM_CENTER }}>
      <div className={cn("flex max-w-full items-center gap-2 rounded-md px-2.5 py-1 text-micro tabular-nums text-muted-foreground", FLOATING_CHROME_SURFACE_CLASS)}>
        <span ref={operationRef} className="min-w-0 truncate text-tiny text-foreground" data-cam-readout-operation="" />
        <span ref={timeRef} className="shrink-0" data-cam-readout-time="" />
        <span ref={paceRef} className="shrink-0" data-cam-readout-pace="" style={{ display: "none" }} />
      </div>
    </div>
  );
}
