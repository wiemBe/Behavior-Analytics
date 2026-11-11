import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { body, record, openSource, SOURCES_OK } from "@/components/handover/fixtures";
import { failed, forbidden, loading, ok } from "@/components/home/testkit";

import { HandoverWidget } from "./HandoverWidget";

const res = vi.hoisted(() => ({ state: {} as unknown }));
vi.mock("@/hooks/useApiResource", () => ({ useApiResource: () => res.state }));

beforeEach(() => {
  res.state = loading;
});

describe("table.handover", () => {
  it("is loading text with the drill-down and no figure", () => {
    render(<HandoverWidget />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading/);
    expect(screen.getByRole("link", { name: "Open handover" })).toHaveAttribute("href", "/handover");
    expect(screen.queryByText(/\d/)).toBeNull();
  });

  it("says what failed, with the link still there", () => {
    res.state = failed(500);
    render(<HandoverWidget />);
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be read/);
    expect(screen.getByRole("link", { name: "Open handover" })).toHaveAttribute("href", "/handover");
  });

  it("names the permission when refused, with the link still there", () => {
    res.state = forbidden;
    render(<HandoverWidget />);
    expect(screen.getByRole("status")).toHaveTextContent("permission read:anomalies");
    expect(screen.getByRole("link", { name: "Open handover" })).toHaveAttribute("href", "/handover");
  });

  it("names the absence when no handover exists", () => {
    res.state = ok({ handover: null });
    render(<HandoverWidget />);
    expect(screen.getByText("No handover yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open handover" })).toHaveAttribute("href", "/handover");
  });

  it("shows the author, the UTC time, the note and the number of watched rows", () => {
    res.state = ok({ handover: record() });
    render(<HandoverWidget />);
    expect(screen.getByText("ada")).toBeInTheDocument();
    expect(document.querySelector("time")?.textContent).toMatch(/UTC$/);
    expect(screen.getByText(/the gateway volume followed the release/)).toBeInTheDocument();
    expect(screen.getByText("1 watched")).toBeInTheDocument();
    expect(screen.queryByText(/Not read cleanly/)).toBeNull();
  });

  it("says nothing was watched instead of drawing a zero", () => {
    res.state = ok({ handover: record({ body: body({ items: [] }) }) });
    render(<HandoverWidget />);
    expect(screen.getByText("Nothing watched")).toBeInTheDocument();
    expect(screen.queryByText(/^0 watched/)).toBeNull();
  });

  it("cuts a long note and says the snapshot is incomplete when a source was not read", () => {
    const sources = SOURCES_OK.map((s) =>
      s.kind === "offense" ? openSource("offense", { state: "FORBIDDEN", count: null, permission: "read:offenses" }) : s,
    );
    res.state = ok({ handover: record({ note: "n".repeat(300), body: body({ sources }) }) });
    render(<HandoverWidget />);
    const incomplete = screen.getByText("Not read cleanly: Offenses");
    expect(incomplete).toHaveAttribute("title", expect.stringContaining("Offenses"));
    expect(screen.getByText(/^n{240}…$/)).toBeInTheDocument();
  });

  it("is incomplete for a FAILED acted-on source too", () => {
    res.state = ok({
      handover: record({
        body: body({
          acted_on_sources: [{ kind: "alert", state: "FAILED", count: null, permission: null, reason_code: "LOAD_FAILED" }],
        }),
      }),
    });
    render(<HandoverWidget />);
    expect(screen.getByText("Not read cleanly: Alert actions")).toHaveAttribute(
      "title",
      expect.stringContaining("Alert actions"),
    );
  });

  it("names every unread source once, FAILED and FORBIDDEN alike, with no count", () => {
    const sources = SOURCES_OK.map((s) =>
      s.kind === "offense"
        ? openSource("offense", { state: "FAILED", count: null, reason_code: "LOAD_FAILED" })
        : s.kind === "anomaly"
          ? openSource("anomaly", { state: "FORBIDDEN", count: null, permission: "read:anomalies" })
          : s,
    );
    res.state = ok({ handover: record({ body: body({ sources, items: [] }) }) });
    render(<HandoverWidget />);
    expect(screen.getByText("Not read cleanly: Anomalies, Offenses")).toBeInTheDocument();
  });

  it("has no stale state: an old record is still just the record", () => {
    res.state = ok({ handover: record({ written_at: "2020-01-01T00:00:00Z" }) }, { receivedAt: "2026-11-11T07:00:00Z" });
    render(<HandoverWidget />);
    expect(screen.queryByText(/stale/i)).toBeNull();
  });
});
