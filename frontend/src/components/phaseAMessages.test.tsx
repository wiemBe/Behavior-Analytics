// MSG-5: the Phase A detector sentence and the lifecycle transition reasons in the reader's
// language. A coded sentence renders from the `backend` catalog in Turkish; English shows the
// API's text exactly as sent; a row stored before codes keeps its English text in Turkish.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import codes from "@/i18n/backendCodes.generated.json";
import { renderBackendCode, renderBackendMessage } from "@/i18n/backendMessage";
import { createTextTranslator } from "@/i18n/textTranslator";
import type { AnomalyDetail, AnomalyTransition, DetectionDetail } from "@/lib/api";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { setTestLocale } from "../../vitest.setup";

import { Evidence } from "./Evidence";
import { LifecycleTimeline } from "./behavior/LifecycleTimeline";
import { TechnicalDetails } from "./behavior/TechnicalDetails";

const NBSP = " ";
const TR = createTextTranslator("tr", tr);
const EN = createTextTranslator("en", en);

const SPIKE_TEXT =
  "average EPS 6.00 is above the baseline median 2.00 (3.00x expected, 1200 events/bucket); " +
  "robust z=17.99 (threshold ±3.5)";
const SPIKE_PARAMS = {
  observed: 6.0,
  expected: 2.0,
  ratio_text: { code: "anomaly.detector.ratio_times_expected", params: { ratio: 3.0 } },
  delta: 1200.0,
  basis: { code: "anomaly.detector.basis_z", params: { score: 17.99, threshold: 3.5 } },
};
const SPIKE_TR =
  "Ortalama EPS 6,00, baseline medyanı 2,00 değerinin üzerinde (beklenenin 3,00 katı, " +
  "1.200 olay/zaman dilimi); sağlam z=17,99 (eşik ±3,5)";

describe("the catalog renders every Phase A code from its sample", () => {
  const phaseA = Object.entries(codes).filter(([code]) => code.startsWith("anomaly."));

  it("has the full family", () => {
    expect(phaseA.length).toBeGreaterThanOrEqual(56);
  });

  it.each(phaseA)("%s renders in Turkish without a fallback", (code, spec) => {
    const out = renderBackendCode({ code, params: spec.sample as Record<string, unknown> }, "tr", TR);
    expect(out, code).toBeDefined();
    expect(out).not.toMatch(/\{|\}/);
    expect(out).not.toBe(spec.sample_english);
  });
});

describe("Evidence's reason line", () => {
  const data = { anomaly_type: "VOLUME_SPIKE", reason: SPIKE_TEXT };

  it("renders the coded sentence in Turkish, with its nested basis and numbers", () => {
    setTestLocale("tr");
    render(
      <Evidence
        data={{ ...data, reason_code: "anomaly.detector.volume_above", reason_params: SPIKE_PARAMS }}
      />,
    );
    expect(screen.getByText(SPIKE_TR)).toBeInTheDocument();
    expect(screen.queryByText(SPIKE_TEXT)).toBeNull();
  });

  it("shows the API's text unchanged in English even with a code", () => {
    setTestLocale("en");
    render(
      <Evidence
        data={{ ...data, reason_code: "anomaly.detector.volume_above", reason_params: SPIKE_PARAMS }}
      />,
    );
    expect(screen.getByText(SPIKE_TEXT)).toBeInTheDocument();
  });

  it.each([
    ["no code (an alert stored before codes)", {}],
    ["a null code", { reason_code: null, reason_params: null }],
    ["an unknown code", { reason_code: "anomaly.detector.nope", reason_params: {} }],
    ["params that are not an object", { reason_code: "anomaly.detector.volume_above", reason_params: "x" }],
    ["params missing a name", { reason_code: "anomaly.detector.volume_above", reason_params: { observed: 6 } }],
  ])("keeps the API's English text in Turkish for %s", (_name, extra) => {
    setTestLocale("tr");
    render(<Evidence data={{ ...data, ...extra }} />);
    expect(screen.getByText(SPIKE_TEXT)).toBeInTheDocument();
  });
});

