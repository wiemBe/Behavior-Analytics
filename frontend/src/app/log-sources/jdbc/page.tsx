import { getLocale, getTranslations } from "next-intl/server";
import Link from "next/link";
import type { ReactNode } from "react";

import { Pagination } from "@/components/Pagination";
import { PageHeader } from "@/components/ui/PageHeader";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { withRouteMessages } from "@/i18n/RouteMessages";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";
import {
  ApiError, JDBC_FLOW_STATES, JDBC_PROTOCOL_IDS, api, type JdbcSourcePage, type JdbcSourceRow,
} from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import { guideFor } from "@/lib/guideRoutes";

import "../log-sources.css";
import { JdbcRefresh } from "./JdbcRefresh";
import {
  JDBC_PAGE_SIZE, JDBC_Q_MAX, buildJdbcQuery, clearJdbcFilters, parseJdbcQuery, type RawQuery,
} from "./query";
import { jdbcMeasurementView, jdbcStateLabel } from "./states";

const BASE = "/log-sources/jdbc";

/** A missing value is a dash that still names why, for a screen reader and on hover. */
function Missing({ label }: { label: string }) {
  return (
    <span title={label}>
      <span aria-hidden="true">—</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** `host:port`, with an IPv6 literal in brackets so the port is unambiguous. */
function hostPort(host: string | null, port: number | null): string | null {
  if (!host) return null;
  const name = host.includes(":") && !host.startsWith("[") ? `[${host}]` : host;
  return port === null ? name : `${name}:${port}`;
}

function Cell({ row, say, t, locale }: {
  row: JdbcSourceRow; say: TextTranslator; t: TextTranslator; locale: FormatLocale;
}) {
  const fields = row.config.safe_fields;
  const configMissing = jdbcStateLabel("config", row.config.state, say);
  const disabled = row.flow.state === "DISABLED_IN_QRADAR";
  const measure = jdbcMeasurementView(row.volume);
  const measurement = jdbcStateLabel("measurement", row.volume.measurement, say);
  const exactZero = row.volume.measurement === "EXACT" && measure.eventCount === 0;
  const database = [fields?.database_type, fields?.database_name].filter(Boolean).join(" · ");
  const host = hostPort(fields?.database_host ?? null, fields?.database_port ?? null);
  const connection = jdbcStateLabel("connection", row.qradar.status, say);
  const tone = row.qradar.status === "WARNING" ? "warn" : row.qradar.status === "CONNECTION_ERROR" ? "crit" : "";
  const flow =
    row.flow.state === "NO_ATTRIBUTED_EVENTS" && row.qradar.status === "CONNECTED" && exactZero
      ? t("jdbcPage.connectedZero")
      : jdbcStateLabel("flow", row.flow.state, say);
  return (
    <tr>
      <td className="cell-name">
        <Link href={`/log-sources/${row.id}`}>{row.name}</Link>
        {row.protocol_type_id !== 8 && <span className="cell-sub">{row.protocol_name}</span>}
      </td>
      <td className="cell-wrap">
        {database ? database : <Missing label={configMissing} />}
        {row.config.stale && <span className="cell-sub">{t("jdbcPage.configStale")}</span>}
      </td>
      <td className="cell-wrap cell-host">{host ?? <Missing label={configMissing} />}</td>
      <td>
        {row.qradar.enabled ? t("jdbcPage.enabledYes") : t("jdbcPage.enabledNo")}
        {row.qradar.inconsistency && (
          <span className="cell-sub">{jdbcStateLabel("inconsistency", row.qradar.inconsistency, say)}</span>
        )}
      </td>
      <td>{tone ? <span className={`pill ${tone}`}>{connection}</span> : connection}</td>
      <td>
        {flow}
        {row.flow.state === "NOT_MEASURED" && <span className="cell-sub">{measurement}</span>}
        {!disabled && row.volume.stale && <span className="cell-sub">{t("jdbcPage.volumeStale")}</span>}
      </td>
      <td className="num">
        {measure.eventCount === null ? <Missing label={measurement} /> : formatCount(measure.eventCount, locale)}
        {measure.eventCount !== null && row.volume.measurement !== "EXACT" && (
          <span className="cell-sub">{measurement}</span>
        )}
      </td>
      <td className="nowrap">
        {measure.collectedAt ? <UtcTime value={measure.collectedAt} variant="compact" /> : <Missing label={measurement} />}
      </td>
    </tr>
  );
}

function Note({ text, count, t }: { text: string; count: number; t: TextTranslator }) {
  return (
    <li>
      {text}
      <details className="jdbc-details">
        <summary>{t("jdbcPage.details")}</summary>
        <p>{t("jdbcPage.affected", { count })}</p>
      </details>
    </li>
  );
}

function Filters({ params, consoles, t, say }: {
  params: ReturnType<typeof parseJdbcQuery>; consoles: number; t: TextTranslator; say: TextTranslator;
}) {
  const any = <option value="">{t("jdbcPage.filters.any")}</option>;
  return (
    <form className="filters jdbc-filters" method="get" action={BASE} role="search" aria-label={t("jdbcPage.filters.label")}>
      <label className="sr-only" htmlFor="jdbc-q">{t("jdbcPage.filters.search")}</label>
      <input id="jdbc-q" type="search" name="q" defaultValue={params.q} maxLength={JDBC_Q_MAX}
        placeholder={t("jdbcPage.filters.placeholder")} autoComplete="off" />
      <label className="filter-field">
        {t("jdbcPage.filters.enabled")}
        <select name="enabled" defaultValue={params.enabled === undefined ? "" : String(params.enabled)}>
          {any}
          <option value="true">{t("jdbcPage.enabledYes")}</option>
          <option value="false">{t("jdbcPage.enabledNo")}</option>
        </select>
      </label>
      <label className="filter-field">
        {t("jdbcPage.filters.flow")}
        <select name="flow_state" defaultValue={params.flow_state ?? ""}>
          {any}
          {JDBC_FLOW_STATES.map((s) => <option key={s} value={s}>{jdbcStateLabel("flow", s, say)}</option>)}
        </select>
      </label>
      <label className="filter-field">
        {t("jdbcPage.filters.protocol")}
        <select name="protocol" defaultValue={params.protocol === undefined ? "" : String(params.protocol)}>
          {any}
          {JDBC_PROTOCOL_IDS.map((id) => (
            <option key={id} value={id}>{t("jdbcPage.filters.protocolOption", { id })}</option>
          ))}
        </select>
      </label>
      {consoles > 1 ? (
        <label className="filter-field">
          {t("jdbcPage.filters.instance")}
          <input type="text" name="instance_id" defaultValue={params.instance_id ?? ""} />
        </label>
      ) : (
        params.instance_id && <input type="hidden" name="instance_id" value={params.instance_id} />
      )}
      {params.limit !== JDBC_PAGE_SIZE && <input type="hidden" name="limit" value={params.limit} />}
      <button type="submit">{t("jdbcPage.filters.apply")}</button>
    </form>
  );
}

function stateNotice(text: ReactNode, action?: ReactNode, alert = false) {
  return (
    <div className="notice" role={alert ? "alert" : undefined}>
      {text} {action}
    </div>
  );
}

async function JdbcSourcesPage({ searchParams }: { searchParams: Promise<RawQuery> }) {
  const params = parseJdbcQuery(await searchParams);
  const t = asTextTranslator(await getTranslations("logSources"));
  const say = asTextTranslator(await getTranslations());
  const locale = (await getLocale()) as FormatLocale;
  let page: JdbcSourcePage | null = null;
  let failure: "forbidden" | "error" | null = null;
  try {
    page = await api.jdbcSources(params);
  } catch (err) {
    failure = err instanceof ApiError && err.status === 403 ? "forbidden" : "error";
  }

  const here = (next: typeof params) =>
    `${BASE}${buildJdbcQuery({
      ...next,
      limit: next.limit === JDBC_PAGE_SIZE ? undefined : next.limit,
      offset: next.offset ? next.offset : undefined,
    })}`;
  const filtered = Boolean(params.q || params.instance_id || params.protocol || params.enabled !== undefined || params.flow_state);
  const items = page?.items ?? [];
  const live = items.filter((r) => r.flow.state !== "DISABLED_IN_QRADAR");
  const notes: [string, number][] = [
    [t("jdbcPage.partial"), (page?.snapshot.state === "PARTIAL" ? 1 : 0) + items.filter((r) => r.config.state === "PARTIAL").length],
    [t("jdbcPage.truncated"), live.filter((r) => r.volume.measurement === "TRUNCATED").length],
    [t("jdbcPage.insufficient"), live.filter((r) => r.volume.measurement === "PARTIAL").length],
    [t("jdbcPage.notMeasured"), live.filter((r) => r.volume.measurement === "NOT_COLLECTED").length],
    [t("jdbcPage.staleBlocks"), items.filter((r) => r.config.stale || (r.flow.state !== "DISABLED_IN_QRADAR" && r.volume.stale)).length],
  ];
  const snapshot = page?.snapshot;
  const never = snapshot !== undefined && snapshot.last_complete_at === null;

  return (
    <>
      <PageHeader
        guide={guideFor("/log-sources/jdbc")}
        title={t("jdbcPage.title")}
        description={page ? t("jdbcPage.count", { count: page.total }) : undefined}
        actions={
          failure === "forbidden" ? undefined : (
            <>
              <JdbcRefresh />
              <Link href="/log-sources">{t("jdbcPage.allSources")}</Link>
            </>
          )
        }
      />
      {page && (filtered || page.total > 0 || (params.offset ?? 0) > 0) && (
        <Filters params={params} consoles={page.snapshot.consoles} t={t} say={say} />
      )}
      {snapshot && !never && (
        <p className="jdbc-snapshot muted">
          {snapshot.stale && <strong className="jdbc-stale">{t("jdbcPage.inventoryStale")} </strong>}
          {t("jdbcPage.snapshotLast")}: <UtcTime value={snapshot.last_complete_at} variant="compact" />
          {" · "}
          {t("jdbcPage.snapshotAttempt")}: {jdbcStateLabel("attempt", snapshot.state, say)}
        </p>
      )}
      {page && items.length > 0 && (
        <ul className="jdbc-notes">
          {notes.filter(([, n]) => n > 0).map(([text, n]) => <Note key={text} text={text} count={n} t={t} />)}
        </ul>
      )}
      {failure === "forbidden"
        ? stateNotice(t("jdbcPage.forbidden"), undefined, true)
        : failure === "error"
          ? stateNotice(t("jdbcPage.error"), <Link href={here(params)}>{t("jdbcPage.retry")}</Link>, true)
          : page && items.length === 0
            ? page.total > 0
              ? stateNotice(t("jdbcPage.pastEnd"), <Link href={here({ ...params, offset: 0 })}>{t("jdbcPage.firstPage")}</Link>)
              : filtered
                ? stateNotice(t("jdbcPage.noMatch"), <Link href={here(clearJdbcFilters(params))}>{t("jdbcPage.clearFilters")}</Link>)
                : never
                  ? stateNotice(t("jdbcPage.neverCollected"))
                  : page.snapshot.complete
                    ? stateNotice(t("jdbcPage.empty"))
                    : stateNotice(t("jdbcPage.partial"))
            : page && (
              <>
                <TableScroll label={t("jdbcPage.table.label")}>
                  <table className="table-dense jdbc-table" aria-label={t("jdbcPage.table.label")}>
                    <thead>
                      <tr>
                        <th>{t("jdbcPage.table.source")}</th>
                        <th>{t("jdbcPage.table.database")}</th>
                        <th>{t("jdbcPage.table.host")}</th>
                        <th>{t("jdbcPage.table.enabled")}</th>
                        <th>{t("jdbcPage.table.connection")}</th>
                        <th>{t("jdbcPage.table.flow")}</th>
                        <th className="num">{t("jdbcPage.table.events")}</th>
                        <th>{t("jdbcPage.table.observed")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.map((row) => <Cell key={row.id} row={row} say={say} t={t} locale={locale} />)}
                    </tbody>
                  </table>
                </TableScroll>
                <Pagination
                  total={page.total}
                  limit={params.limit ?? JDBC_PAGE_SIZE}
                  offset={params.offset ?? 0}
                  basePath={BASE}
                  params={{
                    q: params.q,
                    instance_id: params.instance_id,
                    protocol: params.protocol === undefined ? undefined : String(params.protocol),
                    enabled: params.enabled === undefined ? undefined : String(params.enabled),
                    flow_state: params.flow_state,
                    limit: params.limit === JDBC_PAGE_SIZE ? undefined : String(params.limit),
                  }}
                />
              </>
            )}
    </>
  );
}

export default withRouteMessages("/log-sources/jdbc", JdbcSourcesPage);
