import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WafSourceEndpointsPage from "./page";
import { WAF_SOURCE_ID, wafEndpoint, wafSource } from "@/components/waf/fixtures";
import { api } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
});

const LONG =
  "/api/v2/organizations/{id}/projects/{id}/environments/{id}/deployments/{id}/artifacts/{id}/download";

async function renderPage() {
  vi.spyOn(api, "wafBehaviorSource").mockResolvedValue(wafSource());
  vi.spyOn(api, "wafBehaviorEndpoints").mockResolvedValue({
    items: [
      wafEndpoint({ id: "l", normalized_endpoint: LONG, state: "INSUFFICIENT_DATA" }),
      wafEndpoint({ id: "n", normalized_endpoint: "/health", state: null }),
    ],
    total: 2,
    limit: 50,
    offset: 0,
  });
  return render(
    await WafSourceEndpointsPage({
      params: Promise.resolve({ id: WAF_SOURCE_ID }),
      searchParams: Promise.resolve({}),
    }),
  );
}

describe("WAF source endpoints: layout", () => {
  it("puts the table in its own region and spaces the filter from it", async () => {
    const { container } = await renderPage();
    expect(container.querySelector(".waf-endpoints .table-scroll")).not.toBeNull();
    expect(screen.getByRole("navigation", { name: "Endpoint filter" })).toHaveClass("waf-filter");
  });

  it("shows a long path in full, and a never-evaluated endpoint as not evaluated", async () => {
    await renderPage();
    expect(screen.getByText(`GET ${LONG}`)).toBeInTheDocument();
    expect(screen.getByText("INSUFFICIENT_DATA")).toBeInTheDocument();
    expect(screen.getByText("Not evaluated")).toBeInTheDocument();
    expect(screen.queryByText("Normal")).not.toBeInTheDocument();
  });
});
