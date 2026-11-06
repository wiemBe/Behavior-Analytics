// SOC-3b: the Related activity section. Each list has its own state and wording, a missing
// measurement is never drawn as zero, and the pad lives in the URL.

import { act, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RelatedActivitySection, type RelatedRead } from "./RelatedActivity";
import type {
  RelatedActivity,
  RelatedAnomaly,
  RelatedOffense,
  RelatedPad,
  RelatedRule,
  RelatedSection,
} from "@/lib/api";

const NOW = Date.parse("2026-11-05T19:05:00Z");

function offense(over: Partial<RelatedOffense> = {}): RelatedOffense {
  return {
    qradar_offense_id: 1501,
    description: "Multiple login failures followed by a success",
    status: "OPEN",
    magnitude: 7,
    severity: 6,
    start_time: "2026-11-05T18:12:00Z",
    last_updated_time: "2026-11-05T18:58:00Z",
    close_time: null,
    offense_type_name: "Username",
    rule_count: 2,
    captured_at: "2026-11-05T19:00:00Z",
    ...over,
  };
}

function other(over: Partial<RelatedAnomaly> = {}): RelatedAnomaly {
  return {
    family: "log_source",
    id: "x-1",
    type: "VOLUME_DROP",
    lifecycle_state: "OPEN",
    severity: "HIGH",
    start: "2026-11-05T18:20:00Z",
    end: "2026-11-05T18:50:00Z",
    triage_status: "IN_PROGRESS",
    ...over,
  };
}

function rule(over: Partial<RelatedRule> = {}): RelatedRule {
  return {
    id: "r-1",
    qradar_id: 701,
    name: "Excessive Failed Logins",
    type: "EVENT",
    related_offenses: 3,
    matched_events: 120,
    measurement: "EXACT",
    ...over,
  };
}

function section<T>(items: T[], over: Partial<RelatedSection<T>> = {}): RelatedSection<T> {
  return { state: "OK", reason_code: null, total: items.length, items, ...over };
}

function activity(over: Partial<RelatedActivity> = {}, pad: RelatedPad = "2h"): RelatedActivity {
  return {
    state: "OK",
    reason_code: null,
    anchor: {
      family: "log_source",
      anomaly_id: "a-1",
      log_source_id: "s-1",
      qradar_log_source_id: 9001,
      log_source_name: "LAB Firewall",
      window_start: "2026-11-05T16:30:00Z",
      window_end: "2026-11-05T20:30:00Z",
      pad,
    },
    offenses: section([offense()]),
    anomalies: section([other()]),
    rules: section([rule()]),
    ...over,
  };
}

async function show(read: RelatedRead, pad: RelatedPad = "2h") {
  await act(async () => {
    render(
      <RelatedActivitySection
        anomalyId="a-1"
        pad={pad}
        read={Promise.resolve(read)}
        nowMs={NOW}
      />,
    );
  });
}

const ok = (data: RelatedActivity): RelatedRead => ({ kind: "ok", data });

function block(name: string): HTMLElement {
  return screen.getByRole("heading", { level: 3, name: new RegExp(`^${name}`) }).closest(
    ".related-block",
  ) as HTMLElement;
}

describe("the heading and the pad", () => {
  it("states the window in UTC and the pad", async () => {
    await show(ok(activity()));
    expect(
      screen.getByRole("heading", { level: 2, name: "Related activity, 16:30–20:30 UTC (±2 h)" }),
    ).toBeInTheDocument();
  });

  it("names both days when the window crosses midnight", async () => {
    const data = activity();
    data.anchor.window_start = "2026-11-05T22:00:00Z";
    data.anchor.window_end = "2026-11-06T02:00:00Z";
    await show(ok(data));
    expect(
      screen.getByRole("heading", {
        level: 2,
        name: "Related activity, 2026-11-05 22:00 – 2026-11-06 02:00 UTC (±2 h)",
      }),
    ).toBeInTheDocument();
  });

  it("keeps the pad in the URL: every choice is a link with ?pad= and the current one is marked", async () => {
    await show(ok(activity({}, "1h")), "1h");
    const nav = screen.getByRole("navigation", { name: "Window padding" });
    const links = within(nav).getAllByRole("link");
    expect(links.map((a) => a.textContent)).toEqual(["30 min", "1 h", "2 h", "6 h"]);
    expect(links.map((a) => a.getAttribute("href"))).toEqual([
      "/anomalies/a-1?pad=30m#related",
      "/anomalies/a-1?pad=1h#related",
      "/anomalies/a-1?pad=2h#related",
      "/anomalies/a-1?pad=6h#related",
    ]);
    expect(within(nav).getByRole("link", { name: "1 h" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "2 h" })).not.toHaveAttribute("aria-current");
    expect(
      screen.getByRole("heading", { level: 2, name: /\(±1 h\)$/ }),
    ).toBeInTheDocument();
  });
});

