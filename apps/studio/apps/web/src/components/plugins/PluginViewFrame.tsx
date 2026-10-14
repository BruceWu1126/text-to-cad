import type { EnvironmentId, PluginViewDocument, ThreadId } from "@t3tools/contracts";
import {
  makeMcpAppHost,
  McpAppHostRefusal,
  mcpAppStyleVariables,
  mcpResourceBytes,
  type McpAppCallToolResult,
  type McpAppHost,
  type McpAppHostContext,
} from "@t3tools/client-runtime/mcp-apps";
import {
  isAtomCommandInterrupted,
  squashAtomCommandFailure,
} from "@t3tools/client-runtime/state/runtime";
import { readMcpAppCsp } from "@t3tools/shared/mcpApp";
import { useEffect, useLayoutEffect, useRef, useState } from "react";

import { APP_BASE_NAME, APP_VERSION } from "~/branding";
import { requestConfirmDialog } from "~/confirmDialog";
import { isElectron } from "~/env";
import { useHtmlRenderTheme } from "~/hooks/useHtmlRenderTheme";
import { observeResize } from "~/lib/observeResize";
import { pluginEnvironment } from "~/state/plugins";
import { useAtomCommand } from "~/state/use-atom-command";
import { Button } from "../ui/button";
import { Spinner } from "../ui/spinner";
import {
  PLUGIN_VIEW_SRCDOC_SANDBOX,
  PLUGIN_VIEW_STAGED_SANDBOX,
  readPluginViewsBridge,
  srcdocPluginViewHtml,
  stagedPluginViewHtml,
  stagedPluginViewOrigin,
} from "./pluginViewHost";

/** What opens one plugin view: the tool whose result is the view, and where it runs. */
export interface PluginViewTarget {
  readonly environmentId: EnvironmentId;
  /** The thread whose server process the view talks to; null for an environment-wide view. */
  readonly threadId: ThreadId | null;
  readonly pluginId: string;
  readonly server: string;
  readonly tool: string;
  readonly title: string;
  readonly resourceUri: string;
  readonly arguments: Record<string, unknown>;
  /** Workspace-relative file the view opens. */
  readonly file?: string;
}

/** Largest file a view may hand the user through `ui/download-file`. */
const MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024;

const refusal = (result: {
  readonly cause: Parameters<typeof squashAtomCommandFailure>[0]["cause"];
}) => {
  const error = squashAtomCommandFailure(result);
  return new McpAppHostRefusal(
    error instanceof Error && error.message.trim() !== "" ? error.message : "Request failed.",
  );
};

