import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { enumLabel } from "@/i18n/enumLabels";
import { ApiError } from "@/lib/api";
import { formatShare } from "@/lib/format";

const { init, use } = vi.hoisted(() => {
  const setOption = vi.fn();
  const resize = vi.fn();
  const dispose = vi.fn();
  return { init: vi.fn(() => ({ setOption, resize, dispose })), use: vi.fn() };
});

vi.mock("echarts/core", async (importOriginal) => {
  const actual = await importOriginal<typeof import("echarts/core")>();
  return { ...actual, init, use };
});
vi.mock("echarts/charts", () => ({ LineChart: {} }));
vi.mock("echarts/components", () => ({
  GridComponent: {},
  MarkAreaComponent: {},
  MarkLineComponent: {},
  TooltipComponent: {},
}));
vi.mock("echarts/renderers", () => ({ CanvasRenderer: {} }));

import { catalog, bucket, pwDetail, scope, value, watch } from "./fixtures";
import { historyHeaderText } from "./history";
import { authoringScopeKindLabel, classifyWatchError, detectorCopy } from "./authoring";
import { honestyNotes, WatchHonesty } from "./WatchHonesty";
import { PwInvestigation } from "./PwInvestigation";
import { PwStatus } from "./PwStatus";
import { ThresholdRules } from "./ThresholdRules";
import { WatchActions } from "./WatchActions";
import { WatchAqlSection } from "./WatchAql";
import { WatchDefinition } from "./WatchDefinition";
import { WatchEditor } from "./WatchEditor";
import { WatchIncidents } from "./WatchIncidents";
import { WatchWorkspace } from "./WatchWorkspace";
import {
  formatCoveragePercent,
  formatEvents,
  pinAvailability,
  pwLatencyFrom,
  pwTypeLabel,
  statusLabel,
} from "./pwFormat";
import type { PwI18n } from "./pwText";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ refresh: vi.fn(), push: vi.fn() }),
}));

const tr: PwI18n = { t: createLocaleTranslator("tr"), locale: "tr" };

beforeEach(() => setTestLocale("tr"));

describe("property-watch mappings in Turkish", () => {
  it("names the write permission in a Turkish 403 refusal", () => {
    expect(
      classifyWatchError(new ApiError(403, "Forbidden", "missing permission"), tr),
    ).toEqual({
      kind: "conflict",
      detail:
        "Bu işlem için property_watch:write gerekir. Bu izni içeren bir rolü vermesi için bir yöneticiden yardım isteyin. Hiçbir şey değiştirilmedi.",
      codes: [],
    });
  });

  it("labels every watch status and leaves an unknown status raw", () => {
    const statuses: Array<Parameters<typeof statusLabel>[0]> = [
      "OK",
      "NOT_ENABLED",
      "NOT_APPLICABLE",
      "SCOPE_STALE",
      "SCOPE_TOO_LARGE",
      "SCOPE_EMPTY",
      "READING_HISTORY",
      "STALE",
    ];
    for (const status of statuses) expect(statusLabel(status, tr)).not.toBe(status);
    expect(statusLabel("FUTURE_STATUS" as never, tr)).toBe("FUTURE_STATUS");
    expect(statusLabel("constructor" as never, tr)).toBe("constructor");
  });

  it("labels every detector and history state, preserving an unknown detector", () => {
    const detectors = [
      "PW_VALUE_SPIKE",
      "PW_VALUE_DROP",
      "PW_VALUE_DISAPPEARED",
      "PW_NEW_VALUE",
      "PW_NEW_COMBINATION",
    ];
    for (const detector of detectors) expect(pwTypeLabel(detector, tr)).not.toBe(detector);
    expect(pwTypeLabel("PW_FUTURE", tr)).toBe("PW_FUTURE");
    expect(pwTypeLabel("constructor", tr)).toBe("constructor");
    expect(enumLabel(tr.t, "severity", "constructor")).toBe("constructor");
    expect(authoringScopeKindLabel("constructor" as never, tr)).toBe("constructor");
    expect(detectorCopy("constructor" as never, tr)).toEqual({
      label: "constructor",
      claim: "constructor",
      learning: "constructor",
    });
    const base = watch().history;
    for (const state of ["NOT_REQUESTED", "READING", "COMPLETE", "PARTIAL", "UNAVAILABLE"] as const) {
      expect(historyHeaderText({ ...base, state }, tr)).not.toBe("");
    }
  });

  it("formats Turkish counts, percentages and per-bucket units", () => {
    expect(formatEvents(1204, undefined, tr)).toBe("1.204");
    expect(formatCoveragePercent(0.318, tr)).toBe("%32");
    expect(formatShare(0.318, "tr")).toBe("%31,8");
    expect(formatEvents(0, "LOWER_BOUND", tr)).toBe("en az 0");
  });

  it("keeps the entire cadence sentence and pin availability in Turkish", () => {
    const cadence = {
      family: "PROPERTY_WATCH" as const,
      cadence_seconds: 3600,
      bucket_seconds: 300,
      settle_seconds: 120,
    };
    const parts = pwLatencyFrom(cadence, tr);
    expect(`${parts.lead}${parts.tail}`).toContain("en çok 1\u00a0sa 7\u00a0dk");
    expect(`${parts.lead}${parts.tail}`).not.toContain("up to");
    expect(pinAvailability({
      cadenceSeconds: cadence.cadence_seconds,
      bucketSeconds: cadence.bucket_seconds,
      settleSeconds: cadence.settle_seconds,
    }, tr)).toContain("en çok 1\u00a0sa 7\u00a0dk");
  });
});

