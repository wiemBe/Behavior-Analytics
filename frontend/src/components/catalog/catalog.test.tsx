// Each catalog widget's figures, units, honesty states and drill-downs, against keyed reads.

import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { failed, forbidden, ok } from "@/components/home/testkit";
import { homeReads } from "@/lib/api";
import { anomaly, fleetClass, fleet, page, summary } from "@/lib/home/fixtures";

import { CatchallSenders } from "./CatchallSenders";
import { cadence, coverage, outage, outageClass, sender, wafSource } from "./fixtures";
import { FleetClasses } from "./FleetClasses";
import { HealthCollection } from "./HealthCollection";
import { HealthDetection } from "./HealthDetection";
import { HealthEvidence } from "./HealthEvidence";
import { OutagePolicy } from "./OutagePolicy";
import { RecentWafAnomalies } from "./RecentWafAnomalies";
import { WafSources } from "./WafSources";

const res = vi.hoisted(() => ({ byKey: {} as Record<string, unknown> }));
vi.mock("@/hooks/useApiResource", () => ({
  useApiResource: (key: string | null) => res.byKey[key ?? ""] ?? { status: "loading" },
}));

const NOW = Date.parse("2026-10-14T14:00:00Z");
beforeEach(() => {
  res.byKey = {};
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe("health.detection", () => {
  const COV = homeReads.coverageSummary().key;
  const RULES = homeReads.ruleHealthSummary().key;

  it("leads with the covered share and its basis, then the verdicts", () => {
    res.byKey = {
      [COV]: ok(coverage()),
      [RULES]: ok([
        { status: "HEALTHY", count: 120 },
        { status: "NOISY", count: 7 },
        { status: "NEVER_OBSERVED", count: 31 },
      ]),
    };
    render(<HealthDetection />);
    expect(screen.getByText("25.0 %")).toBeInTheDocument();
    expect(screen.getByText("50 of 200 techniques covered")).toBeInTheDocument();
    const rules = screen.getByRole("table", { name: "Rule health by verdict" });
    expect(within(rules).getByText("120")).toBeInTheDocument();
    expect(within(rules).getByText("NEVER OBSERVED")).toBeInTheDocument();
  });

  it("words every verdict in one case, as the coverage page does", () => {
    res.byKey = {
      [COV]: ok(coverage()),
      [RULES]: ok([
        { status: "HEALTHY", count: 3 },
        { status: "DEGRADED", count: 5 },
        { status: "SILENT", count: 2 },
        { status: "DEPENDENCY_DEGRADED", count: 1 },
      ]),
    };
    render(<HealthDetection />);
    const rules = screen.getByRole("table", { name: "Rule health by verdict" });
    for (const w of ["HEALTHY", "DEGRADED", "SILENT", "DEPENDENCY DEGRADED"]) {
      expect(within(rules).getByText(w)).toBeInTheDocument();
    }
    expect(within(rules).queryByText(/^(Healthy|Degraded|Silent)$/)).toBeNull();
  });

  it("lists the other technique statuses, never COVERED again", () => {
    res.byKey = { [COV]: ok(coverage()), [RULES]: ok([]) };
    render(<HealthDetection />);
    const note = screen.getByText(/Other statuses:/);
    expect(note).toHaveTextContent("MISSING 100");
    expect(note).toHaveTextContent("NOT EVALUATED 20");
    expect(note).not.toHaveTextContent(/Covered/i);
  });

  it("says coverage was not evaluated instead of 0 % when no technique is mapped", () => {
    res.byKey = {
      [COV]: ok(coverage({ total_techniques: 0, by_status: {}, covered_ratio: 0 })),
      [RULES]: ok([]),
    };
    render(<HealthDetection />);
    expect(screen.getByText(/Coverage has not been evaluated/)).toBeInTheDocument();
    expect(screen.queryByText(/%/)).toBeNull();
  });

  it("keeps coverage and writes the rule half as a dash with the reason when rules are refused", () => {
    res.byKey = { [COV]: ok(coverage()), [RULES]: forbidden };
    render(<HealthDetection />);
    expect(screen.getByText("25.0 %")).toBeInTheDocument();
    expect(screen.getByText(/Rule health is unavailable \(your role cannot read it\)/)).toBeInTheDocument();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

describe("health.collection", () => {
  const KEY = homeReads.searchCadence().key;

  it("shows cadence with its unit, the last run and how late", () => {
    res.byKey = {
      [KEY]: ok([
        cadence(),
        cadence({
          family: "WAF_COLLECTION",
          cadence_seconds: 900,
          last_run: { at: "2026-10-14T12:00:00Z", outcome: "ok", searches_sent: 1, buckets_collected: 1, split_count: 0 },
        }),
      ]),
    };
    render(<HealthCollection />);
    expect(screen.getByText("1 of 2")).toBeInTheDocument();
    const rows = screen.getAllByRole("row");
    expect(within(rows[1]).getByText("15 min")).toBeInTheDocument();
    expect(within(rows[1]).getByText("On time")).toBeInTheDocument();
    // Last ran 120 min ago, due after 15 min + 60 s: 1 h 44 min late.
    expect(within(rows[2]).getByText("1 h 44 min")).toBeInTheDocument();
  });

  it("lets the family name wrap instead of ellipsising it", () => {
    res.byKey = { [KEY]: ok([cadence()]) };
    render(<HealthCollection />);
    const name = screen.getAllByRole("row")[1].querySelector("td.tw-name");
    expect(name).toHaveClass("cw-wrap");
    expect(name?.querySelector("span")).toHaveAttribute("title");
  });

  it("calls a run within the scheduler's one-minute tolerance on time", () => {
    // Due after 900 s + 60 s settle; 30 s past that is inside the tolerance.
    const at = new Date(NOW - (900 + 60 + 30) * 1000).toISOString();
    res.byKey = { [KEY]: ok([cadence({ last_run: { at, outcome: "ok", searches_sent: 1, buckets_collected: 1, split_count: 0 } })]) };
    render(<HealthCollection />);
    expect(within(screen.getAllByRole("row")[1]).getByText("On time")).toBeInTheDocument();
  });

  it("writes a family that never ran as a dash, never on time", () => {
    res.byKey = { [KEY]: ok([cadence({ last_run: null })]) };
    render(<HealthCollection />);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).queryByText("On time")).toBeNull();
    expect(within(row).getAllByText("—").length).toBe(2);
    expect(screen.getByText(/have not run since the upgrade/)).toBeInTheDocument();
  });

  it("explains an empty answer", () => {
    res.byKey = { [KEY]: ok([]) };
    render(<HealthCollection />);
    expect(screen.getByText(/No search family was returned/)).toBeInTheDocument();
  });
});

describe("health.evidence", () => {
  const KEY = homeReads.anomalySummary().key;

  it("shows the pending and failed counts in anomalies", () => {
    res.byKey = { [KEY]: ok(summary({ evidence_pending: 4, evidence_failed: 2 })) };
    render(<HealthEvidence />);
    const row = screen.getByRole("cell", { name: "Failed" }).closest("tr")!;
    expect(within(row).getByText("2")).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Anomalies" })).toBeInTheDocument();
    expect(screen.getByText(/open them to see why/)).toBeInTheDocument();
  });

  it("says nothing failed only when the count is a measured zero", () => {
    res.byKey = { [KEY]: ok(summary()) };
    render(<HealthEvidence />);
    expect(screen.getByText("No evidence collection has failed.")).toBeInTheDocument();
  });
});

describe("table.catchall_senders", () => {
  const KEY = homeReads.newCatchallSenders(200).key;

  it("lists the newest first, five at most, with unknown for an unnamed 24 h count", () => {
    const items = Array.from({ length: 7 }, (_, i) =>
      sender({
        id: `c${i}`,
        identifier: `10.0.0.${i}`,
        first_seen_at: `2026-10-14T0${i}:00:00Z`,
        events_24h: i === 6 ? null : 100 + i,
      }),
    );
    res.byKey = { [KEY]: ok(page(items, 7)) };
    render(<CatchallSenders />);
    const body = screen.getAllByRole("row").slice(1);
    expect(body).toHaveLength(5);
    expect(within(body[0]).getByText("10.0.0.6")).toBeInTheDocument();
    expect(within(body[0]).getByText("unknown")).toBeInTheDocument();
    expect(within(body[1]).getByText(/≥ 105/)).toBeInTheDocument();
    expect(screen.getByText("7 new")).toBeInTheDocument();
    expect(screen.getByText("Showing the newest 5 of 7.")).toBeInTheDocument();
  });

  it("flags a capped read as truncated", () => {
    res.byKey = { [KEY]: ok(page([sender()], 480)) };
    render(<CatchallSenders />);
    expect(screen.getByText(/First 1 of 480/)).toBeInTheDocument();
  });

  it("says what an empty queue means", () => {
    res.byKey = { [KEY]: ok(page([], 0)) };
    render(<CatchallSenders />);
    expect(screen.getByText(/No unreviewed senders/)).toBeInTheDocument();
  });
});

describe("table.fleet_classes", () => {
  const KEY = homeReads.fleetSummary("24h").key;

  it("sorts by mean EPS, writes a missing mean as a dash and marks silence", () => {
    res.byKey = {
      [KEY]: ok(
        fleet([
          fleetClass("FIREWALL", { mean_eps: 40, open_incidents: { NO_EVENTS: 2 } }),
          fleetClass("PROXY", { mean_eps: null, events: null }),
          fleetClass("DNS", { mean_eps: 55.5 }),
          fleetClass("VPN", { sources: { enabled: 0, disabled: 0 } }),
        ]),
      ),
    };
    render(<FleetClasses />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(3);
    expect(within(rows[0]).getByText("DNS")).toBeInTheDocument();
    expect(within(rows[0]).getByText("55.50")).toBeInTheDocument();
    // FIREWALL: 2 sources and 2 open silence incidents.
    expect(within(rows[1]).getAllByText("2")).toHaveLength(2);
    expect(within(rows[2]).getAllByText("—").length).toBeGreaterThan(0);
    expect(screen.getByRole("columnheader", { name: "Mean EPS" })).toBeInTheDocument();
  });

  it("writes the silence of a class with no monitored source as a dash, not 0", () => {
    res.byKey = { [KEY]: ok(fleet([fleetClass("EMAIL", { monitored_sources: 0 })])) };
    render(<FleetClasses />);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getAllByRole("cell")[3]).toHaveTextContent("—");
  });

  it("qualifies incomplete buckets as partial", () => {
    res.byKey = {
      [KEY]: ok(fleet([fleetClass("DNS", { buckets: { expected: 48, complete: 40, partial: 4, missing: 4 } })])),
    };
    render(<FleetClasses />);
    expect(screen.getByText(/40 of 48 intervals collected/)).toBeInTheDocument();
  });

  it("bounds the list and says how many classes are not shown", () => {
    const classes = (["DNS", "VPN", "WAF", "IPS", "PROXY", "EMAIL", "CLOUD", "LINUX", "WINDOWS", "OTHER"] as const).map(
      (c, i) => fleetClass(c, { mean_eps: 10 + i }),
    );
    res.byKey = { [KEY]: ok(fleet(classes)) };
    render(<FleetClasses />);
    expect(screen.getAllByRole("row")).toHaveLength(9);
    expect(screen.getByText("Showing the 8 busiest of 10 classes.")).toBeInTheDocument();
  });

  it("explains a fleet with no classified source", () => {
    res.byKey = { [KEY]: ok(fleet([])) };
    render(<FleetClasses />);
    expect(screen.getByText(/No source class has a source yet/)).toBeInTheDocument();
  });
});

describe("table.outage_policy", () => {
  const KEY = homeReads.outageSummary("24h").key;

  it("leads with the default and lists only the classes that depart from it", () => {
    res.byKey = {
      [KEY]: ok(
        outage([
          outageClass("DNS"),
          outageClass("VPN", { outage_mode: "RECORD_ONLY" }),
          outageClass("WAF", { outage_overrides: 3 }),
        ]),
      ),
    };
    render(<OutagePolicy />);
    expect(screen.getByText(/by default/).closest("p")).toHaveTextContent(/^Alert/);
    expect(screen.getByText(/by default, silence grace 1 bucket/)).toBeInTheDocument();
    expect(screen.getByText(/source overrides/)).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("WAF")).toBeInTheDocument();
    expect(within(rows[0]).getByText("3")).toBeInTheDocument();
    expect(screen.queryByText("DNS")).toBeNull();
  });

  it("says no class departs from the default only when none does", () => {
    res.byKey = { [KEY]: ok(outage([outageClass("DNS")])) };
    render(<OutagePolicy />);
    expect(screen.getByText(/No class departs from the default/)).toBeInTheDocument();
    expect(screen.getByText("0")).toBeInTheDocument();
  });
});

describe("table.outage_policy default unknown", () => {
  it("shows the unmeasured dash with its reason, never a bare 'by default'", () => {
    const body = outage([outageClass("DNS")], { outage_default: undefined as never });
    res.byKey = { [homeReads.outageSummary("24h").key]: ok(body) };
    render(<OutagePolicy />);
    expect(screen.queryByText(/by default/)).toBeNull();
    expect(screen.getAllByText(/default outage mode was not returned/).length).toBeGreaterThan(0);
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });
});

describe("table.outage_policy without classes", () => {
  it("does not count overrides it cannot see", () => {
    res.byKey = { [homeReads.outageSummary("24h").key]: ok(outage([])) };
    render(<OutagePolicy />);
    expect(screen.getByText(/No source class exists yet/)).toBeInTheDocument();
    expect(screen.queryByText(/source overrides/)).toBeNull();
  });
});

describe("waf.sources", () => {
  const KEY = homeReads.wafSources().key;
  const body = (items: ReturnType<typeof wafSource>[], total = items.length) => ({
    items,
    total,
    limit: 200,
    offset: 0,
    range_start: "2026-10-13T14:00:00Z",
    range_end: "2026-10-14T14:00:00Z",
  });

  it("shows the verdict, coverage and endpoints against the ceiling, attention first", () => {
    res.byKey = {
      [KEY]: ok(
        body([
          wafSource(),
          wafSource({ log_source_id: "w2", log_source_name: "waf-dmz", validation_status: "INVALID", validation_current: false }),
        ]),
      ),
    };
    render(<WafSources />);
    const rows = screen.getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("waf-dmz")).toBeInTheDocument();
    expect(within(rows[0]).getByText("INVALID")).toBeInTheDocument();
    expect(within(rows[1]).getByText("92.0%")).toBeInTheDocument();
    expect(within(rows[1]).getByText("120")).toBeInTheDocument();
    expect(within(rows[1]).getByText("of 500")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "waf-edge-01" })).toHaveAttribute(
      "href",
      "/behavior/waf/sources/w1",
    );
  });

  it("writes a source with no collected bucket as a dash, not 0 %", () => {
    res.byKey = { [KEY]: ok(body([wafSource({ latest_bucket: null })])) };
    render(<WafSources />);
    const row = screen.getAllByRole("row")[1];
    expect(within(row).getByText("—")).toBeInTheDocument();
    expect(within(row).queryByText(/%/)).toBeNull();
  });

  it("flags a capped list as truncated", () => {
    res.byKey = { [KEY]: ok(body([wafSource()], 250)) };
    render(<WafSources />);
    expect(screen.getByText(/First 1 of 250/)).toBeInTheDocument();
  });

  it("explains an empty list", () => {
    res.byKey = { [KEY]: ok(body([])) };
    render(<WafSources />);
    expect(screen.getByText(/No WAF source is registered/)).toBeInTheDocument();
  });
});

