import { VIEWPORT_INSET_PX, VIEWPORT_STACK_BOTTOM, VIEWPORT_TOP_BAR_PX } from "./viewportLayout.js";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { Maximize2, Minimize2 } from "lucide-react";
import { ToolbarButton } from "@text-to-cad/ui/primitives/toolbar-button";
import { cn } from "@text-to-cad/ui/utils";
import PreviewChrome from "../tools/PreviewChrome.jsx";
import { useViewerMobile } from "../../../file-viewer/responsive.js";
import ViewerAlertCard, { alertDismissible, useAlertDismissal } from "../status/ViewerAlertCard.jsx";
import { modelUpdateStatus, ViewUpdateStatus } from "../status/ViewUpdateStatus.jsx";
import ViewerLoadingOverlay from "../status/ViewerLoadingOverlay.js";
import { VIEWER_RENDER_PROFILE, renderProfileKeepsPixelRatio, sceneForRenderProfile } from "../viewport/renderProfile.js";
import DisplayPopover from "./DisplayPopover.jsx";
import { withGroups } from "../../../annotation/geometry.js";
import PlaybackMenu, { OrbitMenu, RoutineMenu } from "../tools/PlaybackMenu.jsx";
import FloatingToolBar, { STRIP_BUTTON_CLASS } from "../tools/FloatingToolBar.js";
import { AnnotationToolbar } from "../../../annotation/toolbar.jsx";
import ToolStack from "../tools/ToolStack.jsx";
import { AnimationToolbar } from "../tools/playbar/AnimationPanel.jsx";
import FileSidebar from "../tools/FileSidebar.jsx";
import { ViewportAnimationBar, animationControlsHaveContent } from "../tools/playbar/ViewportAnimationBar.js";
import QuickEdit from "../tools/quick-edit/QuickEdit.jsx";
import { ViewportTopRight } from "./ViewportTopRight.jsx";
import ShellViewport from "./ShellViewport.jsx";
import ViewportContextMenu from "./ViewportContextMenu.jsx";
import AnnotationSelectionBar from "../tools/annotate/AnnotationSelectionBar.jsx";

// The strip and the panels under it share one column at the top centre, its height the room down
// to the cube's line, so however many panels are up it never runs past the viewer
// (`ToolPanel.jsx` decides which of them gives way).
const INSET = `${VIEWPORT_INSET_PX}px`;
const TOOLBAR_POSITION = Object.freeze({ top: INSET, left: "50%", bottom: VIEWPORT_STACK_BOTTOM, transform: "translateX(-50%)",
  maxWidth: `calc(100% - ${2 * VIEWPORT_INSET_PX}px)` });
// The file sidebar runs down the left edge, the viewer's full height within its inset: the cube is
// in the bottom-right corner.
const SIDEBAR_POSITION = Object.freeze({ top: INSET, left: INSET, bottom: INSET });

/** One of the view's controls: a tile of the strip (or of preview's row in its place), named on hover. */
function ViewControl({ label, disabled = false, tooltipSide = "top", onClick, children }) {
  return <ToolbarButton label={label} tooltipSide={tooltipSide} disabled={disabled} onClick={onClick} className={STRIP_BUTTON_CLASS}>
    {children}
  </ToolbarButton>;
}

