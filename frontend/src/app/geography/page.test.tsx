import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";

import { answer, notCollected, partialLowerBound } from "@/components/geo/fixtures";
import { ApiError, geoApi } from "@/lib/api";

import GeographyError from "./error";
import Loading from "./loading";
import GeographyPage from "./page";

vi.mock("echarts/core", () => ({
  init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), on: vi.fn() }),
  use: vi.fn(),
  registerMap: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ MapChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

async function renderPage(params: Record<string, string> = {}) {
  return render(await GeographyPage({ searchParams: Promise.resolve(params) }));
}

beforeEach(() => vi.restoreAllMocks());

describe("GeographyPage", () => {
  it("has one h1, the range in UTC with its zone, and every country in the table", async () => {
    const spy = vi.spyOn(geoApi, "countries").mockResolvedValue(answer());
    await renderPage();
    expect(spy).toHaveBeenCalledWith("24h", "SOURCE");
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Geography");
    expect(screen.getByText(/^Last 24 h:/)).toHaveTextContent(
      "2026-11-01 10:00:00 UTC to 2026-11-02 10:00:00 UTC",
    );
    const table = screen.getByRole("table", { name: "Events by country" });
    expect(within(table).getAllByRole("row")).toHaveLength(19);
  });

  it("reads the direction and the window from the URL", async () => {
    const spy = vi.spyOn(geoApi, "countries").mockResolvedValue(answer({ direction: "DESTINATION" }));
    await renderPage({ window: "7d", direction: "DESTINATION" });
    expect(spy).toHaveBeenCalledWith("7d", "DESTINATION");
    const direction = screen.getByRole("navigation", { name: "Address direction" });
    expect(within(direction).getByRole("link", { name: "Destination" })).toHaveAttribute(
      "aria-current",
      "page",
    );
    expect(within(direction).getByRole("link", { name: "Source" })).toHaveAttribute(
      "href",
      "/geography?window=7d&direction=SOURCE",
    );
    const window = screen.getByRole("navigation", { name: "Window" });
    expect(within(window).getByRole("link", { name: "7 d" })).toHaveAttribute("aria-current", "page");
    expect(within(window).getByRole("link", { name: "1 h" })).toHaveAttribute(
      "href",
      "/geography?window=1h&direction=DESTINATION",
    );
  });

  it("falls back to the source addresses and 24 h for values it does not know", async () => {
    const spy = vi.spyOn(geoApi, "countries").mockResolvedValue(answer());
    await renderPage({ window: "99y", direction: "up" });
    expect(spy).toHaveBeenCalledWith("24h", "SOURCE");
  });

  it("links the guide section", async () => {
    vi.spyOn(geoApi, "countries").mockResolvedValue(answer());
    await renderPage();
    expect(screen.getByRole("link", { name: /Guide/ })).toHaveAttribute("href", "/guide/geography");
  });

  it("says NOT_COLLECTED instead of drawing an empty map", async () => {
    vi.spyOn(geoApi, "countries").mockResolvedValue(notCollected());
    await renderPage();
    expect(screen.getByText("Not collected.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows a partial range with its coverage", async () => {
    vi.spyOn(geoApi, "countries").mockResolvedValue(partialLowerBound());
    await renderPage();
    expect(screen.getByText(/The counts cover only the measured buckets/)).toBeInTheDocument();
    expect(screen.getByText("Buckets measured").nextElementSibling).toHaveTextContent("211 of 288");
  });

  it("says what failed and what to do when the API is down", async () => {
    vi.spyOn(geoApi, "countries").mockRejectedValue(new Error("down"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The country counts could not be shown.");
    expect(alert).toHaveTextContent(/reload the page/i);
    expect(screen.queryByRole("table")).toBeNull();
    // The controls stay, so the analyst can try another window.
    expect(screen.getByRole("navigation", { name: "Window" })).toBeInTheDocument();
  });

  it("names the permission on 403", async () => {
    vi.spyOn(geoApi, "countries").mockRejectedValue(new ApiError(403, "no"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:geo");
  });

  it("says when the rate limit was hit, not that the data is wrong", async () => {
    vi.spyOn(geoApi, "countries").mockRejectedValue(new ApiError(429, "slow down", undefined, undefined, 30));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent(/Nothing is wrong with the data/);
  });

  it("is Turkish when the interface is", async () => {
    setTestLocale("tr");
    vi.spyOn(geoApi, "countries").mockResolvedValue(answer());
    await renderPage({ direction: "DESTINATION" });
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Coğrafya");
    expect(screen.getByRole("navigation", { name: "Adres yönü" })).toBeInTheDocument();
    expect(screen.getByText(/^Son 24 sa:/)).toHaveTextContent("UTC");
    expect(screen.getByRole("table", { name: "Ülkeye göre olaylar" })).toBeInTheDocument();
  });

  it("names the permission in Turkish on 403", async () => {
    setTestLocale("tr");
    vi.spyOn(geoApi, "countries").mockRejectedValue(new ApiError(403, "no"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:geo");
    expect(screen.getByRole("alert")).toHaveTextContent("Ülke sayımları gösterilemedi.");
  });
});

describe("route states", () => {
  it("announces loading with the page heading and the controls", () => {
    render(<Loading />);
    expect(screen.getByRole("status")).toHaveTextContent(/loading the country counts/i);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Geography");
    expect(screen.getByRole("navigation", { name: "Window" })).toBeInTheDocument();
  });

  it("shows an error without the exception text", () => {
    render(<GeographyError error={Object.assign(new Error("secret stack"), { digest: "abc" })} reset={() => {}} />);
    expect(screen.getByRole("alert")).toHaveTextContent("abc");
    expect(screen.getByRole("alert")).toHaveTextContent("The country counts");
    expect(screen.queryByText(/secret stack/)).toBeNull();
  });
});
