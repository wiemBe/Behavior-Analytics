import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { QRadarConsoleLatest, QRadarHostEntry } from "@/lib/api";

import {
  cellMissing,
  cellOk,
  consoleLatest,
  hostEntry,
  member,
  mount,
  metricsHost,
  pairEntry,
} from "./fixtures";
import { HealthTable } from "./HealthTable";
import { joinHealth } from "./qradarDetail";

function health(
  console_: QRadarConsoleLatest,
  items: QRadarHostEntry[] = [hostEntry()],
  hostsRead = true,
) {
  return render(
    <HealthTable console_={console_} join={joinHealth(items, console_.hosts)} hostsRead={hostsRead} />,
  );
}

/** The cells of a host's row, by the host's name. */
function rowOf(host: string): HTMLElement {
  return screen.getByText(host, { selector: ".sl-host" }).closest("tr") as HTMLElement;
}
const cells = (row: HTMLElement) => within(row).getAllByRole("cell");

describe("HealthTable: the figures of a host", () => {
  it("shows the fullest mount with its share used, free bytes and mount, then CPU, memory and the pipeline", () => {
    health(consoleLatest());
    const [disk, cpu, memory, queue, spill, stage] = cells(rowOf("qr-console"));
    expect(disk).toHaveTextContent("41.0 % /store");
    expect(disk).toHaveTextContent("549 GiB free"); // 59 % of 1000e9 bytes
    expect(cpu).toHaveTextContent("31.5 %");
    expect(memory).toHaveTextContent("62.3 %");
    expect(queue).toHaveTextContent("1,240");
    expect(spill).toHaveTextContent("0 B");
    expect(stage).toHaveTextContent("38.4 %");
    expect(stage).toHaveTextContent("EventParser");
    for (const cell of [cpu, memory, queue, spill, stage]) expect(cell).toHaveClass("num");
  });

  it("names the unit of every figure column", () => {
    health(consoleLatest());
    for (const name of [
      "CPU (%)",
      "Memory (%)",
      "CRE queue (events)",
      "Throttle spill (bytes on disk)",
      "Busiest stage (avg\u00a0%\u00a0busy)", // non-breaking: the unit stays whole
    ]) {
      expect(screen.getByRole("columnheader", { name })).toBeInTheDocument();
    }
  });

  it("marks a mount over the warning threshold as a warning, in words, and a failing one as critical", () => {
    health(
      consoleLatest({ hosts: [metricsHost("qr-console", { diskPercent: 88, level: "WARN" })] }),
    );
    expect(cells(rowOf("qr-console"))[0].querySelector(".pill.warn")).toHaveTextContent("Warning");
    expect(cells(rowOf("qr-console"))[0]).toHaveTextContent("88.0 %");
  });

  it("marks a mount over the failure threshold as critical", () => {
    health(
      consoleLatest({ hosts: [metricsHost("qr-console", { diskPercent: 97.5, level: "FAIL" })] }),
    );
    expect(cells(rowOf("qr-console"))[0].querySelector(".pill.crit")).toHaveTextContent("Critical");
  });

  it("flags nothing for a mount under the thresholds", () => {
    health(consoleLatest());
    expect(cells(rowOf("qr-console"))[0].querySelector(".pill")).toBeNull();
  });

  it("takes the level from the API and never recomputes it from the percentage", () => {
    health(consoleLatest({ hosts: [metricsHost("qr-console", { diskPercent: 96, level: "OK" })] }));
    expect(cells(rowOf("qr-console"))[0].querySelector(".pill")).toBeNull();
  });

  it("lists every allowlisted mount on expand, each with its own level", () => {
    health(consoleLatest({ hosts: [metricsHost("qr-console", { diskPercent: 90, level: "WARN" })] }));
    const details = rowOf("qr-console").querySelector("details") as HTMLElement;
    expect(within(details).getByText("All 2 mounts")).toBeInTheDocument();
    expect(details).toHaveTextContent("/");
    expect(details).toHaveTextContent("20.0 %");
    expect(details).toHaveTextContent("/store");
    expect(details).toHaveTextContent("90.0 %");
  });

  it("flags a throttle spill above zero, with its bytes", () => {
    health(consoleLatest({ hosts: [metricsHost("qr-console", { spill: 3 * 1024 ** 3 })] }));
    const spill = cells(rowOf("qr-console"))[4];
    expect(spill).toHaveTextContent("3.00 GiB");
    expect(spill.querySelector(".pill.warn")).toHaveTextContent("Spilling");
  });

  it("does not flag a throttle spill of zero, and shows it as a measured zero", () => {
    health(consoleLatest());
    const spill = cells(rowOf("qr-console"))[4];
    expect(spill).toHaveTextContent("0 B");
    expect(spill.querySelector(".pill")).toBeNull();
  });

  it("gives each member of an HA pair its own figures", () => {
    const pair = pairEntry("ha_healthy", ["ACTIVE", "STANDBY"]);
    health(
      consoleLatest({
        hosts: [metricsHost("ep-ha-a", { diskPercent: 30 }), metricsHost("ep-ha-b", { diskPercent: 70 })],
      }),
      [pair],
    );
    expect(cells(rowOf("ep-ha-a"))[0]).toHaveTextContent("30.0 %");
    expect(cells(rowOf("ep-ha-b"))[0]).toHaveTextContent("70.0 %");
    expect(rowOf("ep-ha-a")).toHaveTextContent("Primary");
    expect(rowOf("ep-ha-b")).toHaveTextContent("Secondary");
  });
});

