// The shared helpers the Dashboard now reaches with a translator (round 1 of I18N-3): ages, the
// health summary and legend, request failures, the history label, and the reason codes. Each
// takes the translator last and keeps English without one.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { HealthDistribution } from "@/components/behavior/HealthDistribution";
import { historyMarkAreas } from "@/components/behavior/historyOrigin";
import { createLocaleTranslator } from "@/i18n/fallback";
import { ApiError, actionErrorMessage } from "@/lib/api";
import { healthDistribution, healthSummaryText, type HealthGroup } from "@/lib/behaviorOverview";
import { describeRefusal } from "@/lib/dashboardApi";
import { formatAgo } from "@/lib/home/format";
import { insufficient, reasonInfo } from "@/lib/home/qualifiers";
import { relativeAge, relativeAgeFromSeconds } from "@/lib/time";
import { reasonText } from "@/components/home/qualifierText";
import { buildEpsOption } from "@/components/home/chartOptions";
import { epsChartModel } from "@/lib/home/epsChart";
import { epsPoint, epsSeries } from "@/lib/home/fixtures";
import type { HomePalette } from "@/lib/home/chartPalette";

const tr = createLocaleTranslator("tr");
const en = createLocaleTranslator("en");
const ageTr = (key: string, v?: { count: number }) => tr(`format.${key}`, v);

describe("relative ages", () => {
  it("say dk, sa and gün in Turkish and keep English by default", () => {
    expect(relativeAgeFromSeconds(30, ageTr)).toBe("1\u00a0dk'dan az önce");
    expect(relativeAgeFromSeconds(12 * 60, ageTr)).toBe("12\u00a0dk önce");
    expect(relativeAgeFromSeconds(3 * 3600, ageTr)).toBe("3\u00a0sa önce");
    expect(relativeAgeFromSeconds(2 * 86400, ageTr)).toBe("2\u00a0gün önce");
    expect(relativeAgeFromSeconds(12 * 60)).toBe("12 min ago");
    expect(relativeAge("2026-10-21T17:48:00Z", Date.parse("2026-10-21T18:00:00Z"), ageTr)).toBe("12\u00a0dk önce");
    expect(relativeAge(null, 0, ageTr)).toBe("—");
    expect(formatAgo("2026-10-21T17:48:00Z", Date.parse("2026-10-21T18:00:00Z"), ageTr)).toBe("12\u00a0dk önce");
    expect(formatAgo("2026-10-21T18:10:00Z", Date.parse("2026-10-21T18:00:00Z"), ageTr)).toBeNull();
  });
});

describe("the health distribution", () => {
  const groups: HealthGroup[] = healthDistribution([
    { state: "OPEN" }, { state: "INSUFFICIENT_DATA" }, { state: "NORMAL" }, { state: "NORMAL" },
  ] as never);

  it("words the summary in Turkish and in English", () => {
    expect(healthSummaryText(groups, 4, 1, tr)).toBe(
      "4 izlenen kaynak: 1 dikkat gerekiyor, 1 yetersiz veri, 2 normal. Şu anda 1 kaynak sessiz.",
    );
    expect(healthSummaryText(groups, 4, 1, en)).toBe(
      "4 monitored sources: 1 attention required, 1 insufficient data, 2 normal. 1 is currently silent.",
    );
    expect(healthSummaryText(groups, 4, 1)).toBe(
      "4 monitored sources: 1 attention required, 1 insufficient data, 2 normal. 1 is currently silent.",
    );
    expect(healthSummaryText([], 0, 0, tr)).toBe("İzlenen log source yok.");
  });

  it("words the legend from the group key, and keeps a group the catalog lacks", () => {
    const unknown: HealthGroup[] = [{ key: "paused", label: "Paused", count: 2, tone: "" }];
    const { unmount } = render(<HealthDistribution groups={[...groups, ...unknown]} total={6} summary="s" say={tr} />);
    expect(screen.getByText("Dikkat gerekiyor")).toBeInTheDocument();
    expect(screen.getByText("Yetersiz veri")).toBeInTheDocument();
    expect(screen.getByText("Paused")).toBeInTheDocument();
    unmount();
    render(<HealthDistribution groups={groups} total={4} summary="s" />);
    expect(screen.getByText("Attention required")).toBeInTheDocument();
  });

  it("words the empty notice", () => {
    render(<HealthDistribution groups={[]} total={0} summary="" say={tr} />);
    expect(screen.getByText(/İzlenen log source yok\. Hacim baseline/)).toBeInTheDocument();
  });
});

