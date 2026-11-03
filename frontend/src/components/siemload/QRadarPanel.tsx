"use client";

// How QRadar itself is doing, one section per enabled console. Every field carries its own state:
// a value that was read is shown; one that was not read says so and says why. Nothing here turns a
// missing reading into a zero, and nothing is drawn healthy because it is absent.

import { useTranslations } from "next-intl";
import type { ReactNode } from "react";

import { UtcTime } from "@/components/ui/UtcTime";
import type { QRadarConsoleHealth, QRadarHealthPart, QRadarLicenseRate } from "@/lib/api";
import { formatCount, formatShare } from "@/lib/format";
import { formatDuration } from "@/lib/time";

import { FieldValue } from "./FieldValue";
import { formatMs } from "./siemFormat";

function Row({ label, children, note }: { label: string; children: ReactNode; note?: ReactNode }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>
        {children}
        {note ? <span className="sl-note">{note}</span> : null}
      </dd>
    </>
  );
}

function LicenseLine({
  name,
  line,
}: {
  name: "licenseEps" | "licenseFpm";
  line: QRadarLicenseRate;
}) {
  const t = useTranslations("siemLoad.qradar");
  return (
    <span className="sl-line num">
      {t(name, { allocated: formatCount(line.allocated), total: formatCount(line.total) })}
      {line.overallocated === true ? (
        <>
          {" "}
          <span className="pill warn">{t("overallocated")}</span>
        </>
      ) : null}
    </span>
  );
}