/**
 * The frame every file-family renderer draws itself in: the viewport box with the tool strip at
 * its top centre and the tool stack under it, the file sidebar down its left side, Quick Edit at
 * the top-right, the cube in the
 * bottom-right corner, a 3D view's controls at the strip's end (Display's settings and
 * Preview), the loading, update and alert overlays, and preview
 * mode's controls. The same
 * structure, classes and data attributes for every renderer: hosts, stylesheets and tests key on
 * them. The toolbar is for using tools, the sidebar for reading the file: a tool's own controls are
 * panels in the tool stack, shown by the tool they belong to; what there is to read of the file (a
 * tree, joints, metadata) is the sidebar's sections, whatever the tool. Nothing sits at the bottom
 * centre but a playbar: preview's, or the
 * Animation tool's while it is up.
 *
 * @param {{ shell: ReturnType<typeof import("./useRendererShell.js").useRendererShell>,
 *   tools: import("../tools/FloatingToolBar.js").ViewportTool[],
 *   toolPanels?: import("react").ReactNode, sidebar?: import("react").ReactNode,
 *   references?: readonly import("@text-to-cad/core/prompt").PromptReference[],
 *   referenceChips?: readonly import("../tools/quick-edit/ReferenceChips.jsx").ReferenceChip[] | null,
 *   nameReference?: ((reference: import("@text-to-cad/core/prompt").PromptReference) => string | import("../inspector/pickGlyph.jsx").CompactName) | null,
 *   copySelection?: (() => unknown) | null,
 *   contextMenuItems?: ((press: { clientX: number, clientY: number, shiftKey: boolean }) => object[] | null) | null,
 *   onContextMenuOpenChange?: ((open: boolean) => void) | null,
 *   frameProvider?: ((frame: import("react").ReactNode) => import("react").ReactNode) | null,
 *   onCanvasPointerDown?: ((event: import("react").PointerEvent) => void) | null,
 *   viewportOverlay?: import("react").ReactNode | ((viewport: { runtimeRef: object, hostRef: object,
 *     mountRef: object, viewerReadyTick: number, commitScene: () => boolean }) => import("react").ReactNode) }} props
 *   `tools`: left to right, from `shell.tools`; an EMPTY list draws no strip at all, which is what
 *   a file whose viewport only orbits, pans and zooms hands over. A tool the
 *   file cannot offer is left out, never handed over disabled.
 *   `toolPanels`: the tool stack's panels, top to bottom — each a `ToolPanel`
 *   (`kit/tools/ToolPanel.jsx`), shown or `hidden` by the renderer as its tools say: the
 *   effects a person keeps. The stack is one column under the strip, gone in preview. The
 *   shell's own tools' panels lead it: Annotate's, and the Animation tool's.
 *   `subToolbar`: the renderer's tool in hand's own row under the strip (`kit/tools/SubToolbar.jsx`:
 *   the active tool); the shell's own tools' rows (Annotate's, Animation's) take its place while theirs is up.
 *   `sidebar`: the file sidebar's sections, top to bottom — each a `ToolPanel`, drawn as a
 *   section (`kit/tools/FileSidebar.jsx`): a tree, joints, metadata. None (null): no sidebar.
 *   Gone in preview, kept mounted, so a tree keeps its expansion and scroll.
 *   `referenceChips`: the renderer's description of that selection, as Quick Edit shows it (its
 *   chips, `kit/tools/quick-edit/ReferenceChips.jsx`): a chip each for one or several picks of a kind
 *   on one part, its card, its hover and its removal. Without them Quick Edit counts the references.
 *   `nameReference`: what the renderer calls a reference into its file, for a reference pasted
 *   into Quick Edit's note ("cam"); "" for one it cannot name.
 *   `references`: what is selected, in the prompt grammar — the references a Quick Edit attaches
 *   (`kit/tools/quick-edit/QuickEdit.jsx`), counted in its header; the file itself always goes. A
 *   renderer that hands none has no Quick Edit: only a view whose picks and annotations a note can
 *   carry offers one. The box sizes itself, for as long as it is open: a drag of its corner renders nothing
 *   here. `onClearReferences`: the renderer's clear of that selection, as a press on the
 *   background makes it; Quick Edit's X calls it, and clears Annotate's ink too.
 *   `copySelection`: the viewer's copy key (⌘C / Ctrl+C) while the renderer's own tool is up and
 *   something is selected (Annotate's copies the view with its ink); null when there is nothing to copy.
 *   `contextMenuItems`: what THIS renderer offers on a secondary tap over the canvas; the
 *   gesture, the anchor and the dismissal are the shell's (`ViewportContextMenu.jsx`), and a
 *   renderer that passes none has no viewport menu at all. `onContextMenuOpenChange(open)`
 *   says while that menu is up, for a renderer that marks what the menu is about.
 *   `viewportOverlay`: the renderer's own layer over the canvas; as a function it is given
 *   the viewport, which is five things: its live runtime, the element its pointer events
 *   arrive on, the element the canvas is mounted in, a tick that changes when the runtime is
 *   replaced, and `commitScene()` for a scene that changed IN PLACE. A handle overlay or a
 *   pointer pick (`kit/tools/select/usePointerPick.js`) reads the first, second and fourth;
 *   a renderer that draws its own canvas over the model or publishes a scene progressively
 *   needs the other two.
 *   `frameProvider`: the renderer wraps the WHOLE frame — its own context, above the tool
 *   stack as well as the viewport, because both read it. It is given the frame and returns
 *   it wrapped; a renderer that passes none is mounted exactly as it is.
 *   `onCanvasPointerDown`: a press that landed on the canvas, before anything in the viewport sees
 *   it. The frame focuses itself on such a press whatever the renderer does; this is for a renderer
 *   that also has something to put down when the person reaches for the model.
 *   `onSelectPress`: under Select, a press on the canvas, before the annotation's ink and any part:
 *   the renderer's own pickable overlays (a measurement). True when it picked something, and the
 *   press goes no further; false to let it on.
 *   `onSelectHover`: under Select, the pointer resting on the canvas (null: it left, or Select went
 *   down): the renderer lights its own overlay under it, and returns true when there is one.
 *   `selectsAnnotations`: whether Select picks the annotation's ink, as the renderer's Select filter says; the
 *   ink is drawn either way.
 */
