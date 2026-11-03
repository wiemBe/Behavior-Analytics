// D40-2: the less-text pass moved explanation to the guide and kept every time zone, unit,
// threshold and honesty state on the page. These tests pin both halves.
import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import GuideSectionPage from "@/app/guide/[section]/page";
import en from "../../messages/en";

type Tree = { [key: string]: string | Tree };

function get(ns: string, key: string): string {
  let node: string | Tree = (en as unknown as Tree)[ns];
  for (const part of key.split(".")) node = (node as Tree)[part];
  return node as string;
}

async function guideText(id: string): Promise<string> {
  const { container } = render(await GuideSectionPage({ params: Promise.resolve({ section: id }) }));
  return container.textContent ?? "";
}

describe("D40-2 page text keeps the facts the analyst acts on", () => {
  it.each([
    ["siemLoad", "page.description"],
    ["waf", "behavior.description"],
    ["cadence", "page.description"],
    ["mailAdmin", "page.description"],
    ["qradarAccess", "page.description"],
    ["ips", "page.description"],
    ["outage", "page.description"],
  ])("%s %s says only that times are UTC", (ns, key) => {
    expect(get(ns, key)).toBe("Times are UTC.");
  });

  it("keeps the time zone on the pages whose description still carries a fact", () => {
    expect(get("behavior", "overview.description")).toContain("Times are UTC.");
    expect(get("catchall", "page.description")).toContain("Times are UTC.");
  });

  it("SIEM load keeps the stale threshold, the dash rule and the refused meaning", () => {
    expect(get("siemLoad", "now.inFlightNote")).toContain("Stale after {seconds, number} s without an end.");
    expect(get("siemLoad", "now.windowsNote")).toBe(
      "Wall time covers completed searches only; a dash means none completed. Refused searches never reached QRadar.",
    );
    expect(get("siemLoad", "qradar.arielHeldNote")).toContain("not what it is running");
  });

  it("behavior banner keeps INSUFFICIENT_DATA and drops the third statement", () => {
    expect(get("behavior", "overview.banner.lead")).toContain("no adequate baseline");
    expect(get("behavior", "overview.banner.rest")).toContain("not being judged, so no anomaly count above covers");
    expect(get("behavior", "overview.banner.rest")).not.toContain("clean bill of health");
  });

  it("rules notice keeps the state, both causes and the not-a-gap rule", () => {
    const n = get("rules", "summaryNotice");
    expect(n).toContain("INSUFFICIENT_DATA means the rule cannot be judged");
    expect(n).toContain("a dependency was not observed");
    expect(n).toContain("no last-fired time");
    expect(n).toContain("It is not a detection gap.");
  });

  it("e-mail and QRadar access keep the read-only and secrecy facts", () => {
    expect(get("mailAdmin", "relay.status.note")).toBe("Configured means a host is set, not that the relay answers.");
    expect(get("mailAdmin", "relay.envNote")).toContain("The password is never shown.");
    expect(get("qradarAccess", "check.note")).toContain("Nothing is written to QRadar.");
  });

  it("the e-mail templates intro is gone from the catalog", () => {
    expect((en as unknown as Tree).mailAdmin).toBeTruthy();
    expect(Object.keys(((en as unknown as Tree).mailAdmin as Tree).templates as Tree)).not.toContain("intro");
  });
});

describe("D40-2 guide additions", () => {
  it("siem-load defines Failed with abandoned searches", async () => {
    expect(await guideText("siem-load")).toContain(
      "Failed counts errors, cancellations, timeouts, abandoned searches (their worker stopped before it recorded an end) and searches that found Ariel unavailable",
    );
  });

  it("waf-behavior defines Refused over quota", async () => {
    expect(await guideText("waf-behavior")).toContain(
      "Refused over quota means the key met the volume threshold but the per-bucket quota for new endpoints, or the source's active ceiling, was full.",
    );
  });
});
