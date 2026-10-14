import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { Qualifier } from "@/lib/home/qualifiers";

import type { WidgetState } from "@/widgets/types";

import { WidgetEditContext, WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

const ok = (
  over: Partial<Extract<ResourceState<string>, { status: "ok" }>> = {},
): ResourceState<string> => ({
  status: "ok",
  data: "answer",
  receivedAt: "2026-10-14T13:58:00Z",
  refreshing: false,
  refreshFailed: null,
  ...over,
});

function frame(state: ResourceState<string>) {
  return render(
    <WidgetFrame
      id="frame-under-test"
      title="Fleet state"
      windowText="24 h"
      badge="Partial"
      controls={<button type="button">1 h</button>}
      link={{ href: "/fleet", label: "Fleet" }}
      what="the fleet state"
      permission="read:fleet"
      state={state}
    >
      {({ data }) => <p>{data}</p>}
    </WidgetFrame>,
  );
}

describe("WidgetFrame", () => {
  it.each<ResourceState<string>>([{ status: "idle" }, { status: "loading" }])(
    "renders $status as polite loading text",
    (state) => {
      const view = frame(state);
      expect(screen.getByRole("status")).toHaveTextContent("Loading the fleet state…");
      expect(screen.getByRole("status")).toHaveAttribute("aria-live", "polite");
      view.unmount();
    },
  );

  it("renders forbidden with the required permission", () => {
    frame({ status: "forbidden", httpStatus: 403 });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Your role cannot read the fleet state (permission read:fleet). Nothing was changed.",
    );
  });

  it("renders an error with the failure and recovery action, never exception text", () => {
    frame({ status: "error", httpStatus: 503 });
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("The backend answered with HTTP 503");
    expect(alert).toHaveTextContent("check that the backend is reachable");
    expect(alert.querySelectorAll("p")).toHaveLength(2);
    expect(alert.querySelector(".widget-error-title strong")).toHaveTextContent(
      "The fleet state could not be read.",
    );
    expect(alert.querySelector(".widget-error-action strong")).toBeNull();
  });

  it("uses the same two-part error structure at panel and KPI density", () => {
    const panel = frame({ status: "error", httpStatus: 503 });
    const panelParts = Array.from(
      panel.container.querySelectorAll(".widget-error > p"),
      (node) => node.className,
    );
    expect(panelParts).toEqual(["widget-error-title", "widget-error-action"]);
    panel.unmount();

    const kpi = render(
      <WidgetFrame
        variant="kpi"
        id="kpi-error"
        title="Fleet state"
        link={{ href: "/fleet", label: "Fleet" }}
        what="the fleet state"
        permission="read:fleet"
        state={{ status: "error", httpStatus: 503 }}
      >
        {() => null}
      </WidgetFrame>,
    );
    expect(
      Array.from(
        kpi.container.querySelectorAll(".widget-error > p"),
        (node) => node.className,
      ),
    ).toEqual(panelParts);
  });

  it("renders ok data inside the shared title, control and drill-down chrome", () => {
    frame(ok());
    expect(screen.getByRole("heading", { name: "Fleet state 24 h" })).toBeInTheDocument();
    expect(screen.getByText("Partial")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "1 h" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fleet" })).toHaveAttribute("href", "/fleet");
    expect(screen.getByText("answer")).toBeInTheDocument();
  });

  it("keeps ok data and names a failed refresh with the received time", () => {
    frame(ok({ refreshFailed: { httpStatus: 502 } }));
    expect(screen.getByText("answer")).toBeInTheDocument();
    expect(screen.getByText(/latest refresh failed \(HTTP 502\)/i)).toHaveTextContent(
      "Showing the data received at 13:58:00 UTC",
    );
  });

  it("renders every honesty qualifier in words in both established densities", () => {
    const qualifiers: Qualifier[] = [
      { kind: "INSUFFICIENT_DATA", reason: "No source has a baseline." },
      { kind: "PARTIAL", have: 4, of: 6, unit: "intervals", text: "4 of 6 intervals collected" },
      { kind: "TRUNCATED", shown: 200, total: 345, text: "First 200 of 345" },
      { kind: "NOT_COLLECTED", reason: "No snapshot has been collected." },
    ];
    const view = render(<WidgetQualifiers qualifiers={qualifiers} />);
    expect(screen.getByText("Insufficient data").closest("li")).toHaveTextContent(
      "Insufficient data: No source has a baseline.",
    );
    expect(screen.getByText("Partial").closest("li")).toHaveTextContent(
      "Partial: 4 of 6 intervals collected",
    );
    expect(screen.getByText("Truncated").closest("li")).toHaveTextContent(
      "Truncated: First 200 of 345",
    );
    expect(screen.getByText("Not collected").closest("li")).toHaveTextContent(
      "Not collected: No snapshot has been collected.",
    );
    view.unmount();

    render(<WidgetQualifiers variant="kpi" qualifiers={qualifiers} />);
    expect(screen.getByText("4 of 6 intervals")).toBeInTheDocument();
    expect(screen.getByText("First 200 of 345")).toBeInTheDocument();
  });
});

describe("WidgetFrame, the UR.5.4 state union", () => {
  const view = (state: WidgetState<string>, variant: "kpi" | "panel" = "panel") =>
    render(
      <WidgetFrame
        id="union"
        title="Union"
        link={{ href: "/x", label: "Open" }}
        what="the union data"
        permission="read:fleet"
        variant={variant}
        state={state}
      >
        {({ data }) => <p>{data}</p>}
      </WidgetFrame>,
    );

  it("renders loading as polite text", () => {
    view({ kind: "loading" });
    expect(screen.getByRole("status")).toHaveTextContent("Loading the union data…");
  });

  it("renders ok with its data and no caveat", () => {
    view({ kind: "ok", data: "answer", asOf: "2026-10-14T13:58:00Z" });
    expect(screen.getByText("answer")).toBeInTheDocument();
    expect(screen.queryByText(/Stale/)).toBeNull();
  });

  it("renders empty as its reason", () => {
    view({ kind: "empty", reason: "Nothing is open." });
    expect(screen.getByRole("status")).toHaveTextContent("Nothing is open.");
    expect(screen.queryByText("answer")).toBeNull();
  });

  it("renders no_data as a lack of measurement, never as data", () => {
    view({ kind: "no_data", reason: "No complete bucket in the window." });
    expect(screen.getByRole("status")).toHaveTextContent(
      "No data. No complete bucket in the window.",
    );
    expect(screen.queryByText("answer")).toBeNull();
  });

  it("renders not_collected with its reason", () => {
    view({ kind: "not_collected", reason: "Collection has not run yet." });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Not collected. Collection has not run yet.",
    );
  });

  it("renders stale with the data and how old it is", () => {
    view({ kind: "stale", data: "old answer", asOf: "2026-10-14T09:00:00Z", ageSeconds: 10_800 });
    expect(screen.getByText("old answer")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(/Stale: the latest data is from .*3 h old/);
  });

  it("renders error with the widget's own action and no exception text", () => {
    view({ kind: "error", action: "Check the collector, then refresh." });
    expect(screen.getByRole("alert")).toHaveTextContent("The union data could not be read.");
    expect(screen.getByRole("alert")).toHaveTextContent("Check the collector, then refresh.");
  });

  it("renders forbidden with the permission and no data", () => {
    view({ kind: "forbidden", permission: "read:offenses" });
    expect(screen.getByRole("status")).toHaveTextContent(
      "Your role cannot read the union data (permission read:offenses). Nothing was changed.",
    );
  });

  it("renders every kind in the compact KPI variant too", () => {
    const states: WidgetState<string>[] = [
      { kind: "loading" },
      { kind: "empty", reason: "e" },
      { kind: "no_data", reason: "n" },
      { kind: "not_collected", reason: "c" },
      { kind: "error", action: "a" },
      { kind: "forbidden", permission: "p" },
    ];
    for (const state of states) {
      const { container, unmount } = view(state, "kpi");
      expect(container.querySelector(".kpi-state")).not.toBeNull();
      unmount();
    }
  });

  it("offers Remove in place of the link while a dashboard is being customized", () => {
    const onRemove = vi.fn();
    render(
      <WidgetEditContext.Provider value={{ onRemove }}>
        <WidgetFrame
          id="edit"
          title="Edited"
          link={{ href: "/x", label: "Open" }}
          what="x"
          permission="read:fleet"
          state={{ kind: "ok", data: "d", asOf: null }}
        >
          {({ data }) => <p>{data}</p>}
        </WidgetFrame>
      </WidgetEditContext.Provider>,
    );
    expect(screen.queryByRole("link")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Remove Edited" }));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });
});
