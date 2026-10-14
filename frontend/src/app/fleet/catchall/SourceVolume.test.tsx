import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, catchallApi } from "@/lib/api";

import type { Slot } from "./catchall";
import { bucket, sources } from "./fixtures";
import { SourceVolume } from "./SourceVolume";

// The canvas is covered by BucketChart.test; here the chart is a stand-in that
// exposes what the page hands it.
let lastProps: { slots: Slot[]; selected: number | null; onSelect: (i: number) => void } | null = null;
vi.mock("./BucketChart", () => ({
  BucketChart: (props: NonNullable<typeof lastProps> & { textSummary: string }) => {
    lastProps = props;
    return <div role="img" aria-label="chart">{props.textSummary}</div>;
  },
}));

// 10:00 .. 10:30 UTC window; rows at 10:00, 10:05 (lower bound) and 10:20.
const NOW = "2026-10-13T10:30:00Z";
const rows = () => [
  bucket(0),
  bucket(5, { measurement: "LOWER_BOUND", events: 900, distinct_senders_lower_bound: true }),
  bucket(20, { top_senders: Array.from({ length: 25 }, (_, i) => ({ identifier: `host-${i}`, events: 100 - i })) }),
];

function list(items = rows()) {
  return { catchall_log_source_id: "x", since: "", until: "", items };
}

beforeEach(() => {
  vi.restoreAllMocks();
  lastProps = null;
});

