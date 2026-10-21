import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, type PwPreview } from "@/lib/api";

import { catalog, scope, watch } from "./fixtures";
import { WatchEditor } from "./WatchEditor";

const navigation = vi.hoisted(() => ({ refresh: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("./WatchPickers", () => ({
  WatchScopePicker: ({ onKind, onSelect }: { onKind: (kind: string) => void; onSelect: (value: unknown) => void }) => (
    <button
      type="button"
      onClick={() => {
        onKind("LOG_SOURCE_GROUP");
        onSelect({
          kind: "LOG_SOURCE_GROUP",
          id: "12",
          qradar_id: 12,
          name: "DMZ firewalls",
          detail: "All / DMZ firewalls",
          synced_source_count: 37,
          scope_state: null,
          scope_reason: null,
        });
      }}
    >
      Choose fixture scope
    </button>
  ),
  WatchPropertyPicker: ({ index }: { index: number }) => <div>Property picker {index + 1}</div>,
}));

function propertyOptions() {
  return {
    status: "AVAILABLE" as const,
    fetched_at: "2026-10-18T08:00:00Z",
    scope_type_ids: [1, 2, 3],
    properties: [{ name: "Action", property_type: "String", name_allowed: true, defined_on_type_ids: [1], covers_scope: false }],
    normalized_fields: ["username"],
    detail: null,
  };
}

function preview(over: Partial<PwPreview> = {}): PwPreview {
  return {
    mode: "PREVIEW",
    watch_id: watch().id,
    watch_version: 3,
    window_start: "2026-10-18T07:00:00Z",
    window_end: "2026-10-18T08:00:00Z",
    bucket_seconds: 300,
    searches_sent: 2,
    outcome: "MEASURED",
    reason: null,
    buckets_measured: 12,
    buckets_lower_bound: 0,
    buckets_unmeasured: 0,
    total_events: 1000,
    parsed_events: 900,
    unparsed_events: 100,
    unparsed_bound: "EXACT",
    coverage: 0.9,
    property_parses: true,
    rows_measurement: "EXACT",
    rows: [
      { value_key: "alice", components: ["alice"], events: 600, share: 2 / 3, truncated: false },
      { value_key: '<img src=x onerror="x">', components: ['<img src=x onerror="x">'], events: 300, share: 1 / 3, truncated: false },
    ],
    queries: [
      { search: "GROUPED", text: 'SELECT "username" FROM events' },
      { search: "TOTALS", text: "SELECT SUM(eventcount) FROM events" },
    ],
    ...over,
  };
}

beforeEach(() => {
  vi.restoreAllMocks();
  navigation.refresh.mockReset();
  vi.spyOn(api, "propertyWatchPropertyOptions").mockResolvedValue(propertyOptions());
  vi.spyOn(api, "propertyWatchScope").mockResolvedValue(scope());
  vi.spyOn(api, "updatePropertyWatch").mockResolvedValue(watch());
  vi.spyOn(api, "createPropertyWatch").mockResolvedValue(watch({ enabled: false }));
  vi.spyOn(api, "previewPropertyWatch").mockResolvedValue(preview());
});

async function save() {
  fireEvent.click(screen.getByRole("button", { name: "Save as disabled and preview" }));
  await waitFor(() => expect(api.previewPropertyWatch).toHaveBeenCalled());
}

describe("the pin sentence", () => {
  const cadence = { family: "PROPERTY_WATCH" as const, cadence_seconds: 3600, bucket_seconds: 300, settle_seconds: 120 };

  it("promises no number without a cadence", () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    expect(screen.getByText(/A new pin can be used in a rule after the next property-watch collection\./)).toBeInTheDocument();
  });

  it("states the figure from the catalog's cadence", () => {
    render(<WatchEditor catalog={catalog({ cadence })} mode="create" />);
    expect(screen.getByText(/which runs up to 1 h 7 min after the events arrive/)).toBeInTheDocument();
  });

  it("prefers the watch's own cadence", () => {
    render(
      <WatchEditor
        catalog={catalog({ cadence })}
        mode="edit"
        watch={watch({ cadence: { ...cadence, cadence_seconds: 900 } })}
        initialScope={scope()}
      />,
    );
    expect(screen.getByText(/which runs up to 22 min after the events arrive/)).toBeInTheDocument();
  });
});

describe("property-watch save and preview", () => {
  it("takes the operator create path: saves disabled parameters, then previews the stored id", async () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    fireEvent.change(screen.getByRole("textbox", { name: "Watch name" }), { target: { value: "VPN actions" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose fixture scope" }));
    await save();
    expect(api.createPropertyWatch).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "VPN actions",
        scope_kind: "LOG_SOURCE_GROUP",
        scope_group_id: 12,
        properties: [{ kind: "NORMALIZED", name: "username" }],
      }),
    );
    const body = vi.mocked(api.createPropertyWatch).mock.calls[0][0] as unknown as Record<string, unknown>;
    expect(body).not.toHaveProperty("enabled");
    expect(body).not.toHaveProperty("aql");
    expect(api.previewPropertyWatch).toHaveBeenCalledWith(watch().id);
    expect(await screen.findByText(/saved disabled and the stored definition was previewed/)).toBeInTheDocument();
  });

  it("sends an explicit zero after the operator turns history off", async () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    const history = screen.getByRole("checkbox", {
      name: /Read history from QRadar \(used only for learning\)/,
    });
    expect(history).toBeChecked();
    fireEvent.click(history);
    fireEvent.change(screen.getByRole("textbox", { name: "Watch name" }), { target: { value: "Forward only" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose fixture scope" }));
    await save();
    expect(api.createPropertyWatch).toHaveBeenCalledWith(
      expect.objectContaining({ history_days: 0 }),
    );
  });

  it("patches with expected_updated_at before previewing an edit", async () => {
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />);
    await save();
    expect(api.updatePropertyWatch).toHaveBeenCalledWith(
      watch().id,
      expect.objectContaining({ expected_updated_at: watch().updated_at, enabled: false }),
    );
  });

  it("renders AQL only as escaped read-only code, with no AQL input anywhere", async () => {
    const { container } = render(
      <WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />,
    );
    await save();
    expect(screen.getByText('SELECT "username" FROM events')).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
    for (const field of container.querySelectorAll("input, textarea")) {
      expect((field.getAttribute("name") ?? "").toLowerCase()).not.toContain("aql");
      expect((field.getAttribute("aria-label") ?? "").toLowerCase()).not.toContain("aql");
      expect((field as HTMLInputElement).value).not.toContain("SELECT ");
    }
    expect(container.querySelectorAll("pre code")).toHaveLength(2);
    expect(screen.getAllByText("Read-only")).toHaveLength(2);
    expect(container.querySelector("pre code")?.closest("textarea, [contenteditable]")).toBeNull();
  });

  it("shows the preview coverage, unparsed remainder, per-type reading and lower bounds", async () => {
    vi.spyOn(api, "previewPropertyWatch").mockResolvedValue(
      preview({
        outcome: "PARTIAL",
        reason: "a bucket was cut at the row limit",
        buckets_measured: 11,
        buckets_lower_bound: 1,
        rows_measurement: "LOWER_BOUND",
        unparsed_bound: "UPPER_BOUND",
      }),
    );
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />);
    await save();
    expect(screen.getByText(/11 measured, 1 lower-bound/)).toBeInTheDocument();
    expect(screen.getByText(/at most 100 events/)).toBeInTheDocument();
    expect(screen.getByText(/catalog consulted 3 types/)).toBeInTheDocument();
    expect(screen.getAllByText(/at least 600/).length).toBeGreaterThan(0);
    // The preview has one aggregate per value, so no time series is drawn for it.
    expect(screen.queryByText(/chart|Not collected/i)).toBeNull();
    expect(document.querySelector("canvas, .pw-chart")).toBeNull();
  });

  it.each([
    [
      "scope silence",
      { total_events: 0, parsed_events: 0, unparsed_events: 0, coverage: null, property_parses: null, rows: [] },
      /scope sent no events/,
    ],
    [
      "not applicable",
      { total_events: 100, parsed_events: 0, unparsed_events: 100, coverage: 0, property_parses: false, rows: [] },
      /parsed on none/,
    ],
    [
      "not measured",
      { outcome: "NOT_MEASURED", total_events: null, parsed_events: null, unparsed_events: null, coverage: null, property_parses: null, rows_measurement: null, rows: [] },
      /measured no bucket/,
    ],
  ])("designs the %s empty preview", async (_name, over, words) => {
    vi.spyOn(api, "previewPropertyWatch").mockResolvedValue(preview(over as Partial<PwPreview>));
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />);
    await save();
    expect(screen.getAllByText(words).length).toBeGreaterThan(0);
    expect(screen.queryByText(/chart/i)).toBeNull();
  });
});

