import { ArrowLeft, Check, MessageCircle, Monitor, Moon, Sun } from "lucide-react";
import { Fragment } from "react";

import { DropdownMenu as MenuPrimitive } from "radix-ui";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "@text-to-cad/ui/primitives/dropdown-menu";
import { cn } from "@text-to-cad/ui/utils";

import { FLOATING_SURFACE_CLASS } from "../../lib/floatingSurface.js";

import { DiscordMark, GitHubMark } from "./brandMarks.jsx";
import { feedbackUrl, MadeBy, useFollow } from "./NavbarLinks.jsx";

const NONE = Object.freeze([]);
// A setting's row: 32px, its name and its control spaced apart, the control's centre on one line.
const SETTING_ROW_CLASS = "flex min-h-8 items-center justify-between gap-2 py-0.5 pr-1 pl-2 text-tiny text-foreground";
const COLOR_SCHEMES = Object.freeze([["system", "System", Monitor], ["light", "Light", Sun], ["dark", "Dark", Moon]]);

/**
 * The app's menu, from the navbar's logo over every file: what is the person's rather than a
 * file's. First, where the host has a home, Back to files (`onHome`). Then the colour scheme (`colorScheme`):
 * Theme, one row, its System, Light and Dark a segmented control, the one in force lit, a press keeping the
 * menu open. Then the host's on/off settings (`appSettings`:
 * sharing anonymous usage data), checked at the right, which a press turns without closing
 * the menu; then Send feedback (a new issue on the host's tracker, naming the version and the
 * `platform`), GitHub and Discord; and last, in gray, the version this host runs (a link to its
 * release notes) and who made it, on one line. The menu is 15rem wide and never wider: a long
 * setting's name wraps, and a custom build's long version truncates, whole in its hint. Every link
 * opens the host's way (`links.open`).
 *
 * @param {{ trigger: import("react").ReactElement, onHome?: () => void,
 *   links?: import("../../host/types.js").ViewerLinks, appSettings?: readonly import("../types.js").AppSetting[],
 *   colorScheme?: import("../types.js").ColorSchemeSetting, platform?: string }} props
 */
export function AppMenu({ trigger, onHome, links, appSettings = NONE, colorScheme, platform }) {
  const follow = useFollow(links);
  const feedback = links ? feedbackUrl(links, platform) : "";
  const outward = [
    feedback ? { id: "feedback", label: "Send feedback", href: feedback, icon: MessageCircle } : null,
    links?.github ? { id: "github", label: "GitHub", href: links.github, icon: GitHubMark } : null,
    links?.discord ? { id: "discord", label: "Discord", href: links.discord, icon: DiscordMark } : null,
  ].filter(Boolean);
  const groups = [
    onHome ? [<DropdownMenuItem key="home" onSelect={onHome} data-menu-home=""><ArrowLeft aria-hidden="true" />Back to files</DropdownMenuItem>] : [],
    // The person's settings, one group of like rows: a name on the left in the menu's own text, and
    // its control on the right — Theme's System, Light and Dark as a segmented control, the one in
    // force lit; an on/off setting's checkbox, always drawn, filled while on. A press keeps the menu open.
    [
      ...(colorScheme ? [<div key="theme" className={SETTING_ROW_CLASS} data-menu-theme="">
        <span className="min-w-0">Theme</span>
        <MenuPrimitive.RadioGroup value={colorScheme.preference} onValueChange={colorScheme.onPreferenceChange} aria-label="Theme"
          className="-mr-0.5 flex shrink-0 items-center gap-0.5 rounded-md bg-muted p-0.5">
          {COLOR_SCHEMES.map(([id, label, Icon]) => <MenuPrimitive.RadioItem key={id} value={id} aria-label={label} onSelect={event => event.preventDefault()}
            className="grid size-6 cursor-default place-items-center rounded-sm text-muted-foreground outline-none transition-colors hover:text-foreground focus:bg-accent focus:text-foreground data-[state=checked]:bg-background data-[state=checked]:text-foreground data-[state=checked]:shadow-xs"
            data-theme-option={id}>
            <Icon className="size-3.5" aria-hidden="true" />
          </MenuPrimitive.RadioItem>)}
        </MenuPrimitive.RadioGroup>
      </div>] : []),
      ...appSettings.map(setting => <MenuPrimitive.CheckboxItem key={setting.id} checked={setting.checked} disabled={setting.disabled}
        onCheckedChange={checked => setting.onCheckedChange(checked === true)} onSelect={event => event.preventDefault()}
        className={cn(SETTING_ROW_CLASS, "group/setting cursor-default rounded-sm outline-none focus:bg-accent data-[disabled]:pointer-events-none data-[disabled]:opacity-50")}
        data-slot="dropdown-menu-checkbox-item" data-app-setting={setting.id}>
        <span className="min-w-0">{setting.label}</span>
        <span className="grid size-6 shrink-0 place-items-center" aria-hidden="true">
          <span className="grid size-3.5 place-items-center rounded-[4px] border border-input text-primary-foreground group-data-[state=checked]/setting:border-primary group-data-[state=checked]/setting:bg-primary">
            <Check className="hidden size-3 group-data-[state=checked]/setting:block" strokeWidth={3} />
          </span>
        </span>
      </MenuPrimitive.CheckboxItem>),
    ],
    outward.map(({ id, label, href, icon: Icon }) => <DropdownMenuItem key={id} asChild>
      <a href={href} target="_blank" rel="noreferrer" onClick={follow} data-link={id}><Icon className="size-3.5" aria-hidden="true" />{label}</a>
    </DropdownMenuItem>),
  ].filter(group => group.length);
  // Only the version shortens to fit the line, never the rest of it.
  const version = links?.version ? (links.release
    ? <a href={links.release} target="_blank" rel="noreferrer" onClick={follow} className="min-w-0 truncate hover:text-foreground"
      title={`v${links.version}`} data-menu-version="">v{links.version}</a>
    : <span className="min-w-0 truncate" title={`v${links.version}`} data-menu-version="">v{links.version}</span>) : null;
  const madeBy = links?.x ? <MadeBy links={links} /> : null;
  return <DropdownMenu modal={false}>
    <DropdownMenuTrigger asChild>{trigger}</DropdownMenuTrigger>
    <DropdownMenuContent align="start" sideOffset={6} collisionPadding={8} aria-label="Menu" data-app-menu=""
      className={cn(FLOATING_SURFACE_CLASS, "w-60 max-w-(--radix-dropdown-menu-content-available-width) data-[state=closed]:animate-none!")}>
      {groups.map((group, index) => <Fragment key={index}>{index ? <DropdownMenuSeparator /> : null}{group}</Fragment>)}
      {version || madeBy ? <>
        {groups.length ? <DropdownMenuSeparator /> : null}
        <div className="flex min-w-0 items-center gap-x-1 whitespace-nowrap px-2 py-1.5 text-tiny text-muted-foreground tabular-nums" data-menu-footer="">
          {version}{version && madeBy ? <span className="shrink-0" aria-hidden="true">·</span> : null}{madeBy}
        </div>
      </> : null}
    </DropdownMenuContent>
  </DropdownMenu>;
}
