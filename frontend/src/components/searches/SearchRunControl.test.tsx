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

describe("SearchRunControl queued runs", () => {
  const queuedRow = (over: Partial<SearchExecution> = {}) =>
    execution({
      status: "QUEUED",
      result_count: null,
      duration_ms: null,
      created_at: "2026-11-20T10:00:00Z",
      ...over,
    });

  it("shows the queued state with its UTC time and does not refresh yet", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(queuedRow());
    vi.spyOn(api, "searchExecutions").mockResolvedValue([queuedRow()]);
    render(<SearchRunControl searchId="s-1" pollMs={60_000} />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Run queued at 2026-11-20 10:00:00 UTC");
    expect(status).toHaveTextContent("checks every 5 seconds");
    expect(screen.getByRole("button", { name: "Run queued" })).toBeDisabled();
    expect(refresh).not.toHaveBeenCalled();
  });

  it("follows the row to running and then to the finished result, refreshing once", async () => {
    // The read that would replace the RUNNING frame returns a promise this test
    // resolves only after it has seen that frame: the component polls on a 10 ms
    // timer, so a resolved read could otherwise replace RUNNING with COMPLETED
    // before `findByText` (50 ms re-check) ever observes it, and the transient
    // assertion would flake under load.
    let resolveDone!: (value: SearchExecution[]) => void;
    const donePromise = new Promise<SearchExecution[]>((resolve) => {
      resolveDone = resolve;
    });
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(queuedRow());
    const list = vi
      .spyOn(api, "searchExecutions")
      .mockResolvedValueOnce([queuedRow({ status: "RUNNING", started_at: "2026-11-20T10:00:00Z" })])
      .mockImplementationOnce(() => donePromise);
    render(<SearchRunControl searchId="s-1" pollMs={10} />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/Running in QRadar since/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Running…" })).toBeDisabled();
    resolveDone([execution({ status: "COMPLETED", result_count: 7 })]);
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("returned 7 rows"));
    expect(refresh).toHaveBeenCalledTimes(1);
    const calls = list.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(list.mock.calls.length).toBe(calls);
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("stops polling when the page goes away", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(queuedRow());
    const list = vi.spyOn(api, "searchExecutions").mockResolvedValue([queuedRow()]);
    const view = render(<SearchRunControl searchId="s-1" pollMs={10} />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(list).toHaveBeenCalled());
    view.unmount();
    const calls = list.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 60));
    expect(list.mock.calls.length).toBe(calls);
  });

  it("says the read failed and keeps following", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(queuedRow());
    vi.spyOn(api, "searchExecutions")
      .mockRejectedValueOnce(new ApiError(502, "bad gateway"))
      .mockResolvedValue([execution({ status: "COMPLETED", result_count: 3 })]);
    render(<SearchRunControl searchId="s-1" pollMs={10} />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByText(/progress could not be read/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("returned 3 rows"));
  });

  it("resumes following a run that was already queued when the page loaded", async () => {
    const list = vi
      .spyOn(api, "searchExecutions")
      .mockResolvedValue([execution({ status: "COMPLETED", result_count: 5 })]);
    render(<SearchRunControl searchId="s-1" latest={queuedRow()} pollMs={10} />);
    expect(screen.getByRole("status")).toHaveTextContent("Run queued at 2026-11-20 10:00:00 UTC");
    expect(screen.getByRole("button", { name: "Run queued" })).toBeDisabled();
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("returned 5 rows"));
    expect(list).toHaveBeenCalledWith("s-1");
  });

  it("shows a timed-out run from the row", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(queuedRow());
    vi.spyOn(api, "searchExecutions").mockResolvedValue([
      execution({ status: "TIMEOUT", result_count: null, error_type: "TIMEOUT" }),
    ]);
    render(<SearchRunControl searchId="s-1" pollMs={10} />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("TIMEOUT"));
  });

  it("shows the held and cap sentence of a capacity refusal from the row", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockResolvedValue(queuedRow());
    vi.spyOn(api, "searchExecutions").mockResolvedValue([
      execution({
        status: "FAILED",
        result_count: null,
        error_type: "CAPACITY",
        error_message:
          "No Ariel permit became available within 30 seconds (per-console cap 3, global cap 6)",
        error_message_code: "search.run.capacity",
        error_message_params: { waited: 30, instance_cap: 3, global_cap: 6 },
      }),
    ]);
    render(<SearchRunControl searchId="s-1" pollMs={10} />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    const note = await screen.findByText(/No Ariel permit became available within 30 seconds/);
    expect(note).toHaveTextContent("per-console cap 3, global cap 6");
    expect(note).toHaveTextContent("Nothing was sent to QRadar");
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.queryByText(/latest run failed/)).toBeNull();
  });

  it("answers a 409 with the time the open run started, in UTC, and follows that run", async () => {
    const user = userEvent.setup();
    const conflict = new ApiError(409, "conflict", "a manual run ... since 2026-11-20T10:00:00+00:00", {
      detail: "a manual run of this search has been queued or running since 2026-11-20T10:00:00+00:00",
      detail_code: "search.run.conflict.running",
      detail_params: { since: "2026-11-20T10:00:00+00:00" },
    });
    vi.spyOn(api, "runSearch").mockRejectedValue(conflict);
    vi.spyOn(api, "searchExecutions").mockResolvedValue([
      queuedRow({ id: "e-open", status: "RUNNING", started_at: "2026-11-20T10:00:02Z" }),
    ]);
    render(<SearchRunControl searchId="s-1" pollMs={60_000} />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    const notice = await screen.findByText(/Running in QRadar since/);
    expect(notice).toHaveTextContent("2026-11-20 10:00:02 UTC");
    expect(notice).not.toHaveTextContent("2026-11-20T10:00:02Z");
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.queryByText(/queued or running since/)).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("falls back to the generic line on a 409 without the code", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "runSearch").mockRejectedValue(new ApiError(409, "conflict", "conflict"));
    vi.spyOn(api, "searchExecutions").mockResolvedValue([]);
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The run did not start");
    expect(screen.getByRole("button", { name: "Run now" })).toBeEnabled();
  });

  it("names the open run's conflict when it cannot be followed", async () => {
    const user = userEvent.setup();
    const conflict = new ApiError(409, "conflict", "x", {
      detail: "x",
      detail_code: "search.run.conflict.running",
      detail_params: { since: "2026-11-20T10:00:00+00:00" },
    });
    vi.spyOn(api, "runSearch").mockRejectedValue(conflict);
    vi.spyOn(api, "searchExecutions").mockResolvedValue([]);
    render(<SearchRunControl searchId="s-1" />);
    await user.click(screen.getByRole("button", { name: "Run now" }));
    const notice = await screen.findByText(/queued or running since/);
    expect(notice).toHaveTextContent("2026-11-20 10:00:00 UTC");
  });

  it("reads an abandoned run and a worker error as words, not tokens", async () => {
    const abandoned = execution({
      status: "FAILED",
      error_type: "ABANDONED",
      error_message_code: "search.run.abandoned",
      result_count: null,
    });
    const { unmount } = render(<SearchRunControl searchId="s-1" latest={abandoned} />);
    expect(screen.getByRole("status")).toHaveTextContent(
      "Abandoned: the worker stopped before finishing",
    );
    expect(screen.getByRole("status")).not.toHaveTextContent("ABANDONED");
    unmount();
    const worker = execution({
      status: "FAILED",
      error_type: "KeyError",
      error_message_code: "search.run.worker_failed",
      result_count: null,
    });
    render(<SearchRunControl searchId="s-1" latest={worker} />);
    expect(screen.getByRole("status")).toHaveTextContent("Worker error (KeyError)");
  });
});