describe("HealthTable: a figure that is missing", () => {
  it("says a disk that was not collected is not collected, never a percentage", () => {
    const host = metricsHost("qr-console");
    health(
      consoleLatest({
        hosts: [
          {
            ...host,
            disk: {
              state: "NOT_COLLECTED",
              reason_code: "metric_not_reported",
              reason: "The host did not report this metric in the window.",
              fullest: null,
              mounts: [],
            },
          },
        ],
      }),
    );
    const disk = cells(rowOf("qr-console"))[0];
    expect(disk).toHaveTextContent("Not collected");
    expect(disk).not.toHaveTextContent("%");
    expect(disk).not.toHaveTextContent("0");
    expect(screen.getByText("Not collected: The host did not report this metric in the window.")).toBeInTheDocument();
  });

  it("says a CPU, queue and spill that were not reported are not collected, and prints no zero", () => {
    const host = metricsHost("qr-console");
    health(
      consoleLatest({
        hosts: [
          {
            ...host,
            system: { ...host.system, cpu_percent: cellMissing() },
            pipeline: {
              ...host.pipeline,
              cre_queue: cellMissing("FAILED", "The search failed."),
              throttle_spill_bytes: cellMissing(),
              throttle_spilling: null,
              busiest_stage: null,
            },
          },
        ],
      }),
    );
    const [, cpu, , queue, spill, stage] = cells(rowOf("qr-console"));
    expect(cpu).toHaveTextContent("Not collected");
    expect(queue).toHaveTextContent("Failed");
    expect(spill).toHaveTextContent("Not collected");
    expect(spill).not.toHaveTextContent("B");
    expect(stage).toHaveTextContent("Not collected");
    for (const cell of [cpu, queue, spill, stage]) expect(cell).not.toHaveTextContent(/\b0\b/);
    expect(cpu.querySelector(".pill")).toBeNull();
    // The reason is on the cell for a pointer and under the table for everyone, once.
    expect(within(cpu).getByTitle("QRadar returned no figure for this metric.")).toBeInTheDocument();
    expect(screen.getAllByText("Not collected: QRadar returned no figure for this metric.")).toHaveLength(1);
    expect(screen.getByText("Not collected: The search failed.")).toBeInTheDocument();
  });
});

describe("HealthTable: joining the two lists", () => {
  it("says a server with no Health Metrics has none, with a sentence, as a not collected row", () => {
    health(consoleLatest({ hosts: [metricsHost("other")] }), [hostEntry({ hostname: "qr-silent", members: [member({ hostname: "qr-silent" })] })]);
    const row = rowOf("qr-silent");
    expect(row).toHaveTextContent("Not collected");
    expect(row).toHaveTextContent("No Health Metrics log source for this host answered in the window");
    expect(row.textContent).not.toMatch(/%|GiB| B\b/);
  });

  it("lists a host that sent Health Metrics but is not in the managed host list, below the table body", () => {
    health(consoleLatest({ hosts: [metricsHost("qr-console"), metricsHost("stranger", { diskPercent: 55 })] }));
    const heading = screen.getByText(/Sent Health Metrics but not in the managed host list/);
    const group = heading.closest("tbody") as HTMLElement;
    expect(within(group).getByText("stranger")).toBeInTheDocument();
    expect(group).toHaveTextContent("55.0 %");
    // The matched host is not repeated there.
    expect(within(group).queryByText("qr-console")).not.toBeInTheDocument();
  });

  it("does not draw an unmatched group when every host matched", () => {
    health(consoleLatest());
    expect(screen.queryByText(/not in the managed host list/)).not.toBeInTheDocument();
  });

  it("says the managed host list was not read when it was not, and still shows the metrics hosts", () => {
    health(consoleLatest(), [], false);
    expect(screen.getByText(/not matched to it/)).toBeInTheDocument();
    expect(screen.getByText("qr-console")).toBeInTheDocument();
  });
});

