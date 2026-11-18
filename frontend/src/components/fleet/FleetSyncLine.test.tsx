import { act, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, fleetApi, type CallerInfo, type FleetSyncPass, type FleetSyncStatus } from "@/lib/api";

import { FleetSyncNow, SYNC_POLL_MS } from "./FleetSyncNow";
import { InventoryPassLine } from "./InventoryPassLine";

function session(): SessionValue {
  const permissions = ["admin:*"];
  const caller: CallerInfo = { subject: "u", email: null, permissions, effective_permissions: permissions };
  return { status: "ready", me: caller, error: null, reload: async () => {}, signOut: async () => null };
}

function pass(over: Partial<FleetSyncPass> = {}): FleetSyncPass {
  return {
    status: "COMPLETE",
    finished_at: "2026-11-18T22:10:00Z",
    elapsed_ms: 83_000,
    seen: 8012,
    created: 12,
    updated: 7990,
    pages_done: 17,
    pages_failed: 0,
    failed_page: null,
    failure_kind: null,
    ...over,
  };
}

function status(over: Partial<FleetSyncStatus["instances"][number]> = {}, running = false): FleetSyncStatus {
  return {
    running,
    instances: [
      {
        instance_id: "i1",
        instance_name: "console-a",
        status: "OK",
        last_pass: pass(),
        last_run_at: "2026-11-18T22:10:00Z",
        last_success_at: "2026-11-18T22:10:00Z",
        consecutive_failures: 0,
        ...over,
      },
    ],
  };
}

function mount(onFinished = () => {}) {
  return render(
    <StaticSession value={session()}>
      <FleetSyncNow onFinished={onFinished} />
    </StaticSession>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the sync line", () => {
  it("says COMPLETE in one line with the UTC time and the source count", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(status());
    mount();
    expect(await screen.findByText(/Inventory/)).toHaveTextContent(/Inventory 11-18 22:10 UTC · 8,012 sources/);
    expect(screen.queryByText(/Last sync/)).toBeNull();
  });

  it("says PARTIAL with the failed page and keeps the rest in a details element", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(
      status({
        status: "PARTIAL",
        last_pass: pass({
          status: "PARTIAL",
          seen: 4000,
          pages_done: 8,
          pages_failed: 1,
          failed_page: 9,
          failure_kind: "timeout",
        }),
      }),
    );
    mount();
    const line = await screen.findByText(/Last sync partial/);
    expect(line).toHaveTextContent("Last sync partial (timeout on page 9) — 4,000 sources updated.");
    const details = within(line).getByText("Sync details").closest("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("8 read, 1 failed")).toBeInTheDocument();
    expect(within(details).getByText("1 min 23 s")).toBeInTheDocument();
    expect(within(details).getByText("timeout on page 9")).toBeInTheDocument();
  });

  it("says FAILED with what failed and what to do", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(
      status({
        status: "FAILED",
        consecutive_failures: 2,
        last_pass: pass({ status: "FAILED", seen: 0, pages_done: 0, pages_failed: 1, failed_page: 1, failure_kind: "auth" }),
      }),
    );
    mount();
    const line = await screen.findByText(/Last sync failed/);
    expect(line).toHaveTextContent(/^Last sync failed — token refused on page 1\./);
    // The advice sits in the details, not in the headline: one line, at most 14 words.
    const clone = line.cloneNode(true) as HTMLElement;
    clone.querySelector("details")?.remove();
    expect(clone.textContent!.trim().split(/\s+/).length).toBeLessThanOrEqual(14);
    expect(clone.textContent).not.toMatch(/Check the API token/);
    const details = within(line).getByText("Sync details").closest("details")!;
    expect(within(details).getByText(/Check the API token/)).toBeInTheDocument();
  });

  it("says RUNNING while the collector holds its lock", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(status({ status: "RUNNING" }, true));
    mount();
    expect(await screen.findByText(/Inventory sync running\./)).toHaveTextContent(/8,012 sources/);
  });

  it("says RUNNING when no pass was ever stored", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue({
      running: true,
      instances: [{ ...status().instances[0], status: "RUNNING", last_pass: null, last_run_at: null, last_success_at: null }],
    });
    mount();
    expect(await screen.findByText("Inventory sync running.")).toBeInTheDocument();
  });

  it("says no sync has run when nothing was stored", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue({
      running: false,
      instances: [{ ...status().instances[0], status: "NEVER", last_pass: null, last_run_at: null, last_success_at: null }],
    });
    mount();
    expect(await screen.findByText("No inventory sync has run yet.")).toBeInTheDocument();
  });

  it("says the status could not be read when the API errors", async () => {
    vi.spyOn(fleetApi, "syncStatus").mockRejectedValue(new ApiError(500, "boom"));
    mount();
    expect(await screen.findByText("The last sync time could not be read.")).toBeInTheDocument();
  });

  it("names the console when more than one is listed", async () => {
    const one = status().instances[0];
    vi.spyOn(fleetApi, "syncStatus").mockResolvedValue({
      running: false,
      instances: [one, { ...one, instance_id: "i2", instance_name: "console-b" }],
    });
    mount();
    expect(await screen.findByText(/console-b:/)).toBeInTheDocument();
    expect(screen.getByText(/console-a:/)).toBeInTheDocument();
  });
});

describe("a partial pass counts as finished", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("reloads the list and says it was partial", async () => {
    const before = status();
    const after = status({
      status: "PARTIAL",
      last_run_at: "2026-11-18T23:00:00Z",
      last_pass: pass({ status: "PARTIAL", failed_page: 9, failure_kind: "timeout", seen: 4000 }),
    });
    const read = vi.spyOn(fleetApi, "syncStatus").mockResolvedValue(before);
    vi.spyOn(fleetApi, "queueSync").mockResolvedValue({ queued: true, already_running: false, task_id: "t" });
    const finished = vi.fn();
    mount(finished);
    await userEvent.click(await screen.findByRole("button", { name: "Sync now" }));
    read.mockResolvedValue(after);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(SYNC_POLL_MS);
    });
    expect(await screen.findByText(/finished partly; the list shows the pages/)).toBeInTheDocument();
    expect(finished).toHaveBeenCalledTimes(1);
  });
});

describe("InventoryPassLine", () => {
  it("falls back to an unknown cause for a failure kind it does not know", () => {
    render(<InventoryPassLine pass={pass({ status: "FAILED", failure_kind: "gremlins" })} />);
    expect(screen.getByText(/Last sync failed — unknown cause/)).toBeInTheDocument();
  });

  it("says never when there is no pass", () => {
    render(<InventoryPassLine pass={null} />);
    expect(screen.getByText("No inventory sync has run yet.")).toBeInTheDocument();
  });
});