function detection(over: Partial<DetectionDetail> = {}): DetectionDetail {
  return {
    reason: SPIKE_TEXT,
    reason_code: "anomaly.detector.volume_above",
    reason_params: SPIKE_PARAMS,
    expected_low: 1.7,
    expected_high: 2.3,
    threshold: 3.5,
    baseline_sample_count: 30,
    baseline_completeness: 0.9,
    baseline_version: 2,
    observed_eps: 6,
    expected_eps: 2,
    observed_events: 1800,
    expected_events: 600,
    absolute_delta_events: 1200,
    bucket_seconds: 300,
    ratio: 3,
    ratio_basis: null,
    robust_score_status: "OK",
    robust_z: 17.99,
    fallback_bound: null,
    ...over,
  };
}

function anomaly(det: DetectionDetail | null): AnomalyDetail {
  return {
    id: "a-1",
    log_source_id: "s-1",
    log_source_name: "Lab source",
    anomaly_type: "VOLUME_SPIKE",
    state: "OPEN",
    severity: "HIGH",
    observed_value: 6,
    expected_value: 2,
    deviation_ratio: 3,
    robust_z: 17.99,
    absolute_delta: 1200,
    consecutive_buckets: 2,
    confidence: 0.9,
    detected_at: "2026-10-12T09:05:00Z",
    opened_at: "2026-10-12T09:15:00Z",
    anomaly_start: "2026-10-12T09:00:00Z",
    anomaly_end: null,
    resolved_at: null,
    duration_seconds: null,
    evidence_status: "NOT_REQUESTED",
    suppressed: false,
    explanation: SPIKE_TEXT,
    baseline_version: 2,
    policy_version: 1,
    transitions: [],
    explanation_package: null,
    detection: det,
  };
}

describe("TechnicalDetails' detector reasoning", () => {
  it("renders the coded sentence in Turkish under its label", () => {
    setTestLocale("tr");
    render(<TechnicalDetails anomaly={anomaly(detection())} packaged={null} />);
    expect(screen.getByText("Algılayıcı gerekçesi")).toBeInTheDocument();
    expect(screen.getByText(SPIKE_TR)).toBeInTheDocument();
  });

  it("renders the API's text in English", () => {
    setTestLocale("en");
    render(<TechnicalDetails anomaly={anomaly(detection())} packaged={null} />);
    expect(screen.getByText("Detector reasoning")).toBeInTheDocument();
    expect(screen.getByText(SPIKE_TEXT)).toBeInTheDocument();
  });

  it("keeps the English text for a row stored before codes, even in Turkish", () => {
    setTestLocale("tr");
    render(
      <TechnicalDetails
        anomaly={anomaly(detection({ reason_code: null, reason_params: null }))}
        packaged={null}
      />,
    );
    expect(screen.getByText(SPIKE_TEXT)).toBeInTheDocument();
  });

  it("shows no reasoning line when the detector recorded no sentence", () => {
    setTestLocale("en");
    render(
      <TechnicalDetails anomaly={anomaly(detection({ reason: null, reason_code: null }))} packaged={null} />,
    );
    expect(screen.queryByText("Detector reasoning")).toBeNull();
  });

  it("shows no reasoning line when there is no detection block", () => {
    setTestLocale("en");
    render(<TechnicalDetails anomaly={anomaly(null)} packaged={null} />);
    expect(screen.queryByText("Detector reasoning")).toBeNull();
  });
});

function transition(over: Partial<AnomalyTransition>): AnomalyTransition {
  return {
    from_state: "CANDIDATE",
    to_state: "OPEN",
    occurred_at: "2026-08-02T18:37:39Z",
    bucket_start: "2026-08-02T18:37:00Z",
    reason: "x",
    actor: "anomaly-engine",
    observed_value: 5.98,
    expected_value: 1.98,
    ...over,
  };
}

