/**
 * The surfaces of everything that floats over the model, defined here once so they cannot drift
 * apart; a caller adds layout, never a surface of its own. Both are translucent and blurred, so the
 * model reads through without the text losing to it, with a border that keeps the edge on a light
 * scene and a dark one alike.
 *
 * `FLOATING_SURFACE_CLASS`: the popovers and menus opened over the viewport (a mode menu, the
 * viewport's context menu, the app menu, the explorer, Display's settings, the Animation section's Playback settings) — read while
 * they are up, and opaque enough that their text never competes with the model.
 * `FILE_SIDEBAR_SURFACE_CLASS`: the file sidebar, mostly text, up for as long as a file is open:
 * between the two, the background at 55% and lightly blurred (4px).
 * `FLOATING_CHROME_SURFACE_CLASS`: the tool strip, its sub-toolbars and each panel of the tool
 * stack, which stay up beside the model for as long as a file is open: the background at 30% and
 * barely blurred (1px), so the model behind them is easy to make out, with the same border.
 *
 * Each tones what it floats over toward its own background before laying itself on it. In the
 * light theme the backdrop's contrast is halved and its brightness raised by a third, which keeps
 * white white and lifts black to a third of the way to it; in the dark, its brightness drops by a
 * third, which keeps black black and takes white down to two thirds. Over the theme's own scene
 * nothing changes, and a picture the other way round from the theme (a board keeps KiCad's dark
 * whatever the theme; a white part under the dark theme's strip) cannot take the text: the strip's
 * glyphs keep better than 5:1 over black. The filter's order is the tone's (contrast, then brightness),
 * so each is written whole rather than from Tailwind's backdrop utilities, which compose in an
 * order of their own.
 */
export const FLOATING_SURFACE_CLASS = "border border-border bg-background/75 text-foreground shadow-sm "
  + "[-webkit-backdrop-filter:blur(12px)_contrast(0.5)_brightness(1.333)] [backdrop-filter:blur(12px)_contrast(0.5)_brightness(1.333)] "
  + "dark:[-webkit-backdrop-filter:blur(12px)_brightness(0.667)] dark:[backdrop-filter:blur(12px)_brightness(0.667)]";
export const FILE_SIDEBAR_SURFACE_CLASS = "border border-border bg-background/55 text-foreground shadow-sm "
  + "[-webkit-backdrop-filter:blur(4px)_contrast(0.5)_brightness(1.333)] [backdrop-filter:blur(4px)_contrast(0.5)_brightness(1.333)] "
  + "dark:[-webkit-backdrop-filter:blur(4px)_brightness(0.667)] dark:[backdrop-filter:blur(4px)_brightness(0.667)]";
export const FLOATING_CHROME_SURFACE_CLASS = "border border-border bg-background/30 text-foreground shadow-sm "
  + "[-webkit-backdrop-filter:blur(1px)_contrast(0.5)_brightness(1.333)] [backdrop-filter:blur(1px)_contrast(0.5)_brightness(1.333)] "
  + "dark:[-webkit-backdrop-filter:blur(1px)_brightness(0.667)] dark:[backdrop-filter:blur(1px)_brightness(0.667)]";
