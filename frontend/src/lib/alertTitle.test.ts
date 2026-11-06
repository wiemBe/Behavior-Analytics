import { describe, expect, it } from "vitest";

import { alertTitleText, humanAlertTitle } from "./alertTitle";
import { createTextTranslator } from "@/i18n/textTranslator";
import enums from "../../messages/en/enums.json";
import backend from "../../messages/en/backend.json";

const say = createTextTranslator("en", { enums, backend });

const base = { title: "", title_code: null as string | null, title_params: null as Record<string, unknown> | null };

describe("humanAlertTitle (QW3)", () => {
  it("swaps the detector enum for its name and keeps the rest of the sentence", () => {
    expect(
      humanAlertTitle(
        { title: "VOLUME_DROP on Edge firewall", title_code: "alert.anomaly.title",
          title_params: { anomaly_type: "VOLUME_DROP", log_source: "Edge firewall" } },
        say,
      ),
    ).toBe("Volume drop on Edge firewall");
  });
  it("covers the WAF, IPS and property-watch titles", () => {
    expect(
      humanAlertTitle(
        { title: "WAF_REQUEST_RATE_SPIKE on w: GET /a", title_code: "alert.waf.title",
          title_params: { detector: "WAF_REQUEST_RATE_SPIKE" } },
        say,
      ),
    ).toBe("Request-rate spike on w: GET /a");
    expect(
      humanAlertTitle(
        { title: "IPS_PORT_RATE_SPIKE on s: destination port 443", title_code: "alert.ips.title.destination_port",
          title_params: { detector: "IPS_PORT_RATE_SPIKE" } },
        say,
      ),
    ).not.toMatch(/^IPS_PORT_RATE_SPIKE/);
    expect(
      humanAlertTitle(
        { title: "PW_NEW_VALUE on Admin logins: alice", title_code: "alert.pw.title",
          title_params: { detector: "PW_NEW_VALUE" } },
        say,
      ),
    ).not.toMatch(/^PW_NEW_VALUE/);
  });
  it("leaves a title it cannot map exactly as sent", () => {
    // no code, another code, a detector the catalog lacks, a sentence that does not start with it
    expect(humanAlertTitle({ ...base, title: "VOLUME_DROP on x" }, say)).toBeNull();
    expect(
      humanAlertTitle({ title: "Rule x fired", title_code: "alert.rule.fired", title_params: { name: "x" } }, say),
    ).toBeNull();
    expect(
      humanAlertTitle(
        { title: "FUTURE_TYPE on x", title_code: "alert.anomaly.title", title_params: { anomaly_type: "FUTURE_TYPE" } },
        say,
      ),
    ).toBeNull();
    expect(
      humanAlertTitle(
        { title: "Something else", title_code: "alert.anomaly.title", title_params: { anomaly_type: "VOLUME_DROP" } },
        say,
      ),
    ).toBeNull();
    expect(
      humanAlertTitle(
        { title: "VOLUME_DROP on x", title_code: "alert.anomaly.title", title_params: { anomaly_type: 7 } },
        say,
      ),
    ).toBeNull();
  });
  it("does not map an inherited key such as 'constructor' to a label", () => {
    expect(
      humanAlertTitle(
        { title: "constructor on x", title_code: "alert.anomaly.title", title_params: { anomaly_type: "constructor" } },
        say,
      ),
    ).toBeNull();
  });
});

describe("alertTitleText", () => {
  it("uses the human name in English and the stored text when unmapped", () => {
    expect(
      alertTitleText(
        { title: "NO_EVENTS on A", title_code: "alert.anomaly.title",
          title_params: { anomaly_type: "NO_EVENTS", log_source: "A" } },
        "en",
        say,
      ),
    ).toBe("No events on A");
    expect(alertTitleText({ ...base, title: "Stored text" }, "en", say)).toBe("Stored text");
  });
});
