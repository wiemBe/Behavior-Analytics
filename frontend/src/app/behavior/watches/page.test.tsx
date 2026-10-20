import { cleanup, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PropertyWatchesPage from "./page";
import { catalog, iso, watch } from "@/components/propertywatch/fixtures";
import { pwLatency } from "@/components/propertywatch/pwFormat";
import { partsText } from "@/components/cadence/CadenceLatency";
import { ApiError, api, type PwWatchStatus } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
  vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(catalog());
  vi.spyOn(api, "caller").mockResolvedValue({
    subject: "analyst", email: null, permissions: ["read:*"], effective_permissions: ["read:property_watches"],
  });
});

async function renderPage() {
  return render(await PropertyWatchesPage());
}

const rows = () => screen.getAllByRole("row").slice(1);

describe("the list states", () => {
  it("renders a read-permission forbidden state naming the permission, not an error", async () => {
    vi.spyOn(api, "propertyWatches").mockRejectedValue(new ApiError(403, "Forbidden"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:property_watches");
    expect(screen.getByRole("alert")).toHaveTextContent("forbidden for this account");
  });

  it("says what failed and what to do, without the server's text", async () => {
    vi.spyOn(api, "propertyWatches").mockRejectedValue(new ApiError(500, "secret stack"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.getByRole("alert")).toHaveTextContent("reload the page");
    expect(screen.queryByText(/secret stack/)).toBeNull();
  });

  it("explains an empty list: what a watch is, and that none exists", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No property watch exists yet.")).toBeInTheDocument();
    expect(screen.getByText(/counts the values of one or two properties/)).toBeInTheDocument();
  });

  it("hides New watch from readers and names the permission that adds it", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([]);
    await renderPage();
    expect(screen.queryByRole("link", { name: /new watch/i })).toBeNull();
    expect(document.querySelector('a[href="/behavior/watches/new"]')).toBeNull();
    expect(screen.getByText("property_watch:write").tagName).toBe("CODE");
  });

  it("offers New watch to writers", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([]);
    vi.spyOn(api, "caller").mockResolvedValue({
      subject: "engineer", email: null, permissions: ["property_watch:write"], effective_permissions: ["property_watch:write"],
    });
    await renderPage();
    expect(screen.getByRole("link", { name: "New watch" })).toHaveAttribute("href", "/behavior/watches/new");
  });
});

describe("the notices", () => {
  it("names the collection setting when collection is off", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(catalog({ collection_enabled: false }));
    await renderPage();
    expect(screen.getByText("PW_COLLECTION_ENABLED").tagName).toBe("CODE");
    expect(screen.getByText("COLLECTION OFF.")).toBeInTheDocument();
  });

  it("names the alerting setting in shadow mode, and only then", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    await renderPage();
    expect(screen.getByText("PW_ALERTING_ENABLED").tagName).toBe("CODE");
  });

  it("says the switches were not read instead of implying collection is on", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockRejectedValue(new ApiError(500, "x"));
    await renderPage();
    expect(screen.getByText("SWITCHES NOT READ.")).toBeInTheDocument();
    expect(screen.queryByText("PW_COLLECTION_ENABLED")).toBeNull();
  });

  it("states the latency once and never claims to be live", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    await renderPage();
    expect(screen.getAllByText((_, el) => el?.tagName === "P" && el.textContent === partsText(pwLatency()))).toHaveLength(1);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\blive\b|real-time/i);
  });
});

describe("the latency figure", () => {
  const cadence = { family: "PROPERTY_WATCH" as const, cadence_seconds: 3600, bucket_seconds: 300, settle_seconds: 120 };
  const sentence = () =>
    screen.getAllByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").startsWith("Watches are judged"));

  it("comes from the catalog's cadence, bucket and settle time", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(catalog({ cadence }));
    await renderPage();
    expect(sentence()).toHaveLength(1);
    expect(sentence()[0]).toHaveTextContent("collected 5-minute buckets, up to 1 h 7 min after the events arrive");
    expect(sentence()[0]).not.toHaveTextContent("after each collection of their family");
  });

  it("follows a non-default settle time", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(
      catalog({ cadence: { ...cadence, cadence_seconds: 900, settle_seconds: 600 } }),
    );
    await renderPage();
    expect(sentence()[0]).toHaveTextContent("up to 30 min after the events arrive");
  });

  it("names no number when the catalog has no cadence, or none could be read", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    await renderPage();
    expect(sentence()[0]).toHaveTextContent("after each collection of their family");
    expect(sentence()[0].textContent).not.toMatch(/up to \d/);
  });

  it("links Search cadence only for read:search_cadence, figure or not", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(catalog({ cadence }));
    vi.spyOn(api, "caller").mockResolvedValue({
      subject: "analyst", email: null, permissions: [], effective_permissions: ["read:property_watches"],
    });
    await renderPage();
    expect(screen.queryByRole("link", { name: "Search cadence" })).toBeNull();
    cleanup();
    vi.spyOn(api, "caller").mockResolvedValue({
      subject: "analyst", email: null, permissions: [], effective_permissions: ["read:property_watches", "read:search_cadence"],
    });
    await renderPage();
    expect(screen.getByRole("link", { name: "Search cadence" })).toHaveAttribute("href", "/admin/search-cadence");
    expect(sentence()[0]).toHaveTextContent("up to 1 h 7 min");
  });
});

