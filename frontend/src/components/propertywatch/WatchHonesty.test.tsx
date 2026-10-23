import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { bucket, catalog, iso, watch } from "./fixtures";
import { honestyNotes, WatchHonesty } from "./WatchHonesty";

/** Text of a note, including an instant drawn by UtcTime. */
function asText(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  return renderToStaticMarkup(<>{node}</>).replace(/<[^>]+>/g, "");
}

const full = (n: number) => Array.from({ length: n }, (_, i) => bucket(i));
const input = (over: Partial<Parameters<typeof honestyNotes>[0]> = {}) => ({
  watch: watch(),
  catalog: catalog(),
  buckets: full(12),
  hours: 1,
  ...over,
});
const labels = (notes: ReturnType<typeof honestyNotes>) => notes.map((n) => n.label);

describe("the honesty line", () => {
  it("says nothing when everything was collected exactly", () => {
    expect(honestyNotes(input())).toEqual([]);
  });

  it.each([
    [
      "READING",
      { state: "READING", days_requested: 28, days_read: 9 },
      "Reading history from QRadar: 9 of 28 days. Nothing is judged until it finishes.",
    ],
    [
      "COMPLETE",
      { state: "COMPLETE", days_requested: 28, days_read: 28, finished_at: "2026-10-19T12:00:00Z" },
      "History: 28 days read from QRadar on 2026-10-19, used for baselines and new-value checks. Those buckets were not judged.",
    ],
    [
      "UNAVAILABLE",
      { state: "UNAVAILABLE", days_requested: 28 },
      "QRadar holds no events of this scope in the last 28 days. The watch learns forward.",
    ],
  ] as const)("states %s history without implying health", (_state, history, expected) => {
    const base = watch();
    const notes = honestyNotes(input({ watch: watch({ history: { ...base.history, ...history } }) }));
    expect(notes[0].text).toBe(expected);
  });

  it("states a partial retention floor, every gap and unreadable progress", () => {
    const base = watch();
    const notes = honestyNotes(input({
      watch: watch({
        history: {
          ...base.history,
          state: "PARTIAL",
          days_requested: 28,
          days_read: 9,
          floor: "2026-10-10T09:00:00Z",
          progress_unreadable: true,
          gaps: [{
            start: "2026-10-12T14:00:00Z",
            end: "2026-10-12T16:00:00Z",
            reason: "search failed 3 times at the minimum width",
          }],
        },
      }),
    }));
    expect(asText(notes[0].text)).toContain("earlier buckets are not claimed as zero");
    expect(asText(notes[0].text)).toContain("2026-10-10 09:00:00 UTC");
    expect(asText(notes.find((note) => note.label === "HISTORY GAP")?.text)).toContain(
      "could not be read (search failed 3 times at the minimum width).",
    );
    expect(notes.find((note) => note.label === "HISTORY PROGRESS UNREADABLE")?.text).toContain(
      "not zero days read",
    );
  });

  it("counts intervals, not rows, for what was not collected", () => {
    const notes = honestyNotes(input({ buckets: full(9) }));
    expect(notes).toHaveLength(1);
    expect(notes[0].text).toMatch(/3 of 12 five-minute intervals/);
    expect(notes[0].text).toMatch(/not zero/);
  });

  it("states a window with no bucket as an absence of collection", () => {
    const notes = honestyNotes(input({ buckets: [] }));
    expect(labels(notes)).toEqual(["NOT COLLECTED"]);
    expect(notes[0].text).toMatch(/not because nothing happened/);
  });

  it("says lower bounds are unknown, not zero", () => {
    const buckets = full(12).map((b, i) => (i < 4 ? { ...b, values_measurement: "LOWER_BOUND" } : b));
    const notes = honestyNotes(input({ buckets }));
    expect(labels(notes)).toEqual(["TRUNCATED"]);
    expect(notes[0].text).toBe(
      "4 of 12 collected buckets in this window are lower bounds; absent values in them are unknown, not zero.",
    );
  });

  it("marks buckets without a whole total as PARTIAL", () => {
    const buckets = full(12).map((b, i) => (i === 0 ? { ...b, totals_complete: false } : b));
    expect(labels(honestyNotes(input({ buckets })))).toEqual(["PARTIAL"]);
  });

  it("reports values refused admission, with the time in UTC", () => {
    const buckets = full(12).map((b, i) => (i === 5 ? { ...b, rejected_over_quota: 280 } : b));
    const [note] = honestyNotes(input({ buckets }));
    expect(note.label).toBe("ADMISSION");
    expect(asText(note.text)).toContain("280 values were refused admission at");
    expect(asText(note.text)).toContain("UTC");
  });

  it("reports a membership change with the counts before and after", () => {
    const buckets = full(12).map((b, i) =>
      i >= 6 ? { ...b, scope_member_count: 40, scope_changed: i === 6 } : b,
    );
    const [note] = honestyNotes(input({ buckets }));
    expect(note.label).toBe("MEMBERSHIP CHANGED");
    expect(asText(note.text)).toContain("37 to 40 sources");
    expect(asText(note.text)).toContain(iso(6).slice(0, 10));
  });

  it("names the collection setting when collection is off", () => {
    const notes = honestyNotes(input({ catalog: catalog({ collection_enabled: false }) }));
    expect(notes[0].label).toBe("COLLECTION OFF");
    expect(notes[0].text).toContain("PW_COLLECTION_ENABLED");
  });

  it.each([
    ["NOT_APPLICABLE", "NOT APPLICABLE"],
    ["SCOPE_STALE", "SCOPE STALE"],
    ["SCOPE_TOO_LARGE", "SCOPE TOO LARGE"],
    ["SCOPE_EMPTY", "SCOPE EMPTY"],
    ["STALE", "STALE"],
  ] as const)("states %s with its reason", (status, label) => {
    const notes = honestyNotes(input({ watch: watch({ status, status_reason: "because X" }) }));
    expect(notes[0].label).toBe(label);
    expect(notes[0].text).toBe("because X");
  });

  it("states a watch that never ran, and does not call it healthy", () => {
    const notes = honestyNotes(input({ watch: watch({ status: null, status_reason: null }), buckets: [] }));
    expect(labels(notes)).toContain("NEVER RUN");
  });

  it("says the buckets could not be loaded instead of drawing an empty window", () => {
    const notes = honestyNotes(input({ buckets: null }));
    expect(notes.at(-1)).toMatchObject({ label: "NOT LOADED", tone: "crit" });
  });

  it("renders nothing for no notes and a labelled group for some", () => {
    const { container, rerender } = render(<WatchHonesty notes={[]} />);
    expect(container).toBeEmptyDOMElement();
    rerender(<WatchHonesty notes={honestyNotes(input({ buckets: [] }))} />);
    expect(screen.getByRole("group", { name: /not known/ })).toHaveTextContent("NOT COLLECTED.");
  });
});
