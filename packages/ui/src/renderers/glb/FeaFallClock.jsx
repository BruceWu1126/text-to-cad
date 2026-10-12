import { useLayoutEffect, useRef } from "react";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_CHROME_SURFACE_CLASS } from "../../lib/floatingSurface.js";
import { VIEWPORT_BOTTOM_CENTER } from "../kit/shell/viewportLayout.js";

// Above the colour bar, which is itself one playbar's height above the playbar's line.
const ABOVE_COLOUR_BAR = "5.5rem";

/**
 * The Drop routine's readout, while it plays: the real time ("0.32 s", "0.21 ms after landing", "At rest") and, while it
 * is slowed, by how much ("slowed 2,000×"), small and plain above the colour bar. The routine writes it every frame
 * through `clockRef.current(words)` (`fea/fall.js`'s `fallWords`), never through React; `null` puts it away. It takes no
 * pointer, and is not read out as it changes (a screen reader has the playbar's time).
 */
export default function FeaFallClock({ clockRef }) {
  const boxRef = useRef(null);
  const timeRef = useRef(null);
  const scaleRef = useRef(null);
  useLayoutEffect(() => {
    clockRef.current = (words) => {
      const box = boxRef.current;
      if (!box) return;
      box.style.display = words ? "" : "none";
      if (!words) return;
      if (timeRef.current.textContent !== words.time) timeRef.current.textContent = words.time;
      if (scaleRef.current.textContent !== words.scale) scaleRef.current.textContent = words.scale;
      scaleRef.current.style.display = words.scale ? "" : "none";
    };
    return () => { clockRef.current = null; };
  }, [clockRef]);
  return (
    <div ref={boxRef} className="pointer-events-none absolute inset-x-0 z-20 flex justify-center px-3" aria-hidden="true" data-fea-fall-clock=""
      style={{ display: "none", bottom: `calc(${VIEWPORT_BOTTOM_CENTER} + ${ABOVE_COLOUR_BAR})` }}>
      <div className={cn("flex items-center gap-2 rounded-md px-2.5 py-1 text-micro tabular-nums text-muted-foreground", FLOATING_CHROME_SURFACE_CLASS)}>
        <span ref={timeRef} className="text-foreground" data-fea-fall-time="" />
        <span ref={scaleRef} data-fea-fall-scale="" />
      </div>
    </div>
  );
}
