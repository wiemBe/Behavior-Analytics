// Turkish typography at the places code (not a message) decides it: the capital that opens a
// sentence, the no-break space between a figure and its unit, and the Dashboard template rows that
// hold the longer Turkish lines without a scroll.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { WidgetLoading } from "@/components/home/WidgetFrame";
import { buildEpsOption } from "@/components/home/chartOptions";
import { createLocaleTranslator } from "@/i18n/testTranslator";
import { unitGap, upperFirst } from "@/i18n/measures";
import { SOC_OVERVIEW } from "@/widgets/templates";
import { widgetDef } from "@/widgets/registry";
import { epsChartModel } from "@/lib/home/epsChart";
import { epsPoint, epsSeries } from "@/lib/home/fixtures";
import type { HomePalette } from "@/lib/home/chartPalette";

import { setTestLocale } from "../../vitest.setup";

describe("upperFirst", () => {
  it("capitalises with the rule of the language: i becomes İ in Turkish", () => {
    expect(upperFirst("izinler okunamadı", "tr")).toBe("İzinler okunamadı");
    expect(upperFirst("ışık", "tr")).toBe("Işık");
    expect(upperFirst("kaynak durumları", "tr")).toBe("Kaynak durumları");
    expect(upperFirst("", "tr")).toBe("");
  });

  it("leaves English as it is", () => {
    expect(upperFirst("izinler", "en")).toBe("izinler");
  });

  it("opens the Dashboard's loading sentence with a capital, İ included", () => {
    setTestLocale("tr");
    render(<WidgetLoading what="izleme listesi" />);
    expect(screen.getByRole("status")).toHaveTextContent("İzleme listesi yükleniyor…");
  });

  it("keeps the English loading sentence byte for byte", () => {
    render(<WidgetLoading what="the open offenses" />);
    expect(screen.getByRole("status").textContent).toBe("Loading the open offenses…");
  });
});

describe("the gap between a figure and its unit", () => {
  it("is U+00A0 in Turkish and an ordinary space in English", () => {
    expect(unitGap("tr")).toBe(" ");
    expect(unitGap("en")).toBe(" ");
  });

  it("joins the EPS tooltip with it", () => {
    const palette = new Proxy({}, { get: () => "#000" }) as unknown as HomePalette;
    const model = epsChartModel(epsSeries([epsPoint(0, 5, 5), epsPoint(1, 6, 5)]));
    const tooltip = (locale: "en" | "tr") =>
      (buildEpsOption(model, palette, false, undefined, { t: createLocaleTranslator(locale), locale }) as {
        tooltip: { formatter: (p: unknown) => string };
      }).tooltip.formatter([{ dataIndex: 0 }]);
    expect(tooltip("tr")).toContain("5,00 EPS");
    expect(tooltip("en")).toContain("5.00 EPS");
  });
});

describe("the SOC Overview template rows", () => {
  it("give the KPI cards and the tables the rows their Turkish lines need", () => {
    const rows = (type: string) => SOC_OVERVIEW.layout.widgets.find((w) => w.type === type)!.h;
    expect(rows("kpi.total_eps")).toBeGreaterThanOrEqual(8);
    expect(rows("kpi.log_sources")).toBeGreaterThanOrEqual(8);
    expect(rows("table.recent_anomalies")).toBeGreaterThanOrEqual(16);
    expect(rows("health.log_sources")).toBeGreaterThanOrEqual(16);
    expect(widgetDef("kpi.total_eps")!.size.h).toBeGreaterThanOrEqual(8);
  });
});
