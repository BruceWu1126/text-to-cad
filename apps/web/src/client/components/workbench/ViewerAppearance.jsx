import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@text-to-cad/ui/primitives/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger
} from "@text-to-cad/ui/primitives/dropdown-menu";
import { TooltipHint } from "@text-to-cad/ui/primitives/tooltip";
import { COLOR_SCHEMES } from "../../ui/colorScheme.js";

const ICONS = { system: Monitor, light: Sun, dark: Moon };

/**
 * The web viewer's light/dark control, the page's own (a host that inherits its app's appearance
 * has none): an icon button in the navbar's top-right corner, last, as its other controls look,
 * and a dropdown of System, Light and Dark. Its icon is always the sun or the moon of the look on
 * screen — under System, the one the system resolves to — never the System monitor.
 * @param {{ colorSchemePreference?: string, resolvedColorSchemeMode?: 'light' | 'dark', onColorSchemePreferenceChange?: (value: string) => void }} props
 */
export default function ViewerAppearance({ colorSchemePreference = "system", resolvedColorSchemeMode = "light", onColorSchemePreferenceChange = undefined }) {
  const Icon = resolvedColorSchemeMode === "dark" ? Moon : Sun;
  const label = COLOR_SCHEMES.find(option => option.id === colorSchemePreference)?.label || "System";
  return <DropdownMenu modal={false}>
    <TooltipHint content="Appearance">
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="ghost" size="icon-xs" aria-label={`Appearance: ${label}`} data-viewer-appearance=""
          className="size-6 text-muted-foreground hover:text-foreground aria-expanded:bg-accent aria-expanded:text-accent-foreground">
          <Icon className="size-3.5" aria-hidden="true" />
        </Button>
      </DropdownMenuTrigger>
    </TooltipHint>
    <DropdownMenuContent align="end" sideOffset={6} aria-label="Appearance" className="min-w-32">
      <DropdownMenuRadioGroup value={colorSchemePreference} onValueChange={onColorSchemePreferenceChange}>
        {COLOR_SCHEMES.map(option => {
          const OptionIcon = ICONS[option.id];
          return <DropdownMenuRadioItem key={option.id} value={option.id}>
            <OptionIcon className="size-3.5" aria-hidden="true" />{option.label}
          </DropdownMenuRadioItem>;
        })}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
}
