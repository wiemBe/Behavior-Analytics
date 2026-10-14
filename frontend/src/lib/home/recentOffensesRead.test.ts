import { afterEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";

describe("homeReads.recentOffenses", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("is the open offenses, latest update first, five by default", () => {
    expect(homeReads.recentOffenses().key).toBe(
      "/offenses?status=OPEN&sort=last_updated_time&limit=5",
    );
  });

  it("hands its AbortSignal to the request, so it can be aborted on unmount", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [] }) });
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    await homeReads.recentOffenses().fetch(controller.signal);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0][1].signal).toBe(controller.signal);
  });
});
