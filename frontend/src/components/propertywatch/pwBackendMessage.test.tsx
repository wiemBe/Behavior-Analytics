import { render, screen, waitFor, within, fireEvent } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { ApiError, api } from "@/lib/api";
import { utcTimeText } from "@/components/ui/UtcTime";
import { codedStatusReason, pwSentence, scopeReason } from "./pwReason";

vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init: vi.fn(() => ({ setOption: vi.fn(), resize: vi.fn(), dispose: vi.fn() })), use: vi.fn() };
});
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
}));

import { aql, catalog, pwDetail, scope, spikeDetection, watch } from "./fixtures";
import { classifyWatchError } from "./authoring";
import { honestyNotes } from "./WatchHonesty";
import { historyStateText } from "./history";
import { PwInvestigation } from "./PwInvestigation";
import { PwStatus } from "./PwStatus";
import { WatchAqlSection } from "./WatchAql";
import { WatchDefinition } from "./WatchDefinition";
import { WatchEditor } from "./WatchEditor";
import type { PwI18n } from "./pwText";
import { earlyState } from "@/widgets/pwWidgetState";

const tr: PwI18n = { t: createLocaleTranslator("tr"), locale: "tr" };
const en: PwI18n = { t: createLocaleTranslator("en"), locale: "en" };

const STALE_TEXT =
  "the group inventory was last synced at 2026-10-17T06:00:00+00:00, older than 1 day, 0:00:00";
const STALE = {
  code: "pw.scope.group_stale",
  params: {
    synced: "2026-10-17T06:00:00+00:00",
    limit: { kind: "duration", seconds: 86400 },
  },
};
const STALE_TR = `Grup envanteri en son ${utcTimeText(STALE.params.synced)} tarihinde eşitlendi; bu, 1\u00a0gün sınırından daha eski`;

beforeEach(() => setTestLocale("tr"));

describe("pwSentence", () => {
  it("renders the catalog entry of the code in Turkish", () => {
    expect(pwSentence(STALE_TEXT, STALE, tr)).toBe(STALE_TR);
  });

  it("shows the API text verbatim in English, whatever the code", () => {
    expect(pwSentence(STALE_TEXT, STALE, en)).toBe(STALE_TEXT);
  });

  it("falls back to the API text for a missing, null, unknown or inherited code", () => {
    expect(pwSentence(STALE_TEXT, undefined, tr)).toBe(STALE_TEXT);
    expect(pwSentence(STALE_TEXT, { code: null, params: null }, tr)).toBe(STALE_TEXT);
    expect(pwSentence(STALE_TEXT, { code: "pw.scope.nope", params: {} }, tr)).toBe(STALE_TEXT);
    expect(pwSentence(STALE_TEXT, { code: "constructor", params: {} }, tr)).toBe(STALE_TEXT);
    expect(pwSentence(STALE_TEXT, { code: "pw.scope", params: {} }, tr)).toBe(STALE_TEXT);
  });

  it("falls back when a parameter is missing or of the wrong kind", () => {
    expect(
      pwSentence(STALE_TEXT, { code: STALE.code, params: { synced: STALE.params.synced } }, tr),
    ).toBe(STALE_TEXT);
    expect(
      pwSentence(STALE_TEXT, { code: STALE.code, params: { ...STALE.params, limit: 86400 } }, tr),
    ).toBe(STALE_TEXT);
  });
});

