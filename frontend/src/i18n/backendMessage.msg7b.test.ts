import { describe, expect, it } from "vitest";

import en from "../../messages/en";
import tr from "../../messages/tr";
import { renderBackendCode, renderBackendMessage } from "./backendMessage";
import { createTextTranslator } from "./textTranslator";

const TR = createTextTranslator("tr", tr);
const EN = createTextTranslator("en", en);

const title = (code: string, params: Record<string, unknown>) =>
  renderBackendCode({ code, params }, "tr", TR);

describe("MSG-7b a detector label inside a title is lower-first in Turkish", () => {
  it("lowers the label after other words, for every engine's title", () => {
    expect(title("alert.anomaly.title", { anomaly_type: "VOLUME_DROP", log_source: "Core Firewall" }))
      .toBe("Core Firewall üzerinde hacim düşüşü");
    expect(title("alert.waf.title", {
      detector: "WAF_REQUEST_RATE_SPIKE", log_source: "edge-waf", method: "POST", endpoint: "/x",
    })).toBe("edge-waf üzerinde istek hızı artışı: POST /x");
    expect(title("alert.ips.title.signature", {
      detector: "IPS_SIGNATURE_BURST", log_source: "ips-1", entity: "ET SCAN",
    })).toBe("ips-1 üzerinde imza patlaması: imza ET SCAN");
    expect(title("alert.pw.title", { detector: "PW_NEW_VALUE", watch: "W", value: "a" }))
      .toBe("W üzerinde yeni değer: a");
  });

  it("keeps a source name that begins with a capital or is an acronym", () => {
    expect(title("alert.anomaly.title", { anomaly_type: "NO_EVENTS", log_source: "WAF Edge" }))
      .toBe("WAF Edge üzerinde olay yok");
  });

  it("keeps the English label as the catalog spells it and a value the catalog lacks", () => {
    expect(renderBackendMessage({
      text: "VOLUME_DROP on x",
      code: "alert.anomaly.title",
      params: { anomaly_type: "VOLUME_DROP", log_source: "x" },
    }, "en", EN)).toBe("VOLUME_DROP on x");
    expect(title("alert.anomaly.title", { anomaly_type: "TIMESTAMP_DELAY", log_source: "x" }))
      .toBe("x üzerinde TIMESTAMP_DELAY");
  });

  it("does not touch an enum param of another group", () => {
    expect(title("alert.search.threshold.exact", {
      name: "N", observed: 5, operator: "GT", threshold: 3,
    })).toContain("(eşik > 3)");
  });
});
