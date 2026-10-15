import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api";
import {
  createDashboard,
  deleteDashboard,
  describeRefusal,
  duplicateDashboard,
  fetchMe,
  getDashboard,
  listDashboards,
  patchDashboard,
  readLayout,
  replaceDashboard,
  setDefaultDashboard,
} from "./dashboardApi";

afterEach(() => vi.unstubAllGlobals());

function stub(status: number, body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function lastCall(fetchMock: ReturnType<typeof stub>) {
  const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
  return {
    path: url.replace(/^.*\/api\/v1/, ""),
    method: init.method ?? "GET",
    body: init.body ? JSON.parse(init.body as string) : undefined,
  };
}

const layout = { v: 1 as const, range: "24h" as const, widgets: [] };
const ID = "6f1c2b9e-3d4a-4f6e-9a7b-1c2d3e4f5a6b";

describe("the dashboards client: method, path and body", () => {
  it("lists with the page size that holds a whole list", async () => {
    const fetchMock = stub(200, { items: [], total: 0, limit: 200, offset: 0 });
    await listDashboards();
    expect(lastCall(fetchMock)).toEqual({
      path: "/dashboards?limit=200",
      method: "GET",
      body: undefined,
    });
  });

  it("reads one dashboard by id", async () => {
    const fetchMock = stub(200, { id: ID });
    await getDashboard(ID);
    expect(lastCall(fetchMock)).toMatchObject({ path: `/dashboards/${ID}`, method: "GET" });
  });

  it("creates with the template key and version", async () => {
    const fetchMock = stub(201, { id: ID });
    await createDashboard({ name: "NOC", layout, template_key: "soc_overview", template_version: 1 });
    expect(lastCall(fetchMock)).toEqual({
      path: "/dashboards",
      method: "POST",
      body: { name: "NOC", layout, template_key: "soc_overview", template_version: 1 },
    });
  });

  it("patches only the fields it is given", async () => {
    const fetchMock = stub(200, { id: ID });
    await patchDashboard(ID, { visibility: "SHARED" });
    expect(lastCall(fetchMock)).toEqual({
      path: `/dashboards/${ID}`,
      method: "PATCH",
      body: { visibility: "SHARED" },
    });
  });

  it("duplicates with a POST and no body", async () => {
    const fetchMock = stub(201, { id: ID });
    await duplicateDashboard(ID);
    expect(lastCall(fetchMock)).toEqual({
      path: `/dashboards/${ID}/duplicate`,
      method: "POST",
      body: undefined,
    });
  });

  it("sets the default, and clears it with null", async () => {
    let fetchMock = stub(200, { dashboard_id: ID });
    await setDefaultDashboard(ID);
    expect(lastCall(fetchMock)).toEqual({
      path: "/dashboards/default",
      method: "PUT",
      body: { dashboard_id: ID },
    });
    fetchMock = stub(200, { dashboard_id: null });
    await setDefaultDashboard(null);
    expect(lastCall(fetchMock).body).toEqual({ dashboard_id: null });
  });

  it("reads GET /me", async () => {
    const fetchMock = stub(200, { subject: "a", email: null, permissions: [], effective_permissions: [] });
    await fetchMe();
    expect(lastCall(fetchMock)).toMatchObject({ path: "/me", method: "GET" });
  });
});

describe("the 204 branch of the shared request", () => {
  it("resolves a DELETE's empty 204 to undefined instead of failing on the body", async () => {
    const fetchMock = stub(204, null);
    await expect(deleteDashboard(ID)).resolves.toBeUndefined();
    expect(lastCall(fetchMock)).toMatchObject({ path: `/dashboards/${ID}`, method: "DELETE" });
  });

  it("still parses the body of a 200", async () => {
    stub(200, { items: [], total: 0, limit: 200, offset: 0 });
    await expect(listDashboards()).resolves.toMatchObject({ total: 0 });
  });
});

describe("replaceDashboard", () => {
  const body = { name: "NOC", layout, expected_revision: 3 };

  it("sends a PUT with the revision it was based on and returns the saved dashboard", async () => {
    const fetchMock = stub(200, { id: ID, revision: 4 });
    const result = await replaceDashboard(ID, body);
    expect(lastCall(fetchMock)).toEqual({ path: `/dashboards/${ID}`, method: "PUT", body });
    expect(result).toEqual({ kind: "saved", dashboard: { id: ID, revision: 4 } });
  });

  it("returns a stale write as a conflict read from the wrapped 409 body", async () => {
    stub(409, { detail: { message: "this dashboard changed since you opened it", current_revision: 7 } });
    await expect(replaceDashboard(ID, body)).resolves.toEqual({
      kind: "conflict",
      message: "this dashboard changed since you opened it",
      current_revision: 7,
    });
  });

  it("throws a 409 whose detail is a string: a duplicate name is not a conflict", async () => {
    stub(409, { detail: "you already have a dashboard named 'NOC'" });
    await expect(replaceDashboard(ID, body)).rejects.toMatchObject({
      status: 409,
      message: "you already have a dashboard named 'NOC'",
    });
  });

  it("throws any other error with the server's detail kept", async () => {
    stub(404, { detail: "dashboard not found" });
    const error = await replaceDashboard(ID, body).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 404, message: "dashboard not found" });
  });

  it("throws a 409 object without a numeric revision rather than inventing one", async () => {
    stub(409, { detail: { message: "odd" } });
    await expect(replaceDashboard(ID, body)).rejects.toMatchObject({ status: 409 });
  });
});

