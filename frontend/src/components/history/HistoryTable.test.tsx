import { readFileSync } from "node:fs";
import { join } from "node:path";

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { HistoryScope, HistoryScopeSource } from "@/lib/api";

import { consoleOf, family, scope } from "./fixtures";
import { HistoryTable } from "./HistoryTable";
import { rowsOf } from "./historyRows";

const NOW = new Date("2026-10-21T12:00:00Z");

function source(key: string, name: string | null = `Source ${key}`): HistoryScopeSource {
  return { key, name };
}

function reasonCell(over: Partial<HistoryScope> = {}, familyName = "PHASE_A"): HTMLElement {
  const console = consoleOf({
    mode: "ACTIVE",
    families: [
      family({
        family: familyName,
        scopes: [scope({ scope_key: familyName === "PHASE_A" ? "" : "7", ...over })],
        sources: familyName === "PHASE_A" ? null : [],
      }),
    ],
  });
  render(<HistoryTable console={console} now={NOW} />);
  const row = screen
    .getByRole("rowheader", { name: familyName === "PHASE_A" ? "Source volume" : familyName })
    .closest("tbody");
  if (row === null) throw new Error(`no ${familyName} row`);
  return within(row).getAllByRole("cell")[7]!;
}

describe("the history table reason cell", () => {
  it("shows the last failed step with its absolute UTC time, class and retry sentence", () => {
    const cell = reasonCell({
      last_error_class: "TimeoutError",
      last_error_at: "2026-10-20T09:15:00Z",
    });

    expect(cell).toHaveTextContent(
      "Last step failed at 10-20 09:15 UTC (TimeoutError). The next collection retries.",
    );
    const time = within(cell).getByText("10-20 09:15 UTC");
    expect(time.tagName).toBe("TIME");
    expect(time.parentElement).toHaveClass("warn");
    expect(time).toHaveAttribute("datetime", "2026-10-20T09:15:00Z");
    expect(time).toHaveAttribute("title", "2026-10-20 09:15:00 UTC");
  });

  it("shows no failed-step line when there is no last error", () => {
    const cell = reasonCell({ reason: "Reading is still in progress." });
    expect(cell).toHaveTextContent("Reading is still in progress.");
    expect(cell).not.toHaveTextContent("Last step failed");
  });

  it("shows cut sources without a silent-source line", () => {
    const cell = reasonCell({
      sources_cut: [source("1", "Branch firewall"), source("2", "Core firewall")],
      sources_cut_count: 2,
    });
    expect(cell).toHaveTextContent(
      "QRadar holds less than asked for: Branch firewall, Core firewall.",
    );
    const label = within(cell).getByText("QRadar holds less than asked for:");
    expect(label).toHaveClass("muted");
    expect(label.parentElement?.tagName).toBe("DIV");
    expect(label.parentElement).toHaveTextContent(
      "QRadar holds less than asked for: Branch firewall, Core firewall.",
    );
    expect(cell).not.toHaveTextContent("No event in the window:");
    expect(within(cell).queryByRole("list")).not.toBeInTheDocument();
  });

  it("shows silent sources without a cut-source line", () => {
    const cell = reasonCell({
      sources_silent: [source("3", "Dormant proxy")],
      sources_silent_count: 1,
    });
    expect(cell).toHaveTextContent("No event in the window: Dormant proxy.");
    expect(cell).not.toHaveTextContent("QRadar holds less than asked for:");
    expect(within(cell).queryByRole("list")).not.toBeInTheDocument();
  });

  it("shows the error, cut sources and silent sources in that order", () => {
    const cell = reasonCell({
      reason: "The reading is partial.",
      last_error_class: "LifecycleError",
      last_error_at: "2026-10-20T10:00:00Z",
      sources_cut: [source("1", "Cut source")],
      sources_cut_count: 1,
      sources_silent: [source("2", "Silent source")],
      sources_silent_count: 1,
    });
    const text = cell.textContent ?? "";
    expect(text.indexOf("The reading is partial.")).toBeLessThan(text.indexOf("Last step failed"));
    expect(text.indexOf("Last step failed")).toBeLessThan(
      text.indexOf("QRadar holds less than asked for:"),
    );
    expect(text.indexOf("QRadar holds less than asked for:")).toBeLessThan(
      text.indexOf("No event in the window:"),
    );
  });

  it("shows at most five names and derives the remainder from the whole count", () => {
    const cell = reasonCell({
      sources_silent: Array.from({ length: 25 }, (_, index) =>
        source(String(index + 1), `Silent ${String(index + 1).padStart(2, "0")}`),
      ),
      sources_silent_count: 30,
    });
    expect(cell).toHaveTextContent(
      "No event in the window: Silent 01, Silent 02, Silent 03, Silent 04, Silent 05 and 25 more.",
    );
    expect(cell).not.toHaveTextContent("Silent 06");
    expect(within(cell).queryByRole("list")).not.toBeInTheDocument();
  });

  it("falls back to the source key when a source has no name", () => {
    const cell = reasonCell({ sources_cut: [source("418", null)], sources_cut_count: 1 });
    expect(cell).toHaveTextContent("QRadar holds less than asked for: source 418.");
  });

  it("renders no source lines when the measurement is null", () => {
    const cell = reasonCell({ reason: "No source measurement was made." });
    expect(cell).not.toHaveTextContent("QRadar holds less than asked for:");
    expect(cell).not.toHaveTextContent("No event in the window:");
  });

  it("renders no source lines for measured empty lists and zero counts", () => {
    const cell = reasonCell({
      reason: "Every monitored source was covered.",
      sources_cut: [],
      sources_cut_count: 0,
      sources_silent: [],
      sources_silent_count: 0,
    });
    expect(cell).not.toHaveTextContent("QRadar holds less than asked for:");
    expect(cell).not.toHaveTextContent("No event in the window:");
    expect(cell).not.toHaveTextContent(/\b0\b/);
  });

  it("renders no source lines for a non-Phase-A family row", () => {
    const cell = reasonCell({ reason: "WAF history is complete." }, "WAF");
    expect(cell).toHaveTextContent("WAF history is complete.");
    expect(cell).not.toHaveTextContent("QRadar holds less than asked for:");
    expect(cell).not.toHaveTextContent("No event in the window:");
  });

  it("puts the reason and the later answer on a full-width row under the metrics", () => {
    const console = consoleOf({
      mode: "ACTIVE",
      families: [
        family({
          scopes: [scope({ reason: "Reading is still in progress.", later_yes: "possible" })],
        }),
      ],
    });
    render(<HistoryTable console={console} now={NOW} />);
    const body = screen.getByRole("rowheader", { name: "Source volume" }).closest("tbody");
    if (body === null) throw new Error("no body");
    const detail = within(body).getAllByRole("row")[1];
    if (!detail) throw new Error("no detail row");
    const cell = within(detail).getByRole("cell");
    expect(cell).toHaveAttribute("colspan", "8");
    expect(within(cell).getByText("Reason")).toBeInTheDocument();
    expect(within(cell).getByText("Later Yes")).toBeInTheDocument();
    expect(cell).toHaveTextContent("Reading is still in progress.");
    expect(cell).toHaveTextContent("possible");
  });

  it("lets the reason wrap in the table instead of reserving a fixed column", () => {
    const css = readFileSync(join(process.cwd(), "src/components/history/history.css"), "utf8");
    expect(css).not.toMatch(/\.hb-reason\s*\{[^}]*min-width/);
    expect(css).not.toMatch(/\.hb-later\s*\{[^}]*min-width/);
    expect(css).toMatch(/\.hb-table thead th\s*\{[^}]*white-space:\s*normal/);
    expect(css).toMatch(/overflow-wrap:\s*anywhere/);
  });

  it("keeps all six fields null on a synthesized absent-family row", () => {
    const row = rowsOf(
      consoleOf({
        families: [family({ family: "WAF", scopes: [], sources: [] })],
      }),
    )[0]!;
    expect({
      lastErrorClass: row.lastErrorClass,
      lastErrorAt: row.lastErrorAt,
      sourcesCut: row.sourcesCut,
      sourcesSilent: row.sourcesSilent,
      sourcesCutCount: row.sourcesCutCount,
      sourcesSilentCount: row.sourcesSilentCount,
    }).toEqual({
      lastErrorClass: null,
      lastErrorAt: null,
      sourcesCut: null,
      sourcesSilent: null,
      sourcesCutCount: null,
      sourcesSilentCount: null,
    });
  });
});
