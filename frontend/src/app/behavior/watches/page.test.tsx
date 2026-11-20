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
    expect(screen.getByRole("alert")).toHaveTextContent("are forbidden");
  });

  it("says what failed and what to do, without the server's text", async () => {
    vi.spyOn(api, "propertyWatches").mockRejectedValue(new ApiError(500, "secret stack"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
    expect(screen.getByRole("alert")).toHaveTextContent("Reload to retry");
    expect(screen.queryByText(/secret stack/)).toBeNull();
  });

  it("explains an empty list: what a watch is, and that none exists", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([]);
    await renderPage();
    expect(screen.getByText("No property watch yet.")).toBeInTheDocument();
    expect(screen.getByText(/Create one to track/)).toBeInTheDocument();
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
    expect(screen.getByText("SHADOW MODE.")).toBeInTheDocument();
    expect(screen.getByText(/nothing pages/)).toBeInTheDocument();
  });

  it("says the switches were not read instead of implying collection is on", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockRejectedValue(new ApiError(500, "x"));
    await renderPage();
    expect(screen.getByText("SWITCHES NOT READ.")).toBeInTheDocument();
    expect(screen.queryByText("PW_COLLECTION_ENABLED")).toBeNull();
  });

  it("states no latency sentence (the Guide holds it) and never claims to be live", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    await renderPage();
    expect(screen.queryAllByText((_, el) => el?.tagName === "P" && el.textContent === partsText(pwLatency()))).toHaveLength(0);
    const text = document.body.textContent ?? "";
    expect(text).not.toMatch(/\blive\b|real-time/i);
  });
});

describe("the latency figure", () => {
  const cadence = { family: "PROPERTY_WATCH" as const, cadence_seconds: 3600, bucket_seconds: 300, settle_seconds: 120 };
  const sentence = () =>
    screen.queryAllByText((_, el) => el?.tagName === "P" && (el.textContent ?? "").startsWith("Watches are judged"));

  it("comes from the catalog's cadence, bucket and settle time", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(catalog({ cadence }));
    await renderPage();
    expect(sentence()).toHaveLength(0);
  });

  it("follows a non-default settle time", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    vi.spyOn(api, "propertyWatchCatalog").mockResolvedValue(
      catalog({ cadence: { ...cadence, cadence_seconds: 900, settle_seconds: 600 } }),
    );
    await renderPage();
    expect(sentence()).toHaveLength(0);
  });

  it("states no number whether or not the catalog has a cadence", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch()]);
    await renderPage();
    expect(sentence()).toHaveLength(0);
  });

  it("links Search cadence for no caller (the Guide holds the sentence)", async () => {
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
    expect(screen.queryByRole("link", { name: "Search cadence" })).toBeNull();
    expect(sentence()).toHaveLength(0);
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
      "NOT_APPLICABLE", "STALE", "READING_HISTORY", "OPEN", "RECOVERING", "OK", null,
    ];
    vi.spyOn(api, "propertyWatches").mockResolvedValue(
      all.map((status, i) => watch({ id: String(i), name: `w${i}`, status, status_reason: status ? `reason ${status}` : null })),
    );
    await renderPage();
    for (const r of rows()) {
      const cell = within(r).getAllByRole("cell")[1];
      expect(cell.querySelector(".pill")?.textContent).toBeTruthy();
      if (!["w10", "w1"].includes(within(r).getAllByRole("cell")[0].textContent ?? "")) {
        expect(cell.textContent?.length).toBeGreaterThan(8);
      }
    }
    const ok = rows().find((r) => within(r).getAllByRole("cell")[0].textContent === "w10")!;
    expect(within(ok).getAllByRole("cell")[1]).not.toHaveTextContent("reason OK");
    const never = rows().find((r) => within(r).getAllByRole("cell")[0].textContent === "w11")!;
    expect(never).toHaveTextContent("Never run");
    expect(never).toHaveTextContent("not judged");
    expect(never.querySelector(".pill.ok")).toBeNull();
  });

  it("shows history progress without attention-sorting it when the API gives no overdue limit", async () => {
    const base = watch();
    vi.spyOn(api, "propertyWatches").mockResolvedValue([
      watch({ id: "1", name: "Zulu", status: "OK" }),
      watch({
        id: "2",
        name: "Alpha",
        status: "READING_HISTORY",
        history: { ...base.history, state: "READING", days_requested: 28, days_read: 9 },
      }),
    ]);
    await renderPage();
    expect(rows().map((row) => within(row).getAllByRole("cell")[0].textContent)).toEqual([
      "Alpha", "Zulu",
    ]);
    const reading = rows()[0];
    expect(reading).toHaveTextContent("Reading history");
    expect(reading).toHaveTextContent("9 of 28 days");
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

  it("shows the last bucket in UTC with the age after it, and 'none yet' without one", async () => {
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
    expect(idle).toHaveTextContent("none yet");
  });

  it("right-aligns its figures, names itself, and links each watch by id", async () => {
    vi.spyOn(api, "propertyWatches").mockResolvedValue([watch({ open_incidents: 0 })]);
    await renderPage();
    expect(screen.getByRole("table", { name: "Property watches" })).toBeInTheDocument();
    for (const name of ["Coverage (%)", "Open", "Last bucket"]) {
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