describe("the status of a watch", () => {
  it("is worded in Turkish from its code and keeps its status pill", () => {
    const { container } = render(
      <PwStatus
        status="SCOPE_STALE"
        reason={STALE_TEXT}
        reasonCode={STALE.code}
        reasonParams={STALE.params}
      />,
    );
    expect(screen.getByText("Scope güncel değil")).toBeInTheDocument();
    expect(container.querySelector(".pw-status-reason")?.textContent).toBe(STALE_TR);
    expect(container.textContent).not.toContain(STALE_TEXT);
  });

  it("shows a status stored before codes as the API wrote it", () => {
    render(<PwStatus status="SCOPE_STALE" reason={STALE_TEXT} />);
    expect(screen.getByText(STALE_TEXT)).toBeInTheDocument();
  });

  it("never turns a missing reason into a sentence of the code: the status meaning stays", () => {
    expect(codedStatusReason("SCOPE_STALE", null, tr, STALE)).toBe(
      "Scope'un eşitlenmiş envanteri güncellik sınırından eski olduğundan zaman dilimi toplanmaz ve açık vakalar bekletilir.",
    );
  });

  it("words the actor, the reading progress and the failed search", () => {
    expect(
      codedStatusReason("DISABLED", "disabled by efe", tr, {
        code: "pw.status.disabled_by",
        params: { actor: "efe" },
      }),
    ).toBe("Kapatan: efe");
    expect(
      codedStatusReason("OK", "x", tr, {
        code: "pw.status.reading_history",
        params: { done: 1.04, total: 7 },
      }),
    ).toBe(
      "QRadar geçmişi okunuyor: 1,0 / 7,0 gün; okuma bitene kadar hiçbir şey değerlendirilmez",
    );
    expect(
      codedStatusReason("STALE", "x", tr, {
        code: "pw.status.latest_search_failed",
        params: { bucket: "2026-10-17T06:05:00+00:00" },
      }),
    ).toBe(
      `Son arama başarısız oldu; ${utcTimeText("2026-10-17T06:05:00+00:00")} zaman dilimi toplanmadı olarak kaydedildi`,
    );
  });
});

describe("the scope and the history of a watch", () => {
  it("words the scope banner of the definition tab", () => {
    const { container } = render(
      <WatchDefinition
        watch={watch({ status: "SCOPE_STALE" })}
        catalog={catalog()}
        scope={scope({
          resolved: false,
          state: "SCOPE_STALE",
          reason: STALE_TEXT,
          reason_code: STALE.code,
          reason_params: STALE.params,
        })}
        i18n={tr}
      />,
    );
    expect(container.textContent).toContain(STALE_TR);
  });

  it("words a picker option that does not resolve", () => {
    const option = {
      scope_reason: "5 sources need 3 searches; the limit is 2",
      scope_reason_code: "pw.scope.too_large",
      scope_reason_params: { sources: 1204, searches: 3, limit: 2 },
    };
    expect(scopeReason(option, tr)).toBe("1.204 log source 3 arama gerektiriyor; sınır 2");
    expect(scopeReason(option, en)).toBe("5 sources need 3 searches; the limit is 2");
    expect(scopeReason({ scope_reason: null }, tr)).toBeNull();
  });

  it("words a history gap and keeps one stored before codes as it was", () => {
    const history = {
      ...watch().history,
      state: "PARTIAL" as const,
      gaps: [
        {
          start: "2026-10-10T00:00:00Z",
          end: "2026-10-10T01:00:00Z",
          reason: "search failed 3 times at the minimum width",
          reason_code: "history.gap.search_failed",
          reason_params: { failures: 3 },
        },
        {
          start: "2026-10-11T00:00:00Z",
          end: "2026-10-11T01:00:00Z",
          reason: "could not be read after 3 attempts",
        },
      ],
    };
    const notes = honestyNotes({
      watch: watch({ history }),
      catalog: catalog(),
      buckets: [],
      hours: 1,
      i18n: tr,
    });
    const gaps = notes.filter((note) => note.label === "GEÇMİŞ BOŞLUĞU");
    expect(gaps).toHaveLength(2);
    render(<div>{gaps.map((gap, i) => <p key={i}>{gap.text}</p>)}</div>);
    expect(screen.getByText("Arama en küçük genişlikte 3 kez başarısız oldu", { exact: false }))
      .toBeInTheDocument();
    expect(screen.getByText("could not be read after 3 attempts", { exact: false }))
      .toBeInTheDocument();
  });

  it("words the reason of a partial history", () => {
    const text = historyStateText(
      {
        ...watch().history,
        state: "PARTIAL",
        reason: "the stored history progress could not be read back",
        reason_code: "pw.history.unreadable",
        reason_params: {},
      },
      tr,
    );
    expect(text).toContain("Saklanan geçmiş ilerlemesi geri okunamadı");
  });

  it("words the AQL tab's reason when the scope does not resolve", () => {
    render(
      <WatchAqlSection
        read={{
          kind: "ok",
          aql: aql({
            current: [],
            current_unavailable: "SCOPE_STALE: x",
            current_unavailable_code: "pw.preview.scope_unresolved",
            current_unavailable_params: {
              status: "SCOPE_STALE",
              reason: { code: "pw.scope.group_empty", params: {} },
            },
          }),
        }}
      />,
    );
    expect(
      screen.getByText("Scope güncel değil — grup ve alt grupları eşitlenmiş hiçbir log source içermiyor"),
    ).toBeInTheDocument();
  });
});

