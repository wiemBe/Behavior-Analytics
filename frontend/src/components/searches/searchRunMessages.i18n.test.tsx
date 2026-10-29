import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { ExecutionsTable } from "./ExecutionsTable";
import { SearchEditor } from "./SearchEditor";
import { SearchRunControl } from "./SearchRunControl";
import { classifySaveError } from "./authoring";
import { ApiError, api, type SearchExecution } from "@/lib/api";
import trBackend from "../../../messages/tr/backend.json";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));

function run(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1", search_id: "s-1", query_version: 1, status: "TIMEOUT", trigger: "SCHEDULED",
    triggered_by: null, ariel_search_id: null, ariel_status: null,
    started_at: "2026-10-29T10:00:00Z", completed_at: "2026-10-29T10:05:00Z",
    duration_ms: 300000, result_count: null, truncated: false, error_type: "TIMEOUT",
    error_message: "search exceeded timeout of 300s", retry_count: 0, threshold_breached: false,
    ...over,
  };
}

const TIMEOUT = {
  error_message_code: "search.run.timeout",
  error_message_params: { timeout: 300 },
};
const CAPACITY = {
  error_type: "CAPACITY",
  status: "FAILED",
  error_message:
    "No Ariel permit became available within 30 seconds (per-console cap 3, global cap 6)",
  error_message_code: "search.run.capacity",
  error_message_params: { waited: 30, instance_cap: 3, global_cap: 6 },
};

const FLOOR_TEXT =
  "schedule_cron '*/2 * * * *' (UTC) fires 120 seconds apart " +
  "(2026-10-29T10:00:00+00:00 then 2026-10-29T10:02:00+00:00); " +
  "a search must fire at least 300 seconds apart";
const FLOOR_BODY = {
  detail: FLOOR_TEXT,
  detail_code: "search.cron.below_floor",
  detail_params: {
    cron: "'*/2 * * * *'", timezone: "UTC", gap: 120,
    previous: "2026-10-29T10:00:00+00:00", next: "2026-10-29T10:02:00+00:00", min_seconds: 300,
  },
};
const floor = () => new ApiError(422, FLOOR_TEXT, FLOOR_TEXT, FLOOR_BODY);

const BUDGET_TEXT =
  "the Ariel search budget is full (6 of 6 in flight); waited 30 s; nothing was sent to QRadar";
const budget = (over: Record<string, unknown> = {}) =>
  new ApiError(503, BUDGET_TEXT, BUDGET_TEXT, {
    detail: BUDGET_TEXT,
    detail_code: "search.budget.full",
    detail_params: {
      held: { code: "search.budget.held_of", params: { held: 6, cap: 6 } },
      waited: 30,
    },
    ...over,
  });

// The Turkish `backend` catalog flattened to `backend.<code>` keys, for the pure-helper test.
function flat(node: unknown, path: string, out: Record<string, string>): Record<string, string> {
  if (typeof node === "string") out[path] = node;
  else if (node && typeof node === "object") {
    for (const [key, value] of Object.entries(node)) flat(value, `${path}.${key}`, out);
  }
  return out;
}
const TR_BACKEND = flat(trBackend, "backend", {});

beforeEach(() => vi.restoreAllMocks());

function errorCell(index: number): HTMLElement {
  return screen.getAllByRole("row")[index + 1].querySelectorAll("td")[8] as HTMLElement;
}

describe("run history: the message under the error type", () => {
  it("words a coded timeout and a refused permit in Turkish", () => {
    setTestLocale("tr");
    render(<ExecutionsTable executions={[run(TIMEOUT), run({ id: "e-2", ...CAPACITY })]} />);
    expect(errorCell(0)).toHaveTextContent("Arama, 300 sn zaman aşımını aştı");
    expect(errorCell(1)).toHaveTextContent(
      "30 sn içinde Ariel arama izni alınamadı (konsol başına üst sınır 3, genel üst sınır 6)",
    );
  });

  it("shows the server's English in English, code or not", () => {
    setTestLocale("en");
    render(<ExecutionsTable executions={[run(TIMEOUT), run({ id: "e-2", ...CAPACITY })]} />);
    expect(errorCell(0)).toHaveTextContent("search exceeded timeout of 300s");
    expect(errorCell(1)).toHaveTextContent(/No Ariel permit became available within 30 seconds/);
  });

  it("shows free text and an old row as stored, in Turkish too", () => {
    setTestLocale("tr");
    render(
      <ExecutionsTable
        executions={[
          run({ error_message: "QRadar says no; second", error_type: "SEARCH_FAILED" }),
          run({ id: "e-2" }),
        ]}
      />,
    );
    expect(errorCell(0)).toHaveTextContent("QRadar says no; second");
    expect(errorCell(1)).toHaveTextContent("search exceeded timeout of 300s");
  });

  it("falls back to the stored English for a code the catalog does not know", () => {
    setTestLocale("tr");
    render(
      <ExecutionsTable
        executions={[run({ error_message_code: "search.run.unheard_of", error_message_params: {} })]}
      />,
    );
    expect(errorCell(0)).toHaveTextContent("search exceeded timeout of 300s");
  });

  it("draws nothing under a run with no message", () => {
    setTestLocale("tr");
    render(
      <ExecutionsTable
        executions={[run({ status: "COMPLETED", error_type: null, error_message: null })]}
      />,
    );
    expect(errorCell(0)).toHaveTextContent("—");
    expect(errorCell(0).querySelector(".run-error")).toBeNull();
  });
});

