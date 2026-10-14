import { pluginViewsWithEntrypoint } from "@t3tools/client-runtime/state/plugins";
import type { EnvironmentId, ScopedThreadRef } from "@t3tools/contracts";
import { PuzzleIcon } from "lucide-react";
import { useMemo } from "react";

import { useRightPanelStore } from "~/rightPanelStore";
import { pluginEnvironment } from "~/state/plugins";
import { useEnvironmentQuery } from "~/state/query";
import { MenuItem, MenuSeparator } from "../ui/menu";
import { pluginAppSurface } from "./pluginSurfaces";

/** Rows in the right panel's "+" menu for every enabled plugin view that opens as a thread tab. */
export function PluginThreadViewMenuItems(props: {
  readonly environmentId: EnvironmentId;
  readonly threadRef: ScopedThreadRef;
}) {
  const list = useEnvironmentQuery(
    pluginEnvironment.list({ environmentId: props.environmentId, input: {} }),
  );
  const views = useMemo(
    () => pluginViewsWithEntrypoint(list.data?.plugins ?? [], "thread"),
    [list.data],
  );
  if (views.length === 0) return null;
  return (
    <>
      <MenuSeparator />
      {views.map((choice) => (
        <MenuItem
          key={`${choice.pluginId}/${choice.view.server}/${choice.view.tool}`}
          onClick={() =>
            useRightPanelStore.getState().openPluginApp(props.threadRef, pluginAppSurface(choice))
          }
        >
          <PuzzleIcon />
          {choice.view.title}
        </MenuItem>
      ))}
    </>
  );
}
