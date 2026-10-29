import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import type { WafDetectionDetail } from "@/lib/api";
import {
  wafCodedDetection,
  wafCodedNewEndpointDetection,
  wafCodedRatioDetection,
  wafDetail,
  wafDetection,
} from "./fixtures";
import { WafDetectionBlock } from "./WafDetectionBlock";

// MSG-6: the detector's sentences (reason and caveat) come in the reader's language when the API
// sends their code, and show the API's text exactly as it was written otherwise.

const NAMED = { code: "waf.subject.named", params: { method: "GET", endpoint: "/portal/login" } };
const Z = { code: "waf.basis.z", params: { score: 12.4, threshold: 3.5 } };
const SCOPE = { code: "waf.scope.lower_bound_increase", params: {} };

/** The reason paragraph's exact text: spaces between a number and its unit are kept as written. */
function reasonText(): string | null | undefined {
  return document.querySelector(".waf-reason")?.textContent;
}

function block(detection: Partial<WafDetectionDetail>, type = "WAF_REQUEST_RATE_SPIKE") {
  const base =
    type === "WAF_NEW_ENDPOINT_ACCESS"
      ? wafCodedNewEndpointDetection(detection)
      : type === "WAF_REQUEST_RATE_SPIKE"
        ? wafCodedDetection(detection)
        : wafCodedRatioDetection(detection);
  return render(
    <WafDetectionBlock
      anomaly={wafDetail({ anomaly_type: type as never, detection: base })}
      bucketSeconds={300}
    />,
  );
}

describe("the detector's reason", () => {
  beforeEach(() => setTestLocale("tr"));

  it("is worded in Turkish from its code: request-rate spike", () => {
    block({});
    expect(reasonText()).toBe(
      "412 istek, baseline medyanı olan 41,5 değerinin üzerinde (beklenenin 9,93x katı, +370 istek/zaman dilimi); robust z=21,40 (eşik +3,5)",
    );
  });

  it("is worded in Turkish from its code: a ratio, with its noun phrases, share and points", () => {
    block({}, "WAF_BLOCK_RATIO_SPIKE");
    expect(reasonText()).toBe(
      "318\u00a0/\u00a01.000 WAF eylemi taşıyan istekler, GET /portal/login için engellenen istekler idi (%31,8); 14.250 geçmiş istekten beklenen oran %2,1 idi (+29,7\u00a0puan, 15,1x); robust z=12,40 (eşik +3,5)",
    );
  });

  it("keeps the lower bound's wrapped sentence and its scope in Turkish", () => {
    block({
      reason: "at least 250 requests were attributed to this endpoint, but the count is a lower bound and z=2.53",
      reason_code: "waf.rate.not_proven_lower_bound",
      reason_params: {
        observed: 250,
        reason: { code: "waf.rate.z_not_reached", params: { score: 2.53, threshold: 3.5 } },
      },
    });
    expect(reasonText()).toBe(
      "En az 250 istek bu endpoint'e atfedildi, ancak sayım bir alt sınırdır ve z=2,53 robust skoru, +3,5 eşiğine ulaşmadı; alt sınırdan normal bir karar çıkarılamaz",
    );
  });

  it("words the lower-bound firing sentence with its scope clause", () => {
    block({
      reason_code: "waf.rate.fired_lower",
      reason_params: {
        observed: 412,
        scope: SCOPE,
        expected: 41.5,
        ratio_text: { code: "waf.rate.ratio_none", params: {} },
        delta: 370,
        basis: { code: "waf.basis.mad_outside_band", params: { lower: 22, upper: 68 } },
      },
    });
    expect(reasonText()).toBe(
      "En az 412 istek (alt sınır: sayım bir alt sınırdır: artış kanıtlanabilir, normal davranış kanıtlanamaz), baseline medyanı olan 41,5 değerinin üzerinde (hiç trafik beklenmiyordu, +370 istek/zaman dilimi); MAD=0 olduğundan robust skor kullanılamıyor; gözlenen değer beklenen [22,00, 68,00] aralığının dışında",
    );
  });

  it("words a client-concentration sentence with its volume clause", () => {
    block(
      {
        reason_code: "waf.concentration.fired",
        reason_params: {
          top: 400,
          total: 500,
          subject: NAMED,
          share: { kind: "percent", ratio: 0.8, precision: 1 },
          expected: { kind: "percent", ratio: 0.2, precision: 1 },
          rise: { kind: "points", points: 60, precision: 1, signed: true },
          top5: { kind: "percent", ratio: 0.95, precision: 1 },
          volume: {
            code: "waf.concentration.volume_with_baseline",
            params: { requests: 500, expected: 100.0 },
          },
          basis: Z,
        },
      },
      "WAF_CLIENT_CONCENTRATION_SPIKE",
    );
    expect(reasonText()).toBe(
      "En büyük kaynak IP, GET /portal/login için 400\u00a0/\u00a0500 isteği gönderdi (%80,0); beklenen pay %20,0 idi (+60,0\u00a0puan); en büyük beş kaynak %95,0 gönderdi; endpoint hacmi 500 istek, beklenen 100; robust z=12,40 (eşik +3,5)",
    );
  });

  it("words a new-endpoint sentence with its three timestamps in UTC", () => {
    block({}, "WAF_NEW_ENDPOINT_ACCESS");
    expect(reasonText()).toBe(
      "İlk göründüğü 2026-10-12 08:40:00 UTC tarihinden bu yana GET /portal/login, 4 zaman diliminde 320 istek aldı; doğrulanmış 2026-10-05 08:40:00 UTC – 2026-10-12 08:40:00 UTC geçmişinde bir zaman diliminde hiçbir zaman 20 veya daha fazla kez istenmedi",
    );
  });

  it("shows the API text in English whatever the code", () => {
    setTestLocale("en");
    block({});
    expect(
      screen.getByText("412 requests in the 09:00 bucket against a baseline median of 41.5"),
    ).toBeInTheDocument();
  });

  it("shows the API text for a row stored before codes", () => {
    block({ reason_code: null, reason_params: null });
    expect(
      screen.getByText("412 requests in the 09:00 bucket against a baseline median of 41.5"),
    ).toBeInTheDocument();
  });

  it("shows the API text for a code this build does not know, and for a broken parameter", () => {
    block({ reason_code: "waf.rate.nope", reason_params: {} });
    expect(screen.getByText(/^412 requests in the 09:00 bucket/)).toBeInTheDocument();
  });

  it("falls back to the API text when a nested parameter is of the wrong kind", () => {
    block({ reason_params: { ...(wafCodedDetection().reason_params ?? {}), basis: 7 } });
    expect(screen.getByText(/^412 requests in the 09:00 bucket/)).toBeInTheDocument();
  });

  it("falls back to the incident's explanation, with its own code, when the block has no reason", () => {
    render(
      <WafDetectionBlock
        anomaly={wafDetail({
          explanation: "bucket is partial, not a whole observation",
          explanation_code: "waf.rate.bucket_not_whole",
          explanation_params: { completeness: "partial" },
          detection: wafDetection({ reason: null, reason_code: null, reason_params: null }),
        })}
        bucketSeconds={300}
      />,
    );
    expect(screen.getByText("Zaman dilimi kısmi; bütün bir gözlem sayılmaz")).toBeInTheDocument();
  });
});

