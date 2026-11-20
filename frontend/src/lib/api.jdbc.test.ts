import { afterEach, describe, expect, it, vi } from "vitest";

import { api, ApiError } from "@/lib/api";

const INSTANCE = "2f4a5b6c-7d8e-4f01-9a2b-3c4d5e6f7081";
const SOURCE = "c6d1e2f3-a4b5-4c6d-8e9f-0a1b2c3d4e5f";

function fetchMock() {
  const mock = vi.fn().mockImplementation(() =>
    Promise.resolve(
      new Response(JSON.stringify({ items: [], total: 0, limit: 100, offset: 0, snapshot: {} }), {
        status: 200,
      }),
    ),
  );
  vi.stubGlobal("fetch", mock);
  return mock;
}

function pathOf(call: unknown[]): string {
  const url = String(call[0]);
  return url.slice(url.indexOf("/log-sources"));
}

describe("api.jdbc", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("GETs the list at /log-sources/jdbc with a fixed query order", async () => {
    const fetchMockResult = fetchMock();
    await api.jdbcSources({
      offset: 40, limit: 25, q: "db", protocol: 8, enabled: false,
      flow_state: "RECEIVING", instance_id: INSTANCE,
    });
    expect(pathOf(fetchMockResult.mock.calls[0])).toBe(
      `/log-sources/jdbc?limit=25&offset=40&q=db&instance_id=${INSTANCE}` +
        "&protocol=8&enabled=false&flow_state=RECEIVING",
    );
    expect(fetchMockResult.mock.calls[0][1]?.method).toBeUndefined();
  });

  it("GETs one detail and POSTs the refresh", async () => {
    const fetchMockResult = fetchMock();
    await api.jdbcSource(SOURCE);
    expect(pathOf(fetchMockResult.mock.calls[0])).toBe(`/log-sources/${SOURCE}/jdbc`);

    await api.refreshJdbc(INSTANCE);
    expect(pathOf(fetchMockResult.mock.calls[1])).toBe(
      `/log-sources/jdbc/sync?instance_id=${INSTANCE}`,
    );
    expect(fetchMockResult.mock.calls[1][1]?.method).toBe("POST");

    await api.refreshJdbc();
    expect(pathOf(fetchMockResult.mock.calls[2])).toBe("/log-sources/jdbc/sync");
  });

  it("passes the caller's signal through and omits undefined params", async () => {
    const fetchMockResult = fetchMock();
    const controller = new AbortController();
    await api.jdbcSources({ limit: 10 }, controller.signal);
    expect(fetchMockResult.mock.calls[0][1]?.signal).toBe(controller.signal);
    await api.jdbcSource(SOURCE, controller.signal);
    expect(fetchMockResult.mock.calls[1][1]?.signal).toBe(controller.signal);

    await api.jdbcSources();
    expect(pathOf(fetchMockResult.mock.calls[2])).toBe("/log-sources/jdbc");
    await api.jdbcSources({ q: "db", limit: undefined, offset: undefined });
    expect(pathOf(fetchMockResult.mock.calls[3])).toBe("/log-sources/jdbc?q=db");
  });

  it("turns a non-2xx answer into the existing ApiError", async () => {
    vi.stubGlobal("fetch", vi.fn().mockImplementation(() =>
      Promise.resolve(new Response(JSON.stringify({ detail: "Source not found" }), { status: 404 })),
    ));
    const error = await api.jdbcSource(SOURCE).catch((reason: unknown) => reason);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: "Source not found" });
  });
});
