import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import type { IpsDetectionDetail } from "@/lib/api";
import {
  ipsCodedBlockDetection,
  ipsCodedDetection,
  ipsCodedNewPortDetection,
  ipsDetail,
  ipsDetection,
  ipsSourceDetail,
} from "./fixtures";
import { IpsDetectionBlock } from "./IpsDetectionBlock";

// MSG-6b: the detector's sentences (reason and caveat) come in the reader's language when the API
// sends their code, and show the API's text exactly as it was written otherwise.

const NB = " ";
const PLAIN_REASON = "940 events in the 09:00 bucket against a seasonal median of 120";

/** The reason paragraph's exact text: spaces between a number and its unit are kept as written. */
function reasonText(): string | null | undefined {
  return document.querySelector(".ips-reason")?.textContent;
}

function block(
  detection: IpsDetectionDetail,
  type: "IPS_PORT_RATE_SPIKE" | "IPS_BLOCK_RATIO_SHIFT" | "IPS_NEW_DESTINATION_PORT" = "IPS_PORT_RATE_SPIKE",
  over: Parameters<typeof ipsDetail>[0] = {},
) {
  const anomaly =
    type === "IPS_BLOCK_RATIO_SHIFT"
      ? ipsSourceDetail({ anomaly_type: type, detection, ...over })
      : ipsDetail({ anomaly_type: type, detection, ...over });
  return render(<IpsDetectionBlock anomaly={anomaly} bucketSeconds={300} />);
}

describe("the IPS detector's reason", () => {
  beforeEach(() => setTestLocale("tr"));

  it("is worded in Turkish from its code: a lower-bound spike with a count above 1,000", () => {
    block(ipsCodedDetection());
    expect(reasonText()).toBe(
      "En az 12.345 olay (alt sınır: hacim: sayım bir alt sınırdır: artış kanıtlanabilir, normal davranış kanıtlanamaz), hedef port 445/tcp için baseline medyanı olan 1.000,0 değerinin üzerinde (beklenenin 12,35x katı, +11.345 olay/zaman dilimi); robust z=38,26 (eşik +3,5)",
    );
  });

  it("is worded in Turkish from its code: a block-ratio shift with its share and points", () => {
    block(ipsCodedBlockDetection(), "IPS_BLOCK_RATIO_SHIFT");
    expect(reasonText()).toBe(
      `Eylem taşıyan 60${NB}/${NB}100 olay engellendi (%60,0); 100 geçmiş olaydan beklenen oran %10,0 idi (+50,0${NB}puan, 6,0x); robust z=16,86 (eşik +3,5)`,
    );
  });

  it("is worded in Turkish from its code: a new port with its three timestamps in UTC", () => {
    block(ipsCodedNewPortDetection(), "IPS_NEW_DESTINATION_PORT");
    expect(reasonText()).toBe(
      "İlk göründüğü 2026-10-12 08:40:00 UTC tarihinden bu yana 445/tcp hedef portu 4 zaman diliminde 3.250 olay taşıdı; doğrulanmış 2026-10-05 08:40:00 UTC – 2026-10-12 08:40:00 UTC geçmişinde her zaman diliminde 3 olayın altında kaldı",
    );
  });

  it("words the exact spike and a spike over distinct ports with their own nouns", () => {
    const exact = {
      code: "ips.spike.fired",
      params: {
        observed: 940,
        unit: { code: "ips.noun.distinct_ports", params: {} },
        subject: { code: "ips.subject.busiest_fanout", params: {} },
        expected: 120,
        ratio_text: { code: "ips.spike.ratio_none", params: {} },
        delta: 820,
        basis: { code: "ips.basis.mad_above_p95_events", params: { observed: 940, bound: 210 } },
      },
    };
    block(
      ipsDetection({ reason_code: exact.code, reason_params: exact.params }),
      "IPS_PORT_RATE_SPIKE",
    );
    expect(reasonText()).toBe(
      "940 farklı port, en yoğun kaynak IP'nin farklı portları için baseline medyanı olan 120,0 değerinin üzerinde (beklenen yok, +820 farklı port/zaman dilimi); MAD=0 olduğundan robust skor kullanılamıyor; 940, geçmiş p95 değeri olan 210 sınırının üzerinde",
    );
  });

  it("shows the API text in English whatever the code", () => {
    setTestLocale("en");
    block(ipsCodedDetection());
    expect(reasonText()).toBe(ipsCodedDetection().reason);
  });

  it("shows the API text for a row stored before codes", () => {
    block(ipsDetection({ reason_code: null, reason_params: null }));
    expect(screen.getByText(PLAIN_REASON)).toBeInTheDocument();
  });

  it("shows the API text for a code this build does not know, and for a broken parameter", () => {
    block(ipsDetection({ reason_code: "ips.spike.nope", reason_params: {} }));
    expect(screen.getByText(PLAIN_REASON)).toBeInTheDocument();
    document.body.innerHTML = "";
    block(
      ipsCodedDetection({
        reason: PLAIN_REASON,
        reason_params: { ...(ipsCodedDetection().reason_params ?? {}), basis: 7 },
      }),
    );
    expect(screen.getByText(PLAIN_REASON)).toBeInTheDocument();
  });

  it("falls back to the incident's explanation, with its own code, when the block has no reason", () => {
    render(
      <IpsDetectionBlock
        anomaly={ipsDetail({
          explanation: "the source was in maintenance during this bucket",
          explanation_code: "ips.guard.maintenance",
          explanation_params: {},
          detection: ipsDetection({ reason: null, reason_code: null, reason_params: null }),
        })}
        bucketSeconds={300}
      />,
    );
    expect(screen.getByText("Kaynak bu zaman diliminde bakımdaydı")).toBeInTheDocument();
  });
});

