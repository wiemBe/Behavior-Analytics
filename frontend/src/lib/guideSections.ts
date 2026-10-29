// The guide's language-neutral facts: which group a section belongs to, the routes it describes,
// who it is for, the permissions it needs and which sections it points to. The words (titles,
// text, labels) live in the `guide` catalog namespace, which only server components load.
//
// These sit here and not in the catalog because they are the same in both languages: an identical
// value in the English and Turkish catalogs is rejected unless reviewed, and ids written twice can
// drift. `guide.contract.test.ts` pins every id here against the catalog.

export type GuideGroup = "concepts" | "pages" | "analystTasks" | "adminTasks";
export type GuideAudience = "analyst" | "admin" | "both";

export interface GuideSection {
  id: string;
  group: GuideGroup;
  /** Route patterns the section describes; empty for a concept. A `[param]` route is not linked. */
  routes: readonly string[];
  audience: GuideAudience;
  /** Permission codes; the catalog's optional `permission` text, when present, replaces them. */
  permissions: readonly string[];
  related: readonly string[];
}

/** Index order: the order of the accepted text. */
export const GUIDE_GROUPS: readonly GuideGroup[] = ["concepts", "pages", "analystTasks", "adminTasks"];

export const GUIDE_SECTIONS: readonly GuideSection[] = [
  {
    id: "how-it-works",
    group: "concepts",
    routes: [],
    audience: "both",
    permissions: [],
    related: ["honesty-states", "expected-range", "times-and-cadence"],
  },
  {
    id: "honesty-states",
    group: "concepts",
    routes: [],
    audience: "both",
    permissions: [],
    related: ["how-it-works", "times-and-cadence", "roles"],
  },
  {
    id: "expected-range",
    group: "concepts",
    routes: [],
    audience: "both",
    permissions: [],
    related: ["honesty-states", "anomaly-lifecycle", "anomaly-detail"],
  },
  {
    id: "anomaly-lifecycle",
    group: "concepts",
    routes: [],
    audience: "both",
    permissions: [],
    related: ["expected-range", "alerts", "investigate-anomaly"],
  },
  {
    id: "times-and-cadence",
    group: "concepts",
    routes: [],
    audience: "both",
    permissions: [],
    related: ["how-it-works", "honesty-states", "search-cadence"],
  },
  {
    id: "roles",
    group: "concepts",
    routes: [],
    audience: "both",
    permissions: [],
    related: ["account", "users-and-directory", "honesty-states"],
  },
  {
    id: "dashboard",
    group: "pages",
    routes: ["/"],
    audience: "analyst",
    permissions: ["read:dashboards"],
    related: ["honesty-states", "times-and-cadence", "investigate-anomaly"],
  },
  {
    id: "behavior",
    group: "pages",
    routes: ["/behavior"],
    audience: "analyst",
    permissions: ["read:anomalies"],
    related: ["anomalies", "anomaly-detail", "source-behavior", "investigate-anomaly"],
  },
  {
    id: "anomalies",
    group: "pages",
    routes: ["/anomalies"],
    audience: "analyst",
    permissions: ["read:anomalies"],
    related: ["anomaly-detail", "behavior", "investigate-anomaly"],
  },
  {
    id: "anomaly-detail",
    group: "pages",
    routes: ["/anomalies/[id]"],
    audience: "analyst",
    permissions: ["read:anomalies"],
    related: ["expected-range", "honesty-states", "anomaly-lifecycle", "investigate-anomaly"],
  },
  {
    id: "source-behavior",
    group: "pages",
    routes: ["/behavior/sources", "/behavior/sources/[id]"],
    audience: "analyst",
    permissions: ["read:anomalies"],
    related: ["behavior", "expected-range", "log-sources", "find-a-quiet-source"],
  },
  {
    id: "waf-behavior",
    group: "pages",
    routes: ["/behavior/waf", "/behavior/waf/sources/[id]"],
    audience: "analyst",
    permissions: ["read:anomalies"],
    related: ["waf-source-setup", "expected-range", "anomalies"],
  },
  {
    id: "property-watches",
    group: "pages",
    routes: ["/behavior/watches", "/behavior/watches/[id]"],
    audience: "analyst",
    permissions: ["read:property_watches"],
    related: ["property-watch-setup", "honesty-states", "custom-rules"],
  },
  {
    id: "fleet",
    group: "pages",
    routes: ["/fleet"],
    audience: "analyst",
    permissions: ["read:fleet"],
    related: ["fleet-sources", "classification-rules", "find-a-quiet-source"],
  },
  {
    id: "fleet-sources",
    group: "pages",
    routes: ["/fleet/sources"],
    audience: "analyst",
    permissions: ["read:fleet"],
    related: ["classification-rules", "fleet", "log-sources"],
  },
  {
    id: "catch-all-senders",
    group: "pages",
    routes: ["/fleet/catchall"],
    audience: "analyst",
    permissions: ["read:fleet"],
    related: ["fleet-sources", "log-sources", "honesty-states"],
  },
  {
    id: "offenses",
    group: "pages",
    routes: ["/offenses", "/offenses/[id]"],
    audience: "analyst",
    permissions: ["read:offenses"],
    related: ["alerts", "how-it-works"],
  },
  {
    id: "alerts",
    group: "pages",
    routes: ["/alerts", "/alerts/[id]"],
    audience: "analyst",
    permissions: ["read:alerts"],
    related: ["work-an-alert", "anomaly-lifecycle", "offenses"],
  },
  {
    id: "rules",
    group: "pages",
    routes: ["/rules", "/rules/[id]"],
    audience: "analyst",
    permissions: ["read:rules"],
    related: ["rule-statistics", "coverage", "custom-rules"],
  },
  {
    id: "rule-statistics",
    group: "pages",
    routes: ["/rules/statistics"],
    audience: "analyst",
    permissions: ["read:rules"],
    related: ["rules", "coverage", "search-cadence"],
  },
  {
    id: "custom-rules",
    group: "pages",
    routes: ["/custom-rules", "/custom-rules/[id]"],
    audience: "analyst",
    permissions: ["read:custom_rules"],
    related: ["custom-rule-setup", "property-watches", "alerts"],
  },
  {
    id: "coverage",
    group: "pages",
    routes: ["/coverage"],
    audience: "analyst",
    permissions: ["read:coverage"],
    related: ["rules", "classification-rules"],
  },
  {
    id: "log-sources",
    group: "pages",
    routes: ["/log-sources", "/log-sources/[id]"],
    audience: "analyst",
    permissions: ["read:log_sources"],
    related: ["source-behavior", "fleet-sources", "find-a-quiet-source"],
  },
  {
    id: "searches",
    group: "pages",
    routes: ["/searches", "/searches/[id]", "/searches/new"],
    audience: "analyst",
    permissions: ["read:searches"],
    related: ["search-cadence", "rules", "how-it-works"],
  },
  {
    id: "config-changes",
    group: "pages",
    routes: ["/config-changes"],
    audience: "analyst",
    permissions: [],
    related: ["how-it-works", "honesty-states"],
  },
  {
    id: "account",
    group: "pages",
    routes: ["/account"],
    audience: "both",
    permissions: [],
    related: ["roles", "users-and-directory"],
  },
  {
    id: "investigate-anomaly",
    group: "analystTasks",
    routes: ["/anomalies/[id]"],
    audience: "analyst",
    permissions: ["read:anomalies"],
    related: ["expected-range", "anomaly-lifecycle", "source-behavior", "work-an-alert"],
  },
  {
    id: "work-an-alert",
    group: "analystTasks",
    routes: ["/alerts", "/alerts/[id]"],
    audience: "analyst",
    permissions: ["read:alerts"],
    related: ["investigate-anomaly", "anomaly-lifecycle", "alerts"],
  },
  {
    id: "find-a-quiet-source",
    group: "analystTasks",
    routes: ["/behavior", "/anomalies", "/behavior/sources/[id]", "/fleet", "/log-sources", "/log-sources/[id]"],
    audience: "analyst",
    permissions: ["read:anomalies", "read:fleet", "read:log_sources"],
    related: ["fleet", "source-behavior", "investigate-anomaly", "work-an-alert", "outage-policy"],
  },
  {
    id: "waf-source-setup",
    group: "adminTasks",
    routes: ["/admin/waf-sources", "/admin/waf-sources/[logSourceId]"],
    audience: "admin",
    permissions: ["admin:waf"],
    related: ["waf-behavior", "honesty-states", "ips-source-setup", "search-cadence", "history-backfill"],
  },
  {
    id: "ips-source-setup",
    group: "adminTasks",
    routes: ["/admin/ips-sources", "/admin/ips-sources/[logSourceId]"],
    audience: "admin",
    permissions: ["admin:ips"],
    related: ["waf-source-setup", "honesty-states", "history-backfill"],
  },
  {
    id: "property-watch-setup",
    group: "adminTasks",
    routes: ["/behavior/watches/new"],
    audience: "admin",
    permissions: ["property_watch:write"],
    related: ["property-watches", "custom-rule-setup", "history-backfill", "search-cadence"],
  },
  {
    id: "custom-rule-setup",
    group: "adminTasks",
    routes: ["/custom-rules/new"],
    audience: "admin",
    permissions: ["custom_rule:write"],
    related: ["custom-rules", "property-watch-setup", "work-an-alert"],
  },
  {
    id: "outage-policy",
    group: "adminTasks",
    routes: ["/admin/outage-policy"],
    audience: "admin",
    permissions: ["admin:outage"],
    related: ["fleet", "find-a-quiet-source", "work-an-alert"],
  },
  {
    id: "classification-rules",
    group: "adminTasks",
    routes: ["/admin/classification-rules"],
    audience: "admin",
    permissions: ["read:fleet", "admin:classification"],
    related: ["fleet-sources", "outage-policy"],
  },
  {
    id: "search-cadence",
    group: "adminTasks",
    routes: ["/admin/search-cadence"],
    audience: "admin",
    permissions: ["read:search_cadence", "admin:search_cadence"],
    related: ["searches", "waf-source-setup", "history-backfill"],
  },
  {
    id: "history-backfill",
    group: "adminTasks",
    routes: ["/admin/history-backfill"],
    audience: "admin",
    permissions: ["read:history_backfill", "admin:history_backfill"],
    related: ["how-it-works", "search-cadence", "property-watch-setup", "waf-source-setup"],
  },
  {
    id: "users-and-directory",
    group: "adminTasks",
    routes: ["/admin/users", "/admin/directory"],
    audience: "admin",
    permissions: ["admin:users", "admin:auth"],
    related: ["roles", "account"],
  },
];

const BY_ID = new Map(GUIDE_SECTIONS.map((section) => [section.id, section]));

/** The section for an id, or undefined for anything else (an inherited name included). */
export function guideSection(id: string): GuideSection | undefined {
  return BY_ID.get(id);
}
