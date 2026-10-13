import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HISTORY, SCHEMA } from "./fixtures";
import { MappingHistory } from "./MappingHistory";

describe("MappingHistory", () => {
  it("says what is empty and what would fill it", () => {
    render(<MappingHistory schema={SCHEMA} entries={[]} />);
    expect(screen.getByText(/no changes are recorded for this profile yet/i)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("lists entries in the order served, with UTC time and actor", () => {
    render(<MappingHistory schema={SCHEMA} entries={HISTORY} />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("2026-10-12 23:05:00 UTC");
    expect(rows[0]).toHaveTextContent("system");
    expect(rows[0]).toHaveTextContent("Validated");
    expect(rows[0]).toHaveTextContent("Outcome: valid");
    expect(rows[1]).toHaveTextContent("a.rivera");
  });

  it("shows what a mapping change changed", () => {
    render(<MappingHistory schema={SCHEMA} entries={HISTORY} />);
    const changes = within(screen.getByRole("list", { name: "Mapping changes" }));
    expect(changes.getByText(/URL path:/)).toHaveTextContent("URL path: request_uri → URL");
  });

  it("shows validation reasons", () => {
    render(
      <MappingHistory
        schema={SCHEMA}
        entries={[
          { at: "2026-10-12T20:00:00Z", actor: "system", action: "waf.profile.validated", profile_version: 3,
            mapping_before: null, mapping_after: null, outcome: "INVALID", reasons: ["URL: PROPERTY_REJECTED (QRadar rejected the property WAF_URI)"] },
        ]}
      />,
    );
    expect(screen.getByRole("list", { name: "Reasons" })).toHaveTextContent("URL path: QRadar does not know a property with this name.");
  });

  it("shows an action this build does not know as recorded", () => {
    render(
      <MappingHistory
        schema={SCHEMA}
        entries={[
          { at: "2026-10-12T20:00:00Z", actor: "x", action: "waf.profile.future", profile_version: null,
            mapping_before: null, mapping_after: null, outcome: null, reasons: null },
        ]}
      />,
    );
    expect(screen.getByText("waf.profile.future")).toBeInTheDocument();
  });
});
