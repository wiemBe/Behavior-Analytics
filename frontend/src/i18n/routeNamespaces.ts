// Which catalog namespaces each part of the application sends to the browser (I18N-12b).
//
// The root layout sends the SHELL (the top bar, navigation, account menu, connection status, the
// guide links and what every page's client code shares) plus two lists of single keys: BOUNDARY_KEYS,
// the strings that a route's `error.tsx` / `loading.tsx` read, because those render outside the page,
// TOP_BAR_KEYS, the code names the top bar's search results read (I18N-13), and SHELL_BACKEND_KEYS,
// the refusal sentences the forced password change words (MSG-1). Each page then
// wraps its content in a provider holding the shell plus ROUTE_NAMESPACES for its route
// (`withRouteMessages`, `./RouteMessages.tsx`). The nested provider replaces the root one, so a
// route's set is the shell plus its own. Server components read the whole catalog through
// `getTranslations` and are not listed.
//
// `routeNamespaces.contract.test.ts` walks each route's import graph and fails when a table here
// differs from what the route's browser code reads; its failure message prints the new table.
// A key assembled at run time lists its namespace in `export const I18N_NAMESPACES = [...]` in the
// module that builds it.

export const BOUNDARY_KEYS = [
  "alerts.boundary.detailTitle",
  "alerts.boundary.detailWhatError",
  "alerts.boundary.listWhatError",
  "alerts.title",
  "anomalies.boundary.errorWhat",
  "anomalies.boundary.loadingWhat",
  "anomalies.boundary.title",
  "catchall.page.errorWhat",
  "catchall.page.title",
  "classification.boundary.errorWhat",
  "classification.boundary.loadingWhat",
  "classification.boundary.title",
  "coverage.errorWhat",
  "coverage.title",
  "customRules.boundary.detailTitle",
  "customRules.boundary.detailWhatError",
  "customRules.boundary.listWhatError",
  "customRules.boundary.newTitle",
  "customRules.boundary.newWhatError",
  "customRules.title",
  "dashboardBoundary.error",
  "dashboardBoundary.loading",
  "dashboardBoundary.title",
  "directory.boundary.description",
  "directory.boundary.errorWhat",
  "directory.boundary.loadingWhat",
  "directory.boundary.title",
  "fleet.page.errorWhat",
  "fleet.page.title",
  "ips.detail.errorWhat",
  "ips.detail.title",
  "ips.page.boundaryWhat",
  "ips.page.title",
  "logSources.detail.errorWhat",
  "logSources.detail.title",
  "logSources.errorWhat",
  "logSources.title",
  "login.loading",
  "login.title",
  "offenses.boundary.back",
  "offenses.boundary.detailTitle",
  "offenses.boundary.detailWhatError",
  "offenses.boundary.listWhatError",
  "offenses.title",
  "outage.boundary.errorWhat",
  "outage.boundary.loadingWhat",
  "outage.boundary.title",
  "propertyWatch.boundary.detailWhat",
  "propertyWatch.boundary.detailWhatError",
  "propertyWatch.boundary.listWhat",
  "propertyWatch.boundary.listWhatError",
  "propertyWatch.boundary.newWhat",
  "propertyWatch.boundary.newWhatError",
  "propertyWatch.meta.detail",
  "propertyWatch.meta.list",
  "propertyWatch.meta.new",
  "rules.boundary.detailBack",
  "rules.boundary.detailTitle",
  "rules.boundary.detailWhatError",
  "rules.boundary.listWhatError",
  "rules.boundary.statisticsWhatError",
  "rules.statistics.title",
  "rules.title",
  "searches.detail.errorWhat",
  "searches.detail.title",
  "searches.list.errorWhat",
  "searches.list.title",
  "searches.new.errorWhat",
  "searches.new.title",
  "users.boundary.errorWhat",
  "users.boundary.loadingWhat",
  "users.boundary.title",
  "users.page.description",
  "waf.boundary.adminError",
  "waf.boundary.adminLoading",
  "waf.boundary.adminSourceError",
  "waf.boundary.adminSourceLoading",
  "waf.boundary.adminSourceTitle",
  "waf.boundary.adminTitle",
  "waf.boundary.behaviorError",
  "waf.boundary.behaviorLoading",
  "waf.boundary.behaviorTitle",
  "waf.boundary.endpointsError",
  "waf.boundary.endpointsLoading",
  "waf.boundary.endpointsTitle",
] as const;