describe("property-watch history controls", () => {
  const historyRequestName = /Read history from QRadar/;

  function addPinnedValue(value = "svc-admin") {
    fireEvent.click(screen.getByRole("button", { name: "Add pinned value" }));
    fireEvent.change(screen.getByRole("textbox", { name: "username" }), {
      target: { value },
    });
  }

  it("states the server switch reason and disables the request", () => {
    render(
      <WatchEditor
        catalog={catalog({ history: { ...catalog().history, enabled: false } })}
        mode="create"
      />,
    );
    expect(screen.getByRole("checkbox", { name: /Read history from QRadar/ })).toBeDisabled();
    expect(screen.getByText("History reading is switched off on this server.")).toBeInTheDocument();
  });

  it("uses the per-reading setting for a pinned watch but permits an unpinned step", () => {
    const settingLimited = catalog({
      history: { ...catalog().history, searches_per_tick: 2, searches_free: 16 },
    });
    const pinned = render(<WatchEditor catalog={settingLimited} mode="create" />);
    addPinnedValue();
    expect(screen.getByRole("checkbox", { name: historyRequestName })).toBeDisabled();
    expect(
      screen.getByText(
        "History needs 3 searches per tick for this watch; this server gives a history reading at most 2 (PW_BOOTSTRAP_SEARCHES_PER_TICK).",
      ),
    ).toBeInTheDocument();

    pinned.unmount();
    render(<WatchEditor catalog={settingLimited} mode="create" />);
    expect(screen.getByRole("checkbox", { name: historyRequestName })).toBeEnabled();
  });

  it("keeps the free-budget reason when the tick budget binds", () => {
    const freeLimited = catalog({
      history: { ...catalog().history, searches_per_tick: 12, searches_free: 2 },
    });
    render(<WatchEditor catalog={freeLimited} mode="create" />);
    addPinnedValue();
    expect(screen.getByRole("checkbox", { name: historyRequestName })).toBeDisabled();
    expect(
      screen.getByText("History needs 3 free searches per tick for this watch; 2 are free."),
    ).toBeInTheDocument();
  });

  it("permits history when both limits exactly pay for a pinned step", () => {
    const exact = catalog({
      history: { ...catalog().history, searches_per_tick: 3, searches_free: 3 },
    });
    render(<WatchEditor catalog={exact} mode="create" />);
    addPinnedValue();
    expect(screen.getByRole("checkbox", { name: historyRequestName })).toBeEnabled();
  });

  it("sends zero history days when the per-reading setting disables the option", async () => {
    const settingLimited = catalog({
      history: { ...catalog().history, searches_per_tick: 2, searches_free: 16 },
    });
    render(<WatchEditor catalog={settingLimited} mode="create" />);
    addPinnedValue();
    fireEvent.change(screen.getByRole("textbox", { name: "Watch name" }), {
      target: { value: "Setting limited" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Choose fixture scope" }));
    await save();
    expect(api.createPropertyWatch).toHaveBeenCalledWith(
      expect.objectContaining({ history_days: 0 }),
    );
  });

  it("states the free and needed searches for pinned and unpinned watches", () => {
    const low = catalog({ history: { ...catalog().history, searches_free: 1 } });
    const first = render(<WatchEditor catalog={low} mode="create" />);
    expect(screen.getByText("History needs 2 free searches per tick for this watch; 1 is free.")).toBeInTheDocument();
    first.unmount();
    render(<WatchEditor catalog={low} mode="edit" watch={watch({ coverage: null, collected_through: null, evaluated_through: null })} initialScope={scope()} />);
    expect(screen.getByText("History needs 3 free searches per tick for this watch; 1 is free.")).toBeInTheDocument();
  });

  it("shows unchecked, says nothing of history and sends zero when the budget is short", async () => {
    const low = catalog({ history: { ...catalog().history, searches_free: 1 } });
    render(<WatchEditor catalog={low} mode="create" />);
    expect(screen.getByRole("checkbox", { name: /Read history from QRadar/ })).not.toBeChecked();
    expect(document.querySelector(".pw-editor-summary")?.textContent).not.toMatch(/history/);
    fireEvent.change(screen.getByRole("textbox", { name: "Watch name" }), { target: { value: "Budget" } });
    fireEvent.click(screen.getByRole("button", { name: "Choose fixture scope" }));
    await save();
    expect(api.createPropertyWatch).toHaveBeenCalledWith(expect.objectContaining({ history_days: 0 }));
  });

  it("locks an existing watch after collection and states all three limitations", () => {
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />);
    expect(screen.getByRole("checkbox", { name: /Read history from QRadar/ })).toBeDisabled();
    expect(screen.getByText(/cannot change after history or collected buckets exist/)).toBeInTheDocument();
    expect(screen.getByText(/Events whose property was not parsed then/)).toBeInTheDocument();
    expect(screen.getByText(/membership of a group is today's/)).toBeInTheDocument();
    expect(screen.getByText(/Nothing is judged until the reading finishes/)).toBeInTheDocument();
  });
});

describe("property-watch editor on an enabled watch", () => {
  it("says once that saving disables the watch, and not for a disabled watch", () => {
    const { unmount } = render(
      <WatchEditor catalog={catalog()} mode="edit" watch={watch({ enabled: true })} initialScope={scope()} />,
    );
    expect(screen.getAllByText(/Saving here disables it until you enable it again/)).toHaveLength(1);
    unmount();
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch({ enabled: false })} initialScope={scope()} />);
    expect(screen.queryByText(/Saving here disables it/)).toBeNull();
  });
});

describe("property-watch editor refusals", () => {
  it("renders every 422 reason exactly as sent", async () => {
    vi.spyOn(api, "updatePropertyWatch").mockRejectedValue(
      new ApiError(422, "invalid", { message: "invalid", reasons: ["reason one", "reason two"] }),
    );
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save as disabled and preview" }));
    const reasons = await screen.findByRole("alert", { name: "Server validation reasons" });
    expect(within(reasons).getByText("reason one")).toBeInTheDocument();
    expect(within(reasons).getByText("reason two")).toBeInTheDocument();
    expect(api.previewPropertyWatch).not.toHaveBeenCalled();
  });

  it("does not overwrite a version conflict and offers reload", async () => {
    vi.spyOn(api, "updatePropertyWatch").mockRejectedValue(
      new ApiError(409, "Conflict", {
        message: "watch version conflict",
        current_version: "2026-10-18T08:00:00+00:00",
        updated_by: "grace",
      }),
    );
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save as disabled and preview" }));
    expect(await screen.findByText(/changed by grace/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reload the current watch" }));
    expect(navigation.refresh).toHaveBeenCalled();
  });

  it("names the rules that refuse a scope or property change", async () => {
    vi.spyOn(api, "updatePropertyWatch").mockRejectedValue(
      new ApiError(409, "Conflict", {
        message: "cannot change the scope or properties",
        rules: [{ id: "rule-1", name: "Deny flood" }],
      }),
    );
    render(<WatchEditor catalog={catalog()} mode="edit" watch={watch()} initialScope={scope()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save as disabled and preview" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Deny flood");
    expect(screen.getByRole("link", { name: "Deny flood" })).toHaveAttribute(
      "href",
      "/custom-rules/rule-1",
    );
  });

  it("says when to retry a rate-limited preview and keeps the saved watch", async () => {
    vi.spyOn(api, "previewPropertyWatch").mockRejectedValue(
      new ApiError(429, "Too Many Requests", "5 per 1 minute"),
    );
    render(<WatchEditor catalog={catalog({ preview_rate_limit: "5/minute" })} mode="edit" watch={watch()} initialScope={scope()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save as disabled and preview" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("rate limited to 5/minute");
    expect(alert).toHaveTextContent("Try again in about a minute");
    expect(alert).toHaveTextContent("saved disabled");
  });

  it("shows collection off and inline pin reasons without inventing a preview", () => {
    render(
      <WatchEditor
        catalog={catalog({ collection_enabled: false })}
        mode="edit"
        watch={watch({ pinned_values: [["O'Brien"]] })}
        initialScope={scope()}
      />,
    );
    expect(screen.getByText("PW_COLLECTION_ENABLED").tagName).toBe("CODE");
    expect(screen.getByText("COLLECTION OFF.")).toBeInTheDocument();
    expect(screen.getByText(/Quotes, backslashes/)).toBeInTheDocument();
    expect(screen.getByText("No preview has run for this draft.")).toBeInTheDocument();
  });
});
