import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api, type PwPropertyOptions } from "@/lib/api";

import { WatchPropertyPicker, WatchScopePicker } from "./WatchPickers";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("property-watch scope picker", () => {
  it("searches real options, shows sizes and disables an oversized group with its reason", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "propertyWatchScopeOptions").mockResolvedValue([
      {
        kind: "LOG_SOURCE_GROUP",
        id: "12",
        qradar_id: 12,
        name: "DMZ firewalls",
        detail: "All / DMZ firewalls",
        synced_source_count: 37,
        scope_state: null,
        scope_reason: null,
      },
      {
        kind: "LOG_SOURCE_GROUP",
        id: "13",
        qradar_id: 13,
        name: "All devices",
        detail: "All",
        synced_source_count: null,
        scope_state: "SCOPE_TOO_LARGE",
        scope_reason: "needs 9 chunks, above the limit of 4",
      },
    ]);
    const select = vi.fn();
    render(
      <WatchScopePicker
        kind="LOG_SOURCE_GROUP"
        selectedId=""
        onKind={vi.fn()}
        onSelect={select}
      />,
    );
    fireEvent.change(screen.getByRole("combobox", { name: "Find log source group" }), {
      target: { value: "DMZ" },
    });
    await act(async () => vi.advanceTimersByTime(200));
    expect(api.propertyWatchScopeOptions).toHaveBeenLastCalledWith(
      "LOG_SOURCE_GROUP",
      "DMZ",
      expect.any(AbortSignal),
    );
    const option = screen.getByRole("option", { name: /All devices.*needs 9 chunks/ });
    expect(option).toBeDisabled();
    expect(screen.getByRole("option", { name: /DMZ firewalls.*37 sources/ })).toBeEnabled();
    expect(screen.getByText(/Split the group or use a type scope/)).toBeInTheDocument();
  });

  it("names an option-read failure rather than showing an empty inventory", async () => {
    vi.useFakeTimers();
    vi.spyOn(api, "propertyWatchScopeOptions").mockRejectedValue(new Error("down"));
    render(
      <WatchScopePicker kind="LOG_SOURCE" selectedId="" onKind={vi.fn()} onSelect={vi.fn()} />,
    );
    await act(async () => vi.advanceTimersByTime(200));
    expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
  });
});

describe("property-watch property picker", () => {
  const options: PwPropertyOptions = {
    status: "AVAILABLE",
    fetched_at: "2026-10-18T08:00:00Z",
    scope_type_ids: [1, 2, 3],
    normalized_fields: ["username"],
    detail: null,
    properties: [
      {
        name: "Action",
        property_type: "String",
        name_allowed: true,
        defined_on_type_ids: [1, 2],
        covers_scope: false,
      },
      {
        name: 'Bad " name',
        property_type: "String",
        name_allowed: false,
        defined_on_type_ids: [1],
        covers_scope: false,
      },
    ],
  };

  it("offers normalized and catalog names with applicability and disables unsafe names", () => {
    render(
      <WatchPropertyPicker
        index={0}
        value={{ kind: "CUSTOM_PROPERTY", name: "" }}
        options={options}
        onChange={vi.fn()}
      />,
    );
    fireEvent.focus(screen.getByRole("combobox", { name: "Property 1" }));
    const list = screen.getByRole("listbox", { name: "Properties for property 1" });
    expect(within(list).getByRole("option", { name: /username/ })).toHaveTextContent("QRadar normalized field");
    expect(within(list).getByRole("option", { name: /Action/ })).toHaveTextContent("Defined on 2 of 3 types");
    expect(within(list).getByRole("option", { name: /Bad/ })).toBeDisabled();
  });

  it("keeps a typed name outside the catalog and renders hostile option text inert", () => {
    const change = vi.fn();
    const { container, rerender } = render(
      <WatchPropertyPicker
        index={0}
        value={{ kind: "CUSTOM_PROPERTY", name: "" }}
        options={{
          ...options,
          properties: [{ ...options.properties[0], name: '<img src=x onerror="x">' }],
        }}
        onChange={change}
      />,
    );
    const input = screen.getByRole("combobox", { name: "Property 1" });
    fireEvent.change(input, { target: { value: "Typed Name" } });
    expect(change).toHaveBeenLastCalledWith({ kind: "CUSTOM_PROPERTY", name: "Typed Name" });
    rerender(
      <WatchPropertyPicker
        index={0}
        value={{ kind: "CUSTOM_PROPERTY", name: "Typed Name" }}
        options={{
          ...options,
          properties: [{ ...options.properties[0], name: '<img src=x onerror="x">' }],
        }}
        onChange={change}
      />,
    );
    expect(screen.getByText(/No catalog entry matches/)).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });
});