describe("the cron floor refusal in the editor", () => {
  it("classifies a coded floor refusal as a schedule error, worded in Turkish", () => {
    setTestLocale("tr");
    const say = (key: string, values?: Record<string, string | number>) => {
      const template = TR_BACKEND[key];
      return template.replace(/\{(\w+)\}/g, (_, name: string) => String(values?.[name] ?? ""));
    };
    say.has = (key: string) => key in TR_BACKEND;
    const refusal = classifySaveError(floor(), { locale: "tr", t: say as never });
    expect(refusal.kind).toBe("schedule");
    expect(refusal).toMatchObject({
      detail: expect.stringContaining("120\u00a0sn arayla tetikleniyor"),
    });
  });

  it("groups and localises a fractional or large gap in Turkish", () => {
    setTestLocale("tr");
    const say = (key: string, values?: Record<string, string | number>) =>
      TR_BACKEND[key].replace(/\{(\w+)\}/g, (_, name: string) => String(values?.[name] ?? ""));
    say.has = (key: string) => key in TR_BACKEND;
    const gap = (value: number) =>
      classifySaveError(
        new ApiError(422, FLOOR_TEXT, FLOOR_TEXT, {
          ...FLOOR_BODY,
          detail_params: { ...FLOOR_BODY.detail_params, gap: value },
        }),
        { locale: "tr", t: say as never },
      );
    expect(gap(299.5)).toMatchObject({ detail: expect.stringContaining("299,5\u00a0sn arayla") });
    expect(gap(59.999)).toMatchObject({ detail: expect.stringContaining("59,999\u00a0sn arayla") });
    expect(gap(0)).toMatchObject({ detail: expect.stringContaining("(UTC) 0\u00a0sn arayla") });
  });

  it("shows the Turkish sentence under the schedule", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "createSearch").mockRejectedValue(floor());
    const user = userEvent.setup();
    render(<SearchEditor mode="create" />);
    await user.type(screen.getByRole("textbox", { name: "Ad" }), "Fast");
    await user.type(screen.getByRole("textbox", { name: "AQL" }), "SELECT 1");
    await user.click(screen.getByRole("button", { name: "Devre dışı olarak kaydet" }));
    const line = await screen.findByText(/schedule_cron '\*\/2 \* \* \* \*' \(UTC\) 120\ssn arayla/i);
    expect(line.textContent).toContain("bir arama en az 300\u00a0sn arayla tetiklenmelidir");
    expect(line.textContent).toContain("2026-10-29 10:00:00 UTC, ardından 2026-10-29 10:02:00 UTC");
    expect(line.textContent).toContain("ardından");
    expect(line.textContent).not.toContain("2026-10-29T10:00:00+00:00");
  });

  it("keeps the English sentence in English", async () => {
    setTestLocale("en");
    vi.spyOn(api, "createSearch").mockRejectedValue(floor());
    const user = userEvent.setup();
    render(<SearchEditor mode="create" />);
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Fast");
    await user.type(screen.getByRole("textbox", { name: "AQL" }), "SELECT 1");
    await user.click(screen.getByRole("button", { name: "Save as disabled" }));
    expect(await screen.findByText(FLOOR_TEXT)).toBeInTheDocument();
  });

  it("still recognises an uncoded floor sentence from an older server", () => {
    setTestLocale("en");
    const old = new ApiError(422, FLOOR_TEXT, FLOOR_TEXT, { detail: FLOOR_TEXT });
    expect(classifySaveError(old)).toEqual({ kind: "schedule", detail: FLOOR_TEXT });
  });

  it("does not take a coded sentence of another family for a schedule error", () => {
    setTestLocale("en");
    const other = new ApiError(422, "schedule_cron is odd", "schedule_cron is odd", {
      detail: "schedule_cron is odd",
      detail_code: "search.threshold.unreachable",
      detail_params: {},
    });
    expect(classifySaveError(other).kind).toBe("error");
  });
});

describe("the manual-run budget refusal", () => {
  it("shows the budget sentence in Turkish with the held pair", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "runSearch").mockRejectedValue(budget());
    const user = userEvent.setup();
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Şimdi çalıştır" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Ariel arama bütçesi dolu (eşzamanlı çalışan: 6 / 6); 30 sn beklendi; QRadar'a hiçbir şey gönderilmedi.",
    );
  });

  it("words the cap variant and a fractional wait in Turkish", async () => {
    setTestLocale("tr");
    vi.spyOn(api, "runSearch").mockRejectedValue(
      budget({
        detail_params: { held: { code: "search.budget.held_cap", params: { cap: 6 } }, waited: 2.5 },
      }),
    );
    const user = userEvent.setup();
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Şimdi çalıştır" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ariel arama bütçesi dolu (eşzamanlı çalışan: üst sınır 6); 2,5 sn beklendi; QRadar'a hiçbir şey gönderilmedi.",
    );
  });

  it("shows the server's English sentence, capitalised and ended, in English", async () => {
    setTestLocale("en");
    vi.spyOn(api, "runSearch").mockRejectedValue(budget());
    const user = userEvent.setup();
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent(
        "The Ariel search budget is full (6 of 6 in flight); waited 30 s; nothing was sent to QRadar.",
      ),
    );
  });

  it("keeps the generic line for any other 503", async () => {
    setTestLocale("en");
    const other = new ApiError(503, "Service Unavailable", "Service Unavailable", {
      detail: "Service Unavailable",
    });
    vi.spyOn(api, "runSearch").mockRejectedValue(other);
    const user = userEvent.setup();
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The run did not start. Reload the page and try again.",
    );
  });
});
