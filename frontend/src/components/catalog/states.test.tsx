// The states every catalog widget shares: loading, error, forbidden and the drill-down link.
// Each widget's own figures and honesty states are in its own test file.

import { render, screen } from "@testing-library/react";
import type { ComponentType } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { failed, forbidden, loading } from "@/components/home/testkit";

import { CatchallSenders } from "./CatchallSenders";
import { FleetClasses } from "./FleetClasses";
import { HealthCollection } from "./HealthCollection";
import { HealthDetection } from "./HealthDetection";
import { HealthEvidence } from "./HealthEvidence";
import { OutagePolicy } from "./OutagePolicy";
import { RecentWafAnomalies } from "./RecentWafAnomalies";
import { WafSources } from "./WafSources";

const res = vi.hoisted(() => ({ state: {} as unknown }));
vi.mock("@/hooks/useApiResource", () => ({ useApiResource: () => res.state }));

const CASES: [string, ComponentType, string, string, string, RegExp][] = [
  ["health.detection", HealthDetection, "/coverage", "read:coverage", "Coverage", /detection coverage/],
  ["health.collection", HealthCollection, "/admin/search-cadence", "read:search_cadence", "Cadence", /search cadence/],
  ["health.evidence", HealthEvidence, "/anomalies?evidence_status=FAILED", "read:anomalies", "Failed", /evidence counts/],
  ["table.catchall_senders", CatchallSenders, "/fleet/catchall", "read:fleet", "Review", /catch-all senders/],
  ["table.fleet_classes", FleetClasses, "/fleet?window=24h", "read:fleet", "Fleet", /fleet summary/],
  ["table.outage_policy", OutagePolicy, "/admin/outage-policy", "read:fleet", "Policy", /outage policy/],
  ["waf.sources", WafSources, "/behavior/waf", "read:anomalies", "WAF", /WAF sources/],
  [
    "table.recent_waf_anomalies",
    RecentWafAnomalies,
    "/anomalies?subject_kind=WAF_ENDPOINT&active_only=true",
    "read:anomalies",
    "View all",
    /active WAF anomalies/,
  ],
];

beforeEach(() => {
  res.state = loading;
});

describe.each(CASES)("%s", (_type, Widget, href, permission, linkName, what) => {
  it("is polite loading text with the drill-down already there", () => {
    render(<Widget />);
    expect(screen.getByRole("status")).toHaveTextContent(/^Loading/);
    expect(screen.getByRole("link", { name: linkName })).toHaveAttribute("href", href);
  });

  it("says what failed and what to do, with no exception text", () => {
    res.state = failed(500);
    render(<Widget />);
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be read/);
    expect(screen.getByRole("alert")).toHaveTextContent(/Refresh/);
    expect(screen.getByRole("alert")).toHaveTextContent(/HTTP 500/);
  });

  it("names the permission when the role cannot read it", () => {
    res.state = forbidden;
    render(<Widget />);
    expect(screen.getByRole("status")).toHaveTextContent(`permission ${permission}`);
    expect(screen.getByRole("status").textContent).toMatch(what);
  });
});

it.each(["1h", "7d"] as const)("fleet classes keeps window %s", (window) => {
  render(<FleetClasses range={window} />);
  expect(screen.getByRole("link", { name: "Fleet" })).toHaveAttribute("href", `/fleet?window=${window}`);
});