describe("offenses", () => {
  it("lists id, description, magnitude, status, start and last update in UTC, linked to the offense page", async () => {
    await show(ok(activity()));
    const b = block("Offenses on this source");
    const row = within(b).getAllByRole("row")[1];
    expect(within(row).getByRole("link", { name: "Open offense 1501" })).toHaveAttribute(
      "href",
      "/offenses/1501",
    );
    expect(within(row).getByText("Multiple login failures followed by a success")).toBeInTheDocument();
    expect(within(row).getByText("7")).toBeInTheDocument();
    expect(within(row).getByText("OPEN")).toBeInTheDocument();
    expect(within(row).getByText("11-05 18:12")).toBeInTheDocument();
    expect(within(row).getByText("11-05 18:58")).toBeInTheDocument();
    expect(within(b).getByText("Started (UTC)")).toBeInTheDocument();
  });

  it("says 'N of M' when more match than are shown, and words the truncation", async () => {
    await show(
      ok(
        activity({
          offenses: section([offense()], {
            state: "TRUNCATED",
            reason_code: "related_activity.reason.items_truncated",
            total: 34,
          }),
        }),
      ),
    );
    const b = block("Offenses on this source");
    expect(within(b).getByText("1 of 34")).toBeInTheDocument();
    expect(within(b).getByText(/The list holds the first 1 of 34/)).toBeInTheDocument();
    expect(within(b).getByRole("table")).toBeInTheDocument();
  });

  it("an empty OK list says none, and claims no more than offense collection covers", async () => {
    await show(ok(activity({ offenses: section([]) })));
    const b = block("Offenses on this source");
    expect(within(b).getByText(/No offense on this source in this window/)).toBeInTheDocument();
    expect(within(b).getByText(/none that ended before offense collection started/)).toBeInTheDocument();
    expect(within(b).queryByRole("table")).not.toBeInTheDocument();
  });

  it("NOT_COLLECTED is not an empty list: it says nothing was collected and what to do, and draws no count", async () => {
    await show(
      ok(
        activity({
          offenses: section([], {
            state: "NOT_COLLECTED",
            reason_code: "related_activity.reason.offense_not_collected",
          }),
        }),
      ),
    );
    const b = block("Offenses on this source");
    expect(within(b).getByText(/Offense collection has not run for this console/)).toBeInTheDocument();
    expect(within(b).getByText(/Ask an administrator/)).toBeInTheDocument();
    expect(within(b).queryByText(/No offense on this source in this window/)).not.toBeInTheDocument();
    expect(within(b).queryByText("0")).not.toBeInTheDocument();
    expect(within(b).queryByRole("table")).not.toBeInTheDocument();
  });

  it("STALE keeps the list and states the age of the newest snapshot beside its UTC time", async () => {
    await show(
      ok(
        activity({
          offenses: section([offense({ captured_at: "2026-11-05T18:25:00Z" })], {
            state: "STALE",
            reason_code: "related_activity.reason.offense_stale",
          }),
        }),
      ),
    );
    const b = block("Offenses on this source");
    expect(within(b).getByText(/Offense collection is behind/)).toBeInTheDocument();
    expect(
      within(b).getByText(/captured 40 min ago \(2026-11-05 18:25:00 UTC\)/),
    ).toBeInTheDocument();
    expect(within(b).getByRole("table")).toBeInTheDocument();
  });

  it("STALE with no listed snapshot does not invent an age", async () => {
    await show(
      ok(
        activity({
          offenses: section([], { state: "STALE", reason_code: "related_activity.reason.offense_stale" }),
        }),
      ),
    );
    const b = block("Offenses on this source");
    expect(within(b).getByText(/the age of the collection cannot be stated/)).toBeInTheDocument();
    expect(within(b).queryByText(/No offense on this source/)).not.toBeInTheDocument();
  });

  it("PARTIAL keeps the list and warns it may be incomplete", async () => {
    await show(
      ok(activity({ offenses: section([offense()], { state: "PARTIAL", reason_code: "x" }) })),
    );
    const b = block("Offenses on this source");
    expect(within(b).getByText(/may be incomplete/)).toBeInTheDocument();
    expect(within(b).getByRole("table")).toBeInTheDocument();
  });

  it("a magnitude the snapshot lacks is a dash, not 0", async () => {
    await show(ok(activity({ offenses: section([offense({ magnitude: null })]) })));
    const row = within(block("Offenses on this source")).getAllByRole("row")[1];
    expect(within(row).queryByText("0")).not.toBeInTheDocument();
    expect(within(row).getAllByText("—").length).toBeGreaterThan(0);
  });
});

