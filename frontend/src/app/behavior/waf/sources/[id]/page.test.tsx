import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WafSourceError from "./error";
import Loading from "./loading";
import WafSourceEndpointsPage from "./page";
import { WAF_SOURCE_ID, wafEndpoint, wafSource } from "@/components/waf/fixtures";
import { ApiError, api, type WafEndpointInventory } from "@/lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
});

async function renderPage(
  endpoints: WafEndpointInventory[] = [wafEndpoint()],
  source = wafSource(),
  search: Record<string, string | undefined> = {},
  total = endpoints.length,
) {
  vi.spyOn(api, "wafBehaviorSource").mockResolvedValue(source);
  const list = vi
    .spyOn(api, "wafBehaviorEndpoints")
    .mockResolvedValue({ items: endpoints, total, limit: 50, offset: 0 });
  const view = render(
    await WafSourceEndpointsPage({
      params: Promise.resolve({ id: WAF_SOURCE_ID }),
      searchParams: Promise.resolve(search),
    }),
  );
  return { ...view, list };
}

describe("WAF source endpoints", () => {
  it("names the source as the single h1 and states its validation and ceiling", async () => {
    const { container } = await renderPage();
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("lab-waf-behavior-01");
    expect(screen.getByText("VALID")).toBeInTheDocument();
    expect(screen.getByText(/132 of 500 endpoints active/)).toBeInTheDocument();
  });

  it("lists a retired endpoint and a never-evaluated one, each in its own words", async () => {
    await renderPage([
      wafEndpoint({ id: "r", normalized_endpoint: "/old", is_active: false, retired_at: "2026-10-05T00:00:00Z" }),
      wafEndpoint({ id: "n", normalized_endpoint: "/new", state: null }),
    ]);
    const retired = within(screen.getByText("GET /old").closest("tr")!);
    expect(retired.getByText("Retired")).toBeInTheDocument();
    const fresh = within(screen.getByText("GET /new").closest("tr")!);
    expect(fresh.getByText("Not evaluated")).toBeInTheDocument();
    expect(fresh.queryByText("NORMAL")).not.toBeInTheDocument();
  });

  it("shows the virtual-host caveat only when the source exposes no virtual host", async () => {
    await renderPage();
    expect(screen.queryByText(/does not expose a virtual host/)).not.toBeInTheDocument();
    await renderPage([wafEndpoint()], wafSource({ virtual_host_available: false }));
    expect(screen.getByText(/does not expose a virtual host/)).toBeInTheDocument();
  });

  it("filters to active endpoints through the API, not in the browser", async () => {
    const { list } = await renderPage([wafEndpoint()], wafSource(), { active: "1" });
    expect(list).toHaveBeenCalledWith(WAF_SOURCE_ID, expect.objectContaining({ active_only: true }));
    expect(screen.getByRole("link", { name: "Active only" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: "All endpoints" })).not.toHaveAttribute("aria-current");
  });

  it("pages the inventory and keeps the filter in the paging links", async () => {
    await renderPage([wafEndpoint()], wafSource(), { active: "1" }, 120);
    const next = screen.getByRole("link", { name: "Next" });
    expect(next.getAttribute("href")).toContain("active=1");
    expect(next.getAttribute("href")).toContain("offset=50");
  });

  it("says what is empty and why when the source is not collected", async () => {
    await renderPage([], wafSource({ validation_current: false, validation_status: "UNVALIDATED" }));
    expect(screen.getByRole("status")).toHaveTextContent(/No endpoint is tracked/);
    expect(screen.getByRole("status")).toHaveTextContent(/not collected/);
  });

  it("says nothing has been admitted yet when a collected source has no endpoints", async () => {
    await renderPage([]);
    expect(screen.getByRole("status")).toHaveTextContent(/None has been admitted yet, so nothing is being judged/);
  });
});

describe("WAF source endpoints: failures", () => {
  it("says the source does not exist on a 404, and where to go", async () => {
    vi.spyOn(api, "wafBehaviorSource").mockRejectedValue(new ApiError(404, "WAF source not found"));
    vi.spyOn(api, "wafBehaviorEndpoints").mockRejectedValue(new ApiError(404, "WAF source not found"));
    render(
      await WafSourceEndpointsPage({
        params: Promise.resolve({ id: "x" }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("This WAF source does not exist.");
    expect(screen.getByRole("alert")).toHaveTextContent(/Open WAF behavior/);
  });

  it("makes 'Open WAF behavior' a link to /behavior/waf on a 404", async () => {
    vi.spyOn(api, "wafBehaviorSource").mockRejectedValue(new ApiError(404, "WAF source not found"));
    vi.spyOn(api, "wafBehaviorEndpoints").mockRejectedValue(new ApiError(404, "WAF source not found"));
    render(
      await WafSourceEndpointsPage({
        params: Promise.resolve({ id: "x" }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("link", { name: "Open WAF behavior" })).toHaveAttribute(
      "href",
      "/behavior/waf",
    );
  });

  it("says what failed on an outage, without the exception text", async () => {
    vi.spyOn(api, "wafBehaviorSource").mockRejectedValue(new Error("ECONNREFUSED 10.1.2.3"));
    vi.spyOn(api, "wafBehaviorEndpoints").mockResolvedValue({ items: [], total: 0, limit: 50, offset: 0 });
    render(
      await WafSourceEndpointsPage({
        params: Promise.resolve({ id: "x" }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    expect(screen.getByRole("alert").textContent).not.toContain("ECONNREFUSED");
  });

  it("names a missing permission on a 403", async () => {
    vi.spyOn(api, "wafBehaviorSource").mockRejectedValue(new ApiError(403, "no"));
    vi.spyOn(api, "wafBehaviorEndpoints").mockRejectedValue(new ApiError(403, "no"));
    render(
      await WafSourceEndpointsPage({
        params: Promise.resolve({ id: "x" }),
        searchParams: Promise.resolve({}),
      }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(/permission/);
  });
});

describe("route states", () => {
  it("announces loading and offers a retry on error", async () => {
    const { unmount } = render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading WAF source endpoints");
    unmount();
    const reset = vi.fn();
    render(<WafSourceError error={new Error("x")} reset={reset} />);
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(reset).toHaveBeenCalled();
  });
});

describe("WAF source endpoints: header", () => {
  it("separates the four facts of the header instead of running them together", async () => {
    const { container } = await renderPage();
    const meta = container.querySelector(".page-meta") as HTMLElement;
    const separators = Array.from(meta.querySelectorAll('[aria-hidden="true"]')).map((e) => e.textContent);
    expect(separators).toEqual(["·", "·"]);
    expect(meta).toHaveTextContent("Profile enabled");
    expect(meta).toHaveTextContent("132 of 500 endpoints active");
  });
});
