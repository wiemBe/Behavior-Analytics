// SOC-13f: the control on a resolved WAF, IPS, property-watch or rule anomaly. Each family
// proposes its interval on its own bucket grid, names its own baseline in the confirmation, posts
// to its own path, and a rule never waits for "Applied".

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { StaticSession, type SessionValue } from "@/components/auth/session";
import { ApiError, trainingExclusionApi, type AnomalyDetail } from "@/lib/api";
import type { ExclusionLoad } from "@/lib/trainingExclusionLoad";

import { ExcludeInterval } from "./ExcludeInterval";
import { ANOMALY, CAN, READ_ONLY, exclusion, resolved } from "./fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }) }));

const OK: ExclusionLoad = { kind: "ok", items: [] };

const family = (over: Record<string, unknown>) => resolved(over as Partial<AnomalyDetail>);

const FAMILIES = {
  waf: family({
    subject_kind: "WAF_ENDPOINT",
    anomaly_type: "WAF_REQUEST_RATE_SPIKE",
    waf_endpoint: { id: "ep-1" },
  }),
  ipsEntity: family({
    subject_kind: "IPS_ENTITY",
    anomaly_type: "IPS_PORT_RATE_SPIKE",
    ips_entity: { id: "ent-1" },
  }),
  ipsSource: family({ subject_kind: "IPS_SOURCE", anomaly_type: "IPS_PORT_FANOUT_SPIKE" }),
  pwValue: family({
    subject_kind: "PW_VALUE",
    anomaly_type: "PW_VALUE_SPIKE",
    pw_value: { value_id: "val-1" },
  }),
  ruleStorm: family({ subject_kind: "RULE", anomaly_type: "RULE_STORM", rule: { rule_id: "rule-1" } }),
  ruleSilence: family({ subject_kind: "RULE", anomaly_type: "RULE_SILENCE", rule: { rule_id: "rule-1" } }),
} as const;

const PATH: Record<keyof typeof FAMILIES, string> = {
  waf: "waf",
  ipsEntity: "ips",
  ipsSource: "ips",
  pwValue: "property_watch",
  ruleStorm: "rule",
  ruleSilence: "rule",
};
const BASELINE: Record<keyof typeof FAMILIES, RegExp> = {
  waf: /from this endpoint's request baseline/,
  ipsEntity: /from this entity's IPS event baseline/,
  ipsSource: /from this source's IPS baseline/,
  pwValue: /from this property value's baseline/,
  ruleStorm: /from this rule's storm baseline/,
  ruleSilence: /from this rule's silence baseline/,
};

function mount(
  anomaly: AnomalyDetail,
  over: { bucket?: number | null; load?: ExclusionLoad; value?: SessionValue } = {},
) {
  return render(
    <StaticSession value={over.value ?? CAN}>
      <ExcludeInterval
        anomaly={anomaly}
        bucketSeconds={over.bucket === undefined ? 600 : over.bucket}
        load={over.load ?? OK}
      />
    </StaticSession>,
  );
}

const openButton = () => screen.queryByRole("button", { name: "Exclude interval from training" });
const names = Object.keys(FAMILIES) as Array<keyof typeof FAMILIES>;

beforeEach(() => {
  vi.restoreAllMocks();
});

