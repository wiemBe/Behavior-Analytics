import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WafBehaviorError from "./error";
import Loading from "./loading";
import WafBehaviorPage from "./page";
import { NO_RUN_RECORDED, WAF_SOURCE_ID, wafSource } from "@/components/waf/fixtures";
import { ApiError, api, type PagedWafSources, type WafSourceBehavior } from "@/lib/api";

function paged(items: WafSourceBehavior[], dispatch?: PagedWafSources["dispatch"]): PagedWafSources {
  return {
    items,
    dispatch,
    total: items.length,
    limit: 50,
    offset: 0,
    range_start: "2026-10-12T09:00:00Z",
    range_end: "2026-10-13T09:00:00Z",
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  items: WafSourceBehavior[] = [wafSource()],
  params: Record<string, string | undefined> = {},
  dispatch?: PagedWafSources["dispatch"],
) {
  const call = vi.spyOn(api, "wafBehaviorSources").mockResolvedValue(paged(items, dispatch));
  const view = render(await WafBehaviorPage({ searchParams: Promise.resolve(params) }));
  return { ...view, call };
}

describe("WAF behavior: composition", () => {
  it("keeps the time zone and the not-overflow reading after the d40 text cut", async () => {
    await renderPage();
    // The header says only the title; the range line carries its own UTC times and the
    // definitions moved to the Guide (UIQ-8a).
    expect(screen.queryByText("Times are UTC.")).not.toBeInTheDocument();
    expect(document.body.textContent).toContain("UTC");
    expect(document.body.textContent).not.toContain("Below admission threshold is low-volume noise");
    expect(document.body.textContent).not.toContain("A key-bucket is one endpoint key");
    expect(document.body.textContent).not.toContain("Refused over quota means");
  });

  it("has one h1 and leads with how many sources are judged", async () => {
    const { container } = await renderPage([
      wafSource(),
      wafSource({ log_source_id: "b", log_source_name: "second", enabled: false }),
    ]);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WAF behavior");
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/sources judged/)).toBeInTheDocument();
  });

  it("states the range overflow covers, in UTC", async () => {
    await renderPage();
    expect(screen.getByText(/2026-10-12 09:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-13 09:00:00 UTC/)).toBeInTheDocument();
  });

  it("asks for a bounded range and marks the active one", async () => {
    const { call } = await renderPage([wafSource()], { range: "7d" });
    const { since, until } = call.mock.calls[0][0]!;
    const days = (Date.parse(until!) - Date.parse(since!)) / 86400_000;
    expect(days).toBeCloseTo(7, 5);
    expect(screen.getByRole("link", { name: "7 days" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "24 hours" })).not.toHaveAttribute("aria-current");
  });

  it("falls back to 24 hours for a range it does not know", async () => {
    const { call } = await renderPage([wafSource()], { range: "forever" });
    const { since, until } = call.mock.calls[0][0]!;
    expect((Date.parse(until!) - Date.parse(since!)) / 3600_000).toBeCloseTo(24, 5);
  });
});

describe("WAF behavior: collection evidence (FIX-71e)", () => {
  const noBucket = { latest_bucket: null, collection: NO_RUN_RECORDED };

  it("does not count VALID sources nothing ran for as judged", async () => {
    await renderPage([
      wafSource({ log_source_id: "a", log_source_name: "alpha", ...noBucket }),
      wafSource({ log_source_id: "b", log_source_name: "bravo", ...noBucket }),
    ]);
    expect(screen.getByText("0 of 2")).toBeInTheDocument();
    expect(screen.getAllByText("No source run recorded")).toHaveLength(2);
  });

  it("says once that the last dispatch failed, with its UTC time", async () => {
    await renderPage([wafSource()], {}, { outcome: "failed", at: "2026-10-13T08:45:00Z" });
    const line = screen.getByText(/The last dispatch failed/);
    expect(line).toHaveTextContent("2026-10-13 08:45:00 UTC");
    expect(screen.getAllByText(/The last dispatch failed/)).toHaveLength(1);
  });

  it.each([
    ["a dispatch that succeeded", { outcome: "dispatched", at: "2026-10-13T08:45:00Z" }],
    ["no recorded dispatch", null],
  ])("shows no dispatch line for %s", async (_name, dispatch) => {
    await renderPage([wafSource()], {}, dispatch);
    expect(screen.queryByText(/The last dispatch failed/)).not.toBeInTheDocument();
  });
});

