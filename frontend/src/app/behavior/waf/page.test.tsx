import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WafBehaviorError from "./error";
import Loading from "./loading";
import WafBehaviorPage from "./page";
import { WAF_SOURCE_ID, wafSource } from "@/components/waf/fixtures";
import { ApiError, api, type PagedWafSources, type WafSourceBehavior } from "@/lib/api";

function paged(items: WafSourceBehavior[]): PagedWafSources {
  return {
    items,
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
) {
  const call = vi.spyOn(api, "wafBehaviorSources").mockResolvedValue(paged(items));
  const view = render(await WafBehaviorPage({ searchParams: Promise.resolve(params) }));
  return { ...view, call };
}

describe("WAF behavior: composition", () => {
  it("has one h1 and leads with how many sources are judged", async () => {
    const { container } = await renderPage([
      wafSource(),
      wafSource({ log_source_id: "b", log_source_name: "second", enabled: false }),
    ]);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("WAF behavior");
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    expect(screen.getByText(/The others produce no verdicts/)).toBeInTheDocument();
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
    expect(row.getByText("No bucket collected yet")).toBeInTheDocument();
    expect(row.queryByText(/%/)).not.toBeInTheDocument();
  });
});

describe("WAF behavior: empty and failed", () => {
  it("says what is empty and why, and that empty is not normal", async () => {
    await renderPage([]);
    expect(screen.getByRole("status")).toHaveTextContent(/No WAF source has a behavior profile/);
    expect(screen.getByRole("status")).toHaveTextContent(/not a statement that WAF traffic is normal/);
  });

  it("says what failed and what to do on an outage, without the exception text", async () => {
    vi.spyOn(api, "wafBehaviorSources").mockRejectedValue(new Error("connect ECONNREFUSED 10.0.0.9:8000"));
    render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/);
    expect(alert).toHaveTextContent(/check that it is running/);
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
    expect(lead).toHaveTextContent("profile enabled and valid for its current version");
    expect(lead).not.toHaveTextContent("VALID");
  });
});
