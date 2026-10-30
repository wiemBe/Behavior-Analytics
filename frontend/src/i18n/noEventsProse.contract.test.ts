// POL-4: the Turkish prose says the NO_EVENTS verdict in words. The token may follow in
// parentheses or code, or quote a backend title; it never stands for the word alone.

import { describe, expect, it } from "vitest";

import guide from "../../messages/tr/guide.json";
import outage from "../../messages/tr/outage.json";

const guideText = JSON.stringify(guide);

describe("NO_EVENTS in the Turkish catalogs", () => {
  it("names the anomaly in words where the guide mentions the token", () => {
    expect(guideText).toContain("Olay yok (NO_EVENTS) anomalisi");
    expect(guideText).toContain("**Olay yok** anomalisinin kaynak adından önce **NO_EVENTS on**");
  });

  it("names it in words on the outage page", () => {
    const text = JSON.stringify(outage);
    expect(text).toContain("Olay yok (<code>NO_EVENTS</code>)");
    expect(text).not.toContain("Yalnızca <code>NO_EVENTS</code>");
  });
});