describe("HealthTable: the window and its state", () => {
  it("states the window once, in UTC, with its cadence", () => {
    health(consoleLatest());
    const line = screen.getByText(/^Window/);
    expect(line).toHaveTextContent("2026-11-02 10:00:00 UTC");
    expect(line).toHaveTextContent("2026-11-02 10:15:00 UTC");
    expect(line).toHaveTextContent("one window per 15 min");
    expect(line).toHaveTextContent("2026-11-02 10:16:10 UTC");
    expect(screen.getAllByText(/^Window/)).toHaveLength(1);
  });

  it("says in the window sentence that the busiest stage is the average, not the maximum", () => {
    health(consoleLatest());
    const line = screen.getByText(/^Window/);
    expect(line).toHaveTextContent(
      "Each figure is the maximum in that window, from QRadar's own Health Metrics, except the busiest stage, which is the window average.",
    );
  });

  it("shows a stale window's figures with the staleness said above them", () => {
    health(
      consoleLatest({
        metrics: { state: "STALE", reason_code: "stale", reason: "The newest window is older than the cadence allows." },
        hosts: [metricsHost("qr-console", { diskPercent: 66 })],
      }),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Stale. The newest window is older than the cadence allows.");
    expect(cells(rowOf("qr-console"))[0]).toHaveTextContent("66.0 %");
  });

  it("marks the figures of a partial window as lower bounds", () => {
    health(
      consoleLatest({
        metrics: { state: "PARTIAL", reason_code: "truncated", reason: "The search hit its row cap." },
      }),
    );
    expect(screen.getByRole("status")).toHaveTextContent("Partial. The search hit its row cap.");
    const [disk, cpu, , queue] = cells(rowOf("qr-console"));
    expect(disk).toHaveTextContent("≥ 41.0 %");
    expect(cpu).toHaveTextContent("≥ 31.5 %");
    expect(queue).toHaveTextContent("≥ 1,240");
  });

  it("draws no table for a part that was not collected, and says why", () => {
    health(
      consoleLatest({
        window: null,
        hosts: [],
        metrics: { state: "NOT_COLLECTED", reason_code: "no_window", reason: "The family has not stored a window yet." },
        last_metrics_window_end: null,
      }),
    );
    expect(screen.getByText("Not collected")).toBeInTheDocument();
    expect(screen.getByText("The family has not stored a window yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("draws no table for a failed part, in the error tone, and names the last window that was collected", () => {
    health(
      consoleLatest({
        hosts: [],
        metrics: { state: "FAILED", reason_code: "search_failed", reason: "The Ariel search failed." },
      }),
    );
    expect(screen.getByText("Failed")).toHaveClass("crit");
    expect(screen.getByText("The Ariel search failed.")).toBeInTheDocument();
    expect(screen.getByText(/The last window that was collected ended at/)).toHaveTextContent(
      "2026-11-02 10:15:00 UTC",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("says no host sent Health Metrics when the window holds none", () => {
    health(consoleLatest({ hosts: [] }), []);
    expect(screen.getByText("No host sent Health Metrics in the window.")).toBeInTheDocument();
  });

  it("keeps a measured zero a zero for a different cell", () => {
    const host = metricsHost("qr-console");
    health(consoleLatest({ hosts: [{ ...host, system: { ...host.system, cpu_percent: cellOk(0) } }] }));
    expect(cells(rowOf("qr-console"))[1]).toHaveTextContent("0.0 %");
  });
});

describe("HealthTable: a total carried from an earlier window", () => {
  const carried = (over: Partial<ReturnType<typeof mount>> = {}) => {
    const fullest = mount({ total_from: "2026-11-02T09:45:00Z", ...over });
    return consoleLatest({
      hosts: [
        metricsHost("qr-console", {
          disk: { state: "OK", reason_code: null, reason: null, fullest, mounts: [fullest] },
        }),
      ],
    });
  };

  it("says when the total is from, in UTC, under the free figure", () => {
    health(carried());
    const [disk] = cells(rowOf("qr-console"));
    expect(disk).toHaveTextContent("466 GiB free"); // 500e9 bytes
    expect(disk).toHaveTextContent(/total as of .*09:45 UTC/);
  });

  it("does not say it when the total is the window's own", () => {
    health(consoleLatest());
    const [disk] = cells(rowOf("qr-console"));
    expect(disk).toHaveTextContent("549 GiB free");
    expect(disk).not.toHaveTextContent("total as of");
  });

  it("shows no free figure and no note when the free space is unknown, never a zero", () => {
    health(carried({ total_bytes: null, total_from: null, free_bytes: null }));
    const [disk] = cells(rowOf("qr-console"));
    expect(disk).not.toHaveTextContent("free");
    expect(disk).not.toHaveTextContent("total as of");
    expect(disk).not.toHaveTextContent("0 B");
  });
});
