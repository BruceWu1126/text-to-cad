import type { EnvironmentId, ThreadId } from "@t3tools/contracts";

import type { PluginAppSurface } from "~/rightPanelStore";
import { PluginViewFrame } from "./PluginViewFrame";

/** A plugin view as a right-panel tab, running against the thread's plugin servers. */
export default function PluginViewPanel(props: {
  readonly environmentId: EnvironmentId;
  readonly threadId: ThreadId;
  readonly surface: PluginAppSurface;
  readonly onSendMessage?: ((text: string) => Promise<void>) | undefined;
}) {
  const { surface } = props;
  return (
    <PluginViewFrame
      key={surface.id}
      target={{
        environmentId: props.environmentId,
        threadId: props.threadId,
        pluginId: surface.pluginId,
        server: surface.server,
        tool: surface.tool,
        title: surface.title,
        resourceUri: surface.resourceUri,
        arguments: surface.arguments,
        ...(surface.file === undefined ? {} : { file: surface.file }),
      }}
      onSendMessage={props.onSendMessage}
    />
  );
}
