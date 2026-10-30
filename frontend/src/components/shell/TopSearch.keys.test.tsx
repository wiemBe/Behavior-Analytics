// The root provider carries single keys for the top bar's result rows (I18N-13, `TOP_BAR_KEYS`).
// This renders a row for every code the catalog names, twice per language: once over the whole
// catalog, recording every key the rows resolve, and once over only what the root provider sends.
//
//  - the recorded keys outside the shell's namespaces must equal `TOP_BAR_KEYS`: a key the top bar
//    reads that the list lacks fails, and so does a key the list holds that nothing reads;
//  - the rows over the sent messages must read exactly as the rows over the whole catalog: a key
//    the root provider lacks would show the raw code to the analyst, and fails here.
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import en from "../../../messages/en";
import tr from "../../../messages/tr";
import { TopSearch } from "./TopSearch";
import { ipsListItem, portEntity } from "@/components/ips/fixtures";
import { incident } from "@/components/propertywatch/fixtures";
import { wafListItem } from "@/components/waf/fixtures";
import type { AnomalySummary } from "@/lib/api";
import { SHELL_NAMESPACES, TOP_BAR_KEYS, shellMessages } from "@/i18n/routeNamespaces";

type Catalog = Record<string, unknown>;

const reading = vi.hoisted(() => ({
  locale: "en" as "en" | "tr",
  messages: {} as Record<string, unknown>,
  resolved: new Set<string>(),
  // A translator keeps its identity between renders, as next-intl's does (the search's effect depends on it).
  made: new Map<string, unknown>(),
}));

vi.mock("next-intl", async (importOriginal) => {
  const actual = await importOriginal<typeof import("next-intl")>();
  return {
    ...actual,
    useLocale: () => reading.locale,
    useTranslations: (namespace?: string) => {
      const cached = reading.made.get(`${reading.locale}:${namespace ?? ""}`);
      if (cached) return cached;
      const inner = actual.createTranslator({
        locale: reading.locale,
        messages: reading.messages as never,
        namespace: namespace as never,
        timeZone: "UTC",
      });
      const full = (key: string) => (namespace ? `${namespace}.${key}` : key);
      const say = ((key: string, values?: never) => {
        reading.resolved.add(full(key));
        return inner(key as never, values);
      }) as typeof inner;
      say.has = (key) => {
        const found = inner.has(key as never);
        if (found) reading.resolved.add(full(key));
        return found;
      };
      reading.made.set(`${reading.locale}:${namespace ?? ""}`, say);
      return say;
    },
  };
});

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

/** A code taken from the catalog, which the API's types may not list. */
const sent = (over: Record<string, unknown>) => over as Partial<AnomalySummary>;

function codes(catalog: Catalog, group: string, namespace = "enums"): string[] {
  return Object.keys(((catalog[namespace] as Catalog)[group] ?? {}) as Catalog);
}

/** One row per code the catalog names; each code is found by the source name "k" plus its number. */
function everyRow(catalog: Catalog): { anomalies: AnomalySummary[]; offenses: unknown[] } {
  const anomalies: AnomalySummary[] = [];
  const named = (row: AnomalySummary): AnomalySummary => {
    row.id = `row-${anomalies.length}`;
    return row;
  };
  const states = codes(catalog, "anomalyState");
  const state = (i: number) => states[i % states.length];
  for (const [i, type] of codes(catalog, "anomalyType").entries()) {
    if (type.startsWith("IPS_")) {
      anomalies.push(named(ipsListItem(sent({ anomaly_type: type, state: state(i) }))));
    } else if (type.startsWith("WAF_")) {
      anomalies.push(named(wafListItem(sent({ anomaly_type: type, state: state(i) }))));
    } else {
      anomalies.push(
        named({ ...ipsListItem(sent({ anomaly_type: type, state: state(i) })), subject_kind: "LOG_SOURCE", ips_entity: null } as AnomalySummary),
      );
    }
  }
  for (const type of codes(catalog, "propertyWatchAnomalyType")) {
    anomalies.push(named(incident(sent({ anomaly_type: type, log_source_name: "lab-pw" }))));
  }
  for (const state of states) anomalies.push(named(ipsListItem(sent({ state }))));
  for (const dimension of codes(catalog, "dimension", "ips")) {
    anomalies.push(
      named(ipsListItem({ ips_entity: portEntity(sent({ dimension, value_key: "qid:5000123", display_name: null }) as never) })),
    );
  }
  anomalies.push(named(ipsListItem({ subject_kind: "IPS_SOURCE", ips_entity: null })));
  anomalies.push(named(ipsListItem({ ips_entity: null })));
  const offenses = codes(catalog, "offenseStatus").map((status, i) => ({
    qradar_offense_id: 4000 + i,
    description: `Scan ${status}`,
    status,
  }));
  return { anomalies, offenses };
}

/** The text of every result row, found five at a time (the search keeps five per list). */
async function panelRows(catalog: Catalog): Promise<string[]> {
  const { anomalies, offenses } = everyRow(catalog);
  const seen: string[] = [];
  for (let from = 0; from < anomalies.length; from += 5) {
    // The search reads the active anomalies once per mount, so each batch is a new mount.
    const { unmount } = render(<TopSearch />);
    const batch = anomalies.slice(from, from + 5);
    api.activeAnomalies.mockImplementation(() => page(batch));
    api.searchOffenses.mockImplementation(() => page(from === 0 ? offenses : []));
    // Every source is named "lab-…".
    fireEvent.change(screen.getByRole("combobox"), { target: { value: "lab" } });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(250);
    });
    for (const option of screen.getAllByRole("option")) seen.push(option.textContent ?? "");
    unmount();
  }
  return seen;
}

const shellNamespaces = new Set<string>(SHELL_NAMESPACES);

beforeEach(() => {
  vi.useFakeTimers();
  reading.resolved = new Set();
  api.searchSources.mockImplementation(() => page([]));
  api.searchRules.mockImplementation(() => page([]));
});
afterEach(() => vi.useRealTimers());

describe.each([
  ["en", en as Catalog],
  ["tr", tr as Catalog],
] as const)("the top bar's keys in %s", (locale, catalog) => {
  async function rowsOver(messages: Catalog): Promise<string[]> {
    reading.locale = locale;
    reading.messages = messages;
    reading.made = new Map();
    return panelRows(catalog);
  }

  it("are exactly the keys the result rows read outside the shell's namespaces", async () => {
    await rowsOver(catalog);
    const read = [...reading.resolved].filter((key) => !shellNamespaces.has(key.split(".")[0])).sort();
    expect(read).toEqual([...TOP_BAR_KEYS].sort());
  });

  it("are enough: the rows over the root provider's messages read as over the whole catalog", async () => {
    const whole = await rowsOver(catalog);
    expect(whole.length).toBeGreaterThan(30);
    const viaRoot = await rowsOver(shellMessages(catalog));
    expect(viaRoot).toEqual(whole);
  });

  it("are all names: no row of a known code shows a code", async () => {
    const whole = await rowsOver(catalog);
    for (const row of whole) {
      expect(row).not.toMatch(
        /(IPS|WAF|PW)_[A-Z_]+|VOLUME_|NO_EVENTS|PARSING_DEGRADATION|UNKNOWN_EVENT_SPIKE|TIMESTAMP_DELAY|CARDINALITY_DROP|COLLECTION_ERROR|REPEATED_PAYLOAD/,
      );
    }
  });
});
