import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";

import { catalog, watch } from "./fixtures";
import { WatchActions } from "./WatchActions";

const navigation = vi.hoisted(() => ({ refresh: vi.fn(), push: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => navigation }));

beforeEach(() => {
  vi.restoreAllMocks();
  navigation.refresh.mockReset();
  navigation.push.mockReset();
});

describe("property-watch enable, disable and delete", () => {
  it("makes enable explicit and states the family switch, cap and search budget", () => {
    render(<WatchActions watch={watch({ enabled: false })} catalog={catalog({ collection_enabled: false })} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    const confirmation = screen.getByRole("group", { name: "Enable watch confirmation" });
    expect(confirmation).toHaveTextContent("family switch is off");
    expect(confirmation).toHaveTextContent("2 of 10 enabled-watch slots");
    expect(confirmation).toHaveTextContent("4 of 20 searches per tick");
    expect(within(confirmation).getByText("PW_COLLECTION_ENABLED").tagName).toBe("CODE");
  });

  it("renders an enable 409 naming the cap or budget exactly", async () => {
    vi.spyOn(api, "updatePropertyWatch").mockRejectedValue(
      new ApiError(409, "Conflict", "enabling would use 42 of the 40-search tick budget"),
    );
    render(<WatchActions watch={watch({ enabled: false })} catalog={catalog()} />);
    fireEvent.click(screen.getByRole("button", { name: "Enable" }));
    fireEvent.click(screen.getByRole("button", { name: "Enable watch" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("42 of the 40-search tick budget");
  });

  it("disables with the optimistic token", async () => {
    vi.spyOn(api, "updatePropertyWatch").mockResolvedValue(watch({ enabled: false }));
    render(<WatchActions watch={watch({ enabled: true })} catalog={catalog()} />);
    fireEvent.click(screen.getByRole("button", { name: "Disable" }));
    expect(api.updatePropertyWatch).toHaveBeenCalledWith(watch().id, {
      expected_updated_at: watch().updated_at,
      enabled: false,
    });
  });

  it("confirms deletion in words and renders the dependency 409 with rule links", async () => {
    vi.spyOn(api, "deletePropertyWatch").mockRejectedValue(
      new ApiError(409, "Conflict", {
        message: "cannot delete the watch: custom rules pin its values",
        rules: [
          { id: "rule-1", name: "Deny flood" },
          { id: "rule-2", name: "Admin silence" },
        ],
      }),
    );
    render(<WatchActions watch={watch()} catalog={catalog()} />);
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    const confirm = screen.getByRole("group", { name: "Delete watch confirmation" });
    expect(confirm).toHaveTextContent("Delete VPN usernames");
    expect(confirm).toHaveTextContent("aggregate values, baselines and incident history");
    fireEvent.click(within(confirm).getByRole("button", { name: "Delete this property watch" }));
    const refusal = await screen.findByText(/cannot delete the watch/);
    expect(refusal).toHaveTextContent("Deny flood");
    expect(screen.getByRole("link", { name: "Deny flood" })).toHaveAttribute("href", "/custom-rules/rule-1");
    expect(screen.getByRole("link", { name: "Admin silence" })).toHaveAttribute("href", "/custom-rules/rule-2");
  });
});