describe("other anomalies", () => {
  it("shows the detector's name, the lifecycle state and the triage status as separate labels, linked", async () => {
    await show(ok(activity()));
    const b = block("Other anomalies on this source");
    const head = within(b).getAllByRole("columnheader").map((h) => h.textContent);
    expect(head).toEqual(["Anomaly", "Detector state", "Triage", "Started (UTC)"]);
    const row = within(b).getAllByRole("row")[1];
    expect(within(row).getByRole("link", { name: "Volume drop" })).toHaveAttribute("href", "/anomalies/x-1");
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("OPEN");
    expect(cells[2]).toHaveTextContent("In progress");
    expect(cells[1]).not.toHaveTextContent("In progress");
    expect(cells[2]).not.toHaveTextContent("OPEN");
    expect(within(row).getByText("11-05 18:20")).toBeInTheDocument();
  });

  it("names the family and uses the family's own detector catalog", async () => {
    await show(
      ok(
        activity({
          anomalies: section([
            other({ id: "w-1", family: "waf", type: "WAF_BLOCK_RATIO_SPIKE" }),
            other({ id: "p-1", family: "property_watch", type: "PW_NEW_VALUE" }),
          ]),
        }),
      ),
    );
    const b = block("Other anomalies on this source");
    expect(within(b).getByRole("link", { name: "Block-ratio spike" })).toHaveAttribute("href", "/anomalies/w-1");
    expect(within(b).getByRole("link", { name: "New value" })).toHaveAttribute("href", "/anomalies/p-1");
    expect(within(b).getByText("WAF")).toBeInTheDocument();
    expect(within(b).getByText("Property watch")).toBeInTheDocument();
  });

  it("a custom-rule breach is not linked and does not claim a triage status", async () => {
    await show(
      ok(
        activity({
          anomalies: section([
            other({ id: "c-1", family: "custom_rule", type: "CUSTOM_RULE_BREACH", triage_status: "NEW" }),
          ]),
        }),
      ),
    );
    const row = within(block("Other anomalies on this source")).getAllByRole("row")[1];
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
    expect(within(row).getByText("Custom rule breach")).toBeInTheDocument();
    expect(within(row).getByText("Not available")).toBeInTheDocument();
    expect(within(row).queryByText("New")).not.toBeInTheDocument();
  });

  it("empty and truncated", async () => {
    await show(ok(activity({ anomalies: section([]) })));
    expect(
      within(block("Other anomalies on this source")).getByText("No other anomaly on this source in this window."),
    ).toBeInTheDocument();
  });

  it("TRUNCATED shows the total", async () => {
    await show(
      ok(
        activity({
          anomalies: section([other()], { state: "TRUNCATED", reason_code: "x", total: 31 }),
        }),
      ),
    );
    expect(within(block("Other anomalies on this source")).getByText("1 of 31")).toBeInTheDocument();
  });
});

