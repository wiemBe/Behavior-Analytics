import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { bucket, HOSTILE, incident, value, watch } from "./fixtures";
import { activeIncidentsByValue, WatchValuesTable } from "./WatchValuesTable";

function table(
  values = [value("a", "alice")],
  over: Partial<Parameters<typeof WatchValuesTable>[0]> = {},
) {
  const onToggle = vi.fn();
  render(
    <WatchValuesTable
      values={values}
      latest={bucket(11)}
      selected={new Set()}
      maxSelected={5}
      onToggle={onToggle}
      incidents={new Map()}
      sort="events"
      sortHref={(s) => `/x?sort=${s}`}
      {...over}
    />,
  );
  return { onToggle };
}

// The value cell, not any cell that happens to repeat the word ("unknown", "zero").
const row = (name: string) =>
  [...document.querySelectorAll(".pw-value-text")].find((el) => el.textContent === name)!.closest("tr")!;

describe("the values table", () => {
  it("renders attacker-controlled text as text, never as markup", () => {
    const { container } = render(
      <WatchValuesTable
        values={[value("h", HOSTILE)]}
        latest={bucket(11)}
        selected={new Set()}
        maxSelected={5}
        onToggle={() => {}}
        incidents={new Map()}
        sort="events"
        sortHref={() => "/x"}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    expect(screen.getByRole("checkbox", { name: `Plot ${HOSTILE}` })).toBeInTheDocument();
  });

  it("is named, right-aligns its figures and names its unit", () => {
    table();
    expect(screen.getByRole("table", { name: "Tracked values" })).toBeInTheDocument();
    const events = screen.getByRole("columnheader", { name: "Events, latest bucket" });
    expect(events).toHaveClass("num");
    expect(screen.getByRole("columnheader", { name: "Expected range" })).toHaveClass("num");
    expect(screen.getByRole("table").querySelector("caption")).toHaveTextContent("events per 5 min");
    expect(within(row("alice")).getByText("120").closest("td")).toHaveClass("num");
  });

  it("names history and its floor in the first-seen caption", () => {
    const base = watch();
    table(undefined, {
      history: { ...base.history, state: "COMPLETE", floor: "2026-10-10T09:00:00Z" },
    });
    expect(screen.getByRole("table").querySelector("caption")).toHaveTextContent(
      "history read from QRadar, from 2026-10-10 09:00:00 UTC",
    );
    expect(screen.getByRole("columnheader", { name: /First seen \(UTC; history read from 2026-/ })).toBeInTheDocument();
  });

  it("marks sort state and links the sortable headers", () => {
    table();
    expect(screen.getByRole("columnheader", { name: "Events, latest bucket" })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByRole("columnheader", { name: "Value" })).toHaveAttribute("aria-sort", "none");
    expect(screen.getByRole("link", { name: "Value" })).toHaveAttribute("href", "/x?sort=value");
  });

  it("reads each kind of reading in words, and an unknown is never a zero", () => {
    table([
      value("a", "exact"),
      value("b", "bound", { events: 1500, measurement: "LOWER_BOUND" }),
      value("c", "zero", { reading: "MEASURED_ZERO", events: 0 }),
      value("d", "unknown", { reading: "UNKNOWN", events: null, measurement: null }),
      value("e", "untracked", { reading: "NOT_AN_OBSERVATION", events: null, measurement: null }),
      value("f", "nobucket", { reading: "NO_BUCKET", events: null, measurement: null }),
    ]);
    expect(row("exact")).toHaveTextContent("120");
    expect(row("bound")).toHaveTextContent("at least 1,500");
    expect(row("zero").cells[2]).toHaveTextContent(/^0$/);
    expect(row("unknown").cells[2]).toHaveTextContent("unknown");
    expect(row("unknown").cells[2]).not.toHaveTextContent(/^0$/);
    expect(row("untracked").cells[2]).toHaveTextContent("not tracked then");
    expect(row("nobucket").cells[2]).toHaveTextContent("not collected");
  });

  it("shows no share for a lower bound and a share for an exact value", () => {
    table([value("a", "exact", { events: 96 }), value("b", "bound", { events: 96, measurement: "LOWER_BOUND" })]);
    expect(row("exact").cells[4]).toHaveTextContent("10.0 %");
    expect(row("bound").cells[4]).toHaveTextContent("—");
  });

  it("states learning as n of 168 cells, and no range while learning", () => {
    table([value("a", "newbie", { baseline_state: "LEARNING", reliable_cells: 31 })]);
    expect(row("newbie")).toHaveTextContent("Learning (31 of 168 cells)");
    expect(row("newbie").cells[3]).toHaveTextContent("learning");
  });

  it("marks a pinned value, a cut value and a retired value", () => {
    table([value("a", "p", { pinned: true, truncated: true, active: false })]);
    expect(row("p")).toHaveTextContent("pinned");
    expect(row("p")).toHaveTextContent("cut at the length limit");
    expect(row("p")).toHaveTextContent("no longer tracked");
  });

  it("shows an active incident on its value and ignores resolved ones", () => {
    const map = activeIncidentsByValue([
      incident(),
      incident({ id: "r", state: "RESOLVED" }),
      incident({ id: "n", pw_value: undefined }),
    ]);
    table([value("a", "svc-backup")], { incidents: map });
    expect(row("svc-backup")).toHaveTextContent("Value spike · OPEN");
    expect(map.get("svc-backup")).toHaveLength(1);
  });

  it("toggles on click, and disables unchecked boxes once five are plotted", () => {
    const five = ["1", "2", "3", "4", "5"].map((n) => value(n, `v${n}`));
    const { onToggle } = table([...five, value("6", "v6")], { selected: new Set(["1", "2", "3", "4", "5"]) });
    expect(screen.getByRole("checkbox", { name: "Plot v6" })).toBeDisabled();
    expect(screen.getByRole("checkbox", { name: "Plot v1" })).toBeEnabled();
    fireEvent.click(screen.getByRole("checkbox", { name: "Plot v1" }));
    expect(onToggle).toHaveBeenCalledWith("1");
  });

  it("joins the components of a combination value", () => {
    table([value("a", "alice\u0001DENY", { components: ["alice", "DENY"] })]);
    expect(screen.getByText("alice × DENY")).toBeInTheDocument();
  });
});

describe("the values table at width", () => {
  const LONG = `svc-${"very-long-service-account-name-".repeat(3)}end@corp.example.internal`;

  it("cuts a long value to two lines and keeps the whole text one disclosure away", () => {
    table([value("l", LONG)]);
    const cell = document.querySelector("td.pw-value-cell") as HTMLElement;
    expect(cell.querySelector(".pw-value-clamp")).toHaveTextContent(LONG);
    const details = cell.querySelector("details.pw-value-full") as HTMLElement;
    expect(details).not.toHaveAttribute("open");
    // A summary is focusable and opens from the keyboard; a title attribute is not enough.
    expect(within(details).getByText("Show the full value").tagName).toBe("SUMMARY");
    expect(details.querySelector(".pw-value-text")).toHaveTextContent(LONG);
  });

  it("offers no disclosure for a short value", () => {
    table([value("s", "alice")]);
    expect(document.querySelector("details.pw-value-full")).toBeNull();
  });

  it("renders a hostile long value as text in the cell and in the disclosure", () => {
    const hostile = `${HOSTILE}${"x".repeat(80)}`;
    const { container } = render(
      <WatchValuesTable
        values={[value("h", hostile)]}
        latest={bucket(11)}
        selected={new Set()}
        maxSelected={5}
        onToggle={() => {}}
        incidents={new Map()}
        sort="events"
        sortHref={() => "/x"}
      />,
    );
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("details.pw-value-full .pw-value-text")?.textContent).toBe(hostile);
  });

  it("has no column of its own for the baseline: every column fits at 1024 px", () => {
    table();
    const headers = screen.getAllByRole("columnheader").map((h) => h.textContent);
    expect(headers).toEqual([
      "Plot", "Value", "Events, latest bucket", "Expected range", "Share of parsed events", "First seen (UTC)",
    ]);
  });

  it("keeps the baseline state and the open incidents under the value", () => {
    const v = value("a", "alice");
    table([v], { incidents: activeIncidentsByValue([incident({ pw_value: { ...incident().pw_value!, value_key: "alice" } })]) });
    const cell = document.querySelector("td.pw-value-cell") as HTMLElement;
    expect(cell).toHaveTextContent("Baseline reliable (168 of 168 cells)");
    expect(cell).toHaveTextContent("Value spike · OPEN");
  });
});
