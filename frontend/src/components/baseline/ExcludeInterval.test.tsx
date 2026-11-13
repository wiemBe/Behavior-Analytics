import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, trainingExclusionApi, type AnomalyDetail } from "@/lib/api";
import type { ExclusionLoad } from "@/lib/trainingExclusionLoad";

import { ExcludeInterval } from "./ExcludeInterval";
import { ANOMALY, CAN, READ_ONLY, exclusion, resolved, session } from "./fixtures";

const refresh = vi.fn();
vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh, push: vi.fn() }) }));

const OK: ExclusionLoad = { kind: "ok", items: [] };

function mount(
  over: {
    anomaly?: AnomalyDetail;
    bucket?: number | null;
    load?: ExclusionLoad;
    value?: SessionValue;
  } = {},
) {
  return render(
    <StaticSession value={over.value ?? CAN}>
      <ExcludeInterval
        anomaly={over.anomaly ?? resolved()}
        bucketSeconds={over.bucket === undefined ? 300 : over.bucket}
        load={over.load ?? OK}
      />
    </StaticSession>,
  );
}

const openButton = () => screen.queryByRole("button", { name: "Exclude interval from training" });
const startBox = () => screen.getByLabelText("Start (UTC)");
const endBox = () => screen.getByLabelText("End (UTC, exclusive)");
const reasonBox = () => screen.getByLabelText("Reason");
const submit = () => screen.getByRole("button", { name: "Exclude interval" });

async function openForm() {
  await userEvent.click(openButton()!);
}
async function set(box: HTMLElement, text: string) {
  await userEvent.clear(box);
  await userEvent.type(box, text);
}

beforeEach(() => {
  vi.restoreAllMocks();
  refresh.mockClear();
});

describe("who sees the control, and when", () => {
  it("offers it for a resolved anomaly to a caller with anomaly:triage, with the protection line", () => {
    mount();
    expect(openButton()).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Baseline training" })).toBeInTheDocument();
    expect(screen.getByText(/detected incident span is excluded from training automatically/)).toBeInTheDocument();
    expect(screen.getByText(/No analyst exclusion exists for this incident/)).toBeInTheDocument();
  });

  it("gives a read-only caller the protection line and the permission, but no control", () => {
    mount({ value: READ_ONLY });
    expect(openButton()).toBeNull();
    expect(screen.getByText(/excluded from training automatically/)).toBeInTheDocument();
    expect(screen.getByText(/needs the anomaly:triage permission/)).toBeInTheDocument();
  });

  it("accepts the administrator wildcard", () => {
    mount({ value: { ...session([]), me: { ...session([]).me!, permissions: ["admin:*"] } } });
    expect(openButton()).toBeEnabled();
  });

  it("says nothing about the caller until the session is known", () => {
    mount({ value: session([], "loading") });
    expect(openButton()).toBeNull();
    expect(screen.queryByText(/needs the anomaly:triage permission/)).toBeNull();
  });

  it.each(["CANDIDATE", "OPEN", "RECOVERING"] as const)("%s: a reason line and no control", (state) => {
    mount({ anomaly: resolved({ state, resolved_at: null }) });
    expect(screen.getByText("Available once the anomaly is resolved.")).toBeInTheDocument();
    expect(openButton()).toBeNull();
    expect(screen.queryByText(/Training protection/)).toBeNull();
  });

  it("a suppressed anomaly is neither active nor resolved", () => {
    mount({ anomaly: resolved({ state: "SUPPRESSED" }) });
    expect(screen.getByText("Only a resolved anomaly can be excluded from training.")).toBeInTheDocument();
    expect(openButton()).toBeNull();
  });

  it.each(["WAF_ENDPOINT", "IPS_ENTITY", "IPS_SOURCE", "PW_VALUE", "RULE"] as const)(
    "%s: nothing at all",
    (subject_kind) => {
      const { container } = mount({ anomaly: resolved({ subject_kind }) });
      expect(container).toBeEmptyDOMElement();
    },
  );

  it("does not guess a bucket width the page could not read", () => {
    mount({ bucket: null });
    expect(openButton()).toBeNull();
    expect(screen.getByText(/bucket width of this source could not be read/)).toBeInTheDocument();
  });
});

