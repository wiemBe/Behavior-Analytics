// One row per IPS source: can it be judged, how whole is its latest bucket, how close is each
// entity dimension to its ceiling, and did admission overflow in that bucket.
//
// Sources that need attention are listed first. Every figure that can be unmeasured says so in
// words: a source with no collected bucket has no latest figures and no overflow figure, which is
// not "0" and not "none".

import { useTranslations } from "next-intl";
import Link from "next/link";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { IpsAdmission, IpsSourceBehavior } from "@/lib/api";
import { NOT_MEASURED, formatCount } from "@/lib/behavior";
import { wordOrValue } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { ActiveAnomaliesCell } from "./IpsActiveAnomalies";
import {
  admissionNotMeasured,
  dimensionAtCeiling,
  dimensionOverflows,
  groupByMeasurement,
  ipsValidationView,
  isOverflowing,
  sortByAttention,
} from "./ipsSourceHealth";

import "@/components/ui/controls.css";
import "./ips.css";

function Unmeasured({ meaning }: { meaning: string }) {
  return (
    <span className="muted">
      {NOT_MEASURED}
      <span className="sr-only"> {meaning}</span>
    </span>
  );
}

function NoBucket() {
  const t = useTranslations("ips.behavior");
  return (
    <>
      <Unmeasured meaning={t("noBucketMeaning")} />
      <span className="cell-sub">{t("noBucketYet")}</span>
    </>
  );
}

function LatestCell({ s }: { s: IpsSourceBehavior }) {
  const t = useTranslations("ips.behavior");
  const { locale } = useBehaviorI18n();
  const b = s.latest_bucket;
  if (!b) return <NoBucket />;
  return (
    <>
      <UtcTime value={b.bucket_start} variant="compact" className="nowrap" />
      <span className="cell-sub">{t("events", { count: formatCount(b.total_events, locale) })}</span>
      {b.in_maintenance && <span className="cell-sub">{t("inMaintenance")}</span>}
      <DimensionLines s={s} />
    </>
  );
}

function DimensionLines({ s }: { s: IpsSourceBehavior }) {
  const t = useTranslations("ips.behavior");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const b = s.latest_bucket;
  if (!b) return null;
  const groups = groupByMeasurement(b.dimensions);
  const exact = groups.find((g) => g.measurement === "EXACT")?.dimensions.length ?? 0;
  const name = (d: string) => wordOrValue(i18n, "ips.behavior.dim", d);
  return (
    <>
      <span className="cell-sub ips-measurement">
        {t("exactOf", {
          exact: formatCount(exact, locale),
          total: formatCount(b.dimensions.length, locale),
        })}
      </span>
      {groups
        .filter((g) => g.measurement !== "EXACT")
        .map((g) => (
          <span key={g.measurement} className="cell-sub ips-measurement">
            {t("measurementLine", {
              label: wordOrValue(i18n, "ips.behavior.measurement", g.measurement),
              names: g.dimensions.map(name).join(", "),
            })}
          </span>
        ))}
    </>
  );
}

function PressureCell({ s }: { s: IpsSourceBehavior }) {
  const t = useTranslations("ips.behavior");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  return (
    <>
      {s.entities.map((p) => (
        <span key={p.dimension} className="ips-pressure">
          {t("pressure", {
            dimension: wordOrValue(i18n, "ips.behavior.dimShort", p.dimension),
            active: formatCount(p.active, locale),
            ceiling: formatCount(p.active_ceiling, locale),
          })}
          {dimensionAtCeiling(p) && <span className="cell-sub">{t("atCeiling")}</span>}
        </span>
      ))}
    </>
  );
}

