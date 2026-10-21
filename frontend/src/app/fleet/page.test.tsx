import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import FleetPage from "./page";
import Loading from "./loading";
import FleetError from "./error";
import { ApiError, fleetApi, type FleetSummary } from "@/lib/api";

function summary(over: Partial<FleetSummary> = {}): FleetSummary {
  return {
    window: "24h",
    window_start: "2026-10-12T08:00:00Z",
    window_end: "2026-10-13T08:00:00Z",
    bucket_seconds: 300,
    classes: [
      {
        source_class: "WAF",
        sources: { enabled: 2, disabled: 0 },
        buckets: {
          expected: 576,
          complete: 576,
          complete_from_history: 0,
          partial: 0,
          missing: 0,
        },
        events: 10,
        mean_eps: 1,
        expected_eps: 1,
        baseline_sources: 2,
        baseline_share: 1,
        monitored_sources: 2,
        open_incidents: {},
        top_sources: [],
      },
    ],
    internal: { sources: { enabled: 4, disabled: 1 } },
    new_sources: 3,
    conflict_sources: 1,
    unclassified_sources: 0,
    ...over,
  };
}

async function renderPage(params: Record<string, string> = {}) {
  return render(await FleetPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => vi.restoreAllMocks());

describe("FleetPage", () => {
  it("has one h1, the window in UTC, and the window switch", async () => {
    const spy = vi.spyOn(fleetApi, "summary").mockResolvedValue(summary());
    await renderPage({ window: "7d" });
    expect(spy).toHaveBeenCalledWith("7d");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByText(/2026-10-12 08:00:00 UTC to 2026-10-13 08:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "7 d" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "24 h" })).not.toHaveAttribute("aria-current");
  });

  it("states how expected EPS is computed, across every cell the window spans", async () => {
    vi.spyOn(fleetApi, "summary").mockResolvedValue(summary());
    await renderPage();
    expect(screen.getByText(/across every UTC weekday and hour cell the window spans/)).toBeInTheDocument();
    expect(screen.queryByText(/most directly/)).toBeNull();
  });

  it("links the review-queue counts to the filtered list", async () => {
    vi.spyOn(fleetApi, "summary").mockResolvedValue(summary());
    await renderPage();
    expect(screen.getByRole("link", { name: "3 new sources" })).toHaveAttribute(
      "href",
      "/fleet/sources?review_status=NEW",
    );
    expect(screen.getByRole("link", { name: /1 source with a conflicting class/ })).toHaveAttribute(
      "href",
      "/fleet/sources?has_conflict=true",
    );
    // A zero is text, not a link to an empty list.
    expect(screen.getByText("0 unclassified sources").closest("a")).toBeNull();
  });

  it("counts internal sources apart", async () => {
    vi.spyOn(fleetApi, "summary").mockResolvedValue(summary());
    await renderPage();
    expect(screen.getByText(/4 enabled, 1 disabled/)).toBeInTheDocument();
  });

  it("says what is empty and why when no class is returned", async () => {
    vi.spyOn(fleetApi, "summary").mockResolvedValue(summary({ classes: [] }));
    await renderPage();
    expect(screen.getByText("No classes were returned.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says what failed and what to do when the API is down", async () => {
    vi.spyOn(fleetApi, "summary").mockRejectedValue(new Error("down"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("could not be shown");
    expect(alert).toHaveTextContent(/reload/i);
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("names the permission on 403", async () => {
    vi.spyOn(fleetApi, "summary").mockRejectedValue(new ApiError(403, "no"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:fleet");
  });
});

describe("route states", () => {
  it("announces loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading the log-source fleet/i);
  });
  it("shows an error without the exception text", () => {
    render(<FleetError error={Object.assign(new Error("secret stack"), { digest: "abc" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("abc");
    expect(screen.queryByText(/secret stack/)).toBeNull();
  });
});
