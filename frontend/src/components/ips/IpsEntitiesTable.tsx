// The entity inventory of one IPS source.
//
// Answers "which entities are tracked, and how adequate is each baseline". The state column is the
// worst detector state; `null` means no detector has evaluated the entity, which is said in words
// and never drawn as NORMAL. A row with active anomalies links to the Anomalies list filtered to
// that entity.

import { useTranslations } from "next-intl";
import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { IpsEntityInventory } from "@/lib/api";
import { formatCount, stateTone } from "@/lib/behavior";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { entityLabel, ipsStateWord } from "./ipsLabels";

import "@/components/ui/controls.css";
import "./ips.css";

function Utc({ value, bare = false }: { value: string; bare?: boolean }) {
  return <UtcTime value={value} variant="compact" bare={bare} className="nowrap" />;
}

function BaselineCell({ e }: { e: IpsEntityInventory }) {
  const t = useTranslations("ips.bsource.entities");
  const { locale } = useBehaviorI18n();
  const { cells_total: total, reliable_cells: reliable } = e.baseline;
  return (
    <>
      {total === 0 ? (
        <span>{t("noCells")}</span>
      ) : (
        t("reliableOfTotal", { reliable: formatCount(reliable, locale), total: formatCount(total, locale) })
      )}
      {(total === 0 || reliable === 0) && (
        <span className="cell-sub">{total === 0 ? t("noBaseline") : t("noneReliable")}</span>
      )}
    </>
  );
}

export function IpsEntitiesTable({ entities }: { entities: IpsEntityInventory[] }) {
  const t = useTranslations("ips.bsource.entities");
  const i18n = useBehaviorI18n();
  return (
    <TableScroll label={t("tableLabel")}>
      <table className="table-dense">
        <caption className="sr-only">{t("caption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("entity")}</th>
            <th scope="col" className="num">
              {t("baseline")}
              <span className="cell-sub">{t("baselineSub")}</span>
            </th>
            <th scope="col" className="num">
              {t("lastSeen")}
            </th>
            <th scope="col">{t("tracking")}</th>
            <th scope="col">{t("state")}</th>
          </tr>
        </thead>
        <tbody>
          {entities.map((e) => (
            <tr key={e.id}>
              <td className="cell-name">
                <span className="ips-entity">{entityLabel(e, i18n)}</span>
              </td>
              <td className="num">
                <BaselineCell e={e} />
              </td>
              <td className="num">
                <Utc value={e.last_seen_at} bare />
                <span className="cell-sub">
                  {t("firstSeen")} <Utc value={e.first_seen_at} bare />
                </span>
              </td>
              <td>
                {e.retired_at ? (
                  <>
                    <span className="pill pill-quiet warn">{t("retired")}</span>
                    <span className="cell-sub">
                      {t.rich("retiredAt", { time: () => <Utc value={e.retired_at as string} /> })}
                    </span>
                  </>
                ) : e.active ? (
                  t("active")
                ) : (
                  <span className="pill pill-quiet">{t("inactive")}</span>
                )}
              </td>
              <td>
                {e.state ? (
                  <span
                    className={`pill pill-quiet ${stateTone(e.state)}${e.state === "INSUFFICIENT_DATA" ? " muted" : ""}`}
                  >
                    {ipsStateWord(e.state, i18n)}
                  </span>
                ) : (
                  <>
                    <span className="muted">{t("notEvaluated")}</span>
                    <span className="cell-sub">{t("noDetector")}</span>
                  </>
                )}
                {e.active_anomaly_count > 0 && (
                  <span className="cell-sub">
                    <Link href={`/anomalies?ips_entity_id=${encodeURIComponent(e.id)}`}>
                      {t("activeAnomalies", { count: e.active_anomaly_count })}
                    </Link>
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
