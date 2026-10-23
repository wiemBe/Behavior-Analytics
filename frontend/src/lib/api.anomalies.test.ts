import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("anomaly list query", () => {
  it("serializes multiple subject kinds as repeated keys", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], total: 0, limit: 25, offset: 0 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await api.anomalies({
      subject_kind: ["IPS_ENTITY", "IPS_SOURCE"],
      active_only: true,
      limit: 25,
    });

    const [url] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain(
      "/anomalies?subject_kind=IPS_ENTITY&subject_kind=IPS_SOURCE&active_only=true&limit=25",
    );
  });
});
