import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ScheduleControl } from "./ScheduleControl";
import { ScheduleLabel } from "./ScheduleLabel";
import { ApiError, api, type ScheduledSearch } from "@/lib/api";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

function updated(cron: string): ScheduledSearch {
  return { schedule_cron: cron } as ScheduledSearch;
}

beforeEach(() => {
  vi.restoreAllMocks();
  refresh.mockClear();
});

describe("ScheduleLabel", () => {
  it("names a preset", () => {
    render(<ScheduleLabel cron="0 * * * *" />);
    expect(screen.getByText("Every 1 h")).toBeInTheDocument();
    expect(document.querySelector("code")).toBeNull();
  });

  it("shows Custom with the cron kept visible", () => {
    render(<ScheduleLabel cron="17 3 * * 1" />);
    expect(screen.getByText(/Custom/)).toBeInTheDocument();
    expect(screen.getByText("17 3 * * 1").tagName).toBe("CODE");
  });
});

describe("ScheduleControl", () => {
  it("does not restate the section heading as a 'Schedule:' prefix", () => {
    render(<ScheduleControl searchId="s-1" cron="0 * * * *" />);
    expect(screen.queryByText(/^Schedule:/)).not.toBeInTheDocument();
  });

  it("keeps a custom cron on one line", () => {
    render(<ScheduleLabel cron="17 3 * * 1-5" />);
    expect(screen.getByText("17 3 * * 1-5")).toHaveClass("cron");
  });

  it("shows a preset schedule as its label and selects it", () => {
    render(<ScheduleControl searchId="s-1" cron="0 */4 * * *" />);
    expect(screen.getByText("Every 4 h", { selector: "strong" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Change to" })).toHaveValue("14400");
  });

  it("shows a custom cron verbatim and never rewrites it by itself", () => {
    const patch = vi.spyOn(api, "setSearchSchedule");
    render(<ScheduleControl searchId="s-1" cron="17 3 * * 1" />);
    expect(screen.getByText("17 3 * * 1").tagName).toBe("CODE");
    const select = screen.getByRole("combobox", { name: "Change to" });
    expect(select).toHaveValue("custom");
    expect(screen.getByRole("option", { name: "Custom (keep current)" })).toBeInTheDocument();
    expect(patch).not.toHaveBeenCalled();
  });

  it("does not offer 'keep current' for a preset schedule", () => {
    render(<ScheduleControl searchId="s-1" cron="0 * * * *" />);
    expect(screen.queryByRole("option", { name: /custom/i })).not.toBeInTheDocument();
  });

  it("patches the preset's cron, shows success and refreshes", async () => {
    const patch = vi.spyOn(api, "setSearchSchedule").mockResolvedValue(updated("0 */2 * * *"));
    const user = userEvent.setup();
    render(<ScheduleControl searchId="s-1" cron="17 3 * * 1" />);

    await user.selectOptions(screen.getByRole("combobox", { name: "Change to" }), "Every 2 h");

    expect(patch).toHaveBeenCalledWith("s-1", "0 */2 * * *");
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/saved/i));
    expect(screen.getByText("Every 2 h", { selector: "strong" })).toBeInTheDocument();
    expect(document.querySelector("code")).toBeNull();
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("reverts and names the permission on a 403, leaving a custom cron intact", async () => {
    vi.spyOn(api, "setSearchSchedule").mockRejectedValue(new ApiError(403, "forbidden"));
    const user = userEvent.setup();
    render(<ScheduleControl searchId="s-1" cron="17 3 * * 1" />);
    const select = screen.getByRole("combobox", { name: "Change to" });

    await user.selectOptions(select, "Every 5 min");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("search:write");
    expect(alert).toHaveTextContent(/nothing was changed/i);
    expect(select).toHaveValue("custom");
    expect(screen.getByText("17 3 * * 1")).toBeInTheDocument();
    expect(refresh).not.toHaveBeenCalled();
  });

  it("reverts and says what failed on another error", async () => {
    vi.spyOn(api, "setSearchSchedule").mockRejectedValue(new ApiError(422, "cron rejected"));
    const user = userEvent.setup();
    render(<ScheduleControl searchId="s-1" cron="0 * * * *" />);
    const select = screen.getByRole("combobox", { name: "Change to" });

    await user.selectOptions(select, "Every 8 h");

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/not changed/i);
    expect(alert).toHaveTextContent("cron rejected");
    expect(select).toHaveValue("3600");
    expect(refresh).not.toHaveBeenCalled();
  });

  it("is pending while the request is in flight", async () => {
    let resolve!: (v: ScheduledSearch) => void;
    vi.spyOn(api, "setSearchSchedule").mockReturnValue(
      new Promise<ScheduledSearch>((r) => {
        resolve = r;
      }),
    );
    const user = userEvent.setup();
    render(<ScheduleControl searchId="s-1" cron="0 * * * *" />);
    const select = screen.getByRole("combobox", { name: "Change to" });
    await user.selectOptions(select, "Every 15 min");
    expect(screen.getByRole("status")).toHaveTextContent("Saving…");
    expect(select).toBeDisabled();
    resolve(updated("*/15 * * * *"));
    await waitFor(() => expect(select).toBeEnabled());
  });
});
