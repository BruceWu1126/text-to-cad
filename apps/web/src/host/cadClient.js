import { createCadClient } from "@text-to-cad/core/client";

/**
 * The web viewer's connection to the server it was loaded from. Its catalog is read every two
 * seconds while the page is seen. A hidden page asks nothing: it is read again the moment it is
 * shown (`CadViewer` refreshes the catalog on visibility and focus), so a tab in the background is
 * current when it is shown, and a build's feed reads the catalog itself when the build settles.
 * The CAD app polls nothing; this is the web's alone.
 */
export function createWebCadClient({ document: page = globalThis.document, location = globalThis.location, ...options } = {}) {
  const session = location?.pathname.match(/^\/s\/[^/]+/)?.[0] || "";
  return createCadClient({ origin: session ? `${location.origin}${session}` : "", ...options, shouldPoll: () => page.visibilityState !== "hidden" });
}
