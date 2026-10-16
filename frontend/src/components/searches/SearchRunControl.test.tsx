import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SearchRunControl } from "./SearchRunControl";
import { ApiError, api, type SearchExecution } from "@/lib/api";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh }) }));

function execution(over: Partial<SearchExecution> = {}): SearchExecution {
  return {
    id: "e-1",
    search_id: "s-1",
    query_version: 2,
    status: "COMPLETED",
    trigger: "MANUAL",
    triggered_by: "ada",
    ariel_search_id: null,
    ariel_status: null,
    started_at: null,
    completed_at: null,
    duration_ms: 900,
    result_count: 42,
    truncated: false,
    error_type: null,
    error_message: null,
    retry_count: 0,
    threshold_breached: false,
    ...over,
  };
}

beforeEach(() => {
  refresh.mockClear();
  vi.restoreAllMocks();
});

describe("SearchRunControl", () => {
  it("runs the stored search by id and reports a measured count", async () => {
    const user = userEvent.setup();
    const run = vi.spyOn(api, "runSearch").mockResolvedValue(execution());
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(run).toHaveBeenCalledTimes(1));
    expect(run).toHaveBeenCalledWith("s-1");
    expect(run.mock.calls[0]).toHaveLength(1);
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("returned 42 rows");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("reports a truncated run as a lower bound", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(
      execution({ truncated: true, result_count: 500, threshold_breached: false }),
    );
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("at least 500");
    expect(status).toHaveTextContent("no verdict");
    expect(status).not.toHaveTextContent("returned 500");
  });

  it("names a failed run's error type and omits the consecutive-failure count until refresh", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(
      execution({ status: "FAILED", result_count: null, error_type: "SEARCH_FAILED" }),
    );
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Error type SEARCH_FAILED");
    expect(status).toHaveTextContent("QRadar reported that the search failed.");
    expect(status).toHaveTextContent("after 3 consecutive failed runs");
    expect(status).not.toHaveTextContent("recorded:");
  });

  it("ignores a second click while the run is in flight", async () => {
    let release: (value: SearchExecution) => void = () => {};
    const run = vi.spyOn(api, "runSearch").mockReturnValue(
      new Promise((resolve) => {
        release = resolve;
      }),
    );
    const user = userEvent.setup();
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("button", { name: "Running…" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Running…" }));
    await act(async () => {
      release(execution());
    });
    await waitFor(() => expect(screen.queryByRole("status")).not.toBeNull());
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("names search:execute on a 403 and stays retryable", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockRejectedValue(new ApiError(403, "search:execute required"));
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("search:execute");
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });
});