describe("the IPS detector's caveat", () => {
  beforeEach(() => setTestLocale("tr"));
  const CAVEAT = "More events on a port describe traffic volume, not intent.";

  it("is worded in Turkish from its code, on the spike and the block-ratio block", () => {
    block(ipsCodedDetection());
    expect(
      screen.getByText(
        "Bir porttaki daha fazla olay trafik hacmini tanımlar, niyeti değil: bir tarama, bir dağıtım ve yanlış yapılandırılmış bir istemci burada birbirine benzer.",
      ),
    ).toBeInTheDocument();
    document.body.innerHTML = "";
    block(ipsCodedBlockDetection(), "IPS_BLOCK_RATIO_SHIFT");
    expect(
      screen.getByText(
        "Engelleme, profilin eylem eşlemesi üzerinden değerlendirilir. Daha yüksek bir engelleme oranı IPS kararlarını tanımlar; bir saldırının başarılı olduğunu göstermez.",
      ),
    ).toBeInTheDocument();
  });

  it("is worded in Turkish on the new-port block too", () => {
    block(ipsCodedNewPortDetection(), "IPS_NEW_DESTINATION_PORT");
    expect(
      screen.getByText(/^Yeni yoğunlaşan bir hedef port, bir yoklama kadar kolayca/),
    ).toBeInTheDocument();
  });

  it("shows the API text in English, for a missing code and for an unknown one", () => {
    setTestLocale("en");
    block(ipsDetection({ caveat: CAVEAT, caveat_code: "ips.caveat.port_rate", caveat_params: {} }));
    expect(screen.getByText(CAVEAT)).toBeInTheDocument();
    setTestLocale("tr");
    document.body.innerHTML = "";
    block(ipsDetection({ caveat: CAVEAT }));
    expect(screen.getByText(CAVEAT)).toBeInTheDocument();
    document.body.innerHTML = "";
    block(ipsDetection({ caveat: CAVEAT, caveat_code: "ips.caveat.nope", caveat_params: {} }));
    expect(screen.getByText(CAVEAT)).toBeInTheDocument();
  });

  it("draws nothing when there is no caveat", () => {
    const { container } = block(
      ipsDetection({ caveat: null, caveat_code: "ips.caveat.port_rate", caveat_params: {} }),
    );
    expect(container.querySelectorAll("p.subtitle")).toHaveLength(1);
  });
});