describe("property-watch honesty in Turkish", () => {
  it("covers NOT_COLLECTED, PARTIAL and TRUNCATED without turning gaps into zero", () => {
    const buckets = Array.from({ length: 9 }, (_, index) =>
      bucket(index, {
        values_measurement: index < 2 ? "LOWER_BOUND" : "EXACT",
        totals_complete: index !== 2,
      }),
    );
    const notes = honestyNotes({ watch: watch(), catalog: catalog(), buckets, hours: 1, i18n: tr });
    expect(notes.map((note) => note.label)).toEqual(["TOPLANMADI", "KIRPILMIŞ", "KISMİ"]);
    expect(String(notes[0].text)).toContain("3 / 12");
    expect(String(notes[1].text)).toContain("2 / 9");
    expect(String(notes[2].text)).toContain("1 / 9");
  });

  it("distinguishes a failed load, no collected buckets and collection off", () => {
    expect(honestyNotes({ watch: watch(), catalog: catalog(), buckets: null, hours: 1, i18n: tr })
      .some((note) => note.label === "YÜKLENMEDİ")).toBe(true);
    expect(honestyNotes({ watch: watch(), catalog: catalog(), buckets: [], hours: 1, i18n: tr })
      .some((note) => note.label === "TOPLANMADI")).toBe(true);
    expect(honestyNotes({ watch: watch(), catalog: catalog({ collection_enabled: false }), buckets: [bucket(0)], hours: 1, i18n: tr })
      .some((note) => note.label === "TOPLAMA KAPALI")).toBe(true);
  });

  it("renders the Turkish honesty group label", () => {
    render(<WatchHonesty notes={[{ label: "KISMİ", tone: "warn", text: "neden" }]} i18n={tr} />);
    expect(screen.getByRole("group", { name: "Bu pencere hakkında bilinmeyenler" })).toBeInTheDocument();
  });
});

