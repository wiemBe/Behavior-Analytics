import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type PwPropertyOptions } from "@/lib/api";

import { WatchPropertyPicker } from "./WatchPickers";

const options: PwPropertyOptions = {
  status: "AVAILABLE", fetched_at: "2026-11-20T08:00:00Z", catalog_status: "COMPLETE",
  scope_type_ids: [1], normalized_fields: ["username"], detail: null,
  applicability_complete: true, limit_reached: false,
  properties: [{ name: "Action", name_allowed: true, property_type: "string",
    defined_on_type_ids: [1], covers_scope: true }],
};

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe("stored property options", () => {
  it.each([
    ["NEVER", /not collected/], ["REFRESHING", /refreshing/], ["FAILED", /refresh failed/],
    ["STALE", /stale/], ["COMPLETE", /complete/], ["PARTIAL", /incomplete/],
  ] as const)("explains the %s snapshot in one short line", (state, message) => {
    render(<WatchPropertyPicker index={0} value={{ kind: "CUSTOM_PROPERTY", name: "" }}
      options={{ ...options, catalog_status: state,
        status: state === "NEVER" ? "UNAVAILABLE" : "AVAILABLE",
        properties: [], applicability_complete: state !== "PARTIAL" }} onChange={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent(message);
    expect(screen.getByRole("status").textContent?.split(/\s+/).length).toBeLessThanOrEqual(14);
    fireEvent.focus(screen.getByRole("combobox"));
    if (["NEVER", "REFRESHING", "FAILED", "PARTIAL"].includes(state)) {
      expect(screen.queryByText(/No catalog match/)).toBeNull();
    }
  });

  it("encodes q and the limit in the API request", async () => {
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify(options)));
    await api.propertyWatchPropertyOptions("LOG_SOURCE_TYPE", "212", "Action_%", undefined, 75);
    const url = new URL(String(fetch.mock.calls[0][0]));
    expect(url.searchParams.get("q")).toBe("Action_%");
    expect(url.searchParams.get("limit")).toBe("75");
    await api.propertyWatchPropertyOptions("LOG_SOURCE_TYPE", "212");
    const initial = new URL(String(fetch.mock.calls[1][0]));
    expect(initial.searchParams.get("q") ?? "").toBe("");
    expect(initial.searchParams.get("limit")).toBe("200");
  });

  it("marks a bounded page and unknown applicability without blocking free typing", () => {
    const change = vi.fn();
    render(<WatchPropertyPicker index={0} value={{ kind: "CUSTOM_PROPERTY", name: "" }}
      options={{ ...options, catalog_status: "PARTIAL", applicability_complete: false,
        limit_reached: true }} onChange={change} />);
    fireEvent.focus(screen.getByRole("combobox"));
    expect(screen.getByText(/Showing 1 of more.*refine the search/)).toBeInTheDocument();
    expect(screen.getByRole("option", { name: /Action/ })).toHaveTextContent("Applicability unknown");
    expect(screen.queryByText(/On 1 of 1/)).toBeNull();
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "Typed property" } });
    expect(change).toHaveBeenLastCalledWith({ kind: "CUSTOM_PROPERTY", name: "Typed property" });
  });

  it("debounces q, sends a bound, and ignores a late search from the previous scope", async () => {
    vi.useFakeTimers();
    let finish: (value: PwPropertyOptions) => void = () => {};
    const search = vi.spyOn(api, "propertyWatchPropertyOptions")
      .mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }))
      .mockResolvedValue({ ...options, properties: [{ ...options.properties[0], name: "Second" }] });
    const props = { index: 0, value: { kind: "CUSTOM_PROPERTY" as const, name: "Action" },
      options, onChange: vi.fn(), scopeKind: "LOG_SOURCE_GROUP" as const, scopeId: "10" };
    const { rerender } = render(<WatchPropertyPicker {...props} />);
    expect(screen.getByRole("status")).toHaveTextContent("Loading property options");
    await act(async () => vi.advanceTimersByTime(200));
    expect(search).toHaveBeenLastCalledWith("LOG_SOURCE_GROUP", "10", "Action", expect.any(AbortSignal), 200);
    const signal = search.mock.calls[0][3];
    rerender(<WatchPropertyPicker {...props} scopeId="20" value={{ ...props.value, name: "Second" }} />);
    expect(signal?.aborted).toBe(true);
    await act(async () => vi.advanceTimersByTime(200));
    await act(async () => finish({ ...options, catalog_status: "FAILED" }));
    fireEvent.focus(screen.getByRole("combobox"));
    expect(screen.getByRole("option", { name: /Second/ })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Property catalog complete");
    expect(screen.queryByText(/refresh failed/)).toBeNull();
  });

  it("reports a search failure and retries when the text changes", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "propertyWatchPropertyOptions").mockRejectedValueOnce(new Error("down"))
      .mockResolvedValue(options);
    const props = { index: 0, value: { kind: "CUSTOM_PROPERTY" as const, name: "Act" },
      options, onChange: vi.fn(), scopeKind: "LOG_SOURCE_TYPE" as const, scopeId: "1" };
    const { rerender } = render(<WatchPropertyPicker {...props} />);
    await act(async () => vi.advanceTimersByTime(200));
    expect(screen.getByRole("status")).toHaveTextContent(/failed to load.*retry/);
    fireEvent.focus(screen.getByRole("combobox"));
    expect(screen.queryByRole("option", { name: /Action/ })).toBeNull();
    rerender(<WatchPropertyPicker {...props} value={{ ...props.value, name: "Action" }} />);
    await act(async () => vi.advanceTimersByTime(200));
    expect(screen.getByRole("status")).toHaveTextContent("Property catalog complete");
  });
});
