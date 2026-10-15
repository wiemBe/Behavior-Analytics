// The option reads of the configurable widgets (UI-3.5b): where a config key's candidate ids
// come from. Each is one existing GET read, mapped to `{ id, label }`; the layout stores the id.

import { endpointLabel } from "@/components/waf/wafLabels";
import {
  homeReads,
  type PagedWafEndpoints,
  type PagedWafSources,
  type SourceBehavior,
  type WafEndpointInventory,
} from "@/lib/api";

import type { OptionsSource } from "./types";

const byLabel = <T extends { label: string }>(a: T, b: T) => a.label.localeCompare(b.label);

/** An endpoint as the picker and the widget title name it: method and path, host if it has one. */
export function endpointOptionLabel(e: WafEndpointInventory): string {
  const name = e.virtual_host ? `${endpointLabel(e)} · ${e.virtual_host}` : endpointLabel(e);
  return e.is_active ? name : `${name} (retired)`;
}

/** The monitored log sources (`GET /behavior/sources`, a plain list). */
export const MONITORED_SOURCES: OptionsSource = {
  what: "monitored log sources",
  emptyReason: "No log source is monitored yet, so there is no EPS to chart.",
  read: () => {
    const read = homeReads.sourceBehaviors();
    return {
      ...read,
      options: (rows: SourceBehavior[]) => ({
        options: rows
          .map((r) => ({ id: r.log_source_id, label: r.name }))
          .sort(byLabel),
        total: rows.length,
      }),
    };
  },
};

/** The WAF sources (`GET /behavior/waf/sources`). */
export const WAF_SOURCES: OptionsSource = {
  what: "WAF sources",
  emptyReason: "No WAF source has a behavior profile yet, so no endpoint is tracked.",
  read: () => {
    const read = homeReads.wafSources();
    return {
      ...read,
      options: (page: PagedWafSources) => ({
        options: page.items
          .map((s) => ({ id: s.log_source_id, label: s.log_source_name }))
          .sort(byLabel),
        total: page.total,
      }),
    };
  },
};

/** One WAF source's endpoints (`GET /behavior/waf/sources/{id}/endpoints`, paged). */
export const WAF_SOURCE_ENDPOINTS: OptionsSource = {
  what: "endpoints",
  emptyReason: "This source has tracked no endpoint yet.",
  dependsOn: "waf_source",
  needs: "Choose a WAF source first",
  read: (source) => {
    if (source === null) return null;
    const read = homeReads.wafEndpointsOf(source);
    return {
      ...read,
      options: (page: PagedWafEndpoints) => ({
        options: page.items
          .map((e) => ({ id: e.id, label: endpointOptionLabel(e) }))
          .sort(byLabel),
        total: page.total,
      }),
    };
  },
};