describe("rules", () => {
  it("shows name, related offenses and matched events with their measurement", async () => {
    await show(ok(activity()));
    const row = within(block("Rules from those offenses")).getAllByRole("row")[1];
    expect(within(row).getByRole("link", { name: "Excessive Failed Logins" })).toHaveAttribute(
      "href",
      "/rules/r-1",
    );
    const cells = within(row).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("3");
    expect(cells[2]).toHaveTextContent("120");
    expect(cells[2]).toHaveTextContent("Exact");
    expect(cells[1]).toHaveClass("num");
    expect(cells[2]).toHaveClass("num");
  });

  it("null matched events read 'Not measured' with the item's measurement, never 0", async () => {
    await show(
      ok(
        activity({
          rules: section([rule({ id: "r-2", qradar_id: 702, name: "Beaconing", matched_events: null, measurement: "NOT_COLLECTED" })], {
            state: "PARTIAL",
            reason_code: "related_activity.reason.rules_partial",
          }),
        }),
      ),
    );
    const b = block("Rules from those offenses");
    const row = within(b).getAllByRole("row")[1];
    const events = within(row).getAllByRole("cell")[2];
    expect(events).toHaveTextContent("Not measured");
    expect(events).toHaveTextContent("Not collected");
    expect(events).not.toHaveTextContent(/\b0\b/);
    expect(within(b).getByText(/that is not zero/)).toBeInTheDocument();
  });

  it("a measured zero is a 0 with its measurement", async () => {
    await show(ok(activity({ rules: section([rule({ matched_events: 0 })]) })));
    const events = within(within(block("Rules from those offenses")).getAllByRole("row")[1]).getAllByRole("cell")[2];
    expect(events).toHaveTextContent("0");
    expect(events).toHaveTextContent("Exact");
  });

  it("a lower bound reads '≥ N'", async () => {
    await show(ok(activity({ rules: section([rule({ matched_events: 80, measurement: "LOWER_BOUND" })]) })));
    const events = within(within(block("Rules from those offenses")).getAllByRole("row")[1]).getAllByRole("cell")[2];
    expect(events).toHaveTextContent("≥ 80");
    expect(events).toHaveTextContent("Lower bound");
  });

  it("a rule missing from the catalog says so with the QRadar id, and is not linked", async () => {
    await show(
      ok(
        activity({
          rules: section([rule({ id: null, qradar_id: 90210, name: null, type: null, matched_events: null, measurement: "NOT_COLLECTED" })]),
        }),
      ),
    );
    const row = within(block("Rules from those offenses")).getAllByRole("row")[1];
    expect(within(row).getByText("Rule not in the catalog (QRadar id 90210)")).toBeInTheDocument();
    expect(within(row).queryByRole("link")).not.toBeInTheDocument();
  });

  it("NOT_COLLECTED and STALE are worded from the offenses, and show no table when nothing is listed", async () => {
    await show(
      ok(
        activity({
          rules: section([], { state: "NOT_COLLECTED", reason_code: "related_activity.reason.rules_not_collected" }),
        }),
      ),
    );
    const b = block("Rules from those offenses");
    expect(within(b).getByText(/offenses were not collected, so the rules that fired cannot be resolved/)).toBeInTheDocument();
    expect(within(b).queryByText(/name no rule/)).not.toBeInTheDocument();
    expect(within(b).queryByRole("table")).not.toBeInTheDocument();
  });

  it("STALE keeps its rows under a warning", async () => {
    await show(
      ok(activity({ rules: section([rule()], { state: "STALE", reason_code: "related_activity.reason.rules_stale" }) })),
    );
    const b = block("Rules from those offenses");
    expect(within(b).getByText(/Offense collection is behind/)).toBeInTheDocument();
    expect(within(b).getByRole("table")).toBeInTheDocument();
  });

  it("an OK empty list says the offenses name no rule", async () => {
    await show(ok(activity({ rules: section([]) })));
    expect(within(block("Rules from those offenses")).getByText("The offenses in this window name no rule.")).toBeInTheDocument();
  });
});

describe("a section state this build does not know", () => {
  it("withholds the list and says so instead of treating it as OK", async () => {
    const data = activity();
    (data.rules as { state: string }).state = "REDACTED";
    await show(ok(data));
    const b = block("Rules from those offenses");
    expect(within(b).getByText(/does not recognise the state REDACTED/)).toBeInTheDocument();
    expect(within(b).queryByRole("table")).not.toBeInTheDocument();
  });
});

describe("the anomaly has no log source", () => {
  it("says so once, with no lists", async () => {
    const data = activity({
      state: "NOT_APPLICABLE",
      reason_code: "related_activity.reason.no_log_source",
      offenses: section([], { state: "NOT_APPLICABLE", reason_code: "x" }),
      anomalies: section([], { state: "NOT_APPLICABLE", reason_code: "x" }),
      rules: section([], { state: "NOT_APPLICABLE", reason_code: "x" }),
    });
    data.anchor.log_source_id = null;
    data.anchor.log_source_name = null;
    await show(ok(data));
    expect(screen.getByText(/has no single log source/)).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.queryByText(/No offense/)).not.toBeInTheDocument();
  });
});

describe("loading, error and forbidden", () => {
  it("shows a loading state with the pad control while the read is pending", () => {
    render(
      <RelatedActivitySection anomalyId="a-1" pad="6h" read={new Promise(() => {})} nowMs={NOW} />,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Reading the offenses, anomalies and rules");
    expect(screen.getByRole("link", { name: "6 h" })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("heading", { level: 2, name: "Related activity" })).toBeInTheDocument();
  });

  it("a failed read says what failed and what to do, and keeps the heading", async () => {
    await show({ kind: "failed", why: "error" });
    expect(screen.getByRole("alert")).toHaveTextContent(/could not be loaded/);
    expect(screen.getByRole("alert")).toHaveTextContent(/Reload the page/);
    expect(screen.getByRole("alert")).toHaveTextContent(/rest of this page is unaffected/);
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("forbidden names the permission", async () => {
    await show({ kind: "failed", why: "forbidden" });
    expect(screen.getByRole("alert")).toHaveTextContent("read:anomalies");
  });

  it("a rate limit shows its own sentence", async () => {
    await show({ kind: "failed", why: "limited", message: "Try again in 12 s." });
    expect(screen.getByRole("alert")).toHaveTextContent("Try again in 12 s.");
  });

  it("not found is worded as not found", async () => {
    await show({ kind: "failed", why: "notFound" });
    expect(screen.getByRole("alert")).toHaveTextContent(/was not found/);
  });
});
