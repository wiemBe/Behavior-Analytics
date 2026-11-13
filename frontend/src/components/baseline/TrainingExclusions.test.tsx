import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, trainingExclusionApi } from "@/lib/api";
import type { ExclusionLoad } from "@/lib/trainingExclusionLoad";

import { TrainingExclusions } from "./TrainingExclusions";
import { CAN, READ_ONLY, SOURCE, exclusion } from "./fixtures";

function mount(initial: ExclusionLoad, value: SessionValue = CAN) {
  return render(
    <StaticSession value={value}>
      <TrainingExclusions sourceId={SOURCE} initial={initial} />
    </StaticSession>,
  );
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("states", () => {
  it("empty: says what is empty, and draws no table", () => {
    mount({ kind: "ok", items: [] });
    expect(screen.getByRole("heading", { name: "Baseline training exclusions" })).toBeInTheDocument();
    expect(screen.getByText("No analyst training exclusions affect this baseline.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("error: says what failed, offers Retry, and does not claim there are none", async () => {
    const read = vi
      .spyOn(trainingExclusionApi, "forSource")
      .mockResolvedValue({ items: [exclusion()], limit: 100 });
    mount({ kind: "error" });
    expect(screen.getByText("Training exclusions could not be loaded.")).toBeInTheDocument();
    expect(screen.queryByText(/No analyst training exclusions/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(read).toHaveBeenCalledWith(SOURCE);
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByText("Training exclusions could not be loaded.")).toBeNull();
  });

  it("loading: keeps the heading, shows no rows, while Retry is in flight", async () => {
    let finish: (v: { items: never[]; limit: number }) => void = () => {};
    vi.spyOn(trainingExclusionApi, "forSource").mockReturnValue(new Promise((r) => (finish = r)));
    mount({ kind: "error" });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByText("Loading training exclusions…")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Baseline training exclusions" })).toBeInTheDocument();
    expect(screen.queryByRole("table")).toBeNull();
    finish({ items: [], limit: 100 });
    expect(await screen.findByText("No analyst training exclusions affect this baseline.")).toBeInTheDocument();
  });

  it("a failed Retry stays an error", async () => {
    vi.spyOn(trainingExclusionApi, "forSource").mockRejectedValue(new ApiError(500, "boom"));
    mount({ kind: "error" });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Training exclusions could not be loaded.")).toBeInTheDocument();
  });

  it("a Retry the caller may not make names read:anomalies", async () => {
    vi.spyOn(trainingExclusionApi, "forSource").mockRejectedValue(new ApiError(403, "forbidden"));
    mount({ kind: "error" });
    await userEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText(/needs the read:anomalies permission/)).toBeInTheDocument();
  });

  it("forbidden: names the permission and has no Retry", () => {
    mount({ kind: "forbidden", permission: "read:anomalies" });
    expect(screen.getByText("Reading training exclusions needs the read:anomalies permission.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(screen.queryByText(/No analyst training exclusions/)).toBeNull();
  });
});

describe("rows", () => {
  const rows = [
    exclusion({ id: "p", state: "PENDING_REBUILD" }),
    exclusion({ id: "a", state: "APPLIED", starts_at: "2026-11-03T08:00:00Z", ends_at: "2026-11-03T09:00:00Z" }),
    exclusion({ id: "o", state: "OUTSIDE_LOOKBACK", starts_at: "2026-09-01T08:00:00Z", ends_at: "2026-09-01T09:00:00Z" }),
    exclusion({
      id: "r",
      state: "REVOKED",
      starts_at: "2026-11-02T08:00:00Z",
      ends_at: "2026-11-02T09:00:00Z",
      revoked_by: "a.rivera",
      revoked_at: "2026-11-02T12:00:00Z",
      revocation_reason: "Traffic was real after all",
    }),
  ];

  it("shows interval, scope, reason, who and when, and the effect state of each row", () => {
    mount({ kind: "ok", items: rows });
    const table = screen.getByRole("table");
    const body = within(table).getAllByRole("row").slice(1);
    expect(body).toHaveLength(4);
    expect(body[0]).toHaveTextContent("04 Nov 08:00–10:00 UTC");
    expect(body[0]).toHaveTextContent("Source volume");
    expect(body[0]).toHaveTextContent("Collector restarted during the incident");
    expect(body[0]).toHaveTextContent("k.osei");
    expect(body[0]).toHaveTextContent("11-04 10:30 UTC");
    expect(body[0]).toHaveTextContent("Pending next baseline rebuild");
    expect(body[1]).toHaveTextContent("Applied by a baseline rebuild");
    expect(body[2]).toHaveTextContent("Outside the baseline lookback");
    expect(body[3]).toHaveTextContent("Revoked");
    expect(body[3]).toHaveTextContent("Revoked by a.rivera: Traffic was real after all");
  });

  it("offers Revoke on every row but a revoked one", () => {
    mount({ kind: "ok", items: rows });
    expect(screen.getAllByRole("button", { name: "Revoke" })).toHaveLength(3);
  });

  it("an unknown scope key is shown as the server sent it", () => {
    mount({ kind: "ok", items: [exclusion({ scope_key: "waf:request_count" })] });
    expect(screen.getByRole("table")).toHaveTextContent("waf:request_count");
  });

  it("a read-only caller sees the rows and the permission, but no Revoke", () => {
    mount({ kind: "ok", items: rows }, READ_ONLY);
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(screen.queryByRole("columnheader", { name: "Action" })).toBeNull();
    expect(screen.getByText("Revoking an exclusion needs the anomaly:triage permission.")).toBeInTheDocument();
  });

  it("says when the newest hundred are shown", () => {
    const many = Array.from({ length: 100 }, (_, i) => exclusion({ id: `x${i}` }));
    mount({ kind: "ok", items: many });
    expect(screen.getByText("Showing the newest 100 exclusions.")).toBeInTheDocument();
  });
});

describe("revoking", () => {
  const row = exclusion();
  async function open() {
    await userEvent.click(screen.getByRole("button", { name: "Revoke" }));
  }

  it("opens a form that names the interval and takes focus", async () => {
    mount({ kind: "ok", items: [row] });
    await open();
    const form = screen.getByRole("form", { name: "Revoke training exclusion" });
    expect(form).toHaveTextContent("Revoke the exclusion of 04 Nov 08:00–10:00 UTC");
    await waitFor(() => expect(screen.getByLabelText("Reason for revoking")).toHaveFocus());
  });

  it("needs a reason and sends nothing without one", async () => {
    const revoke = vi.spyOn(trainingExclusionApi, "revoke");
    mount({ kind: "ok", items: [row] });
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Revoke exclusion" }));
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a reason of 1 to 1000 characters.");
    await userEvent.type(screen.getByLabelText("Reason for revoking"), "   ");
    await userEvent.click(screen.getByRole("button", { name: "Revoke exclusion" }));
    expect(revoke).not.toHaveBeenCalled();
  });

  it("sends the trimmed reason and shows the row as revoked, with when it returns to training", async () => {
    const revoke = vi.spyOn(trainingExclusionApi, "revoke").mockResolvedValue(
      exclusion({
        state: "REVOKED",
        revoked_by: "k.osei",
        revoked_at: "2026-11-05T09:00:00Z",
        revocation_reason: "Traffic was real",
      }),
    );
    mount({ kind: "ok", items: [row] });
    await open();
    await userEvent.type(screen.getByLabelText("Reason for revoking"), " Traffic was real ");
    await userEvent.click(screen.getByRole("button", { name: "Revoke exclusion" }));
    expect(revoke).toHaveBeenCalledWith(row.id, { reason: "Traffic was real" });
    expect(await screen.findByText(/returns to training at the next baseline rebuild\.$/)).toHaveAttribute(
      "role",
      "status",
    );
    expect(screen.queryByRole("form")).toBeNull();
    expect(screen.queryByRole("button", { name: "Revoke" })).toBeNull();
    expect(screen.getByRole("table")).toHaveTextContent("Revoked by k.osei: Traffic was real");
  });

  it.each([
    [404, "the training exclusion was not found"],
    [422, "a reason of 1 to 1000 characters is required"],
    [409, "the interval partly overlaps an active exclusion for the same baseline"],
  ])("shows the server's refusal (%i) as it came and keeps the form", async (status, sentence) => {
    vi.spyOn(trainingExclusionApi, "revoke").mockRejectedValue(new ApiError(status, sentence));
    mount({ kind: "ok", items: [row] });
    await open();
    await userEvent.type(screen.getByLabelText("Reason for revoking"), "because");
    await userEvent.click(screen.getByRole("button", { name: "Revoke exclusion" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(sentence);
    expect(screen.getByLabelText("Reason for revoking")).toHaveValue("because");
    expect(screen.getByRole("table")).not.toHaveTextContent("Revoked by");
  });

  it("names the permission on a 403 and says a failed request was not revoked", async () => {
    vi.spyOn(trainingExclusionApi, "revoke").mockRejectedValueOnce(
      new ApiError(403, "missing required permission: anomaly:triage"),
    );
    mount({ kind: "ok", items: [row] });
    await open();
    await userEvent.type(screen.getByLabelText("Reason for revoking"), "because");
    await userEvent.click(screen.getByRole("button", { name: "Revoke exclusion" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Revoking an exclusion needs the anomaly:triage permission.",
    );
    vi.spyOn(trainingExclusionApi, "revoke").mockRejectedValueOnce(new ApiError(500, "boom"));
    await userEvent.click(screen.getByRole("button", { name: "Revoke exclusion" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("The exclusion was not revoked. boom");
  });

  it("ignores a second submit that arrives before the first has answered", async () => {
    const revoke = vi.spyOn(trainingExclusionApi, "revoke").mockReturnValue(new Promise(() => {}));
    mount({ kind: "ok", items: [row] });
    await open();
    await userEvent.type(screen.getByLabelText("Reason for revoking"), "because");
    const form = screen.getByRole("form", { name: "Revoke training exclusion" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(revoke).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape and on Cancel", async () => {
    mount({ kind: "ok", items: [row] });
    await open();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("form")).toBeNull();
    await open();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form")).toBeNull();
  });
});
