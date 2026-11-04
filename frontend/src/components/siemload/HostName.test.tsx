import { readFileSync } from "node:fs";
import path from "node:path";

import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import en from "../../../messages/en";

import { HOST_RUN_MAX, HostName } from "./HostName";

describe("HostName", () => {
  it("keeps a name with a hyphen and no dot as one run", () => {
    const { container } = render(<HostName host="qr-console" />);
    expect(container.querySelector(".sl-host")).toHaveClass("sl-label");
    expect(container.querySelector(".sl-host")?.children).toHaveLength(0);
    expect(container.querySelector("wbr")).toBeNull();
    expect(container).toHaveTextContent("qr-console");
  });

  it("offers a break only after each dot", () => {
    const { container } = render(<HostName host="node-01.eventprocessors.corp.example.com" />);
    expect([...container.querySelectorAll(".sl-label")].map((el) => el.textContent)).toEqual([
      "node-01.",
      "eventprocessors.",
      "corp.",
      "example.",
      "com",
    ]);
    expect(container.querySelectorAll("wbr")).toHaveLength(4);
  });

  it("breaks a 50-character FQDN only after dots: every break opportunity is a wbr behind a dot", () => {
    const host = "dp-01.eventprocessor.datacenter-1.corp.example.com";
    expect(host).toHaveLength(50);
    const { container } = render(<HostName host={host} />);
    const root = container.querySelector(".sl-host") as HTMLElement;
    expect(root.textContent).toBe(host);
    const labels = [...root.querySelectorAll(".sl-label")];
    expect(labels.slice(0, -1).every((el) => el.textContent?.endsWith("."))).toBe(true);
    for (const wbr of root.querySelectorAll("wbr")) {
      expect(wbr.previousElementSibling?.textContent).toMatch(/\.$/);
    }
    expect(root.querySelectorAll("wbr")).toHaveLength(host.split(".").length - 1);
  });

  it("reads a short dotted name as one run of text, in order", () => {
    const { container } = render(<HostName host="qradarce2.local" />);
    expect(container.querySelector(".sl-host")?.textContent).toBe("qradarce2.local");
  });
});

describe("HostName: a short dotted name keeps its line while the table has room (FIX-26)", () => {
  it("renders a dotted name of up to HOST_RUN_MAX characters as one unbreakable run", () => {
    const { container } = render(<HostName host="qradarce2.local" />);
    const root = container.querySelector(".sl-host") as HTMLElement;
    expect(root).toHaveClass("sl-label");
    expect(root.children).toHaveLength(0);
    expect(container.querySelector("wbr")).toBeNull();
    expect(root.textContent).toBe("qradarce2.local");
  });

  it("keeps a name of exactly HOST_RUN_MAX characters whole and breaks the next length after dots", () => {
    const whole = "a".repeat(HOST_RUN_MAX - 6) + ".local";
    expect(whole).toHaveLength(HOST_RUN_MAX);
    expect(render(<HostName host={whole} />).container.querySelector("wbr")).toBeNull();
    const longer = "a" + whole;
    expect(render(<HostName host={longer} />).container.querySelectorAll("wbr")).toHaveLength(1);
  });

  it("styles a run as nowrap, so an auto layout sizes the column to the whole name", () => {
    const sheet = readFileSync(path.join(__dirname, "siemload.css"), "utf8");
    expect(/\.sl-host-name \.sl-label,[^{]*\{([^}]*)\}/.exec(sheet)?.[1]).toMatch(/white-space:\s*nowrap/);
  });
});

describe("the SIEM load table headers keep a parenthesised unit whole (FIX-26)", () => {
  const h = en.siemLoad.detail;
  const headers = [
    h.health.cpu, h.health.memory, h.health.creQueue, h.health.spill, h.health.stage,
    h.notices.severity, h.notices.lastSeen,
  ];

  it("has no ordinary space inside a parenthesis", () => {
    for (const header of headers) {
      const inside = /\(([^)]*)\)/.exec(header)?.[1];
      expect(inside, header).toBeDefined();
      expect(inside, header).not.toMatch(/ /);
    }
  });

  it("joins the words of the long units with U+00A0", () => {
    expect(h.health.spill).toBe("Throttle spill (bytes\u00a0on\u00a0disk)");
    expect(h.health.stage).toBe("Busiest stage (avg\u00a0%\u00a0busy)");
    expect(h.notices.severity).toBe("Severity (0\u00a0to\u00a010)");
  });
});
