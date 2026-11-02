import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setTestLocale } from "../../../vitest.setup";
import type { CustomRuleCatalogMetric, CustomRuleSubjectKind } from "@/lib/api";
import { createLocaleTranslator } from "@/i18n/testTranslator";

import en from "../../../messages/en/customRules.json";
import tr from "../../../messages/tr/customRules.json";
import { CustomRuleEditor } from "./CustomRuleEditor";
import { catalog } from "./fixtures";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }) }));
vi.mock("./SubjectPicker", () => ({ SubjectPicker: () => <div aria-label="subject picker" /> }));
vi.mock("./PreviewChart", () => ({
  PreviewChart: () => <div aria-label="preview chart" />,
  PreviewSummary: () => <p>preview</p>,
}));

/** The eleven CustomRuleMetric values in catalog.py, in catalog order. */
const METRICS: Array<{
  metric: string;
  subject_kind: CustomRuleSubjectKind;
  unit: string;
  is_ratio: boolean;
  measurement: CustomRuleCatalogMetric["measurement"];
}> = [
  { metric: "EVENT_COUNT", subject_kind: "LOG_SOURCE", unit: "events/bucket", is_ratio: false, measurement: "EXACT_ONLY" },
  { metric: "AVERAGE_EPS", subject_kind: "LOG_SOURCE", unit: "events/s", is_ratio: false, measurement: "EXACT_ONLY" },
  { metric: "EPS", subject_kind: "FLEET_CLASS", unit: "events/s", is_ratio: false, measurement: "LOWER_BOUND_PROVES_INCREASE" },
  { metric: "REQUEST_COUNT", subject_kind: "WAF_ENDPOINT", unit: "requests/bucket", is_ratio: false, measurement: "LOWER_BOUND_PROVES_INCREASE" },
  { metric: "BLOCK_RATIO", subject_kind: "WAF_ENDPOINT", unit: "share", is_ratio: true, measurement: "LOWER_BOUND_PROVES_INCREASE" },
  { metric: "SERVER_ERROR_RATIO", subject_kind: "WAF_ENDPOINT", unit: "share", is_ratio: true, measurement: "LOWER_BOUND_PROVES_INCREASE" },
  { metric: "CLIENT_ERROR_RATIO", subject_kind: "WAF_ENDPOINT", unit: "share", is_ratio: true, measurement: "LOWER_BOUND_PROVES_INCREASE" },
  { metric: "EVENTS", subject_kind: "IPS_ENTITY", unit: "events/bucket", is_ratio: false, measurement: "LOWER_BOUND_PROVES_INCREASE" },
  { metric: "TOTAL_EVENTS", subject_kind: "IPS_SOURCE", unit: "events/bucket", is_ratio: false, measurement: "EXACT_ONLY" },
  { metric: "BLOCKED_SHARE", subject_kind: "IPS_SOURCE", unit: "share", is_ratio: true, measurement: "LOWER_BOUND_PROVES_INCREASE" },
  { metric: "PW_VALUE_EVENTS", subject_kind: "PW_VALUE", unit: "events/bucket", is_ratio: false, measurement: "LOWER_BOUND_PROVES_INCREASE" },
];

const NOTES = {
  en: en.authoring.metricNotes,
  tr: tr.authoring.metricNotes,
} as const;

/** A backend note. Rendering `metric.note` puts these tokens on the page. */
const BACKEND_NOTE = "exact only when COMPLETE; a PARTIAL bucket; SUM(eventcount), never COUNT(*); log_source_metric.event_count";

