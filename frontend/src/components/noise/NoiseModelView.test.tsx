import { fireEvent, render, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  api,
  type NoiseModel,
  type NoiseModelCell,
  type NoiseReason,
} from "@/lib/api";
import { MODEL_DISABLED_DETAIL } from "@/lib/noiseModel";

import { NoiseModelView } from "./NoiseModelView";
import { SRC } from "./noiseFixtures";

function reason(code: string, text: string): NoiseReason {
  return { code, params: {}, text };
}

function cell(over: Partial<NoiseModelCell> = {}): NoiseModelCell {
  return {
    metric: "event_count",
    weekday: 6,
    hour: 11,
    state: "OK",
    reason: null,
    sample_count: 10,
    observation_count: 10,
    reliable: true,
    mean: 3,
    variance: 3,
    dispersion: 1,
    model: "poisson",
    nb_r: null,
    nb_p: null,
    buckets_scored: 47,
    upper_tail_below: 12,
    lower_tail_below: 0,
    shrinkage: { n: 10, k: 8, raw_median: 3, pooled_median: 3.5, weight: 0.555556, estimate: 3.2 },
    level_shift: {
      recent_median: 30,
      cell_median: 3,
      p05: 0,
      p95: 6,
      recent_samples: 12,
      cell_samples: 10,
      started_at: "2026-10-31T11:45:00Z",
    },
    ...over,
  };
}

const NOT_COUNTS = reason(
  "noise.model.not_counts",
  "The stored observations are not non-negative integer counts, so no tail probability is reported.",
);

const insufficient = cell({
  metric: "average_eps",
  weekday: 1,
  hour: 0,
  state: "INSUFFICIENT_DATA",
  reason: NOT_COUNTS,
  mean: 50,
  variance: 0.25,
  dispersion: 0.005,
  model: null,
  buckets_scored: null,
  upper_tail_below: null,
  lower_tail_below: null,
  level_shift: null,
  shrinkage: { n: 8, k: 8, raw_median: 50, pooled_median: 50, weight: 0.5, estimate: 50 },
});

const unjudged = cell({
  weekday: 2,
  hour: 3,
  state: "NOT_JUDGED",
  reason: reason("noise.model.not_judged", "Not judged: no reliable baseline cell."),
  sample_count: 0,
  mean: null,
  variance: null,
  dispersion: null,
  model: null,
  buckets_scored: null,
  upper_tail_below: null,
  lower_tail_below: null,
  level_shift: null,
  shrinkage: { n: 0, k: 8, raw_median: null, pooled_median: null, weight: 0, estimate: null },
});

function model(over: Partial<NoiseModel> = {}): NoiseModel {
  return {
    log_source_id: SRC,
    state: "OK",
    reason: null,
    window_days: 28,
    covered_days: 28,
    first_bucket: "2026-10-10T00:00:00Z",
    last_bucket: "2026-11-07T00:00:00Z",
    p_threshold: 0.001,
    shrinkage_k: 8,
    cells: [cell(), insufficient, unjudged],
    calibration: [
      {
        anomaly_type: "VOLUME_SPIKE",
        replayed_flags: 6,
        noise_flags: 2,
        excluded_by_direction: false,
        reason: null,
      },
    ],
    calibration_reason: null,
    ...over,
  };
}

