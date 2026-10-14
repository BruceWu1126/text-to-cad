import * as Effect from "effect/Effect";
import * as Schema from "effect/Schema";
import * as Electron from "electron";

import * as DesktopIpc from "../ipc/DesktopIpc.ts";
import { PLUGIN_VIEW_RELEASE_CHANNEL, PLUGIN_VIEW_STAGE_CHANNEL } from "./pluginViewChannels.ts";
import {
  makePluginViewDocuments,
  PLUGIN_VIEW_SCHEME,
  pluginViewNavigationAllowed,
} from "./pluginViewDocuments.ts";

const documents = makePluginViewDocuments();

/** The renderer stages a view's HTML and gets the `mcp-app://` URL its frame loads. */
const stagePluginView = DesktopIpc.makeIpcMethod({
  channel: PLUGIN_VIEW_STAGE_CHANNEL,
  payload: Schema.Struct({ html: Schema.String, csp: Schema.Unknown }),
  result: Schema.String,
  handler: (input) => Effect.sync(() => documents.stage(input.html, input.csp)),
});

/** The frame was torn down: its document and its origin's storage go with it. */
const releasePluginView = DesktopIpc.makeIpcMethod({
  channel: PLUGIN_VIEW_RELEASE_CHANNEL,
  payload: Schema.String,
  result: Schema.Void,
  handler: (url) =>
    Effect.gen(function* () {
      const origin = documents.release(url);
      if (origin === null) return;
      yield* Effect.promise(() =>
        Electron.session.defaultSession.clearStorageData({ origin }).catch(() => undefined),
      );
    }),
});

/** Keeps every view frame on its own origin, in any window. */
const guardViewFrames = (_event: Electron.Event, contents: Electron.WebContents) => {
  contents.on("will-frame-navigate", (event) => {
    if (event.isMainFrame) return;
    if (!pluginViewNavigationAllowed(event.frame?.url, event.url)) event.preventDefault();
  });
};

/**
 * Serves staged plugin views on `mcp-app://` and exposes staging to the
 * renderer. Runs once the app is ready; the scheme's privileges are
 * registered before that with T3's own schemes (`ElectronProtocol`).
 */
export const installPluginViews = Effect.fn("desktop.pluginViews.install")(function* () {
  const ipc = yield* DesktopIpc.DesktopIpc;
  yield* ipc.handle(stagePluginView);
  yield* ipc.handle(releasePluginView);
  yield* Effect.acquireRelease(
    Effect.sync(() => {
      Electron.protocol.handle(PLUGIN_VIEW_SCHEME, (request) => documents.serve(request.url));
      Electron.app.on("web-contents-created", guardViewFrames);
    }),
    () =>
      Effect.sync(() => {
        Electron.app.off("web-contents-created", guardViewFrames);
        Electron.protocol.unhandle(PLUGIN_VIEW_SCHEME);
      }),
  );
});
