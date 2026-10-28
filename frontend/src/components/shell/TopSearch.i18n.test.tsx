// The top bar's result rows speak the interface language (I18N-13): the detector's name, the
// state and the offense status come from the same lookups as the anomaly and offense lists, and a
// code this build does not know shows as it was sent.
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TopSearch } from "./TopSearch";
import { ipsListItem, portEntity } from "@/components/ips/fixtures";
import { incident } from "@/components/propertywatch/fixtures";
import { wafListItem } from "@/components/waf/fixtures";
import type { AnomalySummary } from "@/lib/api";
import { setTestLocale } from "../../../vitest.setup";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), refresh: vi.fn() }),
  usePathname: () => "/",
}));

const api = vi.hoisted(() => ({
  searchSources: vi.fn(),
  searchRules: vi.fn(),
  searchOffenses: vi.fn(),
  activeAnomalies: vi.fn(),
}));
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  shellApi: api,
}));

const page = (items: unknown[]) => Promise.resolve({ items, total: items.length, limit: 5, offset: 0 });

/** A value the API could send that this build's types do not list (an unknown state, a new dimension). */
const sent = (over: Record<string, unknown>) => over as Partial<AnomalySummary>;

function phaseA(over: Partial<AnomalySummary> = {}): AnomalySummary {
  return {
    ...ipsListItem({ id: "a-1" }),
    subject_kind: "LOG_SOURCE",
    ips_entity: null,
    log_source_name: "core-fw-01",
    anomaly_type: "VOLUME_SPIKE",
    state: "OPEN",
    ...over,
  } as AnomalySummary;
}

async function search(value: string) {
  fireEvent.change(screen.getByRole("combobox"), { target: { value } });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250);
  });
}

function rows(group: RegExp): string[] {
  return within(screen.getByRole("group", { name: group }))
    .getAllByRole("option")
    .map((option) => option.textContent ?? "");
}

const ANOMALIES = /Active anomalies|Etkin anomaliler/;
const OFFENSES = /Offenses|Offense.lar/;

beforeEach(() => {
  vi.useFakeTimers();
  api.searchSources.mockImplementation(() => page([]));
  api.searchRules.mockImplementation(() => page([]));
  api.searchOffenses.mockImplementation(() => page([]));
  api.activeAnomalies.mockImplementation(() => page([]));
});
afterEach(() => vi.useRealTimers());

const CASES: Array<{
  name: string;
  row: () => AnomalySummary;
  query: string;
  en: string;
  tr: string;
}> = [
  {
    name: "an IPS entity row",
    row: () => ipsListItem({ id: "i1" }),
    query: "lab-ips",
    en: "Destination port 445/tcpDestination-port rate spike · lab-ips-01 · OPEN",
    tr: "Hedef port 445/tcpHedef port hızı artışı · lab-ips-01 · AÇIK",
  },
  {
    name: "an IPS source-level row",
    row: () => ipsListItem({ id: "i2", subject_kind: "IPS_SOURCE", ips_entity: null, state: "RECOVERING" }),
    query: "lab-ips",
    en: "Source-levelDestination-port rate spike · lab-ips-01 · RECOVERING",
    tr: "Kaynak düzeyiHedef port hızı artışı · lab-ips-01 · DÜZELİYOR",
  },
  {
    name: "an IPS signature row with a name",
    row: () =>
      ipsListItem({
        id: "i3",
        ips_entity: portEntity({ dimension: "SIGNATURE", value_key: "qid:5000123", display_name: "SMB probe" }),
        anomaly_type: "IPS_SIGNATURE_BURST",
        state: "CANDIDATE",
      }),
    query: "smb",
    en: "SMB probe (QID 5000123)Signature burst · lab-ips-01 · CANDIDATE",
    tr: "SMB probe (QID 5000123)İmza patlaması · lab-ips-01 · ADAY",
  },
  {
    name: "an IPS signature row with no name",
    row: () =>
      ipsListItem({
        id: "i4",
        ips_entity: portEntity({ dimension: "SIGNATURE", value_key: "qid:5000123", display_name: null }),
        anomaly_type: "IPS_SIGNATURE_BURST",
      }),
    query: "5000123",
    en: "Signature QID 5000123Signature burst · lab-ips-01 · OPEN",
    tr: "İmza QID 5000123İmza patlaması · lab-ips-01 · AÇIK",
  },
  {
    name: "an IPS row whose entity was not recorded",
    row: () => ipsListItem({ id: "i5", ips_entity: null }),
    query: "lab-ips",
    en: "Entity not recordedDestination-port rate spike · lab-ips-01 · OPEN",
    tr: "Varlık kaydedilmediHedef port hızı artışı · lab-ips-01 · AÇIK",
  },
  {
    name: "a Phase A row",
    row: () => phaseA(),
    query: "core-fw",
    en: "core-fw-01Volume spike · OPEN",
    tr: "core-fw-01Hacim artışı · AÇIK",
  },
  {
    name: "a Phase A row in an insufficient-data state",
    row: () => phaseA({ anomaly_type: "NO_EVENTS", state: "INSUFFICIENT_DATA" }),
    query: "core-fw",
    en: "core-fw-01No events · INSUFFICIENT_DATA",
    tr: "core-fw-01Olay yok · YETERSİZ VERİ",
  },
  {
    name: "a WAF row",
    row: () => wafListItem({ id: "w1", anomaly_type: "WAF_BLOCK_RATIO_SPIKE", state: "RESOLVED" }),
    query: "lab-waf",
    en: "lab-waf-behavior-01Block-ratio spike · RESOLVED",
    tr: "lab-waf-behavior-01Engelleme oranı artışı · ÇÖZÜLDÜ",
  },
  {
    name: "a property-watch row",
    row: () => incident({ id: "p1", anomaly_type: "PW_NEW_VALUE", state: "SUPPRESSED" }),
    query: "vpn",
    en: "VPN usernamesNew value · SUPPRESSED",
    tr: "VPN usernamesYeni değer · BASTIRILDI",
  },
  {
    name: "a type and a state this build does not know",
    row: () => phaseA(sent({ anomaly_type: "VOLUME_COLLAPSE", state: "ARCHIVED" })),
    query: "core-fw",
    en: "core-fw-01VOLUME_COLLAPSE · ARCHIVED",
    tr: "core-fw-01VOLUME_COLLAPSE · ARCHIVED",
  },
  {
    name: "a property-watch type this build does not know",
    row: () => incident({ id: "p2", anomaly_type: "PW_VALUE_FLIP", state: "OPEN" }),
    query: "vpn",
    en: "VPN usernamesPW_VALUE_FLIP · OPEN",
    tr: "VPN usernamesPW_VALUE_FLIP · AÇIK",
  },
  {
    name: "an IPS row with an IPS type this build does not know",
    row: () => ipsListItem({ id: "i6", anomaly_type: "IPS_NEW_THING", state: "OPEN" }),
    query: "lab-ips",
    en: "Destination port 445/tcpIPS_NEW_THING · lab-ips-01 · OPEN",
    tr: "Hedef port 445/tcpIPS_NEW_THING · lab-ips-01 · AÇIK",
  },
  {
    name: "an IPS row that carries a Phase A type (an IPS row never borrows its name)",
    row: () => ipsListItem({ id: "i7", anomaly_type: "VOLUME_SPIKE" }),
    query: "lab-ips",
    en: "Destination port 445/tcpVOLUME_SPIKE · lab-ips-01 · OPEN",
    tr: "Hedef port 445/tcpVOLUME_SPIKE · lab-ips-01 · AÇIK",
  },
  {
    name: "an IPS entity of a dimension this build does not know",
    row: () => ipsListItem({ id: "i8", ips_entity: portEntity(sent({ dimension: "PROTOCOL", value_key: "gre" }) as never) }),
    query: "lab-ips",
    en: "PROTOCOL greDestination-port rate spike · lab-ips-01 · OPEN",
    tr: "PROTOCOL greHedef port hızı artışı · lab-ips-01 · AÇIK",
  },
  {
    name: "names that are properties of every object",
    row: () => phaseA(sent({ anomaly_type: "constructor", state: "toString" })),
    query: "core-fw",
    en: "core-fw-01constructor · toString",
    tr: "core-fw-01constructor · toString",
  },
];

