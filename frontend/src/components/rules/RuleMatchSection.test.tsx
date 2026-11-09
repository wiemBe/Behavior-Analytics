import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { RuleMatchSectionView } from "./RuleMatchSection";
import { incident, section } from "./ruleMatchFixtures";

function show(s: Parameters<typeof RuleMatchSectionView>[0]["section"], failed = false) {
  return render(<RuleMatchSectionView section={s} failed={failed} />);
}

const status = () => document.querySelector(".match-status") as HTMLElement;

describe("the rule match-anomaly section", () => {
  it("asks its question under the heading", () => {
    show(section());
    expect(screen.getByRole("heading", { name: "Match anomalies" })).toBeInTheDocument();
    expect(
      screen.getByText("Is this rule's match rate being judged, and is there an open change?"),
    ).toBeInTheDocument();
  });

  it("error says what failed and does not render a state", () => {
    show(null, true);
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Match anomalies could not be loaded.");
    expect(alert).toHaveTextContent("The health card above is not affected.");
    expect(document.querySelector(".match-status")).toBeNull();
  });

  it("NOT_COLLECTED, collection off", () => {
    show(
      section({
        state: "NOT_COLLECTED",
        reason: "Rule-match collection is off.",
        reason_code: "rule.match_anomaly.collection_off",
        judged_through: null,
      }),
    );
    expect(status()).toHaveTextContent("Not collected");
    expect(status()).toHaveTextContent("Rule-match collection is off.");
  });

  it("NOT_COLLECTED, no window recorded", () => {
    show(
      section({
        state: "NOT_COLLECTED",
        reason: "No match window has been recorded.",
        reason_code: "rule.match_anomaly.no_window",
        judged_through: null,
      }),
    );
    expect(status()).toHaveTextContent("No match window has been recorded.");
    expect(screen.queryByText("Judged through")).toBeNull();
  });

  it("NOT_EVENT carries the evidence word", () => {
    show(
      section({
        state: "NOT_EVENT",
        reason: "Match collection does not prove a zero for this rule type (LOWER_BOUND).",
        match_evidence: "LOWER_BOUND",
        judged_through: null,
      }),
    );
    expect(status()).toHaveTextContent("Not provable");
    expect(status()).toHaveTextContent("(LOWER_BOUND)");
    expect(screen.getByText("Lower bound (matches are at least this)")).toBeInTheDocument();
  });

  it("NOT_EVENT, not measured", () => {
    show(section({ state: "NOT_EVENT", reason: "Match collection does not prove a zero for this rule type (NOT_MEASURED).", match_evidence: "NOT_MEASURED" }));
    expect(screen.getByText("Not measured")).toBeInTheDocument();
  });

  it("DISABLED names the inventory sync time in UTC", () => {
    show(
      section({
        state: "DISABLED",
        reason: "Disabled in the inventory sync at 2026-11-09 06:00 UTC. Match anomalies are not opened.",
        inventory_synced_at: "2026-11-09T06:00:00Z",
      }),
    );
    expect(status()).toHaveTextContent("Disabled");
    expect(status()).toHaveTextContent("Match anomalies are not opened.");
    const dt = screen.getByText("Inventory synced");
    expect(dt.nextElementSibling).toHaveTextContent("2026-11-09 06:00:00 UTC");
  });

  it("MONITORING_OFF names health_monitoring_enabled and says the health status does not read it", () => {
    show(
      section({
        state: "MONITORING_OFF",
        reason:
          "Match anomalies are off for this rule (health_monitoring_enabled). The health status does not read that flag.",
      }),
    );
    expect(status()).toHaveTextContent("Monitoring off");
    expect(status()).toHaveTextContent("health_monitoring_enabled");
    expect(status()).toHaveTextContent("The health status does not read that flag.");
  });

  it("MODIFIED shows QRadar's modified time", () => {
    show(
      section({
        state: "MODIFIED",
        reason: "QRadar reports this rule modified at 2026-11-09 07:30 UTC. Match anomalies are not judged across that time.",
        modified_at: "2026-11-09T07:30:00Z",
      }),
    );
    expect(status()).toHaveTextContent("Modified");
    expect(screen.getByText("Modified in QRadar").nextElementSibling).toHaveTextContent("2026-11-09 07:30:00 UTC");
  });

  it("INSUFFICIENT_DATA gives the specific reason, the counts and the window times, and no rate", () => {
    show(
      section({
        state: "INSUFFICIENT_DATA",
        reason: "The 7-day span is not fully measured (3 missing, 2 truncated, 1 failed, 0 of another width, of 168 windows).",
        window_start: "2026-11-09T09:00:00Z",
        window_end: "2026-11-09T10:00:00Z",
        judged_through: "2026-11-09T09:00:00Z",
        counts: {
          expected_windows: 168,
          complete_windows: 162,
          missing_windows: 3,
          truncated_windows: 2,
          failed_windows: 1,
          wrong_width_windows: 0,
          presence: 0.62,
          presence_minimum: 0.8,
        },
      }),
    );
    expect(status()).toHaveTextContent("Insufficient data");
    expect(status()).toHaveTextContent("The 7-day span is not fully measured");
    expect(screen.getByText("Windows in the span").nextElementSibling).toHaveTextContent("162 of 168 complete");
    expect(screen.getByText("Missing, truncated, failed windows").nextElementSibling).toHaveTextContent(
      "3 missing, 2 truncated, 1 failed",
    );
    expect(screen.getByText("Judged window").nextElementSibling).toHaveTextContent(
      "2026-11-09 09:00:00 UTC to 2026-11-09 10:00:00 UTC",
    );
    expect(screen.getByText("Windows with a match").nextElementSibling).toHaveTextContent(
      "62.0 % of baseline windows; at least 80.0 % needed",
    );
    expect(screen.queryByText("Windows of another width")).toBeNull();
  });

  it("INSUFFICIENT_DATA for a learning storm cell shows samples and weeks, never a rate", () => {
    show(
      section({
        state: "INSUFFICIENT_DATA",
        reason: "The storm cell for this weekday and slot is still learning.",
        counts: { sample_count: 3, minimum_samples: 8, visits: 2, minimum_visits: 4, median: null, median_minimum: null },
      }),
    );
    expect(screen.getByText("Samples in the cell").nextElementSibling).toHaveTextContent("3 of 8 needed");
    expect(screen.getByText("Weeks sampled").nextElementSibling).toHaveTextContent("2 of 4 needed");
    expect(screen.queryByText("Median")).toBeNull();
  });

  it("INSUFFICIENT_DATA never draws an absent count as 0", () => {
    show(
      section({
        state: "INSUFFICIENT_DATA",
        reason: "Not judged.",
        counts: { expected_windows: 168, complete_windows: 100, missing_windows: null, truncated_windows: 0, failed_windows: null },
      }),
    );
    expect(screen.getByText("Windows in the span").nextElementSibling).toHaveTextContent("100 of 168 complete");
    expect(screen.queryByText("Missing, truncated, failed windows")).toBeNull();
    expect(screen.queryByText(/missing/)).toBeNull();
  });

  it("TRUNCATED states the cap sentence", () => {
    show(
      section({
        state: "TRUNCATED",
        reason: "The latest window hit the row cap. Silence is not judged. A storm is judged only when this rule has a row.",
      }),
    );
    expect(status()).toHaveTextContent("Truncated");
    expect(status()).toHaveTextContent("The latest window hit the row cap.");
  });

  it("OK says judged through T in UTC and that there is no open anomaly", () => {
    show(section());
    expect(status()).toHaveTextContent("Judged");
    expect(status()).toHaveTextContent("Judged through 2026-11-09 10:00 UTC. No open match anomaly.");
    expect(screen.queryByText("Judged through")).toBeNull();
    expect(screen.getByText("No open or recent match anomaly is stored for this rule.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows an incident whatever the state: detector, figures, band, ratio, times and the link", () => {
    show(section({ incidents: [incident()] }));
    const table = screen.getByRole("table");
    const r = within(table).getAllByRole("row")[1];
    expect(r).toHaveTextContent("Rule storm");
    expect(r).toHaveTextContent("OPEN");
    expect(r).toHaveTextContent("4,800");
    expect(r).toHaveTextContent("310.5");
    expect(r).toHaveTextContent("120 to 640");
    expect(r).toHaveTextContent("15.46 ×");
    expect(r).toHaveTextContent("UTC");
    expect(r).toHaveTextContent("not ended");
    expect(within(r).getByRole("link", { name: "Open" })).toHaveAttribute(
      "href",
      "/anomalies/99999999-0000-4000-8000-000000000001",
    );
  });

  it("keeps an open silence visible under INSUFFICIENT_DATA", () => {
    show(
      section({
        state: "INSUFFICIENT_DATA",
        reason: "The inventory sync at 2026-11-09 01:00 UTC is older than the window ending 2026-11-09 10:00 UTC.",
        incidents: [
          incident({
            detector: "RULE_SILENCE",
            observed: 0,
            expected: 12,
            baseline_low: null,
            baseline_high: null,
            consecutive_buckets: 6,
            ratio: null,
          }),
        ],
      }),
    );
    expect(status()).toHaveTextContent("Insufficient data");
    const r = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(r).toHaveTextContent("Rule silence");
    expect(r).toHaveTextContent("6");
    expect(screen.queryByText(/No incident is stored/)).toBeNull();
  });

  it("draws a null band, streak and ratio as the dash and a measured zero observed as 0", () => {
    show(
      section({
        incidents: [
          incident({ detector: "RULE_SILENCE", observed: 0, expected: null, baseline_low: null, baseline_high: null, consecutive_buckets: null, ratio: null }),
        ],
      }),
    );
    const cells = within(within(screen.getByRole("table")).getAllByRole("row")[1]).getAllByRole("cell");
    // state, observed, expected, band, streak, ratio
    expect(cells[1]).toHaveTextContent(/^0$/);
    expect(cells[2]).toHaveTextContent("—");
    expect(cells[3]).toHaveTextContent("—");
    expect(cells[4]).toHaveTextContent("—");
    expect(cells[5]).toHaveTextContent("—");
  });

  it("lists a resolved incident with its end time", () => {
    show(section({ incidents: [incident({ state: "RESOLVED", ended: "2026-11-09T09:00:00Z" })] }));
    const r = within(screen.getByRole("table")).getAllByRole("row")[1];
    expect(r).toHaveTextContent("RESOLVED");
    expect(r).not.toHaveTextContent("not ended");
    expect(r).toHaveTextContent("11-09 09:00 UTC");
  });

  it("with no incident under a state that is not judged, says that is not a judgment", () => {
    show(section({ state: "DISABLED", reason: "Disabled in the inventory sync.", incidents: [] }));
    expect(screen.getByText(/That is not a judgment/)).toBeInTheDocument();
    expect(screen.queryByText("No open or recent match anomaly is stored for this rule.")).toBeNull();
  });

  it("falls back to a stated gap when the API gave no reason", () => {
    show(section({ reason: null, reason_code: null, reason_params: null }));
    expect(status()).toHaveTextContent("The API gave no reason.");
  });
});