/**
 * The single keys the top bar's search results read (`TopSearch`): the names of an anomaly's
 * detector and state, of an offense's status, and of an IPS row's subject. The top bar is in the root
 * provider, so these go into every page's HTML; a whole `enums`, `ips`, `waf` or `propertyWatch`
 * namespace would be hundreds of kilobytes. The helpers that read them are listed in
 * `I18N_KEYED_CALLS` (checked by the scan), and `TopSearch.keys.test.tsx` renders a row for every
 * code and fails when the keys read differ from this list.
 */
export const TOP_BAR_KEYS = [
  "enums.anomalyState.CANDIDATE",
  "enums.anomalyState.INSUFFICIENT_DATA",
  "enums.anomalyState.NORMAL",
  "enums.anomalyState.OPEN",
  "enums.anomalyState.RECOVERING",
  "enums.anomalyState.RESOLVED",
  "enums.anomalyState.SUPPRESSED",
  "enums.anomalyType.IPS_BLOCK_RATIO_SHIFT",
  "enums.anomalyType.IPS_DESTINATION_RATE_SPIKE",
  "enums.anomalyType.IPS_NEW_DESTINATION_PORT",
  "enums.anomalyType.IPS_PORT_FANOUT_SPIKE",
  "enums.anomalyType.IPS_PORT_RATE_SPIKE",
  "enums.anomalyType.IPS_SIGNATURE_BURST",
  "enums.anomalyType.IPS_SOURCE_CONCENTRATION_SPIKE",
  "enums.anomalyType.NO_EVENTS",
  "enums.anomalyType.VOLUME_DROP",
  "enums.anomalyType.VOLUME_SPIKE",
  "enums.anomalyType.WAF_BLOCK_RATIO_SPIKE",
  "enums.anomalyType.WAF_CLIENT_CONCENTRATION_SPIKE",
  "enums.anomalyType.WAF_CLIENT_ENDPOINT_FANOUT_SPIKE",
  "enums.anomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE",
  "enums.anomalyType.WAF_ERROR_RATIO_SPIKE",
  "enums.anomalyType.WAF_NEW_ENDPOINT_ACCESS",
  "enums.anomalyType.WAF_REQUEST_RATE_SPIKE",
  "enums.offenseStatus.CLOSED",
  "enums.offenseStatus.HIDDEN",
  "enums.offenseStatus.OPEN",
  "enums.propertyWatchAnomalyType.PW_NEW_COMBINATION",
  "enums.propertyWatchAnomalyType.PW_NEW_VALUE",
  "enums.propertyWatchAnomalyType.PW_VALUE_DISAPPEARED",
  "enums.propertyWatchAnomalyType.PW_VALUE_DROP",
  "enums.propertyWatchAnomalyType.PW_VALUE_SPIKE",
  "ips.dimension.DESTINATION_IP",
  "ips.dimension.DESTINATION_PORT",
  "ips.entity.signatureId",
  "ips.subject.entityNotRecorded",
  "ips.subject.sourceLevel",
] as const;

/**
 * The single `backend.*` keys the shell renders itself (MSG-1 round 1): the forced password change
 * in `AppFrame` words the password policy, the session and cross-site refusals and the permission
 * 403 through `refusalMessage.ts`. The root provider carries these keys one by one and never the
 * `backend` namespace; a route that shows more of it lists `backend` in `ROUTE_NAMESPACES`. The
 * history backfill's first-run dialog is mounted in the shell too, and carries its estimate sentences
 * the same way.
 * `routeNamespaces.contract.test.tsx` fails when shell-reachable code names a `backend` key that is
 * not listed here.
 */
