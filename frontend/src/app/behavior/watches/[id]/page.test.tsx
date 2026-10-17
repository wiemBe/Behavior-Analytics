import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PropertyWatchPage from "./page";
import {
  bucket, catalog, incident, iso, paged, point, scope, series, value, watch, WATCH_ID,
} from "@/components/propertywatch/fixtures";
import type { WatchWorkspaceProps } from "@/components/propertywatch/WatchWorkspace";
import { ApiError, api } from "@/lib/api";

// The workspace has its own test; here it reports what the server handed it.
const handed = vi.hoisted(() => ({ props: null as unknown }));
vi.mock("@/components/propertywatch/WatchWorkspace", () => ({
  WatchWorkspace: (props: WatchWorkspaceProps) => {
    handed.props = props;
    return <div data-testid="workspace" />;
  },
}));
const given = () => handed.props as WatchWorkspaceProps;

const values = paged([
  value("id-p", "pin", { pinned: true }),
  value("id-a", "a", { events: 10 }),
  value("id-b", "b", { events: 90 }),
]);

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "propertyWatch").mockResolvedValue(watch());
  vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(catalog());
  vi.spyOn(api, "propertyWatchScope").mockResolvedValue(scope());
  vi.spyOn(api, "propertyWatchBuckets").mockResolvedValue([bucket(0), bucket(1)]);
  vi.spyOn(api, "propertyWatchValues").mockResolvedValue(values);
  vi.spyOn(api, "propertyWatchSeries").mockResolvedValue({
    hours: 24,
    series: [series("id-p", "pin", [point(0)])],
  });
  vi.spyOn(api, "anomalies").mockResolvedValue({ items: [incident()], total: 1, limit: 50, offset: 0 });
});

async function renderPage(query: Record<string, string | string[] | undefined> = {}, id = WATCH_ID) {
  return render(
    await PropertyWatchPage({ params: Promise.resolve({ id }), searchParams: Promise.resolve(query) }),
  );
}