describe("errors keep the server's text", () => {
  it("shows the limit and the duplicate-name sentences as sent", async () => {
    stub(409, { detail: "the limit is 20 dashboards per owner" });
    const error = await createDashboard({ name: "x", layout }).catch((e: unknown) => e);
    expect(describeRefusal(error)).toBe("The limit is 20 dashboards per owner.");
  });

  it("capitalises a lowercase detail and adds one full stop", () => {
    expect(describeRefusal(new ApiError(409, "x", "you already have a dashboard named 'A'"))).toBe(
      "You already have a dashboard named 'A'.",
    );
  });

  it("keeps a detail's existing final full stop", () => {
    expect(describeRefusal(new ApiError(403, "x", "Only the owner can change this."))).toBe(
      "Only the owner can change this.",
    );
  });

  it("flattens a pydantic 422 detail list and removes its raw Value error prefix", async () => {
    stub(422, {
      detail: [{ loc: ["body", "name"], msg: "Value error, name should have at least 1 character" }],
    });
    const error = await patchDashboard(ID, { name: "" }).catch((e: unknown) => e);
    expect(describeRefusal(error)).toBe("Name should have at least 1 character.");
  });

  it("uses a complete sentence for a non-ApiError", () => {
    expect(describeRefusal(new TypeError("fetch failed"))).toBe(
      "The request did not reach the server. Check that the backend is reachable and try again.",
    );
  });

  it("falls back to the HTTP status when there is no server detail", () => {
    expect(describeRefusal(new ApiError(500, "Internal Server Error"))).toBe(
      "The server answered HTTP 500.",
    );
  });
});

describe("readLayout", () => {
  it("reads a version 1 layout and keeps a widget type it does not know", () => {
    const read = readLayout({
      v: 1,
      range: "7d",
      widgets: [{ id: "w_abcd", type: "future.widget", x: 0, y: 0, w: 4, h: 6, range: null, config: {} }],
    });
    expect(read).toMatchObject({ ok: true, layout: { range: "7d" } });
    expect(read.ok && read.layout.widgets[0].type).toBe("future.widget");
  });

  it("does not guess at a version it does not know, and names it", () => {
    const read = readLayout({ v: 2, range: "24h", widgets: [] });
    expect(read).toMatchObject({ ok: false, version: 2 });
    expect(!read.ok && read.reason).toMatch(/version 2/);
  });

  it("refuses a layout that is not shaped like one", () => {
    expect(readLayout(null).ok).toBe(false);
    expect(readLayout({ v: 1, range: "24h" }).ok).toBe(false);
    expect(readLayout({ v: 1, range: "1y", widgets: [] }).ok).toBe(false);
    expect(readLayout({ v: 1, range: "24h", widgets: [{ id: "w_abcd" }] }).ok).toBe(false);
  });
});