export const SHELL_BACKEND_KEYS = [
  "backend.auth.csrf.origin_mismatch",
  "backend.auth.csrf.origin_missing",
  "backend.auth.password.current_wrong",
  "backend.auth.password.equals_username",
  "backend.auth.password.ldap_directory",
  "backend.auth.password.local_only",
  "backend.auth.password.too_long",
  "backend.auth.password.too_short",
  "backend.auth.permission.missing",
  "backend.auth.session.must_change_password",
  "backend.auth.session.required",
  // The first-run dialog of the history backfill words an estimate (MSG-8a): the fit, estimate,
  // assumption, window-limit and collection-switch sentences and the words they are built from.
  "backend.history.assumption.budget_ips",
  "backend.history.assumption.budget_plain",
  "backend.history.assumption.budget_waf",
  "backend.history.assumption.cadence",
  "backend.history.assumption.catchall_rows",
  "backend.history.assumption.catchall_senders",
  "backend.history.assumption.ips_fair_share",
  "backend.history.assumption.ips_sources",
  "backend.history.assumption.ips_values",
  "backend.history.assumption.opening",
  "backend.history.assumption.phase_a_rows",
  "backend.history.assumption.qradar_time",
  "backend.history.assumption.rule_rows",
  "backend.history.assumption.rules",
  "backend.history.assumption.shape_buckets",
  "backend.history.assumption.shape_windows",
  "backend.history.assumption.waf_breakdown",
  "backend.history.assumption.waf_sources",
  "backend.history.estimate.all_fit",
  "backend.history.estimate.all_refused",
  "backend.history.estimate.no_catchall",
  "backend.history.estimate.no_collection",
  "backend.history.estimate.no_ips",
  "backend.history.estimate.no_monitored",
  "backend.history.estimate.no_rules",
  "backend.history.estimate.no_waf",
  "backend.history.estimate.planner_refused",
  "backend.history.estimate.reduced",
  "backend.history.estimate.some_refused",
  "backend.history.estimate.some_refused_reduced",
  "backend.history.estimate.window_limited",
  "backend.history.family.always_on",
  "backend.history.family.fair_share",
  "backend.history.family.switch_off",
  "backend.history.family.switch_on",
  "backend.history.family.zero_endpoint",
  "backend.history.family.zero_entity",
  "backend.history.fit.fits",
  "backend.history.fit.reduced",
  "backend.history.fit.refused",
  "backend.history.interval.day_many",
  "backend.history.interval.day_one",
  "backend.history.interval.hour_many",
  "backend.history.interval.hour_one",
  "backend.history.interval.minute_many",
  "backend.history.interval.minute_one",
  "backend.history.window.limit_retention",
  "backend.history.window.limit_setting",
  "backend.history.window.reduced",
  "backend.history.window.unavailable",
  "backend.history.words.bucket_many",
  "backend.history.words.bucket_one",
  "backend.history.words.plan_many",
  "backend.history.words.plan_one",
  "backend.history.words.source_many",
  "backend.history.words.source_one",
] as const;

export const SHELL_NAMESPACES = [
  "auth",
  "behavior",
  "cadence",
  "common",
  "errors",
  "format",
  "guideLinks",
  "history",
  "password",
  "shell",
  "states",
  "units",
] as const;

