import { render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import NoiseDetailError from "./error";
import Loading from "./loading";
import NoiseDetailPageView from "./page";
import { SRC, current, detail } from "@/components/noise/noiseFixtures";
import { ApiError, api, type NoiseSourceDetail } from "@/lib/api";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

function setup(result: NoiseSourceDetail | Error) {
  return vi
    .spyOn(api, "noiseSource")
    .mockImplementation(async () => (result instanceof Error ? Promise.reject(result) : result));
}

async function renderPage(search: Record<string, string | undefined> = {}) {
  return render(
    await NoiseDetailPageView({
      params: Promise.resolve({ id: SRC }),
      searchParams: Promise.resolve(search),
    }),
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Tune a noisy source: identity and counts", () => {
  it("has one h1 with the source name, its instance, class and criticality", async () => {
    const call = setup(detail());
    const { container } = await renderPage();
    expect(call).toHaveBeenCalledWith(SRC);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1, name: "fw-edge-01" })).toBeInTheDocument();
    expect(screen.getByText(/qradar-prod · Firewall/i)).toBeInTheDocument();
    expect(screen.getByText("Criticality High")).toBeInTheDocument();
  });

  it("puts identity, then counts, then the replay form, in that order", async () => {
    setup(detail());
    await renderPage();
    const headings = screen.getAllByRole("heading").map((h) => h.textContent);
    expect(headings).toEqual(["fw-edge-01", "What this source raised", "Replay and tune"]);
    expect(screen.getByRole("form", { name: "Detection values" })).toBeInTheDocument();
  });

  it("shows 7 d and 28 d incidents, alerts, noise and worst deviation per detector with units", async () => {
    setup(detail());
    await renderPage();
    const table = screen.getByRole("table", { name: /Incidents and alerts per detector/ });
    const all = within(table).getByRole("row", { name: /All detectors/ });
    expect(all).toHaveTextContent("9");
    expect(all).toHaveTextContent("41");
    expect(all).toHaveTextContent("70");
    expect(all).toHaveTextContent("4.2×");
    const spike = within(table).getByRole("row", { name: /Volume spike/ });
    expect(spike).toHaveTextContent("30");
    expect(within(table).getAllByRole("columnheader", { name: /Worst deviation/ })[0]).toHaveTextContent("× expected");
    // 17a's rule: a short denominator reads "n noise / m closed", not a share.
    expect(within(table).getByRole("row", { name: /Volume drop/ })).toHaveTextContent("1 noise / 2 closed");
    expect(within(table).getByRole("row", { name: /Volume drop/ })).not.toHaveTextContent("50");
  });

  it("writes a count that was not collected as 'not measured', never 0", async () => {
    setup(
      detail({
        detectors: [
          {
            ...detail().detectors[0],
            incidents_7d: null,
            alerts_28d: null,
            noise_closed: null,
            noise_closed_total: null,
            worst_ratio: null,
            worst_z: null,
          },
        ],
      }),
    );
    await renderPage();
    const row = screen.getByRole("row", { name: /Volume spike/ });
    expect(within(row).getAllByText("not measured").length).toBeGreaterThanOrEqual(4);
    expect(within(row).queryByText("0")).toBeNull();
  });

  it("says a source without detector rows raised none, and keeps the whole-source row", async () => {
    setup(detail({ detectors: [] }));
    await renderPage();
    expect(screen.getByText(/No detector has raised an incident for this source/)).toBeInTheDocument();
    expect(screen.getByRole("row", { name: /All detectors/ })).toBeInTheDocument();
  });

  it("states that noise and worst deviation cover 28 days", async () => {
    setup(detail());
    await renderPage();
    expect(screen.getByText(/Noise and worst deviation always cover the last 28 days/)).toBeInTheDocument();
  });

  it("labels partial coverage as lower bounds", async () => {
    setup(
      detail({
        coverage: {
          state: "PARTIAL",
          window_days: 28,
          covered_days: 12,
          retention_days: 365,
          reason: null,
        },
      }),
    );
    await renderPage();
    expect(screen.getByText("Partial coverage.")).toBeInTheDocument();
    expect(screen.getByText(/12 of 28 days and are lower bounds/)).toBeInTheDocument();
  });

  it("marks a source without a reliable baseline as not judged, with the backend's reason", async () => {
    setup(
      detail({
        judged: false,
        judgement: "INSUFFICIENT_DATA",
        reason: {
          code: "noise.reason.no_reliable_baseline",
          params: {},
          text: "Not judged: no reliable baseline for this weekday and hour.",
        },
      }),
    );
    await renderPage();
    expect(screen.getAllByText("Not judged").length).toBeGreaterThan(0);
    expect(screen.getByText(/No reliable baseline for this weekday and hour/)).toBeInTheDocument();
  });

  it("says a source that is not monitored has no counts, and draws no counts table", async () => {
    setup(
      detail({
        monitoring_enabled: false,
        judgement: "NOT_COLLECTED",
        judged: false,
        incidents_7d: null,
        incidents_28d: null,
        detectors: [],
      }),
    );
    await renderPage();
    expect(screen.getByText(/This source is not monitored, so no counts are collected/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.getAllByText("Not monitored").length).toBeGreaterThan(0);
    // The form is still there: the analyst can preview and read the reason.
    expect(screen.getByRole("form", { name: "Detection values" })).toBeInTheDocument();
  });

  it("shows maintenance with its end in UTC", async () => {
    setup(detail({ current: current({ maintenance_mode: true, maintenance_until: "2026-11-07T06:00:00Z" }) }));
    await renderPage();
    expect(screen.getByText(/In maintenance/)).toHaveTextContent("UTC");
  });

  it("links back to the list with the list's own window, order and page", async () => {
    setup(detail());
    await renderPage({ window: "7", order: "alerts", offset: "50" });
    expect(screen.getByRole("link", { name: "Back to noisy sources" })).toHaveAttribute(
      "href",
      "/behavior/noise?window=7&order=alerts&offset=50",
    );
  });

  it("links back to the plain list when the URL holds no state, and ignores a crafted one", async () => {
    setup(detail());
    await renderPage({ window: "9999", order: "<script>", offset: "-4" });
    expect(screen.getByRole("link", { name: "Back to noisy sources" })).toHaveAttribute("href", "/behavior/noise");
  });

  it("links to the source's behavior page and its alerts", async () => {
    setup(detail());
    await renderPage();
    expect(screen.getByRole("link", { name: "Open the source's behavior page" })).toHaveAttribute(
      "href",
      `/behavior/sources/${SRC}`,
    );
    expect(screen.getByRole("link", { name: "Alerts of this source" })).toHaveAttribute(
      "href",
      `/alerts?log_source_id=${SRC}`,
    );
  });
});

describe("Tune a noisy source: states that fail", () => {
  it("names read:anomalies when the page is forbidden, and draws no form", async () => {
    setup(new ApiError(403, "forbidden"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent("read:anomalies");
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("says the source does not exist on a 404", async () => {
    setup(new ApiError(404, "not found"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent(/does not exist or was removed/);
  });

  it("says the session expired on a 401", async () => {
    setup(new ApiError(401, "no"));
    await renderPage();
    expect(screen.getByRole("alert")).toHaveTextContent(/session has expired/);
  });

  it("says the counts are unknown, not zero, when the read fails", async () => {
    setup(new ApiError(500, "boom"));
    await renderPage();
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The tuning page could not be loaded");
    expect(alert).toHaveTextContent(/unknown/);
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.getByRole("link", { name: "Back to noisy sources" })).toBeInTheDocument();
  });

  it("renders the loading and error boundaries", () => {
    render(<Loading />);
    expect(screen.getByText(/tuning page/i)).toBeInTheDocument();
    render(<NoiseDetailError error={new Error("x")} reset={() => {}} />);
    expect(screen.getAllByText("Tune a noisy source").length).toBeGreaterThan(0);
  });
});
