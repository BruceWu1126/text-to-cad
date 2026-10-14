import { useAtomValue } from "@effect/atom-react";
import {
  pluginViewsWithEntrypoint,
  type PluginViewChoice,
} from "@t3tools/client-runtime/state/plugins";
import {
  isAtomCommandInterrupted,
  squashAtomCommandFailure,
  type AtomCommandResult,
} from "@t3tools/client-runtime/state/runtime";
import type {
  EnvironmentId,
  InstalledPlugin,
  PluginCatalogEntry,
  PluginCompatibility,
} from "@t3tools/contracts";
import { EllipsisIcon, SearchIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { requestConfirmDialog } from "~/confirmDialog";
import { pluginEnvironment } from "~/state/plugins";
import { useEnvironmentQuery } from "~/state/query";
import { useAtomCommand } from "~/state/use-atom-command";
import { useSettingsScope } from "../settings/SettingsScopeContext";
import { SettingsPageContainer, SettingsRow, SettingsSection } from "../settings/settingsLayout";
import { Alert, AlertDescription } from "../ui/alert";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";
import { Dialog, DialogHeader, DialogPopup, DialogTitle } from "../ui/dialog";
import { InputGroup, InputGroupAddon, InputGroupInput } from "../ui/input-group";
import { Menu, MenuItem, MenuPopup, MenuSeparator, MenuTrigger } from "../ui/menu";
import { RefreshIcon } from "../ui/refresh-icon";
import { Switch } from "../ui/switch";
import { PluginViewFrame } from "./PluginViewFrame";

/** Catalog rows rendered before "Show all"; search narrows the rest. */
const INITIAL_CATALOG_ROWS = 60;
const SEARCH_DEBOUNCE_MS = 250;
const CATALOG_POLL_MS = 2_000;

const COMPATIBILITY_BADGE = {
  works: "success",
  signin: "info",
  partly: "warning",
  codex: "outline",
  unknown: "outline",
  unavailable: "error",
} as const;

function CompatibilityBadge(props: { compatibility: PluginCompatibility }) {
  return (
    <Badge
      size="sm"
      variant={COMPATIBILITY_BADGE[props.compatibility.level]}
      title={props.compatibility.notes.join("\n") || undefined}
    >
      {props.compatibility.label}
    </Badge>
  );
}

const failureMessage = (result: Parameters<typeof squashAtomCommandFailure>[0]) => {
  const error = squashAtomCommandFailure(result);
  return error instanceof Error && error.message.trim() !== "" ? error.message : "Request failed.";
};

/** Settings › Plugins: what this environment has installed, and every catalog to install from. */
export function PluginsSettingsPanel() {
  const { environment } = useSettingsScope();
  const environmentId =
    environment?.connection.phase === "connected" ? environment.environmentId : null;
  return (
    <SettingsPageContainer>
      {environmentId === null ? (
        <SettingsSection title="Plugins">
          <SettingsRow
            title="No environment connected"
            description="Plugins install on an environment. Connect one to manage its plugins."
          />
        </SettingsSection>
      ) : (
        <PluginsSettings key={environmentId} environmentId={environmentId} />
      )}
    </SettingsPageContainer>
  );
}

function PluginsSettings({ environmentId }: { environmentId: EnvironmentId }) {
  const canManage = useAtomValue(pluginEnvironment.install.permissionAtom(environmentId));
  const list = useEnvironmentQuery(pluginEnvironment.list({ environmentId, input: {} }));
  const [query, setQuery] = useState("");
  const [submittedQuery, setSubmittedQuery] = useState("");
  const searchTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (searchTimer.current !== null) clearTimeout(searchTimer.current);
    },
    [],
  );
  const catalog = useEnvironmentQuery(
    pluginEnvironment.catalog({ environmentId, input: { query: submittedQuery } }),
  );
  const [showAll, setShowAll] = useState(false);
  // Keyed by plugin id or catalog key: one action at a time per row.
  const [pending, setPending] = useState<Record<string, string>>({});
  const [actionError, setActionError] = useState<string | null>(null);
  const [openView, setOpenView] = useState<PluginViewChoice | null>(null);

  const install = useAtomCommand(pluginEnvironment.install, { reportFailure: false });
  const setEnabled = useAtomCommand(pluginEnvironment.setEnabled, { reportFailure: false });
  const uninstall = useAtomCommand(pluginEnvironment.uninstall, { reportFailure: false });
  const update = useAtomCommand(pluginEnvironment.update, { reportFailure: false });
  const refreshCatalog = useAtomCommand(pluginEnvironment.refreshCatalog, {
    reportFailure: false,
  });

  const run = async (
    key: string,
    label: string,
    action: () => Promise<AtomCommandResult<unknown, unknown>>,
  ) => {
    setActionError(null);
    setPending((current) => ({ ...current, [key]: label }));
    const result = await action();
    setPending(({ [key]: _done, ...rest }) => rest);
    if (result._tag === "Success") return;
    if (result._tag === "Failure" && !isAtomCommandInterrupted(result)) {
      setActionError(failureMessage(result));
    }
  };

  const plugins = list.data?.plugins ?? [];
  const entries = catalog.data?.entries ?? null;
  const catalogs = catalog.data?.catalogs ?? [];
  const totalPlugins = catalogs.reduce((sum, entry) => sum + entry.pluginCount, 0);
  const failedCatalogs = catalogs.filter((entry) => entry.state === "error");
  const fetchingCatalogs = catalogs.some((entry) => entry.state === "fetching");
  const refreshing = pending["catalog:refresh"] !== undefined;
  // The server never waits on the network for a catalog read; while a catalog is
  // still downloading, read again until it lands.
  const refreshCatalogQuery = catalog.refresh;
  useEffect(() => {
    if (!fetchingCatalogs) return;
    const timer = setTimeout(() => refreshCatalogQuery(), CATALOG_POLL_MS);
    return () => clearTimeout(timer);
    // oxlint-disable-next-line react/exhaustive-effect-dependencies -- Each read schedules the next while fetching.
  }, [fetchingCatalogs, catalog.data, refreshCatalogQuery]);
  const visibleEntries =
    entries === null || showAll ? entries : entries.slice(0, INITIAL_CATALOG_ROWS);
  const globalViews = useMemo(() => pluginViewsWithEntrypoint(plugins, "global"), [plugins]);

  const installedRow = (plugin: InstalledPlugin) => {
    const busy = pending[plugin.id];
    const serverErrors = plugin.servers.filter((server) => server.state === "error");
    const views = globalViews.filter((choice) => choice.pluginId === plugin.id);
    return (
      <SettingsRow
        key={plugin.id}
        title={
          <span className="flex items-center gap-2">
            {plugin.displayName}
            {plugin.version ? (
              <span className="font-normal text-muted-foreground text-xs">{plugin.version}</span>
            ) : null}
          </span>
        }
        description={plugin.description || undefined}
        status={
          <span className="flex flex-col gap-1">
            <span className="flex items-center gap-2">
              <CompatibilityBadge compatibility={plugin.compatibility} />
              {busy ? <span>{busy}…</span> : null}
            </span>
            {plugin.error ? (
              <span className="text-destructive-foreground">{plugin.error}</span>
            ) : null}
            {serverErrors.map((server) => (
              <span className="text-destructive-foreground" key={server.name}>
                {server.name}: {server.error ?? "failed to start"}
              </span>
            ))}
          </span>
        }
        control={
          <>
            {plugin.updateAvailable ? (
              <Button
                size="xs"
                variant="outline"
                disabled={!canManage || busy !== undefined}
                onClick={() =>
                  void run(plugin.id, "Updating", () =>
                    update({ environmentId, input: { pluginId: plugin.id } }),
                  )
                }
              >
                Update
              </Button>
            ) : null}
            <Switch
              aria-label={`${plugin.enabled ? "Disable" : "Enable"} ${plugin.displayName}`}
              checked={plugin.enabled}
              disabled={!canManage || busy !== undefined}
              onCheckedChange={(enabled) =>
                void run(plugin.id, enabled ? "Enabling" : "Disabling", () =>
                  setEnabled({ environmentId, input: { pluginId: plugin.id, enabled } }),
                )
              }
            />
            <Menu>
              <MenuTrigger
                render={
                  <Button
                    aria-label={`More actions for ${plugin.displayName}`}
                    size="icon-xs"
                    variant="ghost-muted"
                  />
                }
              >
                <EllipsisIcon />
              </MenuTrigger>
              <MenuPopup align="end">
                {views.map((choice) => (
                  <MenuItem
                    key={`${choice.view.server}/${choice.view.tool}`}
                    onClick={() => setOpenView(choice)}
                  >
                    Open {choice.view.title}
                  </MenuItem>
                ))}
                {views.length > 0 ? <MenuSeparator /> : null}
                <MenuItem
                  variant="destructive"
                  disabled={!canManage || busy !== undefined}
                  onClick={async () => {
                    const confirmed = await requestConfirmDialog(
                      `Uninstall ${plugin.displayName}? Its servers stop and its files are removed from this environment.`,
                    );
                    if (confirmed !== true) return;
                    await run(plugin.id, "Uninstalling", () =>
                      uninstall({ environmentId, input: { pluginId: plugin.id } }),
                    );
                  }}
                >
                  Uninstall
                </MenuItem>
              </MenuPopup>
            </Menu>
          </>
        }
      />
    );
  };

  const catalogRow = (entry: PluginCatalogEntry) => {
    const busy = pending[entry.key];
    const marketplace = entry.marketplaces[0]?.name;
    const byline = [entry.publisher ? `by ${entry.publisher}` : null, marketplace]
      .filter(Boolean)
      .join(" · ");
    return (
      <SettingsRow
        key={entry.key}
        title={
          <span className="flex items-center gap-2">
            {entry.displayName}
            <CompatibilityBadge compatibility={entry.compatibility} />
          </span>
        }
        description={entry.description || undefined}
        status={byline || undefined}
        control={
          entry.installedId !== null ? (
            <Button size="xs" variant="ghost-muted" disabled>
              Installed
            </Button>
          ) : (
            <Button
              size="xs"
              variant={busy ? "secondary" : "outline"}
              disabled={
                !canManage || busy !== undefined || entry.compatibility.level === "unavailable"
              }
              onClick={() =>
                void run(entry.key, "Installing", () =>
                  install({ environmentId, input: { key: entry.key } }),
                )
              }
            >
              {busy ? "Installing…" : "Install"}
            </Button>
          )
        }
      />
    );
  };

  return (
    <>
      {actionError ? (
        <Alert variant="error" role="status" aria-live="polite">
          <AlertDescription>{actionError}</AlertDescription>
        </Alert>
      ) : null}
      {!canManage ? (
        <Alert variant="info">
          <AlertDescription>
            This connection can browse plugins but lacks permission to install or change them.
          </AlertDescription>
        </Alert>
      ) : null}

      <SettingsSection id="installed-plugins" title="Installed">
        {list.error ? (
          <SettingsRow title="Could not load installed plugins" description={list.error} />
        ) : list.data === null ? (
          <SettingsRow title="Loading installed plugins…" />
        ) : plugins.length === 0 ? (
          <SettingsRow
            title="No plugins installed"
            description="Install one below. Its tools and skills reach every provider in this environment."
          />
        ) : (
          plugins.map(installedRow)
        )}
      </SettingsSection>

      <SettingsSection
        id="plugin-catalog"
        title="Browse"
        headerAction={
          <Button
            size="xs"
            variant="ghost-muted"
            disabled={refreshing}
            onClick={() =>
              void run("catalog:refresh", "Refreshing", () =>
                refreshCatalog({ environmentId, input: { query: submittedQuery, refresh: true } }),
              )
            }
          >
            <RefreshIcon size="sm" refreshing={refreshing || fetchingCatalogs} />
            Refresh
          </Button>
        }
      >
        <div className="px-3 py-3 sm:px-4">
          <InputGroup>
            <InputGroupAddon>
              <SearchIcon />
            </InputGroupAddon>
            <InputGroupInput
              aria-label="Search plugins"
              placeholder={totalPlugins > 0 ? `Search ${totalPlugins} plugins` : "Search plugins"}
              size="sm"
              type="search"
              value={query}
              onChange={(event) => {
                const next = event.currentTarget.value;
                setQuery(next);
                if (searchTimer.current !== null) clearTimeout(searchTimer.current);
                searchTimer.current = setTimeout(() => {
                  searchTimer.current = null;
                  setShowAll(false);
                  setSubmittedQuery(next.trim().slice(0, 120));
                }, SEARCH_DEBOUNCE_MS);
              }}
            />
          </InputGroup>
        </div>
        {failedCatalogs.map((entry) => (
          <SettingsRow
            key={entry.id}
            title={`${entry.name} is unavailable`}
            description={entry.error ?? entry.source}
          />
        ))}
        {catalog.error ? (
          <SettingsRow title="Could not load the catalog" description={catalog.error} />
        ) : visibleEntries === null ? (
          <SettingsRow title="Loading plugins…" />
        ) : visibleEntries.length === 0 && fetchingCatalogs ? (
          <SettingsRow title="Fetching plugin catalogs…" />
        ) : visibleEntries.length === 0 ? (
          <SettingsRow
            title={submittedQuery ? `No plugins match "${submittedQuery}"` : "No plugins found"}
            description={submittedQuery ? "Try a broader search." : undefined}
          />
        ) : (
          visibleEntries.map(catalogRow)
        )}
        {entries !== null && !showAll && entries.length > INITIAL_CATALOG_ROWS ? (
          <div className="px-3 py-2 sm:px-4">
            <Button size="xs" variant="ghost-muted" onClick={() => setShowAll(true)}>
              Show all {catalog.data?.matchCount ?? entries.length}
            </Button>
          </div>
        ) : null}
      </SettingsSection>

      <Dialog open={openView !== null} onOpenChange={(open) => !open && setOpenView(null)}>
        {openView ? (
          <DialogPopup className="h-[80vh] max-w-5xl">
            <DialogHeader>
              <DialogTitle>{openView.view.title}</DialogTitle>
            </DialogHeader>
            <div className="flex min-h-0 flex-1 flex-col">
              <PluginViewFrame
                target={{
                  environmentId,
                  threadId: null,
                  pluginId: openView.pluginId,
                  server: openView.view.server,
                  tool: openView.view.tool,
                  title: openView.view.title,
                  resourceUri: openView.view.resourceUri,
                  arguments: {},
                }}
              />
            </div>
          </DialogPopup>
        ) : null}
      </Dialog>
    </>
  );
}
