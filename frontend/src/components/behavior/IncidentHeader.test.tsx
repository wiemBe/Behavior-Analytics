import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { IncidentHeader } from "./IncidentHeader";
import type { AnomalyDetail } from "@/lib/api";

const LONG =
  "corp-fw-dmz-edge-cluster-01.eu-central-1.prod.internal.example-bank.net : Palo Alto PA-5450 HA pair (primary, vsys1)";

function anomaly(over: Partial<AnomalyDetail> = {}): AnomalyDetail {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: LONG,
    anomaly_type: "VOLUME_DROP",
    state: "OPEN",
    severity: "CRITICAL",
    observed_value: 4.2,
    expected_value: 28.6,
    deviation_ratio: 0.15,
    robust_z: null,
    absolute_delta: -1464,
    consecutive_buckets: 6,
    confidence: 0.42,
    detected_at: "2026-10-12T09:20:00Z",
    opened_at: null,
    anomaly_start: "2026-10-12T09:15:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    baseline_version: 1,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: null,
    ...over,
  };
}

describe("IncidentHeader", () => {
  it("names the page after the source, exactly once, even when the name is very long", () => {
    render(<IncidentHeader anomaly={anomaly()} />);
    const h1s = screen.getAllByRole("heading", { level: 1 });
    expect(h1s).toHaveLength(1);
    expect(h1s[0]).toHaveTextContent(LONG);
    expect(screen.getAllByText(LONG)).toHaveLength(1);
  });

  it("sets the long source name apart from the headline-size title", () => {
    // The identity line is the primary text; the name is not set at page-title
    // size, which wrapped a host name to two 30 px lines.
    render(<IncidentHeader anomaly={anomaly()} />);
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(h1.className).toContain("incident-source");
  });

  it("puts the identity line before the source name", () => {
    render(<IncidentHeader anomaly={anomaly()} />);
    const detector = screen.getByText("Volume drop");
    const h1 = screen.getByRole("heading", { level: 1 });
    expect(detector.compareDocumentPosition(h1) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("states detector, state and severity as text", () => {
    render(<IncidentHeader anomaly={anomaly()} />);
    expect(screen.getByText("Volume drop")).toBeInTheDocument();
    expect(screen.getByText("OPEN").className).toContain("pill-strong");
    expect(screen.getByText("CRITICAL").className).toContain("pill-quiet");
  });

  it("shows the interval in UTC, with real time elements", () => {
    const { container } = render(<IncidentHeader anomaly={anomaly()} />);
    const times = container.querySelectorAll("time");
    expect(times).toHaveLength(1);
    expect(times[0]).toHaveTextContent("2026-10-12 09:15:00 UTC");
    expect(times[0]).toHaveAttribute("datetime", "2026-10-12T09:15:00Z");
  });

  it("says a still-running incident is running rather than inventing an end", () => {
    render(<IncidentHeader anomaly={anomaly({ anomaly_end: null })} />);
    expect(screen.getByText(/still running/)).toBeInTheDocument();
  });

  it("shows both ends of a closed interval", () => {
    const { container } = render(
      <IncidentHeader anomaly={anomaly({ anomaly_end: "2026-10-12T11:00:00Z", state: "RESOLVED" })} />,
    );
    expect(container.querySelectorAll("time")).toHaveLength(2);
    expect(screen.queryByText(/still running/)).toBeNull();
  });

  it("does not invent an interval for an anomaly without a start", () => {
    const { container } = render(<IncidentHeader anomaly={anomaly({ anomaly_start: null })} />);
    expect(container.querySelectorAll("time")).toHaveLength(0);
    expect(screen.getByText("Interval not recorded")).toBeInTheDocument();
  });

  it("falls back to a stated unknown source rather than an empty title", () => {
    render(<IncidentHeader anomaly={anomaly({ log_source_name: null })} />);
    expect(screen.getByRole("heading", { level: 1 })).toHaveTextContent("Unknown source");
  });

  it("flags a suppressed incident", () => {
    render(<IncidentHeader anomaly={anomaly({ suppressed: true })} />);
    expect(screen.getByText("Suppressed")).toBeInTheDocument();
  });
});