describe("SourceVolume", () => {
  it("shows loading, then the chart", async () => {
    let release: (v: ReturnType<typeof list>) => void = () => {};
    vi.spyOn(catchallApi, "buckets").mockReturnValue(new Promise((r) => (release = r)));
    render(<SourceVolume sources={sources()} now={NOW} />);
    expect(screen.getByText(/Loading buckets/)).toBeInTheDocument();
    await act(async () => release(list()));
    expect(await screen.findByRole("img", { name: "chart" })).toBeInTheDocument();
  });

  it("asks for the last 24 hours of the most recently measured source, in UTC", async () => {
    const spy = vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith(
      expect.any(String),
      "2026-10-12T10:30:00.000Z",
      "2026-10-13T10:30:00.000Z",
    );
  });

  it("never asks for more than the 7 days the API allows", async () => {
    const spy = vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await userEvent.click(screen.getByRole("button", { name: "7 days" }));
    await waitFor(() => expect(spy).toHaveBeenCalledTimes(2));
    const [, since, until] = spy.mock.calls[1];
    expect(Date.parse(until) - Date.parse(since)).toBeLessThanOrEqual(7 * 24 * 3600_000);
  });

  it("says how far collection got and counts the gaps as unknown, not zero", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    const line = await screen.findByText(/Collected up to 2026-10-13 10:25:00 UTC/);
    const gaps = lastProps!.slots.filter((s) => s.state === "gap");
    expect(line).toHaveTextContent(`${gaps.length} buckets before that were not measured`);
    expect(line).toHaveTextContent(/unknown, not zero/);
    // 10:10 and 10:15 have no row and lie before the latest measured bucket.
    const at = (m: number) => lastProps!.slots.find((s) => s.t === Date.UTC(2026, 9, 13, 10, m))!;
    expect([at(10).state, at(15).state]).toEqual(["gap", "gap"]);
    // 10:25 .. 10:30 is after the last bucket: claimed as nothing.
    expect(at(25).state).toBe("beyond");
    expect(lastProps!.slots.at(-1)?.state).toBe("beyond");
  });

  it("shows the lower-bound legend only when a bucket is a lower bound", async () => {
    const spy = vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    const key = await screen.findByRole("list", { name: "Chart key" });
    expect(within(key).getByText(/At least: the search was truncated/)).toBeInTheDocument();
    expect(within(key).getByText("Not measured")).toBeInTheDocument();
    spy.mockResolvedValue(list([bucket(0), bucket(5)]));
    await userEvent.click(screen.getByRole("button", { name: "6 hours" }));
    await waitFor(() =>
      expect(within(screen.getByRole("list", { name: "Chart key" })).queryByText(/At least:/)).toBeNull(),
    );
  });

  it("selects the latest measured bucket and describes it in words", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    const panel = await screen.findByRole("group", { name: "Selected bucket" });
    expect(panel).toHaveTextContent("2026-10-13 10:20:00 UTC to 2026-10-13 10:25:00 UTC");
    expect(panel).toHaveTextContent(/1 new sender was admitted/);
    expect(panel).toHaveTextContent(/2 senders were below the admission threshold/);
    expect(panel).toHaveTextContent(/3 senders were refused/);
    expect(panel).toHaveTextContent(/5 senders \(7 events\) are not tracked by name/);
    expect(panel).toHaveTextContent(/never stored by name/);
    // EXACT is said once, by the pill.
    expect(within(panel).getAllByText("Exact")).toHaveLength(1);
  });

  it("lists at most 20 top senders", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    const table = await screen.findByRole("table", { name: /busiest tracked senders/ });
    expect(within(table).getAllByRole("row")).toHaveLength(21);
  });

  it("marks a lower-bound bucket's events and distinct senders as at least", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await screen.findByRole("group", { name: "Selected bucket" });
    await userEvent.click(screen.getByRole("button", { name: "Earlier measured bucket" }));
    const panel = screen.getByRole("group", { name: "Selected bucket" });
    expect(panel).toHaveTextContent(/at least 900/);
    expect(panel).toHaveTextContent(/at least 4/);
  });

  it("walks between measured buckets and disables the ends", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await screen.findByRole("group", { name: "Selected bucket" });
    expect(screen.getByRole("button", { name: "Later measured bucket" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Earlier measured bucket" }));
    await userEvent.click(screen.getByRole("button", { name: "Earlier measured bucket" }));
    expect(screen.getByRole("button", { name: "Earlier measured bucket" })).toBeDisabled();
  });

  it("says a selected gap was not measured and invents no figures for it", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await screen.findByRole("group", { name: "Selected bucket" });
    const gap = lastProps!.slots.findIndex((s) => s.state === "gap");
    act(() => lastProps!.onSelect(gap));
    const panel = screen.getByRole("group", { name: "Selected bucket" });
    expect(panel).toHaveTextContent(/was not measured/);
    expect(within(panel).queryByText("Events")).toBeNull();
    expect(within(panel).queryByRole("table")).toBeNull();
  });

  it("says a bucket after the latest measured one has not been collected", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    await screen.findByRole("group", { name: "Selected bucket" });
    act(() => lastProps!.onSelect(lastProps!.slots.length - 1));
    expect(screen.getByRole("group", { name: "Selected bucket" })).toHaveTextContent(
      /after the latest measured one/,
    );
  });

  it("says no bucket was ever measured, and that this is not zero traffic", async () => {
    const never = sources();
    never.items.forEach((s) => (s.latest_bucket_start = null));
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list([]));
    render(<SourceVolume sources={never} now={NOW} />);
    expect(await screen.findByText("No bucket was measured in this range.")).toBeInTheDocument();
    expect(screen.getByText(/not zero traffic/)).toBeInTheDocument();
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("points at the latest measured bucket when the range holds none", async () => {
    vi.spyOn(catchallApi, "buckets").mockResolvedValue(list([]));
    render(<SourceVolume sources={sources()} now={NOW} />);
    expect(await screen.findByText(/latest measured bucket of .* starts 2026-10-13 17:30:00 UTC/)).toBeInTheDocument();
  });

  it("says what failed, with a retry", async () => {
    const spy = vi
      .spyOn(catchallApi, "buckets")
      .mockRejectedValueOnce(new ApiError(500, "x"))
      .mockResolvedValue(list());
    render(<SourceVolume sources={sources()} now={NOW} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/could not be loaded/);
    await userEvent.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("img", { name: "chart" })).toBeInTheDocument();
    expect(spy).toHaveBeenCalledTimes(2);
  });

  it("names the permission on a 403 and offers no retry", async () => {
    vi.spyOn(catchallApi, "buckets").mockRejectedValue(new ApiError(403, "no"));
    render(<SourceVolume sources={sources()} now={NOW} />);
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("read:fleet");
    expect(within(alert).queryByRole("button")).toBeNull();
  });
});