export const ROUTE_NAMESPACES: Readonly<Record<string, readonly string[]>> = {
  "/account": ["account", "backend"],
  "/account/password": ["backend"],
  "/admin/classification-rules": ["backend", "classification", "enums", "fleet"],
  "/admin/directory": ["backend", "directory"],
  "/admin/history-backfill": ["backend", "enums", "waf"],
  "/admin/ips-sources/[logSourceId]": ["backend", "enums", "fieldMapping", "ips", "waf"],
  "/admin/ips-sources": ["backend", "enums", "fieldMapping", "ips", "waf"],
  "/admin/outage-policy": ["backend", "classification", "enums", "fleet", "outage"],
  "/admin": ["admin", "users"],
  "/admin/search-cadence": [],
  "/admin/users": ["backend", "enums", "users"],
  "/admin/waf-sources/[logSourceId]": ["backend", "enums", "fieldMapping", "waf"],
  "/admin/waf-sources": ["backend", "enums", "fieldMapping", "waf"],
  "/alerts/[id]": ["alerts"],
  "/alerts": [],
  "/anomalies/[id]": ["anomalies", "backend", "classification", "enums", "fleet", "home", "ips", "propertyWatch", "pwchart", "ui", "waf"],
  "/anomalies": ["anomalies", "enums", "ips", "propertyWatch", "pwchart", "waf"],
  "/behavior": [],
  "/behavior/sources/[id]": [],
  "/behavior/sources": [],
  "/behavior/waf": [],
  "/behavior/waf/sources/[id]": [],
  "/behavior/watches/[id]": ["backend", "classification", "enums", "fleet", "home", "propertyWatch", "pwchart", "ui", "waf"],
  "/behavior/watches/new": ["backend", "enums", "propertyWatch", "pwchart", "ui", "waf"],
  "/behavior/watches": [],
  "/config-changes": [],
  "/coverage": [],
  "/custom-rules/[id]": ["backend", "customRules", "enums"],
  "/custom-rules/new": ["backend", "customRules", "enums"],
  "/custom-rules": [],
  "/fleet/catchall": ["catchall", "fleet"],
  "/fleet": [],
  "/fleet/sources": ["backend", "classification", "enums", "fleet"],
  "/guide": [],
  "/guide/[section]": [],
  "/login": ["backend", "login"],
  "/log-sources/[id]": [],
  "/log-sources": [],
  "/offenses/[id]": [],
  "/offenses": [],
  "/": ["backend", "catalog", "catchall", "classification", "coverage", "dashboard", "enums", "fieldMapping", "fleet", "home", "ips", "propertyWatch", "pwchart", "searches", "trend", "waf", "widgets"],
  "/rules/[id]": [],
  "/rules": [],
  "/rules/statistics": [],
  "/searches/[id]": ["backend", "enums", "searches", "trend", "ui", "widgets"],
  "/searches/new": ["backend", "enums", "searches", "trend"],
  "/searches": [],
};

/** The namespaces and key paths of `messages` named, whole namespaces and single keys alike. */
export function pickMessages(
  messages: Readonly<Record<string, unknown>>,
  namespaces: readonly string[],
  keys: readonly string[] = [],
): Record<string, unknown> {
  const picked: Record<string, unknown> = {};
  for (const namespace of namespaces) {
    if (Object.hasOwn(messages, namespace)) picked[namespace] = messages[namespace];
  }
  for (const key of keys) {
    const path = key.split(".");
    let from: unknown = messages;
    let into = picked;
    for (let at = 0; at < path.length; at += 1) {
      if (typeof from !== "object" || from === null || !Object.hasOwn(from, path[at])) break;
      from = (from as Record<string, unknown>)[path[at]];
      if (at === path.length - 1) into[path[at]] = from;
      else {
        if (!Object.hasOwn(into, path[at])) into[path[at]] = {};
        into = into[path[at]] as Record<string, unknown>;
      }
    }
  }
  return picked;
}

/** What the browser gets for a route: the shell plus the route's own namespaces. */
export function routeMessages(
  route: string,
  messages: Readonly<Record<string, unknown>>,
): Record<string, unknown> {
  if (!Object.hasOwn(ROUTE_NAMESPACES, route)) throw new Error(`No message set for route ${route}`);
  return pickMessages(messages, [...SHELL_NAMESPACES, ...ROUTE_NAMESPACES[route]]);
}

/** What the browser gets before a page's provider: the shell, the boundary strings and the top bar's keys. */
export function shellMessages(messages: Readonly<Record<string, unknown>>): Record<string, unknown> {
  return pickMessages(messages, SHELL_NAMESPACES, [...BOUNDARY_KEYS, ...TOP_BAR_KEYS, ...SHELL_BACKEND_KEYS]);
}
