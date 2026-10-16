import { useCallback, useEffect, useState } from "react";
import { ToolbarTooltipScope } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import { FLOATING_CHROME_SURFACE_CLASS } from "../../../lib/floatingSurface.js";
import { VIEWPORT_INSET_PX } from "../shell/viewportLayout.js";

export const PREVIEW_CHROME_IDLE_MS = 1000;
// A browser test on a slow software renderer stretches the idle (`window.__cadPreviewChromeIdleMs`)
// so the chrome is not put away between two of its steps.
const previewChromeIdleMs = () => Number(globalThis.window?.__cadPreviewChromeIdleMs) || PREVIEW_CHROME_IDLE_MS;
const NO_MENUS = Object.freeze([]);

/**
 * The viewer's chrome around preview mode. `children` — the tool strip and its stack, Quick Edit,
 * everything a person edits with — is hidden and inert while `active`. Preview is fullscreen: the
 * navbar steps aside, and the tool strip with it, and preview's own row takes the strip's place at
 * the view's top centre (`corner`: Orbit, Display and the way out), on the strip's surface: Display
 * is a tile of it as it was of the strip, and the way out where Preview was, at its end. The
 * corner and the `playbar` under the model share one idle deadline and a 150ms fade: movement over
 * `surface` wakes them, and hovering them (`data-preview-hover-hold`) or an open menu keeps them up.
 *
 * @param {{ active: boolean, surface?: Element | null,
 *   corner?: import("react").ReactNode | ((menu: (id: string) => (open: boolean) => void) => import("react").ReactNode),
 *   playbar?: import("react").ReactNode | ((menu: (id: string) => (open: boolean) => void) => import("react").ReactNode),
 *   children?: import("react").ReactNode }} props
 *   `corner` and `playbar`, when they are functions, are given `menu(id)`: the `onOpenChange` of a
 *   menu in them, under a name of its own, so the chrome stays up while any of them is open — one
 *   closing as another opens never lets it go.
 */
export default function PreviewChrome({ active, surface, corner = null, playbar, children }) {
  const [visible, setVisible] = useState(true);
  const [openMenus, setOpenMenus] = useState(NO_MENUS);
  const menu = useCallback(id => open => setOpenMenus(current => {
    if (open === current.includes(id)) return current;
    return open ? [...current, id] : current.filter(entry => entry !== id);
  }), []);
  const menuOpen = openMenus.length > 0;
  // A menu the chrome loses on the way out of preview never reports closing: the next preview starts idle.
  useEffect(() => { if (!active) setOpenMenus(NO_MENUS); }, [active]);
  useEffect(() => {
    setVisible(true);
    if (!active || !surface || menuOpen) return;
    let timer;
    let pressing = false;
    const wake = event => {
      clearTimeout(timer);
      setVisible(true);
      if (pressing || event?.target?.closest?.('[data-preview-hover-hold]')) return;
      timer = setTimeout(() => {
        if (!surface.querySelector("[data-preview-hover-hold]:hover")) setVisible(false);
      }, previewChromeIdleMs());
    };
    const leave = () => wake();
    const focus = () => wake();
    const down = event => { pressing = true; wake(event); };
    const up = event => { pressing = false; wake(event); };
    wake();
    surface.addEventListener('pointermove', wake);
    surface.addEventListener('pointerdown', down);
    surface.addEventListener('pointerleave', leave);
    surface.addEventListener('focusin', focus);
    window.addEventListener('pointerup', up);
    window.addEventListener('pointercancel', up);
    return () => {
      clearTimeout(timer);
      surface.removeEventListener('pointermove', wake);
      surface.removeEventListener('pointerdown', down);
      surface.removeEventListener('pointerleave', leave);
      surface.removeEventListener('focusin', focus);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('pointercancel', up);
    };
  }, [active, surface, menuOpen]);
  const shown = !active || visible || menuOpen;
  return <>
    <div data-preview-chrome="" data-visible={!active} hidden={active} inert={active}
      className="pointer-events-none absolute inset-0 z-20">
      <ToolbarTooltipScope enabled={!active}>{children}</ToolbarTooltipScope>
    </div>
    <ToolbarTooltipScope enabled={shown}><div data-preview-controls="" data-preview-active={active ? "" : undefined}
      data-visible={shown} inert={!shown}
      className="pointer-events-none absolute inset-0 z-30 transition-opacity duration-150"
      style={{ opacity: shown ? 1 : 0 }}>
      {active && corner ? <div className="absolute left-1/2 -translate-x-1/2" style={{ top: VIEWPORT_INSET_PX }}>
        <div data-preview-hover-hold="" data-preview-corner=""
          className={cn("pointer-events-auto inline-flex min-h-8 items-center gap-1 rounded-md p-1", FLOATING_CHROME_SURFACE_CLASS)}>
          {typeof corner === "function" ? corner(menu) : corner}
        </div>
      </div> : null}
      {active ? (typeof playbar === "function" ? playbar(menu) : playbar) : null}
    </div></ToolbarTooltipScope>
  </>;
}
