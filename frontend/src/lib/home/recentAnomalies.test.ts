import { describe, expect, it } from "vitest";

import { anomaly, page, summary } from "./fixtures";
import { notJudgedText, recentAnomalies } from "./recentAnomalies";

describe("recentAnomalies", () => {
  it("badges the response total as active, not the number of rows", () => {
    const v = recentAnomalies(page([anomaly()], 18), summary());
    expect(v.badge).toBe("18 active");
    expect(v.shown).toBe("Showing the latest 1 of 18");
    expect(v.empty).toBeNull();
  });

  it("says nothing about a cut-off list when every active anomaly is listed", () => {
    expect(recentAnomalies(page([anomaly()], 1), summary()).shown).toBeNull();
  });

  it("keeps a null deviation null and links the row to the anomaly", () => {
    const v = recentAnomalies(page([anomaly({ id: "z9", deviation_ratio: null })]), summary());
    expect(v.rows[0].deviation).toBeNull();
    expect(v.rows[0].href).toBe("/anomalies/z9");
    expect(v.rows[0].sourceHref).toBe("/behavior/sources/s1");
  });

  it("names the endpoint of a WAF row and links its source to the WAF page", () => {
    const v = recentAnomalies(
      page([
        anomaly({
          anomaly_type: "WAF_REQUEST_RATE_SPIKE",
          subject_kind: "WAF_ENDPOINT",
          waf_endpoint: {
            id: "e1",
            normalized_endpoint: "/login",
            http_method: "POST",
          } as never,
        }),
      ]),
      summary(),
    );
    expect(v.rows[0].subject).toBe("POST /login");
    expect(v.rows[0].sourceName).toBe("src");
    expect(v.rows[0].typeLabel).toBe("Request-rate spike");
    expect(v.rows[0].sourceHref).toBe("/behavior/waf/sources/s1");
  });

  it("states how many sources are not judged when empty, never all clear", () => {
    const v = recentAnomalies(page([]), summary({ insufficient_data_sources: 4 }));
    expect(v.empty).toContain("4 sources");
    expect(v.empty).not.toMatch(/all clear/i);
    expect(v.badge).toBe("0 active");
  });

  it("does not invent the not-judged count when the summary is unreadable", () => {
    const v = recentAnomalies(page([]), null);
    expect(v.empty).toContain("could not be read");
    expect(v.empty).not.toMatch(/: \d/);
  });

  it("uses the singular for one source", () => {
    expect(notJudgedText(1)).toBe("1 source");
  });
});