describe("the detector's caveat", () => {
  beforeEach(() => setTestLocale("tr"));
  const CAVEAT = "A dominant source IP describes traffic composition, not intent.";

  it("is worded in Turkish from its code", () => {
    block({ caveat: CAVEAT, caveat_code: "waf.caveat.client_concentration", caveat_params: {} });
    expect(
      screen.getByText("Baskın bir kaynak IP trafik bileşimini tanımlar; niyeti değil."),
    ).toBeInTheDocument();
  });

  it("is worded in Turkish on the new-endpoint block too", () => {
    block(
      { caveat: "x", caveat_code: "waf.caveat.new_endpoint", caveat_params: {} },
      "WAF_NEW_ENDPOINT_ACCESS",
    );
    expect(
      screen.getByText("Yeni istenen bir endpoint, bir yoklama kadar kolayca bir dağıtım da olabilir."),
    ).toBeInTheDocument();
  });

  it("shows the API text in English, for a missing code and for an unknown one", () => {
    setTestLocale("en");
    block({ caveat: CAVEAT, caveat_code: "waf.caveat.client_concentration", caveat_params: {} });
    expect(screen.getByText(CAVEAT)).toBeInTheDocument();
  });

  it("shows the API text in Turkish when the row has no code or an unknown one", () => {
    block({ caveat: CAVEAT });
    expect(screen.getByText(CAVEAT)).toBeInTheDocument();
    document.body.innerHTML = "";
    block({ caveat: CAVEAT, caveat_code: "waf.caveat.nope", caveat_params: {} });
    expect(screen.getByText(CAVEAT)).toBeInTheDocument();
  });

  it("draws nothing when there is no caveat", () => {
    const { container } = block({ caveat: null, caveat_code: "waf.caveat.new_endpoint" });
    expect(container.querySelectorAll("p.subtitle")).toHaveLength(1);
  });
});
