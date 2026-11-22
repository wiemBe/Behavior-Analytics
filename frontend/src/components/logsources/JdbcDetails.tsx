"use client";

import { useLocale, useTranslations } from "next-intl";
import { useId, type ReactNode } from "react";

import { jdbcMeasurementView, jdbcStateLabel } from "@/app/log-sources/jdbc/states";
import { Disclosure } from "@/components/ui/Disclosure";
import { UtcTime } from "@/components/ui/UtcTime";
import { asTextTranslator } from "@/i18n/textTranslator";
import { JDBC_CONNECTION_CODES, type JdbcSafeFieldName, type JdbcSourceDetail } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import "@/app/log-sources/log-sources.css";

export type JdbcDetailsProps =
  | { data: JdbcSourceDetail; failure?: never }
  | { data?: never; failure: "loading" | "error" | "forbidden" | "not-found" };

const PRIMARY: readonly JdbcSafeFieldName[] = [
  "table_name", "select_list", "compare_field", "polling_interval_seconds",
  "database_type", "database_host", "database_port", "database_name", "schema_name",
  "start_time_local", "source_label",
];
const ADVANCED: readonly JdbcSafeFieldName[] = [
  "tls_hostname", "prepared_statements", "predefined_query", "eps_throttle",
  "use_ssl", "allow_untrusted", "advanced_options",
];

function Missing({ label }: { label: string }) {
  return (
    <span title={label}>
      <span aria-hidden="true">—</span>
      <span className="sr-only">{label}</span>
    </span>
  );
}

