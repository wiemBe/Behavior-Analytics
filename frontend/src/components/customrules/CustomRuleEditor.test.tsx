import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { CustomRuleEditor } from "./CustomRuleEditor";
import { catalog, preview, rule } from "./fixtures";
import { ApiError, api } from "@/lib/api";

const navigation = vi.hoisted(() => ({ push: vi.fn(), refresh: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => navigation }));
vi.mock("./SubjectPicker", () => ({ SubjectPicker: () => <div aria-label="subject picker" /> }));
vi.mock("./PreviewChart", () => ({
  PreviewChart: () => <div aria-label="preview chart" />,
  PreviewSummary: ({ preview: value }: { preview: { fired_count: number } }) => <p>Would have fired {value.fired_count} times</p>,
}));

beforeEach(() => {
  vi.spyOn(api, "previewCustomRule").mockResolvedValue(preview());
});

describe("custom-rule draft editor", () => {
  it("does not invent a zero-threshold summary while the threshold is empty", () => {
    render(<CustomRuleEditor catalog={catalog()} mode="create" />);
    expect(screen.queryByText(/Fires after/)).toBeNull();
    expect(api.previewCustomRule).not.toHaveBeenCalled();
  });

  it("debounces a valid draft preview", async () => {
    vi.useFakeTimers();
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} initialSubjectLabel="edge-web-01" />);
    await act(async () => vi.advanceTimersByTime(449));
    expect(api.previewCustomRule).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTime(1));
    expect(api.previewCustomRule).toHaveBeenCalledTimes(1);
    await act(async () => Promise.resolve());
    expect(screen.getByLabelText("preview chart")).toBeInTheDocument();
    vi.useRealTimers();
  });

  it("renders every server 422 reason as sent", async () => {
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(
      new ApiError(422, "Unprocessable Entity", { message: "invalid", reasons: ["reason one", "reason two"] }),
    );
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText("reason one")).toBeInTheDocument();
    expect(screen.getByText("reason two")).toBeInTheDocument();
  });

  it("names the configured cap and says to disable another rule", async () => {
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(
      new ApiError(409, "Conflict", "at most 100 rules may be enabled at once"),
    );
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("configured limit is 100");
    expect(alert).toHaveTextContent("Disable another rule");
  });

  it("does not overwrite a version conflict and offers a reload", async () => {
    vi.spyOn(api, "updateCustomRule").mockRejectedValue(
      new ApiError(409, "Conflict", {
        message: "rule version conflict",
        current_version: "2026-10-16T08:00:00+00:00",
        updated_by: "grace",
      }),
    );
    render(<CustomRuleEditor catalog={catalog()} mode="edit" rule={rule()} />);
    fireEvent.click(screen.getByRole("button", { name: "Save changes" }));
    expect(await screen.findByText(/changed by grace/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Reload the current rule" }));
    expect(navigation.refresh).toHaveBeenCalled();
  });
});