describe("property-watch component states in Turkish", () => {
  it("renders status, threshold, AQL and incident empty or forbidden states", () => {
    const { rerender } = render(<PwStatus status="NOT_APPLICABLE" reason={null} />);
    expect(screen.getByText("Uygulanamaz")).toBeInTheDocument();
    rerender(<ThresholdRules rules={[]} access="reader" />);
    expect(screen.getByText(/custom_rule:write/)).toBeInTheDocument();
    rerender(<WatchAqlSection read={{ kind: "error", status: 403 }} />);
    expect(screen.getByText(/oluşturulan sorgu metnine erişim izni yok/)).toBeInTheDocument();
    rerender(<WatchIncidents incidents={[]} total={0} watchId="watch-1" />);
    expect(screen.getByText(/Son 30.*vaka tespit edilmedi/)).toBeInTheDocument();
  });

  it("renders definition labels and a grouped Turkish member count", () => {
    render(<WatchDefinition watch={watch()} catalog={catalog()} scope={scope({ member_count: 1204 })} i18n={tr} />);
    expect(screen.getByRole("heading", { name: "İzlediği öğeler" })).toBeInTheDocument();
    expect(screen.getByText((_, node) => node?.tagName === "DD" && node.textContent === "1.204 kaynak"))
      .toBeInTheDocument();
    expect(screen.getByText("%90", { exact: false })).toBeInTheDocument();
  });

  it("renders editor labels, actions and empty workspace states", () => {
    const { rerender } = render(<WatchEditor catalog={catalog()} mode="create" i18n={tr} />);
    expect(screen.getByRole("button", { name: "Devre dışı olarak kaydet ve önizle" })).toBeInTheDocument();
    expect(screen.getByText("Bu taslak için önizleme çalışmadı.")).toBeInTheDocument();
    rerender(<WatchActions watch={watch({ enabled: false })} catalog={catalog()} />);
    expect(screen.getByRole("button", { name: "Etkinleştir" })).toBeInTheDocument();
    rerender(
      <WatchWorkspace
        watch={watch()}
        catalog={catalog()}
        hours={24}
        rangeKey="24h"
        sort="events"
        offset={0}
        buckets={[]}
        values={{ items: [], total: 0, limit: 50, offset: 0 }}
        incidents={[]}
        incidentsTotal={0}
        scope={scope()}
        initialSelected={[]}
        initialSeries={[]}
        windowStart="2026-10-17T09:00:00Z"
        windowEnd="2026-10-18T09:00:00Z"
        i18n={tr}
      />,
    );
    expect(screen.getByText("Bu pencerede izleme için izlenen değer yok.")).toBeInTheDocument();
    expect(screen.getByText("TOPLANMADI.", { exact: false })).toBeInTheDocument();
  });

  it("renders the investigation with Turkish labels and a backend chart note unchanged", () => {
    render(
      <PwInvestigation
        anomaly={pwDetail()}
        data={{
          series: null,
          buckets: null,
          chartNote: "backend chart note",
          windowStart: "2026-10-18T07:00:00Z",
          windowEnd: "2026-10-18T09:00:00Z",
        }}
        i18n={tr}
      />,
    );
    expect(screen.getByRole("heading", { name: "Ölçülenler" })).toBeInTheDocument();
    expect(screen.getByText("backend chart note")).toBeInTheDocument();
    expect(screen.getByText("4,20x", { exact: false })).toBeInTheDocument();
  });

  it("formats a Turkish value table count through the workspace", () => {
    render(
      <WatchWorkspace
        watch={watch()}
        catalog={catalog()}
        hours={24}
        rangeKey="24h"
        sort="events"
        offset={0}
        buckets={[bucket(0)]}
        values={{ items: [value("v-1", "alice", { events: 1204 })], total: 1204, limit: 50, offset: 0 }}
        incidents={[]}
        incidentsTotal={0}
        scope={scope()}
        initialSelected={[]}
        initialSeries={[]}
        windowStart="2026-10-17T09:00:00Z"
        windowEnd="2026-10-18T09:00:00Z"
        i18n={tr}
      />,
    );
    expect(screen.getByText("1.204")).toBeInTheDocument();
    expect(screen.getByText(/1–50 \/ 1.204/)).toBeInTheDocument();
  });
});