/** Each block reads its own evidence; connection status never supplies a flow verdict. */
export function JdbcDetails(props: JdbcDetailsProps) {
  const t = useTranslations("logSources");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as FormatLocale;
  const id = useId();
  const label = (kind: Parameters<typeof jdbcStateLabel>[0], code: string | null | undefined) =>
    jdbcStateLabel(kind, code, say);
  if (!props.data) {
    return (
      <p role={props.failure === "loading" ? "status" : undefined}>
        {t(`jdbcDetail.${props.failure}`)}
      </p>
    );
  }
  const { config, qradar, volume, flow } = props.data;
  const measure = jdbcMeasurementView(volume);
  const measurement = label("measurement", volume.measurement);
  const unavailable = label("config", null);
  const measurementReason = volume.measurement === "EXACT" ? unavailable : measurement;
  const configReason = config.state === "COMPLETE" ? unavailable : label("config", config.state);
  const clock = (value: string | null, reason: string) => value
    ? <UtcTime value={value} className="nowrap num" /> : <Missing label={reason} />;
  const fact = (name: string, value: ReactNode) => <div><dt>{name}</dt><dd>{value}</dd></div>;
  const number = (value: number | null, reason: string, unit?: string) => value === null
    ? <Missing label={reason} />
    : <span className="nowrap num">{unit === t("jdbcDetail.eps") && value > 0 && value < 0.001
      ? `< ${formatCount(0.001, locale)}` : formatCount(value, locale)}{unit && ` ${unit}`}</span>;
  function field(name: JdbcSafeFieldName) {
    const state = config.field_states[name];
    const value = config.safe_fields?.[name];
    const unsupported = config.support === "UNVERIFIED_PROTOCOL";
    const code = state?.code === "SET" && value == null ? undefined : state?.code;
    const reason = unsupported ? label("support", config.support)
      : config.state === "NOT_COLLECTED" ? label("config", config.state)
        : [label("fieldState", code), state?.reason && label("fieldReason", state.reason)]
          .filter(Boolean).join(" · ");
    let shown: ReactNode = <Missing label={reason} />;
    if (!unsupported && config.state !== "NOT_COLLECTED" && state?.code === "SET" && value != null) {
      if (typeof value === "boolean") shown = t(`detail.${value ? "yes" : "no"}`);
      else if (typeof value === "number") {
        const unit = name === "polling_interval_seconds" ? t("jdbcDetail.seconds")
          : name === "eps_throttle" ? t("jdbcDetail.eps") : null;
        shown = <span className="nowrap num">{
          name === "database_port" ? String(value) : formatCount(value, locale)
        }{unit && ` ${unit}`}</span>;
      } else if (name === "start_time_local") {
        shown = <><span className="nowrap num">{value}</span>{" · "}{t("jdbcDetail.timezone")}</>;
      } else shown = value;
    }
    return <div key={name}><dt>{t(`jdbcDetail.fields.${name}`)}</dt><dd>{shown}</dd></div>;
  }
  const tone = qradar.status === "WARNING" ? "warn"
    : qradar.status === "CONNECTION_ERROR" ? "crit" : "";
  const statusCode = qradar.status !== null && JDBC_CONNECTION_CODES.includes(qradar.status)
    ? qradar.status : label("connection", qradar.status);
  const historical = volume.last_observation;
  const disabled = flow.state === "DISABLED_IN_QRADAR";
  // Absent: the stored rows are dated history, so no current-flow wording or measurement is shown.
  const absent = flow.state === "ABSENT_FROM_INVENTORY";
  const history = historical && <>
    {fact(t("jdbcDetail.lastObservation"), clock(historical.observed_at, label("completeness", historical.completeness)))}
    {fact(t("jdbcDetail.windowStart"), clock(historical.start, label("completeness", historical.completeness)))}
    {fact(t("jdbcDetail.windowEnd"), clock(historical.end, label("completeness", historical.completeness)))}
    {fact(t("jdbcPage.table.events"), number(historical.event_count, label("completeness", historical.completeness)))}
    {fact(t("jdbcDetail.completeness"), label("completeness", historical.completeness))}
    {fact(t("jdbcDetail.origin"), label("origin", historical.origin))}
  </>;
  return (
    <div className="jdbc-detail">
      <p className="jdbc-snapshot">
        {qradar.inventory_stale && <strong className="jdbc-stale">{t("jdbcPage.inventoryStale")} </strong>}
        {qradar.inventory_last_complete_at === null ? t("jdbcPage.neverCollected") : <>{t("jdbcPage.snapshotLast")}: {clock(qradar.inventory_last_complete_at, unavailable)}</>}
        {" · "}{t("jdbcPage.snapshotAttempt")}: {label("attempt", qradar.inventory_state)}
        {" · "}{clock(qradar.inventory_last_attempt_at, unavailable)}
      </p>
      <section aria-labelledby={`${id}-config`}>
        <h2 id={`${id}-config`}>{t("jdbcDetail.configuration")}</h2>
        <p>{label("config", config.state)}{absent && <> · {t("jdbcDetail.configHistory")} · {t("jdbcDetail.observed")}: {clock(config.observed_at, configReason)}</>}{config.stale && <> · <span className="jdbc-stale">{t("jdbcPage.configStale")}</span></>}
          {(config.stale || config.failure_code) && <> · {t("jdbcDetail.lastSuccess")}: {clock(config.last_success_at, configReason)}</>}
          {config.failure_code && <> · {t("jdbcDetail.failure")}: <span>{label("failure", config.failure_code)}</span></>}
        </p>
        <dl className="jdbc-facts">{PRIMARY.map(field)}</dl>
        <Disclosure summary={t("jdbcDetail.advanced")}>
          <dl className="jdbc-facts">{ADVANCED.map(field)}</dl>
        </Disclosure>
        <Disclosure summary={t("jdbcDetail.configurationDetails")}>
          <dl className="jdbc-facts">
            {fact(t("jdbcDetail.observed"), clock(config.observed_at, configReason))}
            {fact(t("jdbcDetail.lastSuccess"), clock(config.last_success_at, configReason))}
            {fact(t("jdbcDetail.support"), label("support", config.support))}
          </dl>
        </Disclosure>
      </section>
      <section aria-labelledby={`${id}-connection`}>
        <h2 id={`${id}-connection`}>{t("jdbcPage.table.connection")}</h2>
        <p><span className={tone ? `pill ${tone}` : "pill"}>{label("connection", qradar.status)}</span></p>
        {absent && <p>{t("jdbcDetail.connectionHistory")}</p>}
        <dl className="jdbc-facts">
          {fact(t("jdbcPage.table.enabled"), t(`jdbcPage.${qradar.enabled ? "enabledYes" : "enabledNo"}`))}
          {qradar.inconsistency && fact(t("jdbcDetail.inconsistency"), label("inconsistency", qradar.inconsistency))}
        </dl>
        <Disclosure summary={t("jdbcDetail.connectionDetails")}>
          <dl className="jdbc-facts">
            {fact(t("jdbcDetail.statusCode"), statusCode)}
            {qradar.reason_code && fact(t("jdbcDetail.inventoryReason"), label("reason", qradar.reason_code))}
          </dl>
        </Disclosure>
      </section>
      <section aria-labelledby={`${id}-flow`}>
        <h2 id={`${id}-flow`}>{t("jdbcPage.table.flow")}</h2>
        {absent ? <>
          <p>{label("flow", flow.state)} {t("jdbcDetail.since")} {clock(qradar.absent_since ?? null, unavailable)}</p>
          <p>{t("jdbcDetail.absentHistory")}</p>
          {history ? <dl className="jdbc-facts">{history}</dl> : <p>{t("jdbcDetail.noHistory")}</p>}
        </> : <>
          <p>{label("flow", flow.state)}{!disabled && volume.stale && <> · <span className="jdbc-stale">{t("jdbcPage.volumeStale")}</span></>}</p>
          <p>{measurement}</p>
          {volume.measurement === "TRUNCATED" && <p>{t("jdbcPage.truncated")}</p>}
          {volume.measurement === "PARTIAL" && <p>{t("jdbcPage.insufficient")}</p>}
          {volume.measurement === "NOT_COLLECTED" && <p>{t("jdbcPage.notMeasured")}</p>}
          <dl className="jdbc-facts">
            {fact(t("jdbcPage.table.events"), number(measure.eventCount, measurementReason))}
            {fact(t("jdbcDetail.averageEps"), number(measure.averageEps, measurementReason, t("jdbcDetail.eps")))}
            {fact(t("jdbcDetail.windowStart"), clock(volume.target_start, measurementReason))}
            {fact(t("jdbcDetail.windowEnd"), clock(volume.target_end, measurementReason))}
            {fact(t("jdbcDetail.bucket"), number(volume.bucket_seconds, unavailable, t("jdbcDetail.seconds")))}
            {fact(t("jdbcDetail.observed"), clock(measure.collectedAt, measurementReason))}
          </dl>
          <Disclosure summary={t("jdbcDetail.flowDetails")}>
            <dl className="jdbc-facts">
              {flow.reason_code && fact(t("jdbcDetail.reason"), label("reason", flow.reason_code))}
              {volume.reason_code && fact(t("jdbcDetail.measurementReason"), label("reason", volume.reason_code))}
              {history}
            </dl>
          </Disclosure>
        </>}
      </section>
    </div>
  );
}