describe.each(names)("%s", (name) => {
  it("offers the control for a resolved anomaly and a caller with anomaly:triage", () => {
    mount(FAMILIES[name]);
    expect(openButton()).toBeEnabled();
    expect(screen.getByRole("heading", { name: "Baseline training" })).toBeInTheDocument();
  });

  it("gives a read-only caller the protection line and the permission, no control", () => {
    mount(FAMILIES[name], { value: READ_ONLY });
    expect(openButton()).toBeNull();
    expect(screen.getByText(/needs the anomaly:triage permission/)).toBeInTheDocument();
  });

  it("is not offered while the anomaly is still open", () => {
    mount({ ...FAMILIES[name], state: "OPEN", resolved_at: null });
    expect(openButton()).toBeNull();
    expect(screen.getByText("Available once the anomaly is resolved.")).toBeInTheDocument();
  });

  it("proposes the incident span aligned to this family's grid, and names its baseline", async () => {
    mount(FAMILIES[name], { bucket: name.startsWith("rule") ? 3600 : 600 });
    await userEvent.click(openButton()!);
    // detected 08:07, resolved 09:52: 10-minute grid 08:00 to 10:00; 60-minute grid 08:00 to 10:00.
    expect(screen.getByLabelText("Start (UTC)")).toHaveValue("2026-11-04 08:00");
    expect(screen.getByLabelText("End (UTC, exclusive)")).toHaveValue("2026-11-04 10:00");
    expect(screen.getByText(BASELINE[name])).toBeInTheDocument();
  });

  it("posts to this family's path, with the family's result sentence", async () => {
    const create = vi.spyOn(trainingExclusionApi, "create").mockResolvedValue(
      exclusion({ subject_kind: name.startsWith("rule") ? "rule" : "endpoint" }),
    );
    mount(FAMILIES[name], { bucket: name.startsWith("rule") ? 3600 : 600 });
    await userEvent.click(openButton()!);
    const end = screen.getByLabelText("End (UTC, exclusive)");
    await userEvent.clear(end);
    await userEvent.type(end, "2026-11-04 11:00");
    await userEvent.type(screen.getByLabelText("Reason"), "Scanner ran for the whole window");
    await userEvent.click(screen.getByRole("button", { name: "Exclude interval" }));
    await waitFor(() => expect(create).toHaveBeenCalled());
    expect(create).toHaveBeenCalledWith(
      ANOMALY,
      {
        starts_at: "2026-11-04T08:00:00.000Z",
        ends_at: "2026-11-04T11:00:00.000Z",
        reason: "Scanner ran for the whole window",
      },
      PATH[name],
    );
    expect(
      await screen.findByText(
        name.startsWith("rule")
          ? "Recorded. The rule's next evaluation uses it; the expected range has not changed yet."
          : "Pending next baseline rebuild. The expected range has not changed.",
      ),
    ).toBeInTheDocument();
  });

  it("does not guess a bucket width the page could not read", () => {
    mount(FAMILIES[name], { bucket: null });
    expect(openButton()).toBeNull();
    expect(screen.getByText(/could not be read, so no interval can be proposed/)).toBeInTheDocument();
  });

  it("shows the server's own sentence when it refuses", async () => {
    vi.spyOn(trainingExclusionApi, "create").mockRejectedValue(new ApiError(422, "The interval is not on the bucket grid."));
    mount(FAMILIES[name], { bucket: name.startsWith("rule") ? 3600 : 600 });
    await userEvent.click(openButton()!);
    const end = screen.getByLabelText("End (UTC, exclusive)");
    await userEvent.clear(end);
    await userEvent.type(end, "2026-11-04 11:00");
    await userEvent.type(screen.getByLabelText("Reason"), "x");
    await userEvent.click(screen.getByRole("button", { name: "Exclude interval" }));
    expect(await screen.findByText("The interval is not on the bucket grid.")).toBeInTheDocument();
  });
});

describe("what differs by family", () => {
  it("says what a WAF, source, value and rule exclusion also changes", async () => {
    const note: Record<string, RegExp> = {
      waf: /request count and to every ratio of this endpoint/,
      ipsEntity: /this entity's event-rate baseline only/,
      ipsSource: /top-source share, block ratio and port fan-out/,
      pwValue: /spike, drop and disappeared baselines of this value/,
      ruleStorm: /storm baseline only; the rule's silence baseline is not changed/,
      ruleSilence: /silence baseline only; the rule's storm baseline is not changed/,
    };
    for (const name of names) {
      const { unmount } = mount(FAMILIES[name]);
      await userEvent.click(openButton()!);
      expect(screen.getByText(note[name])).toBeInTheDocument();
      unmount();
    }
  });

  it("a rule takes effect 'from the next evaluation', the others 'after the next baseline rebuild'", async () => {
    const { unmount } = mount(FAMILIES.ruleStorm, { bucket: 3600 });
    await userEvent.click(openButton()!);
    expect(screen.getByText(/This is used from the next evaluation, may make the expected range unavailable/)).toBeInTheDocument();
    expect(screen.queryByText(/after the next baseline rebuild/)).toBeNull();
    unmount();
    mount(FAMILIES.waf);
    await userEvent.click(openButton()!);
    expect(screen.getByText(/This takes effect after the next baseline rebuild, may make/)).toBeInTheDocument();
  });

  it("a rule's own pending row reads 'Used from the next evaluation', never 'Pending next baseline rebuild'", () => {
    mount(FAMILIES.ruleStorm, {
      bucket: 3600,
      load: { kind: "ok", items: [exclusion({ subject_kind: "rule", scope_key: "rule:storm" })] },
    });
    const list = screen.getByRole("list", { name: "Training protection" });
    expect(list).toHaveTextContent("Used from the next evaluation");
    expect(list).not.toHaveTextContent("Pending next baseline rebuild");
  });

  it("explains a misaligned bound with the family's grid, not the source's", async () => {
    mount(FAMILIES.ruleStorm, { bucket: 3600 });
    await userEvent.click(openButton()!);
    const start = screen.getByLabelText("Start (UTC)");
    await userEvent.clear(start);
    await userEvent.type(start, "2026-11-04 07:30");
    expect(screen.getByRole("alert")).toHaveTextContent("60-minute match-window boundaries");
  });

  it.each([
    ["a WAF new endpoint", { ...FAMILIES.waf, anomaly_type: "WAF_NEW_ENDPOINT_ACCESS" }],
    ["an IPS new destination port", { ...FAMILIES.ipsEntity, anomaly_type: "IPS_NEW_DESTINATION_PORT" }],
    ["a new property value", { ...FAMILIES.pwValue, anomaly_type: "PW_NEW_VALUE" }],
    ["a new property combination", { ...FAMILIES.pwValue, anomaly_type: "PW_NEW_COMBINATION" }],
  ])("%s: nothing at all, not a dead button", (_n, anomaly) => {
    const { container } = mount(anomaly as AnomalyDetail);
    expect(container).toBeEmptyDOMElement();
  });
});