describe("table.recent_waf_anomalies", () => {
  const LIST = homeReads.activeWafAnomalies(5).key;

  it("reads only WAF endpoint anomalies and names each by its endpoint", () => {
    expect(LIST).toBe("/anomalies?subject_kind=WAF_ENDPOINT&active_only=true&limit=5");
    res.byKey = {
      [LIST]: ok(
        page(
          [
            anomaly({
              subject_kind: "WAF_ENDPOINT",
              anomaly_type: "WAF_BLOCK_RATIO_SPIKE",
              log_source_name: "waf-edge-01",
              waf_endpoint: {
                id: "e1",
                normalized_endpoint: "/portal/login",
                http_method: "POST",
                virtual_host: null,
                virtual_host_available: false,
                normalizer_version: 1,
              },
            }),
          ],
          3,
        ),
      ),
    };
    render(<RecentWafAnomalies />);
    expect(screen.getByRole("link", { name: "POST /portal/login" })).toBeInTheDocument();
    expect(screen.getByText("3 active")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View all" })).toHaveAttribute(
      "href",
      "/anomalies?subject_kind=WAF_ENDPOINT&active_only=true",
    );
  });

  it("does not claim a clean bill when none is active", () => {
    res.byKey = { [LIST]: ok(page([])) };
    render(<RecentWafAnomalies />);
    expect(screen.getByText(/No active WAF endpoint anomalies/)).toBeInTheDocument();
    expect(screen.getByText(/not collected, so they are not judged/)).toBeInTheDocument();
  });

  it("is an error state when the list failed", () => {
    res.byKey = { [LIST]: failed(500) };
    render(<RecentWafAnomalies />);
    expect(screen.getByRole("alert")).toHaveTextContent(/active WAF anomalies could not be read/);
  });
});
