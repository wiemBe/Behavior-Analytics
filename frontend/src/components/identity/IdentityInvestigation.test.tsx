import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { incident } from "./fixtures";
import { IdentityInvestigation } from "./IdentityInvestigation";

describe("IdentityInvestigation", () => {
  it("names the source and the event, with one summary sentence and the unit", () => {
    const { container } = render(<IdentityInvestigation anomaly={incident()} />);
    expect(container.querySelectorAll("h1")).toHaveLength(1);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("dc-01.example.test");
    expect(screen.getByText("Failed logon spike")).toBeInTheDocument();
    expect(screen.getByText(/4625 Failed logon: 180 events in the bucket against an expected 20\./)).toBeInTheDocument();
    expect(screen.getAllByText("events per 5 min").length).toBe(2);
    expect(screen.getByText("9 ×")).toBeInTheDocument();
  });

  it("lists the stored names, right-aligned counts, linked to the entity timeline", () => {
    render(<IdentityInvestigation anomaly={incident()} />);
    const table = screen.getByRole("table", { name: /Usernames stored for 4625/ });
    const link = within(table).getByRole("link", { name: "svc-backup" });
    expect(link).toHaveAttribute("href", "/entities?kind=user&value=svc-backup");
    expect(link.closest("tr")!.lastElementChild).toHaveClass("num");
    expect(screen.getByText(/Only the top 20 names per bucket are kept/)).toBeInTheDocument();
  });

  it("does not invent a name for an incident that stored none, and says why for 4625", () => {
    render(
      <IdentityInvestigation
        anomaly={incident({ identity: { event_id: 4625, usernames: [] } })}
      />,
    );
    expect(screen.getByText(/No username is stored on this incident\. For this event, names below 3 events/)).toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /Usernames stored/ })).toBeNull();
  });

  it("says the name list is partial when the evidence is", () => {
    render(<IdentityInvestigation anomaly={incident({ evidence_status: "PARTIAL" })} />);
    expect(screen.getByText(/name search was cut or did not run/)).toBeInTheDocument();
  });

  it("marks a truncated count as at least", () => {
    render(<IdentityInvestigation anomaly={incident({ observed_measurement: "LOWER_BOUND" })} />);
    expect(screen.getByText(/The search was truncated: the count is at least/)).toBeInTheDocument();
    expect(screen.getAllByText(/≥ 180/).length).toBeGreaterThan(0);
  });

  it("shows a dash, not zero, when no count or expectation was stored", () => {
    render(
      <IdentityInvestigation
        anomaly={incident({ observed_value: null, expected_value: null, deviation_ratio: null })}
      />,
    );
    expect(screen.getByText(/the count was not stored/)).toBeInTheDocument();
    expect(screen.getByText("No baseline for this detector.")).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThanOrEqual(3);
  });

  it("an unknown event id still renders as the number", () => {
    render(<IdentityInvestigation anomaly={incident({ identity: { event_id: 9999, usernames: [] } })} />);
    expect(screen.getByText(/9999: 180 events/)).toBeInTheDocument();
  });

  it("links to the source page on the event and renders the slots it is given", () => {
    render(
      <IdentityInvestigation
        anomaly={incident()}
        triage={<p>triage slot</p>}
        quiet={<p>quiet slot</p>}
      />,
    );
    expect(screen.getByRole("link", { name: "Open this source's counts" })).toHaveAttribute(
      "href",
      expect.stringMatching(/\/behavior\/identity\/sources\/.*\?event=4625$/),
    );
    expect(screen.getByText("triage slot")).toBeInTheDocument();
    expect(screen.getByText("quiet slot")).toBeInTheDocument();
  });
});