describe("request failures", () => {
  it("are fixed sentences in the language, and the server's own detail stays as sent", () => {
    expect(actionErrorMessage(new ApiError(403, ""), tr)).toBe("Bu işlemi yapma izniniz yok.");
    expect(actionErrorMessage(new ApiError(500, ""), tr)).toBe(
      "Sunucu isteği tamamlayamadı. Kısa süre sonra yeniden deneyin.",
    );
    expect(actionErrorMessage(new Error("x"), tr)).toContain("İstek gönderilemedi.");
    expect(actionErrorMessage(new ApiError(422, "Name too long"), tr)).toBe("Name too long");
    expect(actionErrorMessage(new ApiError(403, ""))).toBe("You do not have permission to perform this action.");
  });

  it("word a dashboard refusal, keeping the detail the server sent", () => {
    expect(describeRefusal(new Error("boom"), tr)).toBe(
      "İstek sunucuya ulaşmadı. Backend'e erişilebildiğini kontrol edip yeniden deneyin.",
    );
    expect(describeRefusal(new ApiError(418, "teapot", undefined), tr)).toBe("Sunucu HTTP 418 ile yanıt verdi.");
    expect(describeRefusal(new ApiError(409, "x", "you already have a dashboard named 'X'"), tr)).toBe(
      "You already have a dashboard named 'X'.",
    );
    expect(describeRefusal(new Error("boom"))).toBe(
      "The request did not reach the server. Check that the backend is reachable and try again.",
    );
  });
});

describe("history label and reason codes", () => {
  const shade = { color: "c", border: "b", text: "t", font: "f" };
  it("labels the widest span with the given words", () => {
    const [[from]] = historyMarkAreas([{ start: 0, end: 10, buckets: 1 }], shade as never, true, "geçmişten");
    expect((from as { label?: { formatter: string } }).label?.formatter).toBe("geçmişten");
    const [[english]] = historyMarkAreas([{ start: 0, end: 10, buckets: 1 }], shade as never);
    expect((english as { label?: { formatter: string } }).label?.formatter).toBe(
      "read from QRadar history, not judged",
    );
  });

  it("puts the Turkish words in the Fleet chart's history band", () => {
    const palette = new Proxy({}, { get: () => "#000" }) as unknown as HomePalette;
    const model = epsChartModel(epsSeries([epsPoint(0, 5, 5), epsPoint(1, 6, 5)]));
    const history = { steps: new Map(), spans: [{ start: 0, end: 300000, buckets: 1 }], partlyOrWholly: 1, labelled: true };
    const label = (option: unknown) =>
      JSON.stringify(option).match(/"formatter":"([^"]*history[^"]*|QRadar ge[^"]*)"/i)?.[1];
    expect(label(buildEpsOption(model, palette, false, history, { t: tr, locale: "tr" }))).toBe(
      "QRadar geçmişinden okundu, değerlendirilmedi",
    );
    expect(label(buildEpsOption(model, palette, false, history))).toBe("read from QRadar history, not judged");
  });

  it("translates a reason by its code and never by its text", () => {
    const coded = insufficient("Some English sentence.", { code: "NO_SNAPSHOT" });
    expect(reasonInfo(coded)).toEqual({ code: "NO_SNAPSHOT" });
    expect(reasonText(tr, coded)).toBe("Henüz offense kesiti toplanmadı.");
    // The same words with no code are shown as they are: the text is never matched.
    const plain = insufficient("No offense snapshot has been collected yet.");
    expect(reasonText(tr, plain)).toBe("No offense snapshot has been collected yet.");
  });
});
