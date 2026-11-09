import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { parseTrafficQuery } from "@/lib/traffic";

import { TrafficControls, type ExportBasis } from "./TrafficControls";

function view(params: Record<string, string>, basis: ExportBasis, source = "s-1") {
  return render(
    <TrafficControls query={parseTrafficQuery(params)} sourceId={source} sources={[]} basis={basis} />,
  );
}

const data = (state: string, total: number): ExportBasis => ({
  kind: "data",
  page: { state: state as "OK", total },
});

describe("Export CSV", () => {
  it("is a download link to the API with the current window, sort and order, and no paging", () => {
    view({ window: "24h", sort: "host", order: "desc", limit: "50", offset: "100" }, data("OK", 80));
    const link = screen.getByRole("link", { name: "Export CSV (all 80 endpoints, 24 hours)" });
    const url = new URL(link.getAttribute("href")!);
    expect(url.pathname.endsWith("/behavior/waf/sources/s-1/traffic.csv")).toBe(true);
    expect(Object.fromEntries(url.searchParams)).toEqual({ window: "24h", sort: "host", order: "desc" });
    expect(url.searchParams.has("limit")).toBe(false);
    expect(url.searchParams.has("offset")).toBe(false);
    expect(link).toHaveAttribute("download");
  });

  it("names IPS destination IPs and the IPS route", () => {
    view({ view: "ips", window: "1h" }, data("PARTIAL", 40));
    const link = screen.getByRole("link", { name: "Export CSV (all 40 destination IPs, 1 hour)" });
    expect(link.getAttribute("href")).toContain("/behavior/ips/sources/s-1/traffic.csv");
  });

  it("says large exports are cut and marked, without naming a number", () => {
    view({}, data("TRUNCATED", 250000));
    expect(document.body.textContent).toContain("cut at the server's limit and marked in the file");
    expect(screen.getByRole("link", { name: /all 250,000 endpoints/ })).toBeInTheDocument();
  });

  it.each([
    ["INSUFFICIENT_DATA", 0, /no complete bucket/],
    ["NOT_COLLECTED", 0, /not collected/],
    ["OK", 0, /nothing to export/],
  ])("is not a link in %s with %i rows and says why", (state, total, reason) => {
    view({}, data(state, total));
    expect(screen.queryByRole("link", { name: /Export CSV/ })).toBeNull();
    expect(screen.getByText(reason)).toBeInTheDocument();
    expect(screen.getByText("Export CSV").parentElement).toHaveAttribute("aria-disabled", "true");
    expect(document.body.textContent).toContain("Export CSV — not available:");
  });

  it("is not a link while loading", () => {
    view({}, { kind: "loading" });
    expect(screen.queryByRole("link", { name: /Export CSV/ })).toBeNull();
    expect(screen.getByText(/while the data is loading/)).toBeInTheDocument();
  });

  it("with no behavior profile says there is nothing to export and never claims a load failure", () => {
    view({}, { kind: "none" }, "");
    expect(screen.queryByRole("link", { name: /Export CSV/ })).toBeNull();
    expect(document.body.textContent).toContain("Export CSV — nothing to export: no source has a behavior profile yet.");
    expect(document.body.textContent).not.toContain("could not be loaded");
    expect(screen.getByText("Export CSV").parentElement).toHaveAttribute("aria-disabled", "true");
  });

  it("is not a link after an error", () => {
    view({}, { kind: "error" });
    expect(screen.queryByRole("link", { name: /Export CSV/ })).toBeNull();
    expect(screen.getByText(/could not be loaded/)).toBeInTheDocument();
  });
});
