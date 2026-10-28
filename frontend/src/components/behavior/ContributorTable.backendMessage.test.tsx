// The explanation dimension's reason in the reader's language: a coded backend sentence renders
// from the `backend` catalog in Turkish and as the API's own text in English. ContributorTable.test
// pins the uncoded English.

import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ExplanationDimension } from "@/lib/api";

import { setTestLocale } from "../../../vitest.setup";

import { ContributorTable } from "./ContributorTable";

const API_TEXT = "field is not populated for this log source";

function unavailable(over: Partial<ExplanationDimension> = {}): ExplanationDimension {
  return {
    dimension: "username",
    availability: "UNAVAILABLE",
    contributors: [],
    baseline_distinct_count: null,
    anomaly_distinct_count: null,
    cardinality_ratio: null,
    baseline_top_share: null,
    anomaly_top_share: null,
    new_value_count: 0,
    disappeared_value_count: 0,
    truncated: false,
    detail: API_TEXT,
    ...over,
  };
}

describe("an UNAVAILABLE dimension's reason", () => {
  it("renders the coded sentence in Turkish", () => {
    setTestLocale("tr");
    render(
      <ContributorTable
        dimension={unavailable({
          detail_code: "explanation.dimension.unpopulated",
          detail_params: {},
        })}
      />,
    );
    expect(screen.getByText(/Bu alan, bu log source'un olaylarında dolu değil/)).toBeInTheDocument();
    expect(screen.queryByText(new RegExp(API_TEXT))).toBeNull();
  });

  it("keeps the API's English text in Turkish when the row has no code (an old row)", () => {
    setTestLocale("tr");
    render(<ContributorTable dimension={unavailable({ detail_code: null, detail_params: null })} />);
    expect(screen.getByText(new RegExp(API_TEXT))).toBeInTheDocument();
  });

  it("shows the API's text unchanged in English even with a code", () => {
    setTestLocale("en");
    render(
      <ContributorTable
        dimension={unavailable({
          detail_code: "explanation.dimension.unpopulated",
          detail_params: {},
        })}
      />,
    );
    expect(screen.getByText(new RegExp(API_TEXT))).toBeInTheDocument();
    expect(screen.queryByText(/Bu log source/)).toBeNull();
  });
});