export default function RendererShell({ shell, tools, toolPanels = null, subToolbar = null, sidebar = null, references = null, referenceChips = null, nameReference = null, onClearReferences = null, copySelection = null, contextMenuItems = null,
  onContextMenuOpenChange = null, viewportOverlay = null,
  frameProvider = null, onCanvasPointerDown = null, onSelectPress = null, onSelectHover = null, selectsAnnotations = true }) {
  const frame = shell.frame;
  const mobile = useViewerMobile();
  const { view, resolvedScene, viewerLoading, scene } = frame;
  // The one presentation state: every gate — the viewport's, the renderer's — reads it.
  const { previewing, setPreviewing } = shell;
  const animation = frame.animation;
  const hasAnimation = animationControlsHaveContent(animation);
  // Speed and Loop, once chosen in the mode on screen — the Animation tool's, the file's, or preview's
  // Playback settings, preview's alone — are what its routine plays with, whatever it authored, until
  // they are chosen again. Unset, the routine's own apply.
  const { speed: chosenSpeed, loop: chosenLoop } = shell.routinePlayback;
  const animationRef = useRef(animation);
  animationRef.current = animation;
  useEffect(() => {
    const runtime = animationRef.current;
    if (!animationControlsHaveContent(runtime)) return;
    if (chosenSpeed != null && Number(runtime.speed) !== chosenSpeed) runtime.onSpeedChange(chosenSpeed);
    if (chosenLoop != null && (runtime.loopEnabled !== false) !== chosenLoop) runtime.onLoopToggle(chosenLoop);
  }, [hasAnimation, animation?.speed, animation?.loopEnabled, animation?.activeClipId, chosenSpeed, chosenLoop]);
  // What those settings change is chosen for the mode on screen and applied to its routine at once.
  const playbackRuntime = hasAnimation ? {
    ...animation,
    onSpeedChange: value => { shell.chooseRoutinePlayback({ speed: value }); animation.onSpeedChange(value); },
    onLoopToggle: value => { shell.chooseRoutinePlayback({ loop: value }); animation.onLoopToggle(value); }
  } : null;
  // Preview orbits the model from the moment it starts, unless the file's Orbit says otherwise:
  // orbit on or off is the file's, kept from one preview to the next.
  const orbitPlaying = shell.playback.orbit;
  const setOrbitPlaying = value => shell.setPlayback({ orbit: typeof value === "function" ? value(shell.playback.orbit) : value });
  // Under Select, what the pointer rests on is lit as a part is: the annotation's mark (and the marks
  // drawn with it) or the renderer's overlay, and the part under it is not.
  const [hoveredAnnotation, setHoveredAnnotation] = useState(null);
  const overSelectable = useRef(false);
  // The pointer cursor over them, as a renderer shows over a part.
  const [pointing, setPointing] = useState(false);
  const selectHover = useRef(onSelectHover);
  selectHover.current = onSelectHover;
  const leaveSelectHover = () => {
    overSelectable.current = false;
    setPointing(false);
    selectHover.current?.(null);
    setHoveredAnnotation(current => (current ? null : current));
  };
  useEffect(() => { if (!frame.selecting) leaveSelectHover(); }, [frame.selecting]);
  const hoverSelectable = event => {
    if (!frame.selecting || event.buttons !== 0 || !(event.target instanceof Element) || !event.target.closest("canvas")) {
      if (overSelectable.current || hoveredAnnotation) leaveSelectHover();
      return;
    }
    const session = frame.annotation;
    const overlay = Boolean(selectHover.current?.(event));
    const id = !overlay && selectsAnnotations && session.items.length ? frame.viewerRef.current?.annotationAt?.(event.clientX, event.clientY) : null;
    const ids = id ? withGroups([id], session.items) : null;
    setHoveredAnnotation(current => (ids && current && current.join() === ids.join()) || (!ids && !current) ? current : ids);
    const over = overlay || Boolean(id);
    if (over && !overSelectable.current) {
      // The part lit under the pointer lets go, as when the pointer leaves the model.
      for (let node = event.target; node && node !== event.currentTarget; node = node.parentElement) {
        node.dispatchEvent(new PointerEvent("pointerleave", { bubbles: false, pointerId: event.pointerId, pointerType: event.pointerType }));
      }
    }
    overSelectable.current = over;
    setPointing(over);
    // Over a mark or a measurement the part under it hears nothing of the pointer.
    if (over) event.stopPropagation();
  };
  useEffect(() => { setPreviewing(false); }, [frame.modelKey, setPreviewing]);
  // One viewport, two render profiles over the same Display settings (`renderProfile.js`): the
  // tools view is drawn for working on the model, preview for looking at it.
  const renderProfile = previewing ? VIEWER_RENDER_PROFILE.PREVIEW : VIEWER_RENDER_PROFILE.TOOLS;
  const drawnScene = useMemo(() => sceneForRenderProfile(resolvedScene, renderProfile), [resolvedScene, renderProfile]);
  // Preview is a state of its own (`useRendererShell`'s `setPreviewing`): it opens on the model as
  // authored, its routine at rest and started only by Autoplay, as taking up the tool does, and
  // leaving it gives the tools view back exactly as it was.
  const enterPreview = () => setPreviewing(true);
  const leavePreview = () => setPreviewing(false);
  // Preview is fullscreen: the page around the view steps aside while it lasts.
  const onFullscreenChange = view.onFullscreenChange;
  useEffect(() => {
    if (!previewing) return undefined;
    onFullscreenChange?.(true);
    return () => onFullscreenChange?.(false);
  }, [previewing, onFullscreenChange]);
  // Once neither preview nor the Animation tool holds the routine, it is put down: paused where it
  // is, a frame past its start kept on the model under every other tool, one at its start back at rest
  // (its Routine, Speed and Loop stay for the next time). Leaving preview with the tool up is not
  // that: the tool's routine comes back as preview found it.
  const putDownRef = useRef(null);
  putDownRef.current = animation?.onPutDown || animation?.onRelease || null;
  const routineHeld = previewing || frame.animateToolActive;
  const wasHeld = useRef(routineHeld);
  useEffect(() => {
    if (wasHeld.current && !routineHeld) putDownRef.current?.();
    wasHeld.current = routineHeld;
  }, [routineHeld]);
  // Annotate's sub-toolbar, one row under the strip while it is the tool: its tools, what the next mark
  // looks like, and its Undo, Redo and Clear (`annotation/toolbar.jsx`). The annotation itself has no
  // panel: it is Quick Edit's chip (its X clears it, a click copies it).
  const colorScheme = view.appearance?.colorScheme === "dark" ? "dark" : "light";
  // Animation's, while it is up: the transport and its settings; there is no playbar under the model
  // in the tools view. Otherwise the renderer supplies its active tool's row.
  const toolRow = frame.annotateToolActive
    ? <AnnotationToolbar annotation={frame.annotation} history={frame.history} colorScheme={colorScheme} />
    : frame.animateToolActive && hasAnimation ? <AnimationToolbar key={frame.modelKey} runtime={playbackRuntime} disabled={viewerLoading || !scene}
      autoplay={shell.autoplay} onAutoplayChange={shell.setAutoplay} />
    : subToolbar;

  const hasContent = Boolean(scene) && !viewerLoading;
  // A load the model did not survive: an alert that cannot be put away (a failed update that
  // keeps the previous version on screen can be, and keeps its chrome).
  const failed = Boolean(frame.viewerAlert) && !alertDismissible(frame.viewerAlert, hasContent);
  // The card's dismissal is the frame's, so it outlives the card (gone in preview): while the person
  // has the card put away, its icon is the navbar's way back to it, leftmost of the right-hand group.
  const alertDismissal = useAlertDismissal(frame.viewerAlert, { hasContent, scope: frame.modelKey, onNavigationActionsChange: view.onNavigationActionsChange });
  // While the model loads, or once it has failed to, the viewer shows none of its own chrome: no
  // tools, no Quick Edit, no cube, no view actions and no update status -- only the load itself,
  // or the card saying why it failed. They arrive with the model, and stay through a rebuild that
  // keeps it on screen (that is `updating`, not loading).
  const chromeHidden = viewerLoading || failed;
  // A host showing the view small (inline in a conversation: `appearance.compact`) gets the model
  // alone, not the tools, Quick Edit, the view actions or the cube; shown full size, all return.
  const compact = Boolean(view.appearance?.compact);
  const toolsHidden = chromeHidden || compact;
  // On a narrow view the side columns start under all the controls, including a wrapped
  // strip and Annotate's two rows. Measure that actual height; tool changes and resizing
  // update one CSS variable without redrawing the model or changing saved panel sizes.
  const topControls = useRef(null);
  useLayoutEffect(() => {
    const controls = topControls.current;
    const viewport = controls?.closest("[data-cad-scene-backdrop]");
    if (!controls || !viewport) return undefined;
    const measure = () => viewport.style.setProperty("--cad-tools-bottom", `${VIEWPORT_INSET_PX * 2 + controls.offsetHeight}px`);
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(controls);
    return () => { observer?.disconnect(); viewport.style.removeProperty("--cad-tools-bottom"); };
  }, [toolsHidden]);
  const copyAction = () => {
    if (previewing) return false;
    if (frame.annotateToolActive) { frame.copyAnnotation(); return true; }
    if (copySelection) { copySelection(); return true; }
    return false;
  };
  frame.copyActionRef.current = copyAction;
  // Quick Edit's X: nothing picked and nothing drawn, as the person would clear each.
  const clearQuickEdit = () => { onClearReferences?.(); if (frame.annotation.hasAnnotation) frame.annotation.forget(); frame.measurements?.clear?.(); };
  const forgetAnnotation = frame.annotation.forget;
  // Quick Edit's annotation: while Annotate is up or there is ink. The annotation goes with a note whenever
  // there is one (its views and annotation file, `frame.quickEditPrompt`); its Annotation chip's X wipes it.
  const annotation = useMemo(() => frame.annotateToolActive || frame.annotation.hasAnnotation
    ? { present: frame.annotation.hasAnnotation, clear: forgetAnnotation } : null,
  [frame.annotateToolActive, frame.annotation.hasAnnotation, forgetAnnotation]);
  // The renderer's overlay and the shell's own layers share one viewport context.
  const overlay = viewport => <>
    {previewing || !contextMenuItems ? null : <ViewportContextMenu viewport={viewport} items={contextMenuItems} onOpenChange={onContextMenuOpenChange} />}
    {/* The ink Select has picked, its bar: restyle, fill, delete. */}
    {frame.selecting && frame.annotation.selected.length ? <AnnotationSelectionBar annotation={frame.annotation} colorScheme={colorScheme} runtimeRef={viewport.runtimeRef}
      viewerReadyTick={viewport.viewerReadyTick} onDelete={frame.deletePicked} disabled={viewerLoading || !scene} /> : null}
    {typeof viewportOverlay === "function" ? viewportOverlay(viewport) : viewportOverlay}
  </>;
  // One place says the view is catching up: a newer revision of the file loading behind the model on
  // screen, or a Display change being prepared — the latter's failure first, since it is the one with
  // something to retry. Under the strip at the top centre, or in its place where there is no strip.
  const updateStatus = failed ? null : <div className="pointer-events-none flex max-w-full items-center" data-viewport-status="">
    <ViewUpdateStatus status={frame.loading.updating && !frame.viewUpdate.status.error
      ? modelUpdateStatus(frame.loading.progress) : frame.viewUpdate.status} onRetry={frame.viewUpdate.retry}
      className="rounded-md bg-background/95 px-1 py-0.5 shadow-sm" />
  </div>;
  const body = (
    <div
      className={cn("relative flex h-full min-h-0 flex-col overflow-hidden bg-background text-foreground", pointing && "cursor-pointer")}
      data-slot="cad-file-view"
      data-cad-surface
      tabIndex={-1}
      onPointerDownCapture={event => {
        if (!(event.target instanceof Element) || !event.target.closest("canvas")) return;
        onCanvasPointerDown?.(event);
        event.currentTarget.focus({ preventScroll: true });
        // Under Select a press picks, front to back: the renderer's own overlays (a measurement),
        // then the annotation's mark (and the marks drawn with it), then a part. Shift adds or takes
        // away; a press that picks one kind lets go of the others, unless Shift keeps them.
        if (!frame.selecting || event.button !== 0) return;
        const session = frame.annotation;
        if (onSelectPress?.(event)) {
          event.stopPropagation();
          event.preventDefault();
          if (!event.shiftKey && session.selected.length) session.select([]);
          return;
        }
        const id = selectsAnnotations && session.items.length ? frame.viewerRef.current?.annotationAt?.(event.clientX, event.clientY) : null;
        if (!id) { if (!event.shiftKey && session.selected.length) session.select([]); return; }
        event.stopPropagation();
        event.preventDefault();
        const picked = event.shiftKey
          ? (session.selected.includes(id) ? session.selected.filter(other => other !== id) : [...session.selected, id])
          : [id];
        if (!event.shiftKey) onClearReferences?.();
        session.select(withGroups(picked, session.items));
      }}
      onPointerMoveCapture={hoverSelectable}
      onPointerLeave={() => { if (overSelectable.current || hoveredAnnotation) leaveSelectHover(); }}
      ref={frame.hostRef}
    >
      <div className="relative flex h-full min-w-0 flex-col overflow-hidden bg-transparent">
        <div className="relative min-h-0 flex-1 overflow-hidden">
          <div className="flex h-full min-w-0">
            {/* The render pane's box. The canvas fills exactly this area, so a camera fit
                centres in what is visible and a change of the pane's size reaches the scene
                as a plain resize. Its background is the
                scene's edge colour: the canvas is resized on the next frame, and one frame of
                the chrome's background above a dark stage is a visible band. */}
            <div
              className="@container/cad-viewport pointer-events-none relative min-w-0 flex-1 overflow-hidden"
              data-cad-scene-backdrop={frame.sceneBackdrop}
              style={{ backgroundColor: frame.sceneBackdrop }}
            >
              <div className="pointer-events-auto absolute inset-0 z-0">
                <div className="absolute inset-0">
                  <ShellViewport
                    ref={frame.viewerRef}
                    scene={scene}
                    modelKey={frame.modelKey}
                    presentationKey={frame.presentationKey}
                    sceneScaleMode={frame.sceneScaleMode}
                    perspective={frame.viewerPerspective}
                    perspectiveRef={frame.activePerspectiveRef}
                    projection={resolvedScene.camera.projection}
                    focalLength={resolvedScene.camera.focalLength}
                    themeSettings={resolvedScene.theme}
                    displaySettings={drawnScene.display}
                    appearance={resolvedScene.appearance}
                    receiveShadows={resolvedScene.view.lighting.enabled}
                    renderMode={resolvedScene.render.enabled}
                    renderConfiguration={resolvedScene.render.configuration}
                    quality={drawnScene.quality}
                    orbitPreview={previewing && orbitPlaying}
                    previewMode={previewing}
                    previewOrbitSpeed={frame.previewOrbitSpeed ?? 1}
                    isLoading={viewerLoading}
                    viewCube={!compact && !failed}
                    viewUpdate={frame.viewUpdate}
                    loadingPresentation={frame.loading}
                    annotationEnabled={frame.annotateToolActive}
                    annotation={frame.annotation}
                    hoveredAnnotation={hoveredAnnotation}
                    annotationView={frame.currentView}
                    onPerspectiveChange={frame.handlePerspectiveChange}
                    onPresentationChange={frame.handlePresentationChange}
                    onViewerAlertChange={frame.setRuntimeAlert}
                    onCameraSettled={frame.onCameraSettled}
                    preserveInteractionPixelRatio={frame.preserveInteractionPixelRatio || renderProfileKeepsPixelRatio(renderProfile)}
                    runtimeLifecycle={frame.runtimeLifecycle}
                  >{overlay}</ShellViewport>
                </div>
              </div>

              {/* Preview: fullscreen, the navbar and the tools put away and the model orbiting, its
                  routines playing. Its row in the strip's place: Orbit, Display, then the way
                  out, where Preview was. Under the model, a
                  file with routines has its playbar, its Routines at its left end and its Playback
                  settings at its right; a static file has nothing there at all. */}
              <PreviewChrome active={previewing} surface={frame.hostElement}
                corner={menu => <>
                  <OrbitMenu orbit={orbitPlaying} onOrbitChange={setOrbitPlaying} onOpenChange={menu("orbit")}
                    speed={frame.previewOrbitSpeed || 1} onSpeedChange={frame.setPreviewOrbitSpeed} />
                  <DisplayPopover disabled={shell.idle} boundary={frame.hostElement} tooltipSide="bottom" mode={frame.displayMode} projection={frame.projection}
                    onOpenChange={menu("display")}>{frame.display}</DisplayPopover>
                  <ViewControl label="Exit preview" tooltipSide="bottom" onClick={leavePreview}><Minimize2 aria-hidden="true" /></ViewControl>
                </>}
                playbar={menu => hasAnimation ? <ViewportAnimationBar key={frame.modelKey} runtime={animation}
                  className="pointer-events-auto" disabled={viewerLoading || !scene}
                  leading={<RoutineMenu animation={animation} onOpenChange={menu("routines")} />}
                  trailing={<PlaybackMenu animation={playbackRuntime} onOpenChange={menu("playback")}
                    autoplay={shell.autoplay} onAutoplayChange={shell.setAutoplay} />} /> : null}>

              {/* The file explorer floats over the top-left corner, above the sidebar, which stays drawn under it. */}
              {toolsHidden || !sidebar ? null : <FileSidebar hidden={previewing} style={SIDEBAR_POSITION}
                layout={frame.toolStack} onLayoutChange={frame.changeToolStack}>{sidebar}</FileSidebar>}
              {toolsHidden ? null : <div className="group/tool-stack pointer-events-none absolute z-20 flex flex-col items-center gap-2" style={TOOLBAR_POSITION}
                data-mobile={mobile ? "" : undefined} data-cad-tool-groups="">
                {/* The view's controls end the strip: Display, then Preview, for a 3D view
                    (`useRendererShell`'s `previewable`); another has neither. */}
                <div ref={topControls} className="flex max-w-full shrink-0 flex-col items-center gap-2" data-cad-top-controls="">
                  <FloatingToolBar tools={tools} trailing={shell.previewable && !previewing ? <>
                    <DisplayPopover disabled={shell.idle} boundary={frame.hostElement} mode={frame.displayMode} projection={frame.projection}>{frame.display}</DisplayPopover>
                    <ViewControl label="Preview" disabled={shell.idle} onClick={enterPreview}><Maximize2 aria-hidden="true" /></ViewControl>
                  </> : null} />
                  {/* The tool in hand's own row, then the view catching up, under the strip. */}
                  {previewing ? null : toolRow}
                  {previewing ? null : updateStatus}
                </div>
                <ToolStack hidden={previewing} layout={frame.toolStack} onLayoutChange={frame.changeToolStack}>{toolPanels}</ToolStack>
              </div>}
              {/* The top-right: the host's notice once a model is on screen (`view.notice`), and Quick Edit
                  under it -- hidden, not unmounted, while the view loads: a note being written outlives a
                  reload of the model. */}
              <ViewportTopRight notice={chromeHidden ? null : view.notice} belowStrip={!toolsHidden}>
                {compact || !references ? null : <QuickEdit key={frame.modelKey} className="self-stretch" hidden={chromeHidden}
                  resource={frame.resource} references={references} chips={referenceChips} nameReference={nameReference} annotation={annotation} measurements={frame.measurements} prompt={frame.quickEditPrompt}
                  onCopy={copyAction} onEscape={frame.escape} onClear={clearQuickEdit} disabled={viewerLoading || !scene} />}
              </ViewportTopRight>

              </PreviewChrome>

              {/* The view catching up (`updateStatus`), at the top centre while there is no strip. */}
              {failed || (!toolsHidden && !previewing) ? null : <div className="pointer-events-none absolute left-1/2 z-30 flex max-w-[calc(100%-1rem)] -translate-x-1/2 items-center"
                style={{ top: VIEWPORT_INSET_PX, height: VIEWPORT_TOP_BAR_PX }}>{updateStatus}</div>}
              <ViewerLoadingOverlay
                loading={frame.presentationState?.file === frame.modelKey && frame.presentationState?.covering ? null : frame.loading}
                operationKey={frame.modelKey}
              />
              {/* The alert card, last: over the tools, the parts tree among them, which a warning the
                  model survives keeps on screen. The file as the alert names it (the catalog's absolute
                  path), which Report Issue keeps out of its issue. */}
              {!previewing ? <ViewerAlertCard alert={frame.viewerAlert} hasContent={hasContent} dismissed={alertDismissal.dismissed} onDismiss={alertDismissal.dismiss}
                onReload={view.reload} file={frame.modelKey || view.file?.path} /> : null}
            </div>

          </div>
        </div>

      </div>
    </div>
  );
  return frameProvider ? frameProvider(body) : body;
}