describe("the problem states", () => {
  it("says a missing watch does not exist and points back to the list", async () => {
    vi.spyOn(api, "propertyWatch").mockRejectedValue(new ApiError(404, "nope"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("does not exist");
    expect(screen.getByRole("link", { name: "Open property watches" })).toHaveAttribute("href", "/behavior/watches");
  });

  it("renders the forbidden state naming the permission", async () => {
    vi.spyOn(api, "propertyWatch").mockRejectedValue(new ApiError(403, "Forbidden"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:property_watches");
    expect(screen.getByRole("alert")).toHaveTextContent("forbidden for this account");
  });

  it("says the session expired on 401", async () => {
    vi.spyOn(api, "propertyWatch").mockRejectedValue(new ApiError(401, "x"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("session has expired");
  });

  it("says what failed and what to do on any other failure, without the server's text", async () => {
    vi.spyOn(api, "propertyWatch").mockRejectedValue(new ApiError(500, "stack trace"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
    expect(screen.queryByText(/stack trace/)).toBeNull();
  });
});

describe("the header", () => {
  it("names the watch, its scope, properties, bucket width, status with reason, and last bucket in UTC", async () => {
    const { container } = await renderPage();
    expect(container.querySelector("h1")).toHaveTextContent("VPN usernames");
    const meta = container.querySelector(".page-meta")!;
    expect(meta).toHaveTextContent("Log source group: DMZ firewalls");
    expect(meta).toHaveTextContent("username");
    expect(meta).toHaveTextContent("5-minute buckets");
    expect(meta).toHaveTextContent("OK");
    expect(meta).toHaveTextContent("The last collected bucket was judged.");
    expect(meta.querySelector("time")).toHaveAttribute("datetime", iso(11));
    expect(meta).toHaveTextContent("UTC");
  });

  it("shows no Edit and no Threshold rules: they are not built", async () => {
    await renderPage();
    expect(screen.queryByRole("button", { name: /edit/i })).toBeNull();
    expect(screen.queryByText(/threshold rules/i)).toBeNull();
  });
});

describe("what the workspace is handed", () => {
  it("reads in the requested window, in the requested order, with ids and not text", async () => {
    await renderPage({ range: "6h", sort: "value", offset: "50" });
    expect(api.propertyWatchBuckets).toHaveBeenCalledWith(WATCH_ID, 6);
    expect(api.propertyWatchValues).toHaveBeenCalledWith(WATCH_ID, {
      hours: 6, sort: "value", limit: 50, offset: 50,
    });
    expect(given()).toMatchObject({ hours: 6, rangeKey: "6h", sort: "value", offset: 50 });
  });

  it("selects the pinned values, then the top of the latest bucket, and reads their series in one request", async () => {
    await renderPage();
    expect(given().initialSelected).toEqual(["id-p", "id-b", "id-a"]);
    expect(api.propertyWatchSeries).toHaveBeenCalledTimes(1);
    expect(api.propertyWatchSeries).toHaveBeenCalledWith(WATCH_ID, ["id-p", "id-b", "id-a"], 24);
  });

  it("honours the values asked for by id in the link from an incident", async () => {
    const asked = "6f1c0f9e-0000-4000-8000-0000000000aa";
    await renderPage({ value: [asked, "alice"] });
    expect(given().initialSelected).toEqual([asked]);
    expect(api.propertyWatchSeries).toHaveBeenCalledWith(WATCH_ID, [asked], 24);
  });

  it("asks for this watch's incidents over a bounded window", async () => {
    await renderPage();
    const arg = vi.mocked(api.anomalies).mock.calls[0][0]!;
    expect(arg).toMatchObject({ subject_kind: "PW_VALUE", property_watch_id: WATCH_ID, limit: 50 });
    expect(Date.parse(arg.until!) - Date.parse(arg.since!)).toBe(30 * 86_400_000);
  });

  it("hands over null, not an empty list, when a secondary read fails", async () => {
    vi.spyOn(api, "propertyWatchBuckets").mockRejectedValue(new ApiError(500, "x"));
    vi.spyOn(api, "propertyWatchValues").mockRejectedValue(new ApiError(500, "x"));
    vi.spyOn(api, "propertyWatchCatalog").mockRejectedValue(new ApiError(500, "x"));
    vi.spyOn(api, "propertyWatchScope").mockRejectedValue(new ApiError(500, "x"));
    vi.spyOn(api, "anomalies").mockRejectedValue(new ApiError(500, "x"));
    await renderPage();
    expect(given()).toMatchObject({ buckets: null, values: null, catalog: null, scope: null, incidents: null, incidentsTotal: null });
    expect(given().initialSelected).toEqual([]);
    expect(api.propertyWatchSeries).not.toHaveBeenCalled();
  });

  it("keeps the page when the first series read fails, leaving the retry to the client", async () => {
    vi.spyOn(api, "propertyWatchSeries").mockRejectedValue(new ApiError(500, "x"));
    await renderPage();
    expect(given().initialSelected.length).toBeGreaterThan(0);
    expect(given().initialSeries).toEqual([]);
  });
});

describe("the server/client boundary", () => {
  // A server page that imports a constant or a function from a "use client" module fails at
  // render time ("Attempted to call X() from the server"), which no component test sees because
  // they mock the module. Found by the screenshot run; pinned here.
  it("takes only components from the client modules it imports", () => {
    const source = readFileSync(join(process.cwd(), "src/app/behavior/watches/[id]/page.tsx"), "utf8");
    const imports = [...source.matchAll(/import\s+(?:type\s+)?\{([^}]*)\}\s+from\s+"@\/([^"]+)"/g)];
    const offenders: string[] = [];
    for (const [, names, path] of imports) {
      let text = "";
      for (const ext of [".tsx", ".ts"]) {
        try {
          text = readFileSync(join(process.cwd(), "src", path + ext), "utf8");
          break;
        } catch {
          /* try the next extension */
        }
      }
      if (!/^\s*(\/\/.*\n|\s)*["']use client["']/.test(text)) continue;
      for (const name of names.split(",").map((n) => n.trim()).filter(Boolean)) {
        if (name.startsWith("type ")) continue;
        if (!/^[A-Z][a-z]/.test(name)) offenders.push(`${name} from ${path}`);
      }
    }
    expect(offenders).toEqual([]);
  });
});
