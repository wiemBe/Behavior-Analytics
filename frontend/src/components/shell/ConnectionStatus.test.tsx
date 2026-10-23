// Connection status: the three signals, the worst-of rule, and the honesty
// states (unknown is neutral, never checked is said, a refused signal is absent).

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { buildSignals, ConnectionStatus, worstTone } from "./ConnectionStatus";
import type { Loaded } from "./TopBar";
import type { HealthReady, SearchCadenceItem, SocOverview } from "@/lib/api";

const READY: Loaded<HealthReady> = { state: "ok", data: { status: "ready", database: "ok" } };
const overview = (
  status: string,
  checked: string | null | "absent" = "2026-10-14T08:55:00Z",
): Loaded<SocOverview> => ({
  state: "ok",
  data: {
    instance_status: status,
    instance_version: "7.5.0 UP9",
    // "absent": an API that predates the field omits it entirely.
    ...(checked === "absent" ? {} : { instance_last_health_check_at: checked }),
  } as SocOverview,
});
const cadence = (
  last_run: SearchCadenceItem["last_run"],
  family: SearchCadenceItem["family"] = "PHASE_A_VOLUME",
): Loaded<SearchCadenceItem[]> => ({
  state: "ok",
  data: [{ family, last_run } as SearchCadenceItem],
});
const run = (outcome: string | null) => ({
  at: "2026-10-14T08:50:00Z",
  outcome,
  searches_sent: 1,
  buckets_collected: 1,
  split_count: 0,
});

function view(
  h: Loaded<HealthReady>,
  o: Loaded<SocOverview>,
  c: Loaded<SearchCadenceItem[]>,
) {
  render(<ConnectionStatus health={h} overview={o} cadence={c} />);
  fireEvent.click(screen.getByText("Status"));
  return within(screen.getByRole("list", { name: "Connection signals" }));
}
const control = () => screen.getByText("Status").parentElement!;

describe("ConnectionStatus", () => {
  it("all ok: three named lines and a Connected word", () => {
    const list = view(READY, overview("HEALTHY"), cadence(run("ok")));
    expect(list.getAllByRole("listitem")).toHaveLength(3);
    expect(list.getByText("Backend and database")).toBeInTheDocument();
    expect(list.getByText("QRadar instance")).toBeInTheDocument();
    expect(list.getByText("Collection")).toBeInTheDocument();
    expect(list.getByText(/7\.5\.0 UP9 · checked/)).toHaveTextContent(
      "7.5.0 UP9 · checked 10-14 08:55 UTC",
    );
    expect(control()).toHaveAccessibleName("Connection status: Connected");
    expect(control().querySelector(".pill")).toHaveClass("ok");
  });

  it("backend down: says Backend unreachable and the control is crit", () => {
    const list = view({ state: "error" }, { state: "error" }, { state: "error" });
    expect(list.getByText("Backend unreachable")).toBeInTheDocument();
    expect(control()).toHaveAccessibleName("Connection status: Problem");
    expect(control().querySelector(".pill")).toHaveClass("crit");
  });

  it("instance UNREACHABLE: crit even with a healthy backend", () => {
    const list = view(READY, overview("UNREACHABLE"), cadence(run("ok")));
    expect(list.getByText("Unreachable")).toHaveClass("crit");
    expect(control()).toHaveAccessibleName("Connection status: Problem");
  });

  it("instance UNKNOWN is neutral, never green", () => {
    const list = view(READY, overview("UNKNOWN"), cadence(run("ok")));
    const pill = list.getByText("Unknown");
    expect(pill).not.toHaveClass("ok");
    expect(pill).not.toHaveClass("crit");
    expect(pill).not.toHaveClass("warn");
    expect(control()).toHaveAccessibleName("Connection status: Unknown");
    expect(control().querySelector(".pill")).not.toHaveClass("ok");
  });

  it("never checked: says so, and does not take a tone from the stored status", () => {
    const list = view(READY, overview("HEALTHY", null), cadence(run("ok")));
    expect(list.getByText(/never checked/)).toBeInTheDocument();
    expect(list.getByText("Healthy")).not.toHaveClass("ok");
    expect(control()).toHaveAccessibleName("Connection status: Unknown");
  });

  it("a check time the API does not report is not called never checked", () => {
    const list = view(READY, overview("HEALTHY", "absent"), cadence(run("ok")));
    expect(list.getByText(/check time not reported/)).toBeInTheDocument();
    expect(list.queryByText(/never checked/)).not.toBeInTheDocument();
  });

  it("omits the collection signal on 403, and does not mark the control bad", () => {
    const list = view(READY, overview("HEALTHY"), { state: "forbidden" });
    expect(list.getAllByRole("listitem")).toHaveLength(2);
    expect(list.queryByText("Collection")).not.toBeInTheDocument();
    expect(control()).toHaveAccessibleName("Connection status: Connected");
  });

  it("omits the instance signal when the overview is refused", () => {
    const list = view(READY, { state: "forbidden" }, cadence(run("ok")));
    expect(list.queryByText("QRadar instance")).not.toBeInTheDocument();
  });

  it("an unreadable (not refused) collection signal is neutral, not Connected", () => {
    view(READY, overview("HEALTHY"), { state: "error" });
    expect(control()).toHaveAccessibleName("Connection status: Unknown");
  });

  it("says when volume collection has no recorded run", () => {
    const list = view(READY, overview("HEALTHY"), cadence(null));
    expect(list.getByText("No run recorded")).toBeInTheDocument();
    expect(control()).toHaveAccessibleName("Connection status: Unknown");
  });

  it("ignores other families when reading collection", () => {
    const list = view(READY, overview("HEALTHY"), cadence(run("failed"), "WAF_COLLECTION"));
    expect(list.getByText("No run recorded")).toBeInTheDocument();
  });

  it("does not show collectors or event processors", () => {
    view(READY, overview("HEALTHY"), cadence(run("ok")));
    expect(screen.queryByText(/collector|event processor/i)).not.toBeInTheDocument();
  });
});

describe("worst-of", () => {
  const sig = (tone: "ok" | "neutral" | "warn" | "crit") => ({
    key: tone,
    name: tone,
    tone,
    value: tone,
    detail: null,
  });
  it("ranks crit over warn over neutral over ok", () => {
    expect(worstTone([sig("ok"), sig("neutral"), sig("warn"), sig("crit")])).toBe("crit");
    expect(worstTone([sig("ok"), sig("neutral"), sig("warn")])).toBe("warn");
    expect(worstTone([sig("ok"), sig("neutral")])).toBe("neutral");
    expect(worstTone([sig("ok"), sig("ok")])).toBe("ok");
    expect(worstTone([])).toBeNull();
  });

  it("a failed collection run outranks a healthy instance", () => {
    const signals = buildSignals(READY, overview("HEALTHY"), cadence(run("failed")), null);
    expect(worstTone(signals)).toBe("crit");
    const partial = buildSignals(READY, overview("HEALTHY"), cadence(run("partial")), null);
    expect(worstTone(partial)).toBe("warn");
  });

  it("shows Checking, not Connected, before any read has answered", () => {
    render(
      <ConnectionStatus
        health={{ state: "loading" }}
        overview={{ state: "loading" }}
        cadence={{ state: "loading" }}
      />,
    );
    expect(control()).toHaveAccessibleName("Connection status: Checking");
  });
});
