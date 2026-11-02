import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type PwPropertyOptions } from "@/lib/api";
import { setTestLocale } from "../../../vitest.setup";

import { catalog } from "./fixtures";
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
  // A labelled input that hands itself back, so a test can see where focus lands.
  WatchPropertyPicker: ({
    index,
    inputRef,
  }: {
    index: number;
    inputRef?: (input: HTMLInputElement | null) => void;
  }) => (
    <div>
      <label htmlFor={`pw-test-property-${index}`}>Property {index + 1}</label>
      <input id={`pw-test-property-${index}`} ref={inputRef} />
    </div>
  ),
}));

function propertyOptions(): PwPropertyOptions {
  return {
    status: "AVAILABLE",
    fetched_at: "2026-11-02T08:00:00Z",
    scope_type_ids: [1, 2, 3],
    properties: [{ name: "Action", property_type: "String", name_allowed: true, defined_on_type_ids: [1], covers_scope: false }],
    normalized_fields: ["username"],
    detail: null,
  };
}

beforeEach(() => {
  navigation.refresh.mockReset();
  setTestLocale("en");
  vi.spyOn(api, "propertyWatchPropertyOptions").mockResolvedValue(propertyOptions());
});

async function chooseScope() {
  fireEvent.click(screen.getByRole("button", { name: "Choose fixture scope" }));
  await waitFor(() => expect(api.propertyWatchPropertyOptions).toHaveBeenCalled());
}

function combinationBox(locale: "en" | "tr" = "en") {
  return screen.getByRole("checkbox", {
    name: locale === "tr" ? /Yeni birleşim/ : /New combination/,
  });
}

/** The visible reason the checkbox describes: mounted, reachable, and never tooltip-only. */
function reasonFor(box: HTMLElement) {
  const id = box.getAttribute("aria-describedby");
  expect(id, "the disabled box describes its reason").toBeTruthy();
  const reason = document.getElementById(id ?? "");
  expect(reason).not.toBeNull();
  return reason!;
}

/** The add button the reason offers in place: the reason's sibling in the option container. */
function inPlaceButton(box: HTMLElement) {
  const option = reasonFor(box).closest(".pw-detector-item");
  const button = option?.querySelector<HTMLButtonElement>("button");
  expect(button, "the reason offers the add action in place").not.toBeNull();
  return button!;
}

/**
 * What either add button must achieve: the second picker exists and holds focus, the box
 * enables, sheds its reason and can be ticked.
 */
function secondPropertyIsLive(box: HTMLElement) {
  expect(screen.getByRole("textbox", { name: "Property 2" })).toHaveFocus();
  expect(box).toBeEnabled();
  expect(box).not.toHaveAttribute("aria-describedby");
  fireEvent.click(box);
  expect(box).toBeChecked();
}

describe("the blocked New combination option (en)", () => {
  it("shows the reason under the option, outside the label, with the add action beside it", async () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    const box = combinationBox();
    expect(box).toBeDisabled();
    expect(reasonFor(box)).toHaveTextContent("Choose the scope first.");
    await chooseScope();
    const reason = reasonFor(box);
    expect(reason).toHaveTextContent("Needs two properties.");
    // The reason and its button live in the option container, not inside the label: a button
    // inside it would flow into the checkbox's accessible name.
    const label = box.closest("label");
    expect(label).not.toBeNull();
    expect(label!.contains(reason)).toBe(false);
    expect(label!.querySelector("button")).toBeNull();
    expect(box).toHaveAccessibleName(/New combination/);
    // The button sits outside the label, so its text is not part of the accessible name.
    expect(box).not.toHaveAccessibleName(/Add a second property/);
    expect(within(label!).queryByRole("button")).toBeNull();
    expect(within(reason.parentElement as HTMLElement).getByRole("button", { name: "Add a second property" }))
      .toBeInTheDocument();
  });

  it("adds the second property in place with the same default and pin padding, focuses its picker and enables the box", async () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    await chooseScope();
    const box = combinationBox();
    fireEvent.click(screen.getByRole("button", { name: "Add pinned value" }));
    fireEvent.click(inPlaceButton(box));
    // The pinned value grew a second part, exactly as the section-2 button pads it.
    expect(screen.getAllByRole("textbox", { name: "username" })).toHaveLength(2);
    secondPropertyIsLive(box);
  });

  it("the section-2 add button and the in-place button add the property the same way", async () => {
    const sectionAdd = () =>
      within(screen.getByRole("group", { name: "2. Properties" })).getByRole("button", {
        name: "Add a second property",
      });

    const first = render(<WatchEditor catalog={catalog()} mode="create" />);
    await chooseScope();
    fireEvent.click(sectionAdd());
    secondPropertyIsLive(combinationBox());
    first.unmount();

    render(<WatchEditor catalog={catalog()} mode="create" />);
    await chooseScope();
    const box = combinationBox();
    fireEvent.click(inPlaceButton(box));
    secondPropertyIsLive(box);
  });

  it("without a scope the reason says to choose the scope first and offers no button", () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    const box = combinationBox();
    expect(box).toBeDisabled();
    const reason = reasonFor(box);
    expect(reason).toHaveTextContent("Choose the scope first.");
    expect(within(reason.parentElement as HTMLElement).queryByRole("button")).toBeNull();
  });

  it("at the property ceiling the reason keeps the validation sentence and offers no button", async () => {
    render(<WatchEditor catalog={catalog({ max_properties: 1 })} mode="create" />);
    await chooseScope();
    const box = combinationBox();
    expect(box).toBeDisabled();
    const reason = reasonFor(box);
    expect(reason).toHaveTextContent("New-combination detection needs exactly two properties.");
    expect(within(reason.parentElement as HTMLElement).queryByRole("button")).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a second property" })).toBeNull();
  });

  it("removing the second property unticks the box, disables it and returns the reason", async () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    await chooseScope();
    const box = combinationBox();
    fireEvent.click(inPlaceButton(box));
    fireEvent.click(box);
    expect(box).toBeChecked();
    fireEvent.click(screen.getByRole("button", { name: "Remove property 2" }));
    expect(box).toBeDisabled();
    expect(box).not.toBeChecked();
    expect(reasonFor(box)).toHaveTextContent("Needs two properties");
  });
});

describe("the blocked New combination option (tr)", () => {
  beforeEach(() => setTestLocale("tr"));

  it("gives the reason and its action in Turkish", async () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    await chooseScope();
    const box = combinationBox("tr");
    expect(box).toBeDisabled();
    expect(reasonFor(box)).toHaveTextContent("İki özellik gerekir.");
    const label = box.closest("label");
    expect(label!.contains(reasonFor(box))).toBe(false);
    expect(box).toHaveAccessibleName(/Yeni birleşim/);
    expect(box).not.toHaveAccessibleName(/İkinci özellik ekle/);
    expect(within(reasonFor(box).parentElement as HTMLElement).getByRole("button", { name: "İkinci özellik ekle" }))
      .toBeInTheDocument();
  });

  it("says to choose the scope first, in Turkish, with no button", () => {
    render(<WatchEditor catalog={catalog()} mode="create" />);
    const reason = reasonFor(combinationBox("tr"));
    expect(reason).toHaveTextContent("Önce scope'u seçin.");
    expect(within(reason.parentElement as HTMLElement).queryByRole("button")).toBeNull();
  });
});