function Console({ health }: { health: QRadarConsoleHealth }) {
  const t = useTranslations("siemLoad.qradar");
  const read = health.cached
    ? t.rich("readCached", {
        age: formatDuration(health.age_seconds),
        ttl: health.cache_ttl_seconds,
        time: () => <UtcTime value={health.as_of} />,
      })
    : t.rich("readAt", { time: () => <UtcTime value={health.as_of} /> });
  const { ariel } = health;

  return (
    <section className="sl-console" aria-label={t("consoleLabel", { name: health.instance_name })}>
      <header className="sl-console-head">
        <h3 className="sl-console-name">{health.instance_name}</h3>
        <p className="sl-asof">{read}</p>
      </header>
      <dl className="sl-facts">
        <Row label={t("reachable")}>
          {health.reachable.state === "OK" && health.reachable.value === false ? (
            <span className="sl-gap">
              <span className="pill crit">{t("reachableNo")}</span>
              {health.reachable.reason ? (
                <span className="sl-reason">{health.reachable.reason}</span>
              ) : null}
            </span>
          ) : (
            <FieldValue field={health.reachable}>{() => t("reachableYes")}</FieldValue>
          )}
        </Row>
        <Row label={t("latency")}>
          <FieldValue field={health.api_latency_ms}>
            {(ms) => <span className="num">{formatMs(ms)}</span>}
          </FieldValue>
        </Row>
        <Row label={t("version")}>
          <FieldValue field={health.version}>{(version) => version}</FieldValue>
        </Row>
        <Row label={t("hosts")}>
          <FieldValue field={health.servers}>
            {(servers) => {
              const notActive = servers.total - servers.active;
              // The table below lists every host with its HA members; the short list stays only
              // while that table could not be read.
              const tableShown = health.hosts?.state === "OK" && health.hosts.value != null;
              const pairs = servers.ha_pairs ?? 0;
              const problem = servers.ha_problem ?? 0;
              return (
                <>
                  <span className="sl-line num">
                    {t("hostsActive", { active: servers.active, total: servers.total })}
                    {notActive > 0 ? (
                      <>
                        {" "}
                        <span className="pill warn">{t("hostsNotActive", { count: notActive })}</span>
                      </>
                    ) : null}
                  </span>
                  {pairs > 0 ? (
                    <span className="sl-line num">
                      {problem > 0 ? (
                        <>
                          {t("hostsHa", { pairs, problem })}{" "}
                          <span className="pill warn">{t("hostsHaCheck")}</span>
                        </>
                      ) : (
                        t("hostsHaHealthy", { pairs })
                      )}
                    </span>
                  ) : null}
                  {tableShown ? (
                    notActive > 0 || problem > 0 ? (
                      <span className="sl-note">{t("hostsListed")}</span>
                    ) : null
                  ) : (
                    <>
                      {servers.not_active.length > 0 ? (
                        <ul className="sl-hosts">
                          {servers.not_active.map((host, at) => (
                            <li key={`${host.hostname ?? ""}-${at}`}>
                              <span className="sl-host">{host.hostname ?? "—"}</span>
                              {" — "}
                              {host.status ?? t("hostStatusNone")}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                      {notActive > servers.not_active.length ? (
                        <span className="sl-note">
                          {t("hostsMore", { count: notActive - servers.not_active.length })}
                        </span>
                      ) : null}
                    </>
                  )}
                </>
              );
            }}
          </FieldValue>
        </Row>
        <Row label={t("eps")}>
          <FieldValue field={health.eps}>
            {(eps) => (
              <>
                <span className="sl-line num">
                  {t("epsAverage", { value: formatCount(eps.average_eps) })} ·{" "}
                  {t("epsPeak", { value: formatCount(eps.peak_eps) })}
                </span>
                <span className="sl-note">
                  {eps.allocated_eps > 0
                    ? t("epsAllocation", {
                        allocated: formatCount(eps.allocated_eps),
                        hosts: eps.hosts,
                        average: formatShare(eps.average_share),
                        peak: formatShare(eps.peak_eps / eps.allocated_eps),
                      })
                    : t("epsNoShare")}
                </span>
              </>
            )}
          </FieldValue>
        </Row>
        <Row label={t("license")}>
          <FieldValue field={health.license}>
            {(license) => (
              <>
                <LicenseLine name="licenseEps" line={license.eps} />
                {license.fpm ? (
                  <LicenseLine name="licenseFpm" line={license.fpm} />
                ) : (
                  <span className="sl-line sl-muted">{t("licenseFpmNone")}</span>
                )}
              </>
            )}
          </FieldValue>
        </Row>
        <Row label={t("ariel")} note={ariel.available.state === "OK" ? t("arielAvailableNote") : null}>
          <FieldValue field={ariel.available}>
            {(available) =>
              available ? (
                t("arielAnswering")
              ) : (
                <span className="pill crit">{t("arielNotAnswering")}</span>
              )
            }
          </FieldValue>
        </Row>
        <Row label={t("arielOurs")}>
          <FieldValue field={ariel.recent}>
            {(recent) => (
              <>
                <span className="sl-line">
                  {t("arielRecent", {
                    searches: recent.searches,
                    minutes: recent.window_minutes,
                    unavailable: recent.unavailable,
                    share: formatShare(recent.unavailable_share),
                  })}
                </span>
                {recent.last_success_at ? (
                  <span className="sl-note">
                    {t.rich("arielLastOk", {
                      time: () => <UtcTime value={recent.last_success_at} />,
                    })}
                  </span>
                ) : null}
                {recent.last_unavailable_at ? (
                  <span className="sl-note">
                    {t.rich("arielLastDown", {
                      time: () => <UtcTime value={recent.last_unavailable_at} />,
                    })}
                  </span>
                ) : null}
              </>
            )}
          </FieldValue>
        </Row>
        <Row label={t("arielInFlight")}>
          <FieldValue field={ariel.ours_in_flight}>
            {(count) => <span className="num">{t("inFlightOurs", { count })}</span>}
          </FieldValue>
        </Row>
        <Row
          label={t("arielHeldLabel")}
          note={ariel.list_probe.state === "OK" ? t("arielHeldNote") : null}
        >
          <FieldValue field={ariel.list_probe}>
            {(held) => (
              <span className="num">
                {held.truncated
                  ? t("arielHeldAtLeast", { count: held.held })
                  : t("arielHeldCount", { count: held.held })}
              </span>
            )}
          </FieldValue>
        </Row>
      </dl>
    </section>
  );
}

export function QRadarPanel({ part }: { part: QRadarHealthPart | undefined }) {
  const t = useTranslations("siemLoad.qradar");
  const state = useTranslations("siemLoad.state");
  const parts = useTranslations("siemLoad.part");
  return (
    <section className="sl-section" aria-labelledby="sl-qradar-h">
      <h2 className="sl-h" id="sl-qradar-h">
        {t("heading")}
      </h2>
      {part === undefined || !part.available ? (
        <div className="notice" role="status">
          <p>
            <strong>{state("partUnavailable", { part: parts("qradar") })}</strong>
          </p>
          <p>{state("reason", { reason: part?.reason ?? state("notRead") })}</p>
        </div>
      ) : part.consoles.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("noConsole")}</strong>
          </p>
          <p>{t("noConsoleHelp")}</p>
        </div>
      ) : (
        part.consoles.map((health) => <Console key={health.instance_id} health={health} />)
      )}
    </section>
  );
}
