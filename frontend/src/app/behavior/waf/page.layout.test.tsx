import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WafBehaviorPage from "./page";
import { wafSource } from "@/components/waf/fixtures";
import { api, type WafSourceBehavior } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(items: WafSourceBehavior[], total = items.length) {
  vi.spyOn(api, "wafBehaviorSources").mockResolvedValue({
    items,
    total,
    limit: 50,
    offset: 0,
    range_start: "2026-10-12T09:00:00Z",
    range_end: "2026-10-13T09:00:00Z",
  });
  return render(await WafBehaviorPage({ searchParams: Promise.resolve({}) }));
}

describe("WAF behavior: layout and honesty of the headline", () => {
  it("keeps the range control and the span it sets together, and the table in its own region", async () => {
    const { container } = await renderPage([wafSource()]);
    const group = container.querySelector(".waf-range") as HTMLElement;
    expect(group).not.toBeNull();
    expect(group.querySelector("nav")).not.toBeNull();
    expect(group).toHaveTextContent(/Admission overflow covers 2026-10-12 09:00:00 UTC/);
    expect(container.querySelector(".waf-sources .table-scroll")).not.toBeNull();
  });

  it("does not present a one-page judged count as a share of a longer list", async () => {
    const { container } = await renderPage(
      [wafSource(), wafSource({ log_source_id: "b", log_source_name: "second", enabled: false })],
      120,
    );
    const lead = container.querySelector(".waf-lead") as HTMLElement;
    expect(lead).toHaveTextContent("1 of 2 sources are listed on this page and collected and judged");
    expect(lead).toHaveTextContent("The list has 120 sources in all");
    expect(lead).not.toHaveTextContent("of 120");
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
  });

  it("keeps the plain wording when every source is on the page", async () => {
    const { container } = await renderPage([wafSource()]);
    const lead = container.querySelector(".waf-lead") as HTMLElement;
    expect(lead).not.toHaveTextContent("listed on this page");
    expect(lead).not.toHaveTextContent("in all");
  });
});