const FORBIDDEN = /COMPLETE|PARTIAL|SUM\(|COUNT\(|_count|\.[A-Za-z_][A-Za-z0-9_]*/;

type MetricRow = (typeof METRICS)[number];

function oneMetric(row: MetricRow, note = BACKEND_NOTE) {
  return catalog({
    metrics: [{ ...row, note }],
    subject_kinds: [{ subject_kind: row.subject_kind, column: "id" }],
  });
}

describe.each(["en", "tr"] as const)("metric note in %s", (locale) => {
  const label = locale === "en" ? "Metric" : "Metrik";

  beforeEach(() => setTestLocale(locale));

  it.each(METRICS)("$metric renders its catalog note and not the backend note", (row) => {
    render(<CustomRuleEditor catalog={oneMetric(row)} mode="create" />);
    const expected = NOTES[locale][row.metric as keyof (typeof NOTES)["en"]];
    const hint = screen.getByText(expected);
    expect(hint).toHaveClass("field-hint");
    expect(hint.textContent).not.toMatch(FORBIDDEN);
    expect(screen.queryByText(BACKEND_NOTE)).not.toBeInTheDocument();
    expect(screen.queryByText(/metricNotes/)).not.toBeInTheDocument();
  });

  it("switches the note when the metric changes", () => {
    render(<CustomRuleEditor catalog={catalog({
      metrics: [
        { ...METRICS[0], note: BACKEND_NOTE },
        { ...METRICS[1], note: BACKEND_NOTE },
      ],
      subject_kinds: [{ subject_kind: "LOG_SOURCE", column: "log_source_id" }],
    })} mode="create" />);
    expect(screen.getByText(NOTES[locale].EVENT_COUNT)).toBeInTheDocument();
    fireEvent.change(screen.getByRole("combobox", { name: label }), { target: { value: "AVERAGE_EPS" } });
    expect(screen.getByText(NOTES[locale].AVERAGE_EPS)).toBeInTheDocument();
    expect(screen.queryByText(NOTES[locale].EVENT_COUNT)).not.toBeInTheDocument();
    expect(screen.queryByText(BACKEND_NOTE)).not.toBeInTheDocument();
  });

  it("renders no note for a metric this frontend has no text for", () => {
    render(<CustomRuleEditor catalog={oneMetric({
      metric: "NOT_A_METRIC",
      subject_kind: "LOG_SOURCE",
      unit: "events/s",
      is_ratio: false,
      measurement: "EXACT_ONLY",
    })} mode="create" />);
    expect(screen.queryByText(BACKEND_NOTE)).not.toBeInTheDocument();
    expect(screen.queryByText(/metricNotes/)).not.toBeInTheDocument();
    expect(screen.queryByText(/COMPLETE|PARTIAL|SUM\(|COUNT\(|_count/)).not.toBeInTheDocument();
    expect(document.querySelector("p.field-hint")).toBeNull();
  });

  it("renders no note for a prototype-key metric", () => {
    render(<CustomRuleEditor catalog={oneMetric({
      metric: "constructor",
      subject_kind: "LOG_SOURCE",
      unit: "events/s",
      is_ratio: false,
      measurement: "EXACT_ONLY",
    })} mode="create" />);
    expect(screen.queryByText(BACKEND_NOTE)).not.toBeInTheDocument();
    expect(document.querySelector("p.field-hint")).toBeNull();
    expect(document.body.textContent).not.toContain("function Object");
  });
});

describe("metric note catalog", () => {
  it("words every catalog metric in both locales without backend tokens", () => {
    const names = METRICS.map((row) => row.metric);
    expect(Object.keys(NOTES.en).sort()).toEqual([...names].sort());
    expect(Object.keys(NOTES.tr).sort()).toEqual([...names].sort());
    for (const locale of ["en", "tr"] as const) {
      for (const metric of names) {
        const text = NOTES[locale][metric as keyof (typeof NOTES)["en"]];
        expect(text.length, `${locale}.${metric}`).toBeGreaterThan(40);
        expect(text, `${locale}.${metric}`).not.toMatch(FORBIDDEN);
      }
    }
  });

  it("keeps a Turkish apostrophe on an English term", () => {
    const say = createLocaleTranslator("tr");
    expect(say("customRules.authoring.metricNotes.EVENT_COUNT")).toContain("source'un");
    expect(say("customRules.authoring.metricNotes.REQUEST_COUNT")).toContain("Endpoint'in");
  });
});

describe("rendered note guard", () => {
  it("shows no forbidden token in the hint for a ratio metric", () => {
    setTestLocale("en");
    render(<CustomRuleEditor catalog={oneMetric(METRICS[4])} mode="create" />);
    const hints = [...document.querySelectorAll("p.field-hint")].map((node) => node.textContent ?? "");
    expect(hints).toContain(NOTES.en.BLOCK_RATIO);
    for (const hint of hints) expect(hint).not.toMatch(FORBIDDEN);
    expect(screen.queryByText(BACKEND_NOTE)).not.toBeInTheDocument();
  });
});
