import { createFileRoute } from "@tanstack/react-router";

import { PluginsSettingsPanel } from "../components/plugins/PluginsSettings";

export const Route = createFileRoute("/settings/plugins")({
  component: PluginsSettingsPanel,
});
