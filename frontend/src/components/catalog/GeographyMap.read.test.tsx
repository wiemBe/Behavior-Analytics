// The Geography widget against the real read: `useApiResource` and `request` are not mocked, only
// the network is. A read the backend answers with an error draws the frame's error state with the
// route link, a read still pending stays loading, and an answer draws the map state.

import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { answer } from "@/components/geo/fixtures";

import { GeographyMap } from "./GeographyMap";

vi.mock("echarts/core", () => ({
  init: () => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn(), on: vi.fn() }),
  use: vi.fn(),
  registerMap: vi.fn(),
}));
vi.mock("echarts/charts", () => ({ MapChart: {} }));
vi.mock("echarts/components", () => ({ TooltipComponent: {} }));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

function reply(status: number, body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

const link = () => screen.getByRole("link", { name: "Open the Geography page" });

describe("the Geography widget's read", () => {
  it.each([404, 500])("draws the error state with the route link when the read answers %i", async (status) => {
    fetchMock.mockImplementation(() => reply(status, { detail: "x" }));
    render(<GeographyMap id={`err-${status}`} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("The country counts could not be read.");
    expect(alert).toHaveTextContent(`HTTP ${status}`);
    expect(screen.queryByText("Loading the country counts…")).toBeNull();
    expect(link()).toHaveAttribute("href", "/geography?window=24h&direction=SOURCE");
  });

  it("stays loading while the read is pending", async () => {
    fetchMock.mockImplementation(() => new Promise(() => {}));
    render(<GeographyMap id="pending" />);
    expect(await screen.findByText("Loading the country counts…")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(link()).toBeInTheDocument();
  });

  it("draws the countries when the read answers", async () => {
    fetchMock.mockImplementation(() => reply(200, answer()));
    render(<GeographyMap id="answered" />);
    await waitFor(() => expect(screen.getByRole("table")).toBeInTheDocument());
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText("Loading the country counts…")).toBeNull();
  });
});
