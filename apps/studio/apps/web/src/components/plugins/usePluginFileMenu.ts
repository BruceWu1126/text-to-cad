import { pluginFileHandlers } from "@t3tools/client-runtime/state/plugins";
import type { ContextMenuItem, EnvironmentId, ScopedThreadRef } from "@t3tools/contracts";
import { useMemo } from "react";

import { useRightPanelStore } from "~/rightPanelStore";
import { pluginEnvironment } from "~/state/plugins";
import { useEnvironmentQuery } from "~/state/query";
import { pluginAppSurface } from "./pluginSurfaces";

const ITEM_PREFIX = "plugin-view:";

/**
 * "Open with <plugin>" entries for a workspace file whose extension an
 * enabled plugin's view claims; activating one opens the view as a tab.
 */
export function usePluginFileMenu(environmentId: EnvironmentId, threadRef: ScopedThreadRef | null) {
  const list = useEnvironmentQuery(
    threadRef === null ? null : pluginEnvironment.list({ environmentId, input: {} }),
  );
  const plugins = list.data?.plugins;
  return useMemo(
    () => ({
      buildItems: (path: string): ContextMenuItem[] =>
        pluginFileHandlers(plugins ?? [], path).map((choice, index) => ({
          id: `${ITEM_PREFIX}${index}`,
          label: `Open with ${choice.pluginName}`,
          ...(index === 0 ? { separatorBefore: true } : {}),
        })),
      /** True when the clicked id was one of these entries. */
      activate: (clicked: string, path: string): boolean => {
        if (!clicked.startsWith(ITEM_PREFIX) || threadRef === null) return false;
        const choice = pluginFileHandlers(plugins ?? [], path)[
          Number(clicked.slice(ITEM_PREFIX.length))
        ];
        if (choice === undefined) return true;
        useRightPanelStore.getState().openPluginApp(threadRef, pluginAppSurface(choice, path));
        return true;
      },
    }),
    [plugins, threadRef],
  );
}
