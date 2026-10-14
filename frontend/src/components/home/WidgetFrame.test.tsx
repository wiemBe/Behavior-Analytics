import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ResourceState } from "@/hooks/useApiResource";
import type { Qualifier } from "@/lib/home/qualifiers";

import { WidgetFrame, WidgetQualifiers } from "./WidgetFrame";

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
