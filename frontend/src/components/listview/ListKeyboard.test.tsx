// SOC-15a: the one-key triage hook and its hint, on a plain table and on both real lists.

import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AlertsPage from "@/app/alerts/page";
import AnomaliesPage from "@/app/anomalies/page";
import { api, type Alert, type AnomalySummary } from "@/lib/api";
import { ListKeyboard } from "./ListKeyboard";

let opened: string[] = [];
const onClick = (e: Event) => {
  const a = (e.target as HTMLElement).closest("a");
  if (a) {
    e.preventDefault();
    opened.push(a.getAttribute("href") ?? "");
  }
};

beforeEach(() => {
  opened = [];
  document.addEventListener("click", onClick);
});
afterEach(() => {
  document.removeEventListener("click", onClick);
  document.body.innerHTML = "";
});

const press = (key: string, init: KeyboardEventInit = {}, target: Element = document.body) =>
  act(() => {
    fireEvent.keyDown(target, { key, ...init });
  });
const marked = () =>
  Array.from(document.querySelectorAll("tbody tr[data-kbd-row]")).map((r) => r.textContent?.trim());

function Plain({ rows = 3 }: { rows?: number }) {
  return (
    <>
      <ListKeyboard table="table.t" open="td.go a" />
      <input aria-label="filter" />
      <table className="t">
        <tbody>
          {Array.from({ length: rows }, (_, i) => (
            <Fragment2 key={i} i={i} />
          ))}
        </tbody>
      </table>
    </>
  );
}
function Fragment2({ i }: { i: number }) {
  return (
    <>
      <tr>
        <td>row {i + 1}</td>
        <td className="go">
          <a href={`/r/${i + 1}`}>open</a>
        </td>
      </tr>
      {i === 0 && (
        <tr className="row-detail">
          <td colSpan={2}>detail of 1</td>
        </tr>
      )}
    </>
  );
}

describe("useListKeyboard", () => {
  it("starts on the first row at the first j, then moves down and up", () => {
    render(<Plain />);
    expect(marked()).toEqual([]);
    press("j");
    expect(marked()).toEqual(["row 1open"]);
    press("j");
    expect(marked()).toEqual(["row 2open"]);
    press("k");
    expect(marked()).toEqual(["row 1open"]);
  });

  it("skips a disclosure's detail row", () => {
    render(<Plain />);
    press("j");
    press("j");
    expect(marked()).toEqual(["row 2open"]);
  });

  it("does nothing on k before any row is marked, and stays on the first row at the top", () => {
    render(<Plain />);
    press("k");
    expect(marked()).toEqual([]);
    press("j");
    press("k");
    expect(marked()).toEqual(["row 1open"]);
  });

  it("marks the row for assistive technology and announces the position", () => {
    render(<Plain />);
    press("j");
    const row = document.querySelector("tr[data-kbd-row]")!;
    expect(row.getAttribute("aria-current")).toBe("true");
    expect(document.querySelector(".list-keys [aria-live]")!.textContent).toBe("Row 1 of 3: row 1");
    press("j");
    expect(document.querySelectorAll("[aria-current]").length).toBe(1);
  });

  it("does not wrap or page at the last row, and says so", () => {
    render(<Plain />);
    press("j");
    press("j");
    press("j");
    press("j");
    expect(marked()).toEqual(["row 3open"]);
    expect(document.querySelector(".list-keys [aria-live]")!.textContent).toMatch(/last on this page/);
    expect(opened).toEqual([]);
  });

  it("opens the marked row's link on Enter and on o, and not before a row is marked", () => {
    render(<Plain />);
    press("Enter");
    press("o");
    expect(opened).toEqual([]);
    press("j");
    press("j");
    press("Enter");
    press("o");
    expect(opened).toEqual(["/r/2", "/r/2"]);
  });

  it("leaves Enter to a focused link or button", () => {
    render(<Plain />);
    press("j");
    const link = screen.getAllByRole("link")[2];
    press("Enter", {}, link);
    expect(opened).toEqual([]);
  });

  it("clears the mark on Escape", () => {
    render(<Plain />);
    press("j");
    press("Escape");
    expect(marked()).toEqual([]);
    expect(document.querySelector("[aria-current]")).toBeNull();
    press("Enter");
    expect(opened).toEqual([]);
  });

  it("ignores every key while focus is in an input", () => {
    render(<Plain />);
    press("j", {}, screen.getByRole("textbox"));
    expect(marked()).toEqual([]);
  });

  it("ignores a select, a textarea and a contenteditable region", () => {
    render(
      <>
        <Plain />
        <select aria-label="s" />
        <textarea aria-label="ta" />
        <div contentEditable suppressContentEditableWarning data-testid="ce" />
      </>,
    );
    const ce = screen.getByTestId("ce");
    Object.defineProperty(ce, "isContentEditable", { value: true });
    press("j", {}, screen.getByLabelText("s"));
    press("j", {}, screen.getByLabelText("ta"));
    press("j", {}, ce);
    expect(marked()).toEqual([]);
  });

  it("ignores a key with Ctrl, Alt or Meta held (Ctrl K stays the search)", () => {
    render(<Plain />);
    press("j", { ctrlKey: true });
    press("j", { altKey: true });
    press("j", { metaKey: true });
    press("k", { ctrlKey: true });
    expect(marked()).toEqual([]);
  });

  it("ignores keys while a dialog is open", () => {
    render(
      <>
        <Plain />
        <div role="dialog" aria-modal="true" />
      </>,
    );
    press("j");
    expect(marked()).toEqual([]);
  });

  it("does nothing when the page has no such table", () => {
    render(<ListKeyboard table="table.missing" open="a" />);
    press("j");
    expect(document.querySelector("[data-kbd-row]")).toBeNull();
  });

  it("stops listening when it unmounts", () => {
    const { unmount } = render(<Plain />);
    unmount();
    render(
      <table className="t">
        <tbody>
          <tr>
            <td>x</td>
          </tr>
        </tbody>
      </table>,
    );
    press("j");
    expect(document.querySelector("[data-kbd-row]")).toBeNull();
  });
});

