import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  ipsCodedReasonText,
  reportForTable,
} from "@/app/admin/ips-sources/ips";
import {
  IPS_SCHEMA,
  report as ipsReport,
} from "@/app/admin/ips-sources/fixtures";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { setTestLocale } from "../../../vitest.setup";

import { SCHEMA, fieldReport, report } from "./fixtures";
import { ValidationReport } from "./ValidationReport";

function IpsReportHarness() {
  const i18n = useBehaviorI18n();
  const api = ipsReport({
    status: "INCONCLUSIVE",
    reasons: ["the log source lookup failed (ProviderError: timeout)"],
    reason_codes: [
      {
        code: "validation.profile.lookup_failed",
        params: { error: "ProviderError: timeout" },
      },
    ],
  });
  return (
    <ValidationReport
      schema={{
        domain: "IPS",
        property_name_pattern: IPS_SCHEMA.property_name_pattern,
        min_coverage_ratio: IPS_SCHEMA.min_coverage_ratio,
        fields: IPS_SCHEMA.fields.map((field) => ({
          ...field,
          default: field.default?.name ?? null,
        })),
        value_maps: IPS_SCHEMA.value_maps,
      }}
      report={reportForTable(api)}
      profileVersion={api.profile_version}
      pending={null}
      requiredCoverage={IPS_SCHEMA.min_coverage_ratio}
      hideColumns={["distinct", "plausible"]}
      formatReason={(reason, populatedRatio, sentence) =>
        ipsCodedReasonText(reason, IPS_SCHEMA, populatedRatio, sentence, i18n)
      }
    />
  );
}

describe("coded validation report sentences", () => {
  it("renders a non-field reason and a percentage detail in Turkish", () => {
    setTestLocale("tr");
    render(
      <ValidationReport
        schema={SCHEMA}
        report={report({
          status: "INCONCLUSIVE",
          reasons: ["the log source lookup failed (ProviderError: timeout)"],
          reason_codes: [
            {
              code: "validation.profile.lookup_failed",
              params: { error: "ProviderError: timeout" },
            },
          ],
          fields: [
            fieldReport({
              status: "INSUFFICIENT_COVERAGE",
              populated_ratio: 0.318,
              detail: "populated on 31.8 % of events, below the required 90 %",
              detail_code: "validation.field.insufficient_coverage",
              detail_params: {
                populated: { kind: "percent", ratio: 0.318, precision: 1 },
                required: { kind: "percent", ratio: 0.9, precision: 0 },
              },
            }),
          ],
        })}
        profileVersion={4}
        pending={null}
        requiredCoverage={0.9}
      />,
    );

    expect(screen.getByRole("list", { name: "Sonucun nedenleri" })).toHaveTextContent(
      "Log source araması başarısız oldu (ProviderError: timeout)",
    );
    expect(within(screen.getByRole("row", { name: /URL path/ })).getByText(
      "Olayların %31,8 oranında doldurulmuş; gerekli %90 oranının altında",
    )).toBeInTheDocument();
  });

  it("keeps the existing result-table wording for a known field reason", () => {
    setTestLocale("tr");
    render(
      <ValidationReport
        schema={SCHEMA}
        report={report({
          status: "INVALID",
          reasons: ["URL: PROPERTY_REJECTED (QRadar rejected the property)"],
          reason_codes: [
            {
              code: "validation.profile.field_reason",
              params: {
                field: "URL",
                status: "PROPERTY_REJECTED",
                detail: {
                  code: "validation.waf.field.property_rejected",
                  params: {},
                },
              },
            },
          ],
        })}
        profileVersion={4}
        pending={null}
        requiredCoverage={0.9}
      />,
    );

    const reasons = screen.getByRole("list", { name: "Sonucun nedenleri" });
    expect(reasons).toHaveTextContent("URL path: QRadar bu adda bir özellik tanımıyor.");
    expect(reasons).not.toHaveTextContent("Alan URL");
  });

  it.each([
    [undefined, undefined],
    ["validation.future.reason", {}],
  ])("falls back to stored English for an old or unknown reason", (code, params) => {
    setTestLocale("tr");
    render(
      <ValidationReport
        schema={SCHEMA}
        report={report({
          reasons: ["future validation sentence"],
          reason_codes: code ? [{ code, params: params ?? {} }] : undefined,
        })}
        profileVersion={4}
        pending={null}
        requiredCoverage={0.9}
      />,
    );
    expect(screen.getByText("future validation sentence")).toBeInTheDocument();
  });

  it("uses the coded fallback after IPS's existing special cases", () => {
    setTestLocale("tr");
    render(<IpsReportHarness />);
    expect(
      screen.getByText("Log source araması başarısız oldu (ProviderError: timeout)"),
    ).toBeInTheDocument();
  });
});