describe("LifecycleTimeline's reasons", () => {
  const confirmed = transition({
    reason: "2 consecutive abnormal bucket(s) reached the confirmation threshold of 2",
    reason_code: "anomaly.lifecycle.confirmed",
    reason_params: { count: 2, threshold: 2 },
  });
  const recovered = transition({
    from_state: "RECOVERING",
    to_state: "RESOLVED",
    reason: "3 consecutive normal bucket(s) reached the recovery threshold of 3",
    reason_code: "anomaly.lifecycle.recovered",
    reason_params: { count: 3, threshold: 3 },
  });

  it("renders the confirmation and recovery sentences in Turkish", () => {
    setTestLocale("tr");
    render(<LifecycleTimeline policyVersion={1} transitions={[confirmed, recovered]} />);
    expect(
      screen.getByText("Art arda 2 anormal zaman dilimi, 2 olan doğrulama eşiğine ulaştı"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Art arda 3 normal zaman dilimi, 3 olan düzelme eşiğine ulaştı"),
    ).toBeInTheDocument();
  });

  it("groups large counts in Turkish", () => {
    setTestLocale("tr");
    render(
      <LifecycleTimeline
        policyVersion={1}
        transitions={[
          transition({
            reason: "1204 consecutive abnormal bucket(s)",
            reason_code: "anomaly.lifecycle.opened",
            reason_params: { count: 1204 },
          }),
        ]}
      />,
    );
    expect(screen.getByText("Art arda 1.204 anormal zaman dilimi")).toBeInTheDocument();
  });

  it("shows the API's English text in English", () => {
    setTestLocale("en");
    render(<LifecycleTimeline policyVersion={1} transitions={[confirmed]} />);
    expect(screen.getByText(confirmed.reason as string)).toBeInTheDocument();
  });

  it("keeps the English text for a transition stored before codes", () => {
    setTestLocale("tr");
    render(
      <LifecycleTimeline
        policyVersion={1}
        transitions={[transition({ reason_code: null, reason_params: null })]}
      />,
    );
    expect(screen.getByText("x")).toBeInTheDocument();
  });
});

describe("a lowercase enum inside a sentence", () => {
  const spec = (codes as Record<string, { sample: Record<string, unknown> }>)[
    "anomaly.detector.volume_bucket_incomplete"
  ];

  it("takes the group's lowercase label in Turkish", () => {
    const out = renderBackendCode(
      { code: "anomaly.detector.volume_bucket_incomplete", params: { completeness: "PARTIAL" } },
      "tr",
      TR,
    );
    expect(out).toBe("Metrik zaman dilimi kısmi; tam bir gözlem değil");
    expect(spec.sample.completeness).toBe("MISSING");
  });

  it("lowercases a value the catalog does not know instead of showing it in capitals", () => {
    const out = renderBackendCode(
      { code: "anomaly.detector.volume_bucket_incomplete", params: { completeness: "STALLED" } },
      "tr",
      TR,
    );
    expect(out).toBe("Metrik zaman dilimi stalled; tam bir gözlem değil");
  });

  it("is not rendered from params in English", () => {
    const text = "metric bucket is partial, not a whole observation";
    expect(
      renderBackendMessage(
        {
          text,
          code: "anomaly.detector.volume_bucket_incomplete",
          params: { completeness: "PARTIAL" },
        },
        "en",
        EN,
      ),
    ).toBe(text);
  });
});

describe("the threshold sentence units", () => {
  it("keeps U+00A0 between the delay and its unit in Turkish", () => {
    const out = renderBackendCode(
      { code: "anomaly.detector.delay_exceeds", params: { delay: 901.4, limit: 900 } },
      "tr",
      TR,
    );
    expect(out).toBe(`Olay gecikmesi 901${NBSP}sn, 900${NBSP}sn sınırını aşıyor`);
  });

  it("renders the cardinality percent with the shared Turkish spelling", () => {
    const out = renderBackendCode(
      {
        code: "anomaly.detector.cardinality_dropped",
        params: { count: 3, fraction: { kind: "percent", ratio: 0.4, precision: 0 }, median: 40 },
      },
      "tr",
      TR,
    );
    expect(out).toContain("%40");
    expect(out).toContain("Ayrı kaynak IP sayısı 3");
  });
});
