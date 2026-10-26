import { english } from "../i18n/index.js";

/** A library row's two times, in seconds: when a page last opened it, and when it last changed. */
export interface Dated {
  opened?: number | null;
  /** A file's time on disk, a model's run. */
  updated?: number | null;
}

/**
 * A row's last activity, in seconds: the later of when it was opened and when it was updated, the
 * order the Home and the name menu's Recent list it in, and the time they show; null with neither.
 */
export function lastActivity(item: Dated): number | null {
  const times = [item.opened, item.updated].filter((time): time is number => typeof time === "number" && Number.isFinite(time));
  return times.length ? Math.max(...times) : null;
}

const MONTHS = { month: "short", day: "numeric" } as const;

/**
 * "2h ago": how long ago `seconds` was, with no verb, in the largest unit that is at least one --
 * "now", then minutes, hours and days -- and from a week on the short date, as `locale` writes it,
 * in the language of `t` (the view's; English by default). Empty with no time.
 */
export function ageLabel(seconds: number | null | undefined, now = Date.now(),
  t: (key: string, params?: Record<string, unknown>) => string = english, locale?: string): string {
  if (typeof seconds !== "number" || !Number.isFinite(seconds)) return "";
  const elapsed = Math.max(0, (now - seconds * 1000) / 1000);
  if (elapsed < 60) return t("home.ageNow");
  if (elapsed < 3600) return t("home.ageMinutes", { n: Math.floor(elapsed / 60) });
  if (elapsed < 86400) return t("home.ageHours", { n: Math.floor(elapsed / 3600) });
  if (elapsed < 7 * 86400) return t("home.ageDays", { n: Math.floor(elapsed / 86400) });
  const date = new Date(seconds * 1000);
  const sameYear = date.getFullYear() === new Date(now).getFullYear();
  return date.toLocaleDateString(locale, sameYear ? MONTHS : { ...MONTHS, year: "numeric" });
}
