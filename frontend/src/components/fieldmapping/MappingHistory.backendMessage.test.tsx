import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setTestLocale } from "../../../vitest.setup";

import { SCHEMA } from "./fixtures";
import { MappingHistory } from "./MappingHistory";

describe("coded mapping-history reasons", () => {
  it("renders a coded non-field reason in Turkish", () => {
    setTestLocale("tr");
    render(
      <MappingHistory
        schema={SCHEMA}
        entries={[
          {
            at: "2026-10-28T12:00:00Z",
            actor: "operator",
            action: "waf.profile.validated",
            profile_version: 4,
            mapping_before: null,
            mapping_after: null,
            outcome: "FAILURE",
            reasons: ["the log source does not exist in QRadar"],
            reason_codes: [
              { code: "validation.profile.source_missing", params: {} },
            ],
          },
        ]}
      />,
    );
    expect(screen.getByText("Bu log source QRadar'da mevcut değil")).toBeInTheDocument();
  });

  it("keeps stored English when an old row has no code", () => {
    setTestLocale("tr");
    render(
      <MappingHistory
        schema={SCHEMA}
        entries={[
          {
            at: "2026-10-28T12:00:00Z",
            actor: "operator",
            action: "waf.profile.validated",
            profile_version: 4,
            mapping_before: null,
            mapping_after: null,
            outcome: "FAILURE",
            reasons: ["old stored reason"],
          },
        ]}
      />,
    );
    expect(screen.getByText("old stored reason")).toBeInTheDocument();
  });

  it("renders provider text inside a composed reason verbatim", () => {
    setTestLocale("tr");
    render(
      <MappingHistory
        schema={SCHEMA}
        entries={[
          {
            at: "2026-10-28T12:00:00Z",
            actor: "operator",
            action: "waf.profile.validated",
            profile_version: 4,
            mapping_before: null,
            mapping_after: null,
            outcome: "FAILURE",
            reasons: ["URL: FUTURE_STATUS (ProviderError: timeout)"],
            reason_codes: [
              {
                code: "validation.profile.field_reason",
                params: {
                  field: "URL",
                  status: "FUTURE_STATUS",
                  detail: "ProviderError: timeout",
                },
              },
            ],
          },
        ]}
      />,
    );
    expect(
      screen.getByText("Alan URL: FUTURE_STATUS (ProviderError: timeout)"),
    ).toBeInTheDocument();
  });
});