describe("the dashboard widget", () => {
  it("words a not-collected reason from the status code", () => {
    const state = earlyState(
      watch({
        status: "SCOPE_STALE",
        status_reason: STALE_TEXT,
        status_reason_code: STALE.code,
        status_reason_params: STALE.params,
      }),
      catalog(),
      createLocaleTranslator("tr"),
      "tr",
    );
    expect(state?.kind).toBe("not_collected");
    expect(JSON.stringify(state)).toContain(
      `Grup envanteri en son ${utcTimeText(STALE.params.synced)}`,
    );
  });
});

describe("a refused draft", () => {
  const body = {
    message: "x",
    reasons: [
      "pinned value 2, part 1: a pinned value cannot be empty",
      "a watch evaluates at least one detector",
      "an old reason with no code",
    ],
    reason_codes: [
      {
        code: "pw.draft.pin_invalid",
        params: {
          index: 2,
          part: 1,
          reason: { code: "pw.reference.pin_empty", params: {} },
        },
      },
      { code: "pw.draft.detector_required", params: {} },
      null,
    ],
  };

  it("words each reason from its own code in Turkish", () => {
    const refusal = classifyWatchError(new ApiError(422, "x", body, { detail: body }), tr);
    expect(refusal).toEqual({
      kind: "reasons",
      reasons: [
        "Sabitlenmiş değer 2, bölüm 1: değer boş olamaz",
        "Bir izleme en az bir algılayıcı değerlendirir",
        "an old reason with no code",
      ],
    });
  });

  it("keeps the server's English in English", () => {
    const refusal = classifyWatchError(new ApiError(422, "x", body, { detail: body }), en);
    expect(refusal).toEqual({ kind: "reasons", reasons: body.reasons });
  });

  it("is read without codes from an older server", () => {
    const { reason_codes: _unused, ...older } = body;
    void _unused;
    const refusal = classifyWatchError(new ApiError(422, "x", older, { detail: older }), tr);
    expect(refusal).toEqual({ kind: "reasons", reasons: body.reasons });
  });

  it("words the history 409 with its state and its list", () => {
    const detail =
      "history is READING on this watch and cannot be requested again; create a new watch to read history";
    const refusal = classifyWatchError(
      new ApiError(409, detail, detail, {
        detail,
        detail_code: "pw.history_change.already_requested",
        detail_params: { state: "READING" },
      }),
      tr,
    );
    expect(refusal.kind).toBe("conflict");
    expect(refusal).toMatchObject({
      detail:
        "Bu izlemede geçmiş durumu OKUNUYOR; yeniden istenemez. Geçmişi okumak için yeni bir izleme oluşturun",
    });
    const marks = "this watch has already collected (collected_through and evaluated_through set); create a new watch to read history";
    const collected = classifyWatchError(
      new ApiError(409, marks, marks, {
        detail: marks,
        detail_code: "pw.history_change.already_collected",
        detail_params: { marks: { kind: "list", items: ["collected_through", "evaluated_through"] } },
      }),
      tr,
    );
    expect(collected).toMatchObject({
      detail:
        "Bu izleme zaten topladı (collected_through ve evaluated_through ayarlı). Geçmişi okumak için yeni bir izleme oluşturun",
    });
  });

  it("shows the editor's server reasons in Turkish", async () => {
    vi.spyOn(api, "propertyWatchPropertyOptions").mockResolvedValue({
      status: "AVAILABLE",
      fetched_at: "2026-10-18T08:00:00Z",
      scope_type_ids: [1],
      properties: [],
      normalized_fields: ["username"],
      detail: null,
    } as never);
    vi.spyOn(api, "updatePropertyWatch").mockRejectedValue(
      new ApiError(422, "x", body, { detail: body }),
    );
    render(
      <WatchEditor
        catalog={catalog()}
        mode="edit"
        watch={watch()}
        initialScope={scope()}
        i18n={tr}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Devre dışı olarak kaydet ve önizle" }));
    const list = await screen.findByRole("alert", { name: "Sunucu doğrulama nedenleri" });
    await waitFor(() =>
      expect(
        within(list).getByText("Sabitlenmiş değer 2, bölüm 1: değer boş olamaz"),
      ).toBeInTheDocument(),
    );
    expect(within(list).getByText("an old reason with no code")).toBeInTheDocument();
  });
});

describe("an incident", () => {
  const spike = {
    code: "pw.detector.spike_fired",
    params: {
      observed: 520,
      median: 100,
      delta: 420,
      ratio: 5.2,
      basis: { code: "pw.detector.basis_z_high", params: { score: 28.33, threshold: 3.5 } },
    },
  };
  const spikeText =
    "520 events is above the median 100.0 (+420, 5.20x); robust z=28.33 (threshold +3.5)";
  const spikeTr =
    "520 olay, 100,0 medyanın üzerinde (+420, 5,20x); sağlam z=28,33 (eşik +3,5)";

  function data() {
    return {
      series: null,
      buckets: null,
      chartNote: "note",
      windowStart: "2026-10-18T07:00:00Z",
      windowEnd: "2026-10-18T09:00:00Z",
    };
  }

  it("words the detector sentence, the claim and the transition reasons", () => {
    const anomaly = pwDetail({
      explanation: spikeText,
      detection: {
        ...spikeDetection(),
        reason: spikeText,
        reason_code: spike.code,
        reason_params: spike.params,
        claim: "the value stayed below 3 events in every bucket of the verified history",
        claim_code: "pw.detector.claim_new_value",
        claim_params: { min_events: 3 },
      },
      transitions: [
        {
          from_state: "OPEN",
          to_state: "RESOLVED",
          occurred_at: "2026-10-18T08:00:00Z",
          bucket_start: "2026-10-18T08:00:00Z",
          reason: "watch definition changed",
          reason_code: "pw.transition.definition_changed",
          reason_params: {},
          actor: "pw-engine",
          observed_value: null,
          expected_value: null,
        },
        {
          from_state: "CANDIDATE",
          to_state: "OPEN",
          occurred_at: "2026-10-18T07:00:00Z",
          bucket_start: "2026-10-18T07:00:00Z",
          reason: "3 consecutive buckets",
          actor: "pw-engine",
          observed_value: null,
          expected_value: null,
        },
      ],
    });
    render(<PwInvestigation anomaly={anomaly} data={data()} i18n={tr} />);
    expect(screen.getByText(spikeTr)).toBeInTheDocument();
    expect(screen.queryByText(spikeText)).toBeNull();
    expect(
      screen.getByText(
        "Değer, doğrulanmış geçmişin her zaman diliminde 3 olayın altında kaldı.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("İzleme tanımı değişti")).toBeInTheDocument();
    // a lifecycle reason with no code stays as the API wrote it
    expect(screen.getByText("3 consecutive buckets")).toBeInTheDocument();
  });

  it("keeps the API's English in English", () => {
    const anomaly = pwDetail({
      detection: {
        ...spikeDetection(),
        reason: spikeText,
        reason_code: spike.code,
        reason_params: spike.params,
      },
    });
    render(<PwInvestigation anomaly={anomaly} data={data()} i18n={en} />);
    expect(screen.getByText(spikeText)).toBeInTheDocument();
  });

  it("falls back to the anomaly's explanation, coded, when the detection block has none", () => {
    const anomaly = pwDetail({
      explanation: spikeText,
      explanation_code: spike.code,
      explanation_params: spike.params,
      detection: null,
    });
    render(<PwInvestigation anomaly={anomaly} data={data()} i18n={tr} />);
    expect(screen.getByText(spikeTr)).toBeInTheDocument();
  });
});

describe("Turkish numbers are grouped, whatever the style", () => {
  const say = (code: string, params: Record<string, unknown>) =>
    pwSentence("x", { code, params }, tr);

  it("groups a fixed number and a count", () => {
    expect(
      say("pw.detector.spike_min_events", { observed: 1204, minimum: 20 }),
    ).toBe("1.204 olay, 20 asgari değerin altında");
    expect(
      say("pw.detector.drop_median_floor", { median: 1260.0, floor: 20.0 }),
    ).toBe("Baseline medyanı 1.260,0, 20,0 trafik tabanının altında");
  });

  it("groups a delta and keeps its sign text", () => {
    expect(
      say("pw.detector.spike_fired", {
        observed: 13204,
        median: 1260,
        delta: 12000,
        ratio: 10.48,
        basis: { code: "pw.detector.basis_z_high", params: { score: 6.1, threshold: 3.5 } },
      }),
    ).toBe(
      "13.204 olay, 1.260,0 medyanın üzerinde (+12.000, 10,48x); sağlam z=6,10 (eşik +3,5)",
    );
  });
});