describe("what it says about existing exclusions", () => {
  it("lists this incident's live exclusions with their effect, and no other", () => {
    mount({
      load: {
        kind: "ok",
        items: [
          exclusion({ id: "a" }),
          exclusion({ id: "b", origin_anomaly_id: "someone-else", starts_at: "2026-11-01T00:00:00Z" }),
          exclusion({ id: "c", state: "REVOKED", starts_at: "2026-11-02T00:00:00Z" }),
        ],
      },
    });
    const list = screen.getByRole("list", { name: "Training protection" });
    expect(list).toHaveTextContent("04 Nov 08:00–10:00 UTC");
    expect(list).toHaveTextContent("Pending next baseline rebuild");
    expect(list.querySelectorAll("li")).toHaveLength(1);
    expect(screen.queryByText(/No analyst exclusion exists/)).toBeNull();
  });

  it("says when the exclusions could not be read instead of saying there are none", () => {
    mount({ load: { kind: "error" } });
    expect(screen.getByText("Analyst exclusions could not be loaded for this incident.")).toBeInTheDocument();
  });

  it("names the permission when the exclusions may not be read", () => {
    mount({ load: { kind: "forbidden", permission: "read:anomalies" } });
    expect(screen.getByText(/Reading analyst exclusions needs the read:anomalies permission/)).toBeInTheDocument();
  });
});

