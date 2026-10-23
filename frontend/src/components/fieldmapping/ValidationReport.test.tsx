import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SCHEMA, fieldReport, report } from "./fixtures";
import { ValidationReport } from "./ValidationReport";

function show(over: Partial<React.ComponentProps<typeof ValidationReport>> = {}) {
  return render(
    <ValidationReport
      schema={SCHEMA}
      report={report()}
      profileVersion={4}
      pending={null}
      requiredCoverage={0.9}
      {...over}
    />,
  );
}

function row(name: RegExp) {
  return screen.getByRole("row", { name });
}

describe("ValidationReport", () => {
  it("shows the window in UTC, the event total and the version checked", () => {
    show();
    expect(screen.getByText("Window (UTC)")).toBeInTheDocument();
    expect(screen.getByText(/2026-10-12 22:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText(/2026-10-12 23:00:00 UTC/)).toBeInTheDocument();
    expect(screen.getByText("10,000")).toBeInTheDocument();
  });

  it("shows populated and plausible shares as percentages", () => {
    show();
    const r = row(/URL path/);
    expect(within(r).getByText("98.0 %")).toBeInTheDocument();
    expect(within(r).getByText("99.0 %")).toBeInTheDocument();
  });

  it("renders a missing measurement as a dash with its meaning, never 0 %", () => {
    show({
      report: report({
        total_events: null,
        fields: [fieldReport({ populated_ratio: null, plausible_ratio: null, distinct_values: null })],
      }),
    });
    const r = row(/URL path/);
    expect(within(r).getAllByText("—")).toHaveLength(3);
    expect(within(r).getAllByText(/not measured/)).toHaveLength(3);
    expect(within(r).queryByText(/0 %/)).not.toBeInTheDocument();
    expect(screen.getByText("Events in window").nextSibling).toHaveTextContent("— not measured");
  });

  it("keeps a measured zero as 0 %", () => {
    show({ report: report({ fields: [fieldReport({ status: "UNPOPULATED", populated_ratio: 0 })] }) });
    expect(within(row(/URL path/)).getByText("0.0 %")).toBeInTheDocument();
  });

  it.each([
    ["PROPERTY_REJECTED", "QRadar does not know a property with this name."],
    ["UNPOPULATED", "The property exists but no event in the window carried it."],
    ["NO_EVENTS", "The source sent no events in the window. This says nothing about the field."],
    ["QUERY_FAILED", "The check could not run. This says nothing about the field."],
  ])("explains %s in the analyst's words", (st, sentence) => {
    show({ report: report({ fields: [fieldReport({ status: st })] }) });
    expect(screen.getByText(sentence)).toBeInTheDocument();
  });

  it("words low coverage with the share and the requirement", () => {
    show({
      report: report({
        fields: [fieldReport({ status: "INSUFFICIENT_COVERAGE", populated_ratio: 0.62 })],
      }),
    });
    expect(screen.getByText("Only 62.0 % of events carry it; collection needs 90.0 %.")).toBeInTheDocument();
  });

  it("does not render NO_EVENTS or QUERY_FAILED as a failure of the field", () => {
    show({
      report: report({
        fields: [
          fieldReport({ status: "NO_EVENTS" }),
          fieldReport({ field: "METHOD", status: "QUERY_FAILED" }),
        ],
      }),
    });
    for (const r of screen.getAllByRole("row").slice(1)) {
      expect(within(r).getByText("No verdict")).toBeInTheDocument();
      expect(within(r).queryByText(/rejected|not populated|low coverage/i)).not.toBeInTheDocument();
    }
  });

  it("lists the reasons the result gives", () => {
    show({ report: report({ status: "INVALID", reasons: ["URL: PROPERTY_REJECTED (no such property)"] }) });
    expect(screen.getByRole("list", { name: "Reasons for the result" })).toHaveTextContent(
      "URL path: QRadar does not know a property with this name.",
    );
    expect(screen.queryByText(/PROPERTY_REJECTED/)).not.toBeInTheDocument();
    expect(screen.getByText("Invalid")).toBeInTheDocument();
  });

  it("says the report is for an older version", () => {
    show({ profileVersion: 5 });
    expect(
      screen.getByText("This report is for version 4; version 5 has not been validated."),
    ).toBeInTheDocument();
  });

  it("says a pending validation is running and since when", () => {
    show({ profileVersion: 5, pending: { since: "2026-10-12T23:52:00Z" } });
    const s = screen.getByText(/Validating version 5/);
    expect(s).toHaveAttribute("role", "status");
    expect(s).toHaveTextContent("Validating version 5 since 23:52 UTC");
    expect(s).toHaveTextContent(/every 5 seconds/);
  });

  it("explains that a version with no report has none, instead of showing an empty result", () => {
    show({ report: null, profileVersion: 2 });
    expect(screen.getByText(/version 2 has not been validated, so there is no report/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("shows pending even when there is no report yet", () => {
    show({ report: null, profileVersion: 1, pending: { since: null } });
    expect(screen.getByRole("status")).toHaveTextContent("Validating version 1");
  });

  it("names the property each field was checked against, or that none is mapped", () => {
    show({
      report: report({
        fields: [
          fieldReport(),
          fieldReport({ field: "VIRTUAL_HOST", property_name: null, status: "NOT_MAPPED", used_by_collection: false, populated_ratio: null, plausible_ratio: null, distinct_values: null }),
        ],
      }),
    });
    expect(within(row(/URL path/)).getByText("URL")).toBeInTheDocument();
    expect(within(row(/Virtual host/)).getByText(/no property mapped/)).toBeInTheDocument();
  });

  it("shows a status this build does not know as recorded", () => {
    show({ report: report({ fields: [fieldReport({ status: "FUTURE_STATUS" })] }) });
    expect(screen.getByText("FUTURE_STATUS")).toBeInTheDocument();
  });
});
