// The endpoint inventory of one WAF source.
//
// Answers "which endpoints are tracked, and how adequate is each baseline". The
// state column is the worst detector state; `null` means no detector has
// evaluated the endpoint, which is said in words and never drawn as NORMAL.

import { useTranslations } from "next-intl";
import { Fragment } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import type { WafEndpointInventory } from "@/lib/api";
import { formatCount, stateTone } from "@/lib/behavior";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { endpointLabel, statePillText } from "./wafLabels";

import "@/components/ui/controls.css";

/** A time with its zone, or, under a header that already says "(UTC)", without it. */
function Utc({ value, bare = false }: { value: string; bare?: boolean }) {
  return <UtcTime value={value} variant="compact" bare={bare} className="nowrap" />;
}

/** The method and path, with a break opportunity after each "/" so a path wraps between
 * segments and a segment breaks inside itself only when it alone is wider than the column. */
function PathLabel({ text }: { text: string }) {
  const parts = text.split("/");
  return (
    <>
      {parts.map((part, i) => (
        <Fragment key={i}>
          {i > 0 && (
            <>
              /<wbr />
            </>
          )}
          {part}
        </Fragment>
      ))}
    </>
  );
}

function BaselineCell({ e }: { e: WafEndpointInventory }) {
  const t = useTranslations("waf.endpoints");
  const { locale } = useBehaviorI18n();
  const { cells_total: total, reliable_cells: reliable } = e.baseline;
  return (
    <>
      {total === 0 ? (
        <span>{t("noCells")}</span>
      ) : (
        <>{t("reliableOfTotal", { reliable: formatCount(reliable, locale), total: formatCount(total, locale) })}</>
      )}
      <span className="cell-sub">
        {total === 0
          ? t("noBaseline")
          : reliable === 0
            ? t("noneReliable")
            : t("reliableCells")}
      </span>
      {e.baseline_rebuilt_at ? (
        <span className="cell-sub">
          {t.rich("rebuilt", { time: () => <Utc value={e.baseline_rebuilt_at as string} /> })}
        </span>
      ) : (
        <span className="cell-sub">{t("neverRebuilt")}</span>
      )}
    </>
  );
}

export function WafEndpointsTable({ endpoints }: { endpoints: WafEndpointInventory[] }) {
  const t = useTranslations("waf.endpoints");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  return (
    <TableScroll label={t("tableLabel")}>
      <table className="table-dense">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("endpoint")}</th>
            <th scope="col" className="num">
              {t("baselineCells")}
              <span className="cell-sub">{t("reliableTotal")}</span>
            </th>
            <th scope="col" className="num">
              {t("lastSeen")}
            </th>
            <th scope="col">{t("tracking")}</th>
            <th scope="col">{t("state")}</th>
          </tr>
        </thead>
        <tbody>
          {endpoints.map((e) => (
            <tr key={e.id}>
              <td className="cell-name">
                <span className="waf-endpoint"><PathLabel text={endpointLabel(e)} /></span>
                {e.virtual_host && (
                  <span className="cell-sub">{t("virtualHost", { host: e.virtual_host })}</span>
                )}
                <span className="cell-sub">
                  {t("normalizer", { version: String(e.normalizer_version) })}
                </span>
              </td>
              <td className="num">
                <BaselineCell e={e} />
              </td>
              <td className="num">
                <Utc value={e.last_seen_at} bare />
                <span className="cell-sub">
                  {t.rich("first", { time: () => <Utc value={e.first_seen_at} bare /> })}
                </span>
              </td>
              <td>
                {e.retired_at ? (
                  <>
                    <span className="pill pill-quiet warn">{t("retired")}</span>
                    <span className="cell-sub">
                      <Utc value={e.retired_at} />
                    </span>
                  </>
                ) : e.is_active ? (
                  t("active")
                ) : (
                  // Inactive without a retirement time: not tracked, reason not recorded.
                  <>
                    <span className="pill pill-quiet">{t("inactive")}</span>
                    <span className="cell-sub">{t("retirementNotRecorded")}</span>
                  </>
                )}
              </td>
              <td>
                {e.state ? (
                  <span className={`pill pill-quiet ${stateTone(e.state)}`}>
                    {statePillText(e.state, i18n)}
                  </span>
                ) : (
                  <>
                    <span className="muted">{t("notEvaluated")}</span>
                    <span className="cell-sub">{t("noDetector")}</span>
                  </>
                )}
                {e.active_anomaly_count > 0 && (
                  <span className="cell-sub">
                    {t("activeAnomalies", {
                      count: e.active_anomaly_count,
                      shown: formatCount(e.active_anomaly_count, locale),
                    })}
                  </span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