describe("the dialog", () => {
  it("opens prefilled with the incident span aligned to the bucket width, with the confirmation text", async () => {
    mount();
    await openForm();
    expect(startBox()).toHaveValue("2026-11-04 08:05");
    expect(endBox()).toHaveValue("2026-11-04 09:55");
    const form = screen.getByRole("form", { name: "Exclude interval from training" });
    expect(form).toHaveTextContent(
      "Exclude 04 Nov 08:05–09:55 UTC from this source-volume baseline. The detected incident span is already excluded automatically. Extend the UTC bounds only if nearby buckets were also affected. This takes effect after the next baseline rebuild, may make the expected range unavailable, and does not change this anomaly or its alerts.",
    );
    expect(form).toHaveTextContent("5-minute bucket width");
    expect(form).toHaveTextContent("at most 7 days");
    expect(startBox()).toHaveFocus();
  });

  it("keeps the confirm control off until the interval is widened, and says why", async () => {
    mount();
    await openForm();
    expect(submit()).toBeDisabled();
    expect(screen.getByText(/Widen the interval to include nearby buckets/)).toBeInTheDocument();
    await set(startBox(), "2026-11-04 08:00");
    expect(submit()).toBeEnabled();
    expect(screen.getByRole("form")).toHaveTextContent("Exclude 04 Nov 08:00–09:55 UTC");
  });

  it.each([
    ["start", "2026-11-04 08:10", /Start cannot be later than the detected span's start, 2026-11-04 08:05 UTC/],
    ["end", "2026-11-04 09:50", /End cannot be earlier than the detected span's end, 2026-11-04 09:55 UTC/],
  ])("refuses narrowing the %s before anything is sent", async (which, text, message) => {
    const create = vi.spyOn(trainingExclusionApi, "create");
    mount();
    await openForm();
    await set(which === "start" ? startBox() : endBox(), text);
    expect(screen.getByRole("alert")).toHaveTextContent(message);
    expect(submit()).toBeDisabled();
    await userEvent.click(submit());
    expect(create).not.toHaveBeenCalled();
  });

  it("refuses a bound off the bucket grid, an unreadable time and a seven-day overrun", async () => {
    mount();
    await openForm();
    await set(startBox(), "2026-11-04 08:03");
    expect(screen.getByRole("alert")).toHaveTextContent("5-minute bucket boundaries");
    await set(startBox(), "tomorrow");
    expect(screen.getByRole("alert")).toHaveTextContent("Start is not a UTC time in the form YYYY-MM-DD HH:MM");
    await set(startBox(), "2026-10-27 08:05");
    expect(screen.getByRole("alert")).toHaveTextContent("at most 7 days");
    expect(submit()).toBeDisabled();
    await set(startBox(), "2026-10-28 09:55");
    expect(screen.queryByRole("alert")).toBeNull();
    expect(submit()).toBeEnabled();
  });

  it("needs a reason before it sends anything", async () => {
    const create = vi.spyOn(trainingExclusionApi, "create");
    mount();
    await openForm();
    await set(startBox(), "2026-11-04 08:00");
    await userEvent.click(submit());
    expect(screen.getByRole("alert")).toHaveTextContent("Enter a reason of 1 to 1000 characters.");
    expect(create).not.toHaveBeenCalled();
    await userEvent.type(reasonBox(), "   ");
    await userEvent.click(submit());
    expect(create).not.toHaveBeenCalled();
  });

  it("closes on Escape and on Cancel, and returns focus to the button", async () => {
    mount();
    await openForm();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("form")).toBeNull();
    await waitFor(() => expect(openButton()).toHaveFocus());
    await openForm();
    await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form")).toBeNull();
  });
});

describe("saving", () => {
  async function fill() {
    await openForm();
    await set(startBox(), "2026-11-04 08:00");
    await set(endBox(), "2026-11-04 10:00");
    await userEvent.type(reasonBox(), "  Collector restarted during the incident  ");
  }

  it("sends UTC instants and the trimmed reason, then says it is pending and never that the band moved", async () => {
    const create = vi.spyOn(trainingExclusionApi, "create").mockResolvedValue(exclusion());
    mount();
    await fill();
    await userEvent.click(submit());
    expect(create).toHaveBeenCalledWith(ANOMALY, {
      starts_at: "2026-11-04T08:00:00.000Z",
      ends_at: "2026-11-04T10:00:00.000Z",
      reason: "Collector restarted during the incident",
    });
    const status = await screen.findByText(/Pending next baseline rebuild\. The expected range has not changed\./);
    expect(status).toHaveAttribute("role", "status");
    expect(screen.queryByRole("form")).toBeNull();
    expect(openButton()).toHaveFocus();
    // The new row is listed in the protection line and the page is asked to read again.
    expect(screen.getByRole("list", { name: "Training protection" })).toHaveTextContent("04 Nov 08:00–10:00 UTC");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("states what an existing exact row already is, not that it was just saved", async () => {
    vi.spyOn(trainingExclusionApi, "create").mockResolvedValue(exclusion({ state: "APPLIED" }));
    mount();
    await fill();
    await userEvent.click(submit());
    expect(await screen.findByText(/already excluded and a baseline rebuild has applied it/)).toBeInTheDocument();
  });

  it("sends once however often the button is pressed", async () => {
    let finish: (row: ReturnType<typeof exclusion>) => void = () => {};
    const create = vi
      .spyOn(trainingExclusionApi, "create")
      .mockReturnValue(new Promise((resolve) => (finish = resolve)));
    mount();
    await fill();
    await userEvent.click(submit());
    const saving = screen.getByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    await userEvent.click(saving);
    expect(create).toHaveBeenCalledTimes(1);
    finish(exclusion());
    await screen.findByText(/Pending next baseline rebuild\. The expected range/);
  });

  it("ignores a second submit that arrives before the first has answered", async () => {
    const create = vi
      .spyOn(trainingExclusionApi, "create")
      .mockReturnValue(new Promise(() => {}));
    mount();
    await fill();
    const form = screen.getByRole("form", { name: "Exclude interval from training" });
    fireEvent.submit(form);
    fireEvent.submit(form);
    expect(create).toHaveBeenCalledTimes(1);
  });

  it.each([
    [409, "the interval partly overlaps an active exclusion for the same baseline"],
    [409, "this baseline already has 50 active exclusions in its lookback"],
    [409, "the anomaly is still active; an interval can be excluded only after it is resolved"],
    [422, "the interval must cover the whole detected incident span; it can only be widened"],
    [422, "the interval must not be longer than 7 days"],
    [422, "a reason of 1 to 1000 characters is required"],
  ])("shows the server's refusal (%i) exactly as it came and keeps the input", async (status, sentence) => {
    vi.spyOn(trainingExclusionApi, "create").mockRejectedValue(new ApiError(status, sentence));
    mount();
    await fill();
    await userEvent.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent(sentence);
    expect(startBox()).toHaveValue("2026-11-04 08:00");
    expect(reasonBox()).toHaveValue("  Collector restarted during the incident  ");
    expect(submit()).toBeEnabled();
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.queryByText(/Pending next baseline rebuild/)).toBeNull();
  });

  it("names the permission on a 403", async () => {
    vi.spyOn(trainingExclusionApi, "create").mockRejectedValue(
      new ApiError(403, "missing required permission: anomaly:triage"),
    );
    mount();
    await fill();
    await userEvent.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Recording an exclusion needs the anomaly:triage permission.",
    );
  });

  it("says the anomaly is gone on a 404, and a failed request is not called a refusal", async () => {
    vi.spyOn(trainingExclusionApi, "create").mockRejectedValueOnce(new ApiError(404, "not found"));
    mount();
    await fill();
    await userEvent.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("The anomaly no longer exists. Reload the page.");
    vi.spyOn(trainingExclusionApi, "create").mockRejectedValueOnce(new ApiError(500, "boom"));
    await userEvent.click(submit());
    expect(await screen.findByRole("alert")).toHaveTextContent("The exclusion was not saved. boom");
  });
});