async function show(result: NoiseModel | Error) {
  const call = vi
    .spyOn(api, "noiseModel")
    .mockImplementation(async () => (result instanceof Error ? Promise.reject(result) : result));
  render(<NoiseModelView logSourceId={SRC} />);
  return call;
}

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("the low-volume model section: states", () => {
  it("shows a loading state while the read is pending, then the model", async () => {
    let release: (m: NoiseModel) => void = () => {};
    vi.spyOn(api, "noiseModel").mockImplementation(() => new Promise((r) => (release = r)));
    render(<NoiseModelView logSourceId={SRC} />);
    expect(screen.getByText(/Reading the stored baseline cells/)).toBeInTheDocument();
    release(model());
    expect(await screen.findByText("Scored")).toBeInTheDocument();
    expect(screen.queryByText(/Reading the stored baseline cells/)).toBeNull();
  });

  it("states that it is a shadow view that opens no alert", async () => {
    await show(model());
    await screen.findByText("Scored");
    expect(screen.getByText(/No detector uses it and it opens no alert/)).toBeInTheDocument();
  });

  it("names the window, the bucket span in UTC, p and the shrinkage k", async () => {
    const call = await show(model());
    await screen.findByText("Scored");
    expect(call).toHaveBeenCalledWith(SRC);
    expect(screen.getByText("28 of 28 days covered")).toBeInTheDocument();
    expect(screen.getByText(/p = 0\.001/)).toBeInTheDocument();
    expect(screen.getByText(/k = 8/)).toBeInTheDocument();
    expect(screen.getAllByText(/UTC/).length).toBeGreaterThan(0);
  });

  it("INSUFFICIENT_DATA carries the backend sentence and no invented number", async () => {
    await show(
      model({
        state: "INSUFFICIENT_DATA",
        reason: reason("noise.model.no_baseline", "Not judged: this source has no baseline cell."),
        cells: [],
      }),
    );
    expect(await screen.findByText("Insufficient data")).toBeInTheDocument();
    expect(screen.getByText("Not judged: this source has no baseline cell.")).toBeInTheDocument();
    expect(screen.getAllByText("The response holds no cells for this metric.")).toHaveLength(2);
  });

  it("PARTIAL names the covered span", async () => {
    await show(model({ state: "PARTIAL", covered_days: 14 }));
    expect(await screen.findByText("Partial")).toBeInTheDocument();
    expect(screen.getByText(/14 of 28 days are stored/)).toBeInTheDocument();
  });

  it("is a stated condition, not an error, when the preview is switched off", async () => {
    await show(new ApiError(404, MODEL_DISABLED_DETAIL, MODEL_DISABLED_DETAIL));
    expect(
      await screen.findByText("The low-volume model preview is switched off"),
    ).toBeInTheDocument();
    expect(screen.getByText(/LOW_VOLUME_MODEL_PREVIEW_ENABLED/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("tells an unknown source apart from the disabled setting", async () => {
    await show(new ApiError(404, "log source not found", "log source not found"));
    expect(await screen.findByText("The model could not be read")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(/switched off/)).toBeNull();
  });

  it("names the read:anomalies permission when forbidden", async () => {
    await show(new ApiError(403, "forbidden"));
    expect(await screen.findByText("The model needs permission")).toBeInTheDocument();
    expect(screen.getByText(/read:anomalies/)).toBeInTheDocument();
  });

  it("says what failed and retries", async () => {
    const call = vi
      .spyOn(api, "noiseModel")
      .mockRejectedValueOnce(new ApiError(500, "boom"))
      .mockResolvedValueOnce(model());
    render(<NoiseModelView logSourceId={SRC} />);
    expect(await screen.findByText("The model could not be loaded")).toBeInTheDocument();
    expect(screen.getByText(/unknown, not empty/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Scored")).toBeInTheDocument();
    expect(call).toHaveBeenCalledTimes(2);
  });
});

describe("the grid and the selected cell", () => {
  it("draws one grid per metric, 7 weekdays by 24 hours", async () => {
    await show(model());
    await screen.findByText("Scored");
    const tables = screen.getAllByRole("table", { name: /Event count|Average rate/ });
    expect(tables).toHaveLength(2);
    expect(within(tables[0]).getAllByRole("row")).toHaveLength(8);
    expect(within(tables[0]).getAllByRole("columnheader")).toHaveLength(25);
  });

  it("states what the cell number is and keeps the metric unit out of the title", async () => {
    await show(model());
    await screen.findByText("Scored");
    const title = screen.getByRole("heading", { name: "Event count" });
    expect(title.textContent).not.toMatch(/events per/);
    expect(
      screen.getByText(
        "Each cell: buckets in the last 28 days below p, by weekday and UTC hour. The metric itself is measured in events per bucket.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText(/measured in events per second/)).toBeInTheDocument();
    expect(screen.getAllByText("UTC", { selector: "[aria-hidden]" })).toHaveLength(2);
  });

  it("selecting a cell shows samples, mean, dispersion, raw median, shrunk estimate with weight and tails", async () => {
    await show(model());
    await screen.findByText("Scored");
    expect(screen.getByText("Select a cell to read its numbers.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Event count, Sat 11:00 UTC/ }));
    const detail = screen.getByRole("heading", { name: "Event count, Sat 11:00 UTC" }).parentElement!;
    expect(within(detail).getByText("Poisson")).toBeInTheDocument();
    expect(within(detail).getByText("10 stored observations")).toBeInTheDocument();
    expect(within(detail).getByText("Mean").nextElementSibling).toHaveTextContent("3 events per bucket");
    expect(within(detail).getByText("Raw median").nextElementSibling).toHaveTextContent("3 events per bucket");
    expect(within(detail).getByText("1 (variance / mean)")).toBeInTheDocument();
    expect(within(detail).getByText(/3\.2 events per bucket/)).toBeInTheDocument();
    expect(within(detail).getByText(/weight 56\s% on this cell, n = 10, k = 8, pooled median 3\.5/)).toBeInTheDocument();
    expect(within(detail).getByText(/12 above the mean/)).toBeInTheDocument();
    expect(within(detail).getByText(/0 below the mean/)).toBeInTheDocument();
    expect(within(detail).getByText(/of 47 buckets scored in 28 days/)).toBeInTheDocument();
    expect(within(detail).getByText(/Proposal only\. Nothing is written\./)).toBeInTheDocument();
    expect(within(detail).getByText(/median is 30; the stored cell median is 3 /)).toBeInTheDocument();
  });

  it("draws every null number as a dash with its reason, never as 0", async () => {
    await show(model());
    await screen.findByText("Scored");
    fireEvent.click(screen.getByRole("button", { name: /Average rate, Mon 00:00 UTC/ }));
    const detail = screen.getByRole("heading", { name: "Average rate, Mon 00:00 UTC" }).parentElement!;
    expect(within(detail).getByText(NOT_COUNTS.text)).toBeInTheDocument();
    expect(within(detail).getByText("not fitted")).toBeInTheDocument();
    const tails = within(detail).getByText("Buckets below p").nextElementSibling!;
    expect(tails).toHaveTextContent("—");
    expect(tails.textContent).not.toMatch(/0/);
    // The cell itself shows a dash, not a 0.
    expect(screen.getByRole("button", { name: /Average rate, Mon 00:00 UTC.*no tail count/ })).toHaveTextContent("—");
  });

  it("a NOT_JUDGED cell has no median, estimate or tail drawn as 0", async () => {
    await show(model());
    await screen.findByText("Scored");
    fireEvent.click(screen.getByRole("button", { name: /Event count, Tue 03:00 UTC/ }));
    const detail = screen.getByRole("heading", { name: "Event count, Tue 03:00 UTC" }).parentElement!;
    expect(within(detail).getByText("Not judged: no reliable baseline cell.")).toBeInTheDocument();
    expect(within(detail).getByText("Raw median").nextElementSibling).toHaveTextContent("—");
    expect(within(detail).getByText("Mean").nextElementSibling).toHaveTextContent("—");
    expect(within(detail).getByText("Shrunk estimate").nextElementSibling).toHaveTextContent(/^—/);
  });

  it("marks the selected cell and deselects on a second click", async () => {
    await show(model());
    await screen.findByText("Scored");
    const btn = screen.getByRole("button", { name: /Event count, Sat 11:00 UTC/ });
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(btn);
    expect(btn).toHaveAttribute("aria-pressed", "false");
    expect(screen.getByText("Select a cell to read its numbers.")).toBeInTheDocument();
  });

  it("shows the tail count in a scored cell and marks a level-shift cell", async () => {
    await show(model());
    await screen.findByText("Scored");
    const btn = screen.getByRole("button", { name: /Event count, Sat 11:00 UTC.*12 buckets below p.*Proposes a new normal/ });
    expect(btn).toHaveTextContent("12");
    expect(btn.className).toMatch(/shift/);
    expect(btn.className).toMatch(/bin-3/);
  });
});

describe("level-shift proposals", () => {
  it("lists them once above the grid and each link selects its cell", async () => {
    await show(model());
    await screen.findByText("Scored");
    expect(screen.getByRole("heading", { name: "1 cell proposes a new normal" })).toBeInTheDocument();
    expect(screen.getByText("Proposals only. Nothing is written back to a baseline.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("link", { name: "Event count, Sat 11:00 UTC" }));
    expect(screen.getByRole("button", { name: /Event count, Sat 11:00 UTC/ })).toHaveAttribute("aria-pressed", "true");
  });

  it("says plainly when no cell proposes one", async () => {
    await show(model({ cells: [insufficient] }));
    await screen.findByText("Scored");
    expect(screen.getByText(/No cell proposes a new normal/)).toBeInTheDocument();
  });
});

describe("calibration", () => {
  it("shows replayed flags and the flags on closed NOISE incidents per detector", async () => {
    await show(model());
    await screen.findByText("Scored");
    const row = screen.getByRole("row", { name: /Volume spike/ });
    expect(row).toHaveTextContent("6");
    expect(row).toHaveTextContent("2");
  });

  it("a refused calibration shows its sentence and no number, not a zero", async () => {
    const why = reason("noise.preview.maintenance", "The source is in maintenance, so nothing is replayed.");
    await show(
      model({
        calibration_reason: why,
        calibration: [
          {
            anomaly_type: "VOLUME_SPIKE",
            replayed_flags: null,
            noise_flags: null,
            excluded_by_direction: false,
            reason: why,
          },
        ],
      }),
    );
    await screen.findByText("Scored");
    const row = screen.getByRole("row", { name: /Volume spike/ });
    expect(row).toHaveTextContent("—");
    expect(row).toHaveTextContent(why.text);
    const [flags, onNoise] = within(row).getAllByRole("cell");
    expect(flags.textContent).toBe("—");
    expect(onNoise.textContent).toBe("—");
    expect(screen.getByText("Not calibrated.")).toBeInTheDocument();
  });

  it("a measured zero stays a zero", async () => {
    await show(
      model({
        calibration: [
          { anomaly_type: "VOLUME_DROP", replayed_flags: 0, noise_flags: 0, excluded_by_direction: false, reason: null },
        ],
      }),
    );
    await screen.findByText("Scored");
    const cells = within(screen.getByRole("row", { name: /Volume drop/ })).getAllByRole("cell");
    expect(cells[0]).toHaveTextContent("0");
    expect(cells[1]).toHaveTextContent("0");
  });
});

describe("number formatting", () => {
  it("draws a missing number as a dash and a measured zero as 0", async () => {
    const { num, count, precise, weightPercent } = await import("@/lib/noiseModel");
    for (const f of [num, count, precise]) {
      expect(f(null)).toBe("—");
      expect(f(undefined)).toBe("—");
      expect(f(Number.NaN)).toBe("—");
      expect(f(0)).toBe("0");
    }
    expect(num(3.14159)).toBe("3.14");
    expect(weightPercent(0.555556)).toBe("56\u00A0%");
  });
});

describe("failure classification", () => {
  it("separates the two 404s and the other statuses", async () => {
    const { modelFailure } = await import("@/lib/noiseModel");
    expect(modelFailure(404, MODEL_DISABLED_DETAIL)).toBe("disabled");
    expect(modelFailure(404, "log source not found")).toBe("unknown");
    expect(modelFailure(404, undefined)).toBe("unknown");
    expect(modelFailure(403, "x")).toBe("forbidden");
    expect(modelFailure(401, "x")).toBe("expired");
    expect(modelFailure(500, "x")).toBe("failed");
    expect(modelFailure(null, undefined)).toBe("failed");
  });
});