describe.each(["en", "tr"] as const)("the anomaly rows in %s", (locale) => {
  beforeEach(() => setTestLocale(locale));

  it.each(CASES)("show $name", async ({ row, query, en, tr }) => {
    api.activeAnomalies.mockImplementation(() => page([row()]));
    render(<TopSearch />);
    await search(query);
    expect(rows(ANOMALIES)).toEqual([locale === "tr" ? tr : en]);
  });
});

describe("a Turkish search finds a row by the words the analyst reads", () => {
  beforeEach(() => setTestLocale("tr"));

  it("matches the IPS subject as it is shown", async () => {
    api.activeAnomalies.mockImplementation(() => page([ipsListItem({ id: "i1" })]));
    render(<TopSearch />);
    await search("hedef port");
    expect(rows(ANOMALIES)).toHaveLength(1);
  });
});

describe("offense rows", () => {
  const offense = (status: string) => ({
    qradar_offense_id: 4001,
    description: "Firewall scan",
    status,
  });

  it.each([
    ["en", "OPEN", "#4001 · OPEN"],
    ["en", "HIDDEN", "#4001 · HIDDEN"],
    ["en", "CLOSED", "#4001 · CLOSED"],
    ["tr", "OPEN", "#4001 · Açık"],
    ["tr", "HIDDEN", "#4001 · Gizli"],
    ["tr", "CLOSED", "#4001 · Kapalı"],
    ["tr", "REOPENED", "#4001 · REOPENED"],
    ["en", "constructor", "#4001 · constructor"],
    ["tr", "constructor", "#4001 · constructor"],
  ] as const)("in %s the status %s reads %s", async (locale, status, expected) => {
    setTestLocale(locale);
    api.searchOffenses.mockImplementation(() => page([offense(status)]));
    render(<TopSearch />);
    await search("scan");
    expect(rows(OFFENSES)).toEqual([`Firewall scan${expected}`]);
  });
});

describe("the result rows do not cut their text off", () => {
  // jsdom has no layout: the rule is pinned in the style sheet. At 1024 px the panel is 340 px wide
  // and the state is the end of the sub-line, so no row may end in an ellipsis (I18N-13 round 1).
  const css = readFileSync(join(process.cwd(), "src/app/globals.css"), "utf8");
  const rule = (selector: string) => {
    const match = new RegExp(`^${selector.replace(".", "\\.")} \\{([^}]*)\\}`, "m").exec(css);
    expect(match, selector).not.toBeNull();
    return match![1];
  };

  it.each([".topsearch-option", ".topsearch-title", ".topsearch-sub"])("%s neither clips nor truncates", (selector) => {
    expect(rule(selector)).not.toMatch(/nowrap|text-overflow|overflow:\s*hidden|overflow-x/);
  });

  it("stacks the sub-line under the subject and lets both wrap", () => {
    expect(rule(".topsearch-option")).toMatch(/flex-direction:\s*column/);
    expect(rule(".topsearch-title")).toMatch(/overflow-wrap:\s*anywhere/);
    expect(rule(".topsearch-sub")).toMatch(/overflow-wrap:\s*anywhere/);
  });
});
