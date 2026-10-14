import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { homeReads } from "@/lib/api";
import { behavior, fleet, fleetClass } from "@/lib/home/fixtures";

import { failed, forbidden, loading, ok } from "./testkit";
import { TopSources } from "./TopSources";

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const FLEET = homeReads.fleetSummary("24h").key;
const BEH = homeReads.sourceBehaviors().key;
const ts = (id: string, events: number, name = id) => ({ id, name, events });

function setup(over: { fleet?: unknown; behaviors?: unknown } = {}) {
  res.byKey = {
    [FLEET]:
      over.fleet ??
      ok(
        fleet([
          fleetClass("FIREWALL", {
            top_sources: [ts("a", 86_400_000, "Core Firewall"), ts("b", 43_200_000, "Edge FW")],
          }),
          fleetClass("PROXY", {
            buckets: { expected: 48, complete: 40, partial: 4, missing: 4 },
            top_sources: [ts("c", 8_640_000, "Proxy")],
          }),
        ]),
      ),
    [BEH]:
      over.behaviors ??
      ok([
        behavior({ log_source_id: "a", name: "Core Firewall", state: "OPEN", deviation_ratio: 3.4 }),
        behavior({ log_source_id: "b", name: "Edge FW", state: "NORMAL", deviation_ratio: null }),
      ]),
  };
}

beforeEach(() => {
  res.byKey = {};
});

describe("TopSources", () => {
  it("lists each source with its EPS, unit, deviation and state word", () => {
    setup();
    render(<TopSources />);
    const row = screen.getByRole("link", { name: "Core Firewall" }).closest("tr")!;
    expect(within(row).getByText("1,000.00")).toBeInTheDocument();
    expect(within(row).getAllByText(/EPS/).length).toBeGreaterThan(0);
    expect(within(row).getByText("3.40x")).toBeInTheDocument();
    expect(within(row).getAllByText("Open").length).toBeGreaterThan(0);
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent("Top log sources by EPS");
  });

  it("labels the deviation with its basis", () => {
    setup();
    render(<TopSources />);
    expect(screen.getByRole("columnheader", { name: /Deviation.*latest bucket/ })).toBeInTheDocument();
  });

  it("writes a null deviation as an em dash, never 0", () => {
    setup();
    render(<TopSources />);
    const row = screen.getByRole("link", { name: "Edge FW" }).closest("tr")!;
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).queryByText(/^0(\.0+)?x?$/)).toBeNull();
  });

  it("says 'at least' for a source whose class window is incomplete, and only for it", () => {
    setup();
    render(<TopSources />);
    expect(within(screen.getByRole("link", { name: "Proxy" }).closest("tr")!).getByText("at least")).toBeInTheDocument();
    expect(within(screen.getByRole("link", { name: "Core Firewall" }).closest("tr")!).queryByText("at least")).toBeNull();
  });

  it("draws the EPS bar relative to the largest and no bar for an unmeasured EPS", () => {
    setup();
    const { container } = render(<TopSources />);
    const widths = Array.from(container.querySelectorAll(".tw-bar-fill")).map((r) =>
      Number(r.getAttribute("width")),
    );
    expect(widths).toEqual([100, 50, 10]);
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("links each row to its source and 'View all' to the inventory", () => {
    setup();
    render(<TopSources />);
    expect(screen.getByRole("link", { name: "Core Firewall" })).toHaveAttribute("href", "/behavior/sources/a");
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute("href", "/behavior/sources");
  });

  it("keeps the full name in a title so a long one can truncate", () => {
    const long = "Güvenlik Duvarı İstanbul Merkez Ofis Çevre Ağ Geçidi Birincil Küme Düğümü 01";
    setup({ fleet: ok(fleet([fleetClass("FIREWALL", { top_sources: [ts("a", 100, long)] })])) });
    render(<TopSources />);
    expect(screen.getByRole("link", { name: long })).toHaveAttribute("title", long);
  });

  it("says the state is unavailable when the behavior list cannot be read, and why", () => {
    setup({ behaviors: failed() });
    render(<TopSources />);
    expect(screen.getByText(/state are unavailable/i)).toBeInTheDocument();
    expect(screen.getAllByText("State unavailable").length).toBeGreaterThan(0);
    for (const state of screen.getAllByText("State unavailable")) {
      expect(state.textContent?.trim()).not.toMatch(/^·/);
    }
  });

  it("is loading text in a polite live region, with no spinner", () => {
    setup({ fleet: loading });
    render(<TopSources />);
    const status = screen.getByRole("status");
    expect(status).toHaveTextContent(/Loading the fleet summary/);
    expect(status).toHaveAttribute("aria-live", "polite");
  });

  it("names what is empty and why", () => {
    setup({ fleet: ok(fleet([])) });
    render(<TopSources />);
    expect(screen.getByText(/No log source has a complete bucket/)).toBeInTheDocument();
  });

  it("names the permission when the read is refused", () => {
    setup({ fleet: forbidden });
    render(<TopSources />);
    expect(screen.getByText(/Your role cannot read the fleet summary \(permission read:fleet\)/)).toBeInTheDocument();
  });

  it("says what failed and what to do, with no exception text", () => {
    setup({ fleet: failed(503) });
    render(<TopSources />);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent(/could not be read/);
    expect(alert).toHaveTextContent(/HTTP 503/);
    expect(alert).toHaveTextContent(/check that the backend is reachable/);
  });

  it("keeps the old data and says so when a refresh failed", () => {
    setup({
      fleet: ok(
        fleet([fleetClass("FIREWALL", { top_sources: [ts("a", 100, "Core Firewall")] })]),
        { refreshFailed: { httpStatus: 500 } },
      ),
    });
    render(<TopSources />);
    expect(screen.getByRole("link", { name: "Core Firewall" })).toBeInTheDocument();
    expect(screen.getByText(/latest refresh failed \(HTTP 500\)/)).toBeInTheDocument();
    expect(screen.getByText(/received at 13:58:00 UTC/)).toBeInTheDocument();
  });
});
