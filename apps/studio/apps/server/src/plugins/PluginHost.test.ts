import { describe, expect, it } from "@effect/vitest";

import { liveOrRestart } from "./PluginHost.ts";

interface Entry {
  readonly id: number;
  live: boolean;
}

describe("liveOrRestart", () => {
  it("restarts a stale entry once when callers race on it", async () => {
    const entries = new Map<string, Promise<Entry>>();
    let started = 0;
    const closed: Array<number> = [];
    const restart = () =>
      liveOrRestart(
        entries,
        "key",
        (entry) => entry.live,
        async (stale) => void closed.push(stale.id),
        async () => ({ id: ++started, live: true }),
      );

    const first = await restart();
    first.live = false;
    const [a, b, c] = await Promise.all([restart(), restart(), restart()]);

    expect(started).toBe(2);
    expect([a.id, b.id, c.id]).toEqual([2, 2, 2]);
    expect(closed).toEqual([1]);
    expect(await entries.get("key")).toBe(a);
  });

  it("starts again after a failed start and forgets the failure", async () => {
    const entries = new Map<string, Promise<Entry>>();
    let attempts = 0;
    const start = () =>
      liveOrRestart(
        entries,
        "key",
        (entry) => entry.live,
        async () => undefined,
        async () => {
          attempts += 1;
          if (attempts === 1) throw new Error("did not start");
          return { id: attempts, live: true };
        },
      );

    await expect(start()).rejects.toThrow("did not start");
    expect(entries.has("key")).toBe(false);
    expect((await start()).id).toBe(2);
  });
});