describe("the key hint", () => {
  it("lists the keys in English, without 'live' or 'real-time'", () => {
    render(<Plain />);
    const hint = document.querySelector(".list-keys-hint")!;
    expect(hint.textContent).toBe("Keysj next rowk previous rowEnter openEsc clear");
    expect(Array.from(hint.querySelectorAll("kbd")).map((k) => k.textContent)).toEqual([
      "j",
      "k",
      "Enter",
      "Esc",
    ]);
    expect(hint.textContent).not.toMatch(/live|real-time/i);
  });
});

// --- the two real lists ------------------------------------------------------------------------

function anomaly(id: string, name: string): AnomalySummary {
  return {
    id,
    log_source_id: `11111111-1111-1111-1111-11111111111${id.slice(-1)}`,
    log_source_name: name,
    anomaly_type: "VOLUME_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 6,
    expected_value: 2,
    deviation_ratio: 3,
    robust_z: 13.5,
    absolute_delta: 1200,
    consecutive_buckets: 2,
    confidence: 0.82,
    detected_at: "2026-11-05T16:55:00Z",
    opened_at: "2026-11-05T16:55:00Z",
    anomaly_start: "2026-11-05T16:55:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "COMPLETE",
    suppressed: false,
    explanation: null,
    triage: { status: "NEW", owner: null, disposition: null, updated_at: null },
  };
}
function alert(id: string, title: string): Alert {
  return {
    id,
    fingerprint: `vol:${id}`,
    title,
    description: null,
    severity: "HIGH",
    status: "OPEN",
    source_type: "ANOMALY",
    opened_at: "2026-10-15T07:00:00Z",
    first_seen_at: "2026-10-15T07:00:00Z",
    last_seen_at: "2026-10-15T08:30:00Z",
    acknowledged_by: null,
    resolved_by: null,
    resolution_reason: null,
    occurrence_count: 4,
    evidence_snapshot: {},
    source_anomaly_ids: ["an-1"],
  };
}

describe("on /anomalies", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "sourceBehaviors").mockResolvedValue([]);
    const items = [anomaly("a-1", "Alpha Firewall"), anomaly("a-2", "Beta Proxy")];
    vi.spyOn(api, "anomalies").mockResolvedValue({ items, total: 2, limit: 25, offset: 0 });
  });

  it("moves with j / k, opens with Enter, clears with Escape, and shows the hint", async () => {
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    expect(document.querySelector(".list-keys-hint")).not.toBeNull();
    press("j");
    press("j");
    expect(marked()[0]).toMatch(/^Beta Proxy/);
    press("k");
    expect(marked()[0]).toMatch(/^Alpha Firewall/);
    press("Enter");
    expect(opened).toEqual(["/anomalies/a-1"]);
    press("o");
    expect(opened).toEqual(["/anomalies/a-1", "/anomalies/a-1"]);
    press("Escape");
    expect(marked()).toEqual([]);
  });

  it("does nothing from an input and with a modifier", async () => {
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    const input = document.querySelector("input, select")!;
    press("j", {}, input);
    press("j", { ctrlKey: true });
    expect(marked()).toEqual([]);
  });

  it("stops at the last row", async () => {
    render(await AnomaliesPage({ searchParams: Promise.resolve({}) }));
    press("j");
    press("j");
    press("j");
    expect(marked()[0]).toMatch(/^Beta Proxy/);
  });
});

describe("on /alerts", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "alerts").mockResolvedValue({
      items: [alert("al-1", "First alert"), alert("al-2", "Second alert")],
      total: 2,
      limit: 100,
      offset: 0,
    });
    vi.spyOn(api, "logSources").mockResolvedValue([]);
  });

  it("moves with j / k, opens with Enter and o, clears with Escape, and shows the hint", async () => {
    render(await AlertsPage({ searchParams: Promise.resolve({}) }));
    expect(document.querySelector(".list-keys-hint")).not.toBeNull();
    press("j");
    expect(marked()[0]).toMatch(/^First alert/);
    press("j");
    press("Enter");
    expect(opened).toEqual(["/alerts/al-2"]);
    press("k");
    press("o");
    expect(opened).toEqual(["/alerts/al-2", "/alerts/al-1"]);
    press("Escape");
    expect(marked()).toEqual([]);
  });

  it("does nothing from an input and with a modifier", async () => {
    render(await AlertsPage({ searchParams: Promise.resolve({}) }));
    const input = document.querySelector("input, select")!;
    press("j", {}, input);
    press("j", { metaKey: true });
    expect(marked()).toEqual([]);
  });
});