describe("the table", () => {
  it("sorts the watches that need attention first", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([
      watch({ id: "1", name: "Fine", status: "OK" }),
      watch({ id: "2", name: "Parses nothing", status: "NOT_APPLICABLE" }),
      watch({ id: "3", name: "Burning", status: "OPEN", open_incidents: 2 }),
    ]);
    await renderPage();
    expect(rows().map((r) => within(r).getAllByRole("cell")[0].textContent)).toEqual([
      "Burning", "Parses nothing", "Fine",
    ]);
  });

  it("puts status and reason in the second column, in words, for every status", async () => {
    const all: Array<PwWatchStatus | null> = [
      "NOT_ENABLED", "DISABLED", "SCOPE_EMPTY", "SCOPE_STALE", "SCOPE_TOO_LARGE",
      "NOT_APPLICABLE", "STALE", "OPEN", "RECOVERING", "OK", null,
    ];
    vi.spyOn(api, "propertyWatches").mockResolvedValue(
      all.map((status, i) => watch({ id: String(i), name: `w${i}`, status, status_reason: status ? `reason ${status}` : null })),
    );
    await renderPage();
    for (const r of rows()) {
      const cell = within(r).getAllByRole("cell")[1];
      expect(cell.querySelector(".pill")?.textContent).toBeTruthy();
      expect(cell.textContent?.length).toBeGreaterThan(8);
    }
    const never = rows().find((r) => within(r).getAllByRole("cell")[0].textContent === "w10")!;
    expect(never).toHaveTextContent("Never run");
    expect(never).toHaveTextContent("not judged");
    expect(never.querySelector(".pill.ok")).toBeNull();
  });

  it("writes 'at least' on a lower-bound coverage and a dash when it is unknown", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([
      watch({ id: "1", name: "lb", coverage: { bucket_start: iso(1), total_events: 10, parsed_events: 9, ratio: 0.88, measurement: "LOWER_BOUND" } }),
      watch({ id: "2", name: "exact" }),
      watch({ id: "3", name: "unknown", coverage: null }),
    ]);
    await renderPage();
    const cov = (name: string) => within(rows().find((r) => within(r).getAllByRole("cell")[0].textContent === name)!).getAllByRole("cell")[4];
    expect(cov("lb")).toHaveTextContent("at least 88 %");
    expect(cov("exact")).toHaveTextContent("96 %");
    expect(cov("unknown")).toHaveTextContent("—");
  });

  it("shows the last bucket in UTC with the age after it, and 'no bucket yet' without one", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([
      watch({ id: "1", name: "ran", collected_through: "2026-10-18T08:55:00Z" }),
      watch({ id: "2", name: "idle", collected_through: null, status: null }),
    ]);
    await renderPage();
    const ran = rows().find((r) => within(r).getAllByRole("cell")[0].textContent === "ran")!;
    const time = within(ran).getByText("2026-10-18 08:55:00 UTC");
    expect(time.tagName).toBe("TIME");
    expect(time).toHaveAttribute("datetime", "2026-10-18T08:55:00Z");
    expect(within(ran).getAllByRole("cell")[6].querySelector(".cell-sub")).not.toBeNull();
    const idle = rows().find((r) => within(r).getAllByRole("cell")[0].textContent === "idle")!;
    expect(idle).toHaveTextContent("no bucket yet");
  });

  it("right-aligns its figures, names itself, and links each watch by id", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch({ open_incidents: 0 })]);
    await renderPage();
    expect(screen.getByRole("table", { name: "Property watches" })).toBeInTheDocument();
    for (const name of ["Coverage, last bucket", "Open incidents", "Last bucket (UTC)"]) {
      expect(screen.getByRole("columnheader", { name })).toHaveClass("num");
    }
    expect(screen.getByRole("link", { name: "VPN usernames" })).toHaveAttribute(
      "href",
      "/behavior/watches/6f1c0f9e-0000-4000-8000-000000000001",
    );
    expect(within(rows()[0]).getAllByRole("cell")[5]).toHaveTextContent(/^0$/);
  });

  it("renders a hostile watch name as text", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch({ name: '<img src=x onerror="x">' })]);
    const { container } = await renderPage();
    expect(container.querySelector("img")).toBeNull();
  });
});