function saveBlob(blob: Blob, name: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

type Loaded =
  | { readonly _tag: "loading" }
  | { readonly _tag: "error"; readonly message: string }
  | { readonly _tag: "ready"; readonly document: PluginViewDocument };

/**
 * A plugin's MCP App view filling its container. The environment runs the
 * view's tool and returns its document; the frame then speaks the MCP Apps
 * bridge, its requests going to the plugin's own server. On desktop the
 * document gets its own `mcp-app://` origin so workers and storage work; in
 * a browser it runs in an opaque-origin `srcdoc` frame.
 */
export function PluginViewFrame(props: {
  readonly target: PluginViewTarget;
  /** Sends text to the thread as the user, after the user approves it. */
  readonly onSendMessage?: ((text: string) => Promise<void>) | undefined;
}) {
  const { target } = props;
  const theme = useHtmlRenderTheme();
  const boxRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLIFrameElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [loaded, setLoaded] = useState<Loaded>({ _tag: "loading" });
  const [generation, setGeneration] = useState(0);
  // Desktop: the staged URL; browser: the srcdoc. One of them, once ready.
  const [frame, setFrame] = useState<
    { readonly src: string; readonly origin: string } | { readonly srcdoc: string } | null
  >(null);
  const [closed, setClosed] = useState(false);

  const openView = useAtomCommand(pluginEnvironment.openView, { reportFailure: false });
  const request = useAtomCommand(pluginEnvironment.request, { reportFailure: false });

  useLayoutEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    setSize({ width: box.clientWidth, height: box.clientHeight });
    return observeResize(box, ([entry]) => {
      if (entry) {
        setSize({
          width: Math.round(entry.contentRect.width),
          height: Math.round(entry.contentRect.height),
        });
      }
    });
  }, []);

  const argumentsKey = JSON.stringify(target.arguments);
  useEffect(() => {
    let cancelled = false;
    // oxlint-disable-next-line react/set-state-in-effect -- Each (re)open starts from a loading state.
    setLoaded({ _tag: "loading" });
    setFrame(null);
    void openView({
      environmentId: target.environmentId,
      input: {
        threadId: target.threadId,
        pluginId: target.pluginId,
        server: target.server,
        tool: target.tool,
        arguments: JSON.parse(argumentsKey) as Record<string, unknown>,
        ...(target.file === undefined ? {} : { file: target.file }),
      },
    }).then((result) => {
      if (cancelled) return;
      if (result._tag === "Success") {
        setLoaded({ _tag: "ready", document: result.value });
      } else if (!isAtomCommandInterrupted(result)) {
        setLoaded({ _tag: "error", message: refusal(result).message });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [
    openView,
    target.environmentId,
    target.threadId,
    target.pluginId,
    target.server,
    target.tool,
    target.file,
    argumentsKey,
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- Bumped by "Try again" to reopen.
    generation,
  ]);

  // The document becomes a frame: staged on desktop, inline elsewhere.
  const document_ = loaded._tag === "ready" ? loaded.document : null;
  useEffect(() => {
    if (document_ === null) return;
    const bridge = readPluginViewsBridge();
    if (bridge === null) {
      // oxlint-disable-next-line react/set-state-in-effect -- The srcdoc is ready at once; staging is async.
      setFrame({ srcdoc: srcdocPluginViewHtml(document_) });
      return;
    }
    let cancelled = false;
    let staged: string | null = null;
    void bridge.stage(stagedPluginViewHtml(document_), document_.csp).then(
      (url) => {
        staged = url;
        if (cancelled) {
          void bridge.release(url);
          return;
        }
        setFrame({ src: url, origin: stagedPluginViewOrigin(url) });
      },
      () => {
        if (!cancelled) setLoaded({ _tag: "error", message: "The view could not be staged." });
      },
    );
    return () => {
      cancelled = true;
      if (staged !== null) void bridge.release(staged);
    };
  }, [document_]);

  const live = { theme, size, props, request };
  const latest = useRef(live);
  useEffect(() => {
    latest.current = live;
  });
  const hostRef = useRef<McpAppHost | null>(null);

  useEffect(() => {
    if (frame === null || document_ === null) return;
    const targetOrigin = "origin" in frame ? frame.origin : "*";
    const contentWindow = () => frameRef.current?.contentWindow ?? null;
    const hostContext = (): McpAppHostContext => {
      const current = latest.current;
      return {
        theme: current.theme.appearance,
        styles: { variables: mcpAppStyleVariables(current.theme.variables) },
        displayMode: "inline",
        availableDisplayModes: ["inline"],
        containerDimensions: { width: current.size.width, height: current.size.height },
        platform: isElectron ? "desktop" : "web",
        locale: navigator.language,
        timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        userAgent: `t3-code/${APP_VERSION}`,
        deviceCapabilities: {
          touch: window.matchMedia("(pointer: coarse)").matches,
          hover: window.matchMedia("(hover: hover)").matches,
        },
        safeAreaInsets: { top: 0, right: 0, bottom: 0, left: 0 },
      };
    };
    const call = async (
      method: "tools/call" | "resources/read",
      params: Record<string, unknown>,
    ): Promise<unknown> => {
      const { target: current } = latest.current.props;
      const result = await latest.current.request({
        environmentId: current.environmentId,
        input: {
          threadId: current.threadId,
          pluginId: current.pluginId,
          server: current.server,
          method,
          params,
        },
      });
      if (result._tag !== "Success") throw refusal(result);
      return result.value.result;
    };
    const ask = async (message: string) => {
      const approved = await requestConfirmDialog(message);
      if (approved === undefined)
        throw new McpAppHostRefusal("T3 could not ask for approval here.");
      if (approved !== true) throw new McpAppHostRefusal("Declined by the user.");
    };
    const csp = readMcpAppCsp(document_.csp);
    const host = makeMcpAppHost({
      app: {
        attachmentId: "",
        server: target.server,
        tool: target.tool,
        resourceUri: target.resourceUri,
        ...(csp === undefined ? {} : { csp }),
      },
      hostVersion: APP_VERSION,
      // A staged view has a real origin to address; a srcdoc view's is opaque,
      // where the window reference alone scopes delivery.
      post: (message) => contentWindow()?.postMessage(message, targetOrigin),
      hostContext,
      // The user opened this view of a plugin they installed, so its calls to
      // its own server run without a prompt each.
      callTool: async (input) =>
        (await call("tools/call", {
          name: input.name,
          arguments: input.arguments,
        })) as McpAppCallToolResult,
      readResource: ({ uri }) => call("resources/read", { uri }),
      openLink: async (url) => {
        if (document.activeElement !== frameRef.current || !navigator.userActivation?.isActive) {
          throw new McpAppHostRefusal("Links open only from a click in the view.");
        }
        window.open(url, "_blank", "noopener,noreferrer");
      },
      sendMessage: async (text) => {
        const send = latest.current.props.onSendMessage;
        if (send === undefined) throw new McpAppHostRefusal("Messages need a thread.");
        await ask(`Send this message from ${latest.current.props.target.title}?\n${text}`);
        await send(text);
      },
      updateModelContext: async () => {
        throw new McpAppHostRefusal("Plugin views cannot add model context yet.");
      },
      requestDisplayMode: async () => "inline",
      downloadFile: async (files) => {
        await ask(`Save ${files.map((file) => file.name).join(", ")} from ${target.title}?`);
        for (const file of files) {
          let bytes: Uint8Array | undefined;
          let mimeType = file.mimeType ?? "application/octet-stream";
          if (file._tag === "embedded") {
            bytes = file.bytes;
          } else {
            const read = (await call("resources/read", { uri: file.uri })) as {
              readonly contents?: ReadonlyArray<unknown>;
            };
            const content = read?.contents?.[0];
            bytes = mcpResourceBytes(content);
            const declared = (content as { readonly mimeType?: unknown } | undefined)?.mimeType;
            if (typeof declared === "string") mimeType = declared;
          }
          if (bytes === undefined) throw new McpAppHostRefusal(`${file.name} has no contents.`);
          if (bytes.byteLength > MAX_DOWNLOAD_BYTES) {
            throw new McpAppHostRefusal(`${file.name} is too large to save.`);
          }
          saveBlob(new Blob([bytes.slice()], { type: mimeType }), file.name);
        }
      },
      onRequestTeardown: () => {
        void host.teardown().then(() => setClosed(true));
      },
      onSizeChanged: () => {
        // The view fills a fixed box; its preferred height does not apply.
      },
    });
    hostRef.current = host;
    host.setToolCall({
      arguments: document_.toolInput,
      result: document_.toolResult as McpAppCallToolResult | undefined,
    });
    const receive = (event: MessageEvent) => {
      if (event.source === null || event.source !== contentWindow()) return;
      if (targetOrigin !== "*" && event.origin !== targetOrigin) return;
      host.receive(event.data);
    };
    window.addEventListener("message", receive);
    return () => {
      window.removeEventListener("message", receive);
      void host.teardown();
      hostRef.current = null;
    };
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- A new document needs a new host; the rest is read through `latest`.
  }, [frame, document_]);

  useEffect(() => {
    hostRef.current?.updateHostContext();
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- Context changes trigger a resend.
  }, [theme, size]);

  const reload = () => {
    setClosed(false);
    setGeneration((value) => value + 1);
  };

  return (
    <div ref={boxRef} className="relative flex min-h-0 flex-1 flex-col">
      {closed ? (
        <Notice text={`${target.title} was closed`} action={reload} actionLabel="Reopen" />
      ) : loaded._tag === "error" ? (
        <Notice text={loaded.message} action={reload} actionLabel="Try again" />
      ) : frame === null ? (
        <div className="flex flex-1 items-center justify-center">
          <Spinner />
        </div>
      ) : (
        <>
          {"srcdoc" in frame ? (
            // Outside the desktop app a view has no origin of its own, so views that start
            // workers from blob: URLs (the CAD viewer does) fail to load their content.
            <p className="text-muted-foreground border-b px-3 py-1.5 text-xs">
              If this view stays blank, open it in the {APP_BASE_NAME} desktop app.
            </p>
          ) : null}
          <iframe
            ref={frameRef}
            title={target.title}
            className="block min-h-0 w-full flex-1 border-0"
            style={{ colorScheme: theme.appearance }}
            sandbox={"src" in frame ? PLUGIN_VIEW_STAGED_SANDBOX : PLUGIN_VIEW_SRCDOC_SANDBOX}
            {...("src" in frame ? { src: frame.src } : { srcDoc: frame.srcdoc })}
          />
        </>
      )}
    </div>
  );
}

function Notice(props: { text: string; actionLabel: string; action: () => void }) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
      <p className="text-muted-foreground text-xs">{props.text}</p>
      <Button size="xs" variant="outline" onClick={props.action}>
        {props.actionLabel}
      </Button>
    </div>
  );
}