describe("WAF behavior: source states", () => {
  it("does not show a source whose profile is not valid for its current version as judged", async () => {
    await renderPage([
      wafSource({ profile_version: 5, validated_profile_version: 4, validation_current: false }),
    ]);
    expect(screen.getByText("0 of 1")).toBeInTheDocument();
    expect(screen.getByText("STALE")).toBeInTheDocument();
    expect(screen.queryByText("VALID")).not.toBeInTheDocument();
  });

  it("shows admission overflow", async () => {
    await renderPage([
      wafSource({
        admission: {
          buckets_in_range: 211,
          buckets_admission_truncated: 64,
          rejected_over_quota: 1893,
          below_admission_threshold: 702,
          untracked_requests: 48210,
        },
      }),
    ]);
    expect(screen.getByText("Overflowing")).toBeInTheDocument();
    expect(screen.getByText("64 of 211 buckets truncated")).toBeInTheDocument();
  });

  it("shows a source with null coverage as unmeasured, not as 0%", async () => {
    await renderPage([wafSource({ latest_bucket: null })]);
    const row = within(screen.getByRole("link", { name: "lab-waf-behavior-01" }).closest("tr")!);
    // The cell is a dash that names the state; the one-line summary above the table is pinned in WafSourcesTable.test.
    expect(row.getByText(/no bucket has been collected/)).toBeInTheDocument();
    expect(row.queryByText(/%/)).not.toBeInTheDocument();
  });
});

describe("WAF behavior: empty and failed", () => {
  it("says what is empty and why, and that empty is not normal", async () => {
    await renderPage([]);
    expect(screen.getByRole("status")).toHaveTextContent(/No WAF source has a behavior profile/);
    expect(screen.getByRole("status")).toHaveTextContent(/Nothing is collected or judged/);
  });

  it("says what failed and what to do on an outage, without the exception text", async () => {
    vi.spyOn(api, "wafBehaviorSources").mockRejectedValue(new Error("connect ECONNREFUSED 10.0.0.9:8000"));
    render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/);
    expect(alert).toHaveTextContent(/Check the backend/);
    expect(alert.textContent).not.toContain("ECONNREFUSED");
  });

  it("names a missing permission on a 403, and an expired session on a 401", async () => {
    vi.spyOn(api, "wafBehaviorSources").mockRejectedValue(new ApiError(403, "no"));
    const { unmount } = render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/do not have permission/);
    unmount();
    vi.spyOn(api, "wafBehaviorSources").mockRejectedValue(new ApiError(401, "no"));
    render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/);
  });
});

describe("route states", () => {
  it("announces loading politely and names what is loading", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading WAF behavior");
  });

  it("offers a retry from the error boundary and shows no exception text", async () => {
    const reset = vi.fn();
    render(<WafBehaviorError error={Object.assign(new Error("secret stack"), { digest: "abc123" })} reset={reset} />);
    expect(screen.getByRole("alert")).toHaveTextContent("WAF behavior could not be displayed");
    expect(screen.getByRole("alert").textContent).not.toContain("secret stack");
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalled();
  });

  it("links each source to its endpoint inventory", async () => {
    await renderPage();
    expect(screen.getByRole("link", { name: "lab-waf-behavior-01" })).toHaveAttribute(
      "href",
      `/behavior/waf/sources/${WAF_SOURCE_ID}`,
    );
  });
});

describe("WAF behavior: headline", () => {
  it("writes 'valid' in lower case inside the sentence", async () => {
    await renderPage();
    const lead = document.querySelector(".waf-lead") as HTMLElement;
    expect(lead).toHaveTextContent("1 of 1 source judged");
    expect(lead).not.toHaveTextContent("VALID");
  });
});
