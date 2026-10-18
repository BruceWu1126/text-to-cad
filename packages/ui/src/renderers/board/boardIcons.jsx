/**
 * A board's glyphs, on lucide's 24-unit grid and line, so they sit in a menu beside lucide's own:
 * the Display modes (Board: the outline with a part on it; Copper: two pads joined by a trace;
 * Assembly: a part's body and legs in its dashed courtyard; Placement: two pads and the straight
 * airwire between them) and Select's kinds (a part with its legs; a pad; a net, two pads and the
 * trace between them).
 */
const icon = (name, children) => {
  function BoardIcon(props) {
    return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round"
      aria-hidden="true" {...props}>{children}</svg>;
  }
  BoardIcon.displayName = name;
  return BoardIcon;
};

export const BoardModeIcon = icon("BoardModeIcon", <><rect x="3" y="5" width="18" height="14" rx="3" /><rect x="9" y="9.5" width="6" height="5" rx="0.8" /></>);
export const CopperModeIcon = icon("CopperModeIcon", <><rect x="3" y="14" width="6" height="6" rx="1" fill="currentColor" /><rect x="15" y="4" width="6" height="6" rx="1" fill="currentColor" /><path d="M9 17h3l3-7" /></>);
export const AssemblyModeIcon = icon("AssemblyModeIcon", <><rect x="3" y="3" width="18" height="18" rx="2" strokeDasharray="2.5 2.5" /><rect x="9" y="7.5" width="6" height="9" rx="0.8" /><path d="M6.5 10H9M6.5 14H9M15 10h2.5M15 14h2.5" /></>);
export const PlacementModeIcon = icon("PlacementModeIcon", <><rect x="3" y="14" width="6" height="6" rx="1" /><rect x="15" y="4" width="6" height="6" rx="1" /><path d="M9 14l6-4" strokeDasharray="2 2.2" /></>);

export const PartKindIcon = icon("PartKindIcon", <><rect x="7" y="5" width="10" height="14" rx="1" /><path d="M3.5 8.5H7M3.5 12H7M3.5 15.5H7M17 8.5h3.5M17 12h3.5M17 15.5h3.5" /></>);
export const PadKindIcon = icon("PadKindIcon", <rect x="6.5" y="6.5" width="11" height="11" rx="2" fill="currentColor" />);
export const NetKindIcon = icon("NetKindIcon", <><rect x="2.5" y="14.5" width="6" height="6" rx="1" fill="currentColor" /><rect x="15.5" y="3.5" width="6" height="6" rx="1" fill="currentColor" /><path d="M8.5 17.5H12l3.5-11" /></>);
