import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchEnableControl } from "./SearchEnableControl";
import { ApiError, api } from "@/lib/api";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

beforeEach(() => {
  refresh.mockClear();
  vi.restoreAllMocks();
});

describe("SearchEnableControl", () => {
  it("enables with a patch of enabled alone", async () => {
    const user = userEvent.setup();
    const setEnabled = vi.spyOn(api, "setSearchEnabled").mockResolvedValue({} as never);
    render(<SearchEnableControl id="s-1" enabled={false} />);
    await user.click(screen.getByRole("button", { name: "Enable search" }));
    await waitFor(() => expect(setEnabled).toHaveBeenCalledTimes(1));
    expect(setEnabled).toHaveBeenCalledWith("s-1", true);
    expect(setEnabled.mock.calls[0]).toHaveLength(2);
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("disables with enabled false and no other field", async () => {
    const user = userEvent.setup();
    const setEnabled = vi.spyOn(api, "setSearchEnabled").mockResolvedValue({} as never);
    render(<SearchEnableControl id="s-1" enabled />);
    await user.click(screen.getByRole("button", { name: "Disable search" }));
    await waitFor(() => expect(setEnabled).toHaveBeenCalledWith("s-1", false));
  });

  it("shows the cap limit and says to disable another search", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "setSearchEnabled").mockRejectedValue(
      new ApiError(409, "the limit is 25 enabled searches", "the limit is 25 enabled searches"),
    );
    render(<SearchEnableControl id="s-1" enabled={false} />);
    await user.click(screen.getByRole("button", { name: "Enable search" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Not enabled: the limit is 25 enabled searches. Disable another search, then enable this one.",
    );
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Enable search" })).toBeEnabled();
  });
});