function overflowParts(a: IpsAdmission, t: ReturnType<typeof useTranslations>, locale: "en" | "tr") {
  const parts: string[] = [];
  if ((a.rejected_over_quota ?? 0) > 0) {
    parts.push(t("refused", { count: formatCount(a.rejected_over_quota as number, locale) }));
  }
  if ((a.untracked_entities ?? 0) > 0) {
    parts.push(t("untrackedEntities", { count: formatCount(a.untracked_entities as number, locale) }));
  }
  if ((a.untracked_events ?? 0) > 0) {
    parts.push(t("untrackedEvents", { count: formatCount(a.untracked_events as number, locale) }));
  }
  if (a.admission_truncated === true) parts.push(t("truncated"));
  return parts;
}

function AdmissionCell({ s }: { s: IpsSourceBehavior }) {
  const t = useTranslations("ips.behavior");
  const i18n = useBehaviorI18n();
  const { locale } = i18n;
  const b = s.latest_bucket;
  if (!b) return <NoBucket />;
  if (admissionNotMeasured(s)) {
    return (
      <>
        <Unmeasured meaning={t("overflowNotMeasuredMeaning")} />
        <span className="cell-sub">{t("overflowNotMeasured")}</span>
      </>
    );
  }
  if (!isOverflowing(s)) return <span>{t("overflowNone")}</span>;
  return (
    <>
      <span className="pill pill-quiet warn">{t("overflowing")}</span>
      {b.admission.filter(dimensionOverflows).map((a) => (
        <span key={a.dimension} className="cell-sub">
          {t("overflowLine", {
            dimension: wordOrValue(i18n, "ips.behavior.dim", a.dimension),
            parts: overflowParts(a, t, locale).join(", "),
          })}
        </span>
      ))}
    </>
  );
}

export function IpsSourcesTable({ sources }: { sources: IpsSourceBehavior[] }) {
  const t = useTranslations("ips.behavior");
  const i18n = useBehaviorI18n();
  const rows = sortByAttention(sources);
  return (
    <>
      <TableScroll label={t("table.label")}>
        <table className="table-dense">
          <caption className="sr-only">{t("table.caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("table.source")}</th>
              <th scope="col">{t("table.validation")}</th>
              <th scope="col" className="num">
                {t("table.active")}
                <span className="cell-sub">{t("table.activeSub")}</span>
              </th>
              <th scope="col">
                {t("table.latest")}
                <span className="cell-sub">{t("table.latestSub")}</span>
              </th>
              <th scope="col" className="num">
                {t("table.entities")}
                <span className="cell-sub">{t("table.entitiesSub")}</span>
              </th>
              <th scope="col">
                {t("table.admission")}
                <span className="cell-sub">{t("table.admissionSub")}</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((s) => {
              const v = ipsValidationView(s, i18n);
              return (
                <tr key={s.log_source_id}>
                  <td className="cell-name">
                    <Link href={`/behavior/ips/sources/${s.log_source_id}`}>{s.log_source_name}</Link>
                    {s.product_label && <span className="cell-sub">{s.product_label}</span>}
                    <span className="cell-sub">
                      <Link href={`/admin/ips-sources/${s.log_source_id}`}>{t("profileSettings")}</Link>
                    </span>
                  </td>
                  <td>
                    <span className={`pill pill-quiet ${v.tone}`}>{v.label}</span>
                    <span className="cell-sub">{v.detail}</span>
                    <span className="cell-sub">
                      {s.enabled ? (
                        t("profileEnabled")
                      ) : (
                        <span className="pill pill-quiet warn">{t("profileDisabled")}</span>
                      )}
                    </span>
                    {s.enabled && !s.validation_current && (
                      <span className="cell-sub">{t("notCollected")}</span>
                    )}
                  </td>
                  <td className="num ips-active">
                    <ActiveAnomaliesCell s={s} />
                  </td>
                  <td>
                    <LatestCell s={s} />
                  </td>
                  <td className="num">
                    <PressureCell s={s} />
                  </td>
                  <td>
                    <AdmissionCell s={s} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      <p className="subtitle">{t("note")}</p>
    </>
  );
}
