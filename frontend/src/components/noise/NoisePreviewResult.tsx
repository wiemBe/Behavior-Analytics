// The replay's answer, as the form's last preview left it.
//
// COMPLETE and PARTIAL show a two-column comparison, current M against proposed N, per detector and
// in total. A detector the run's direction excluded says "not run (direction)": it is neither a 0
// nor a bare dash. INSUFFICIENT_DATA and TOO_LARGE carry no count at all. The recorded history is a
// separate, labelled line and is never presented as M. When the form has moved on since the preview,
// the whole block is dimmed and says so.

import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { useEnumLabels } from "@/i18n/enumLabels";
import type { NoisePreview, NoisePreviewDetector } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";

import "@/components/ui/controls.css";
import "./noise.css";

function Cell({ row, locale }: { row: NoisePreviewDetector | undefined; locale: FormatLocale }) {
  const t = useTranslations("noise.result");
  if (!row) return <span className="muted">{t("notRun")}</span>;
  if (row.excluded_by_direction || row.incidents === null) {
    return <span className="muted">{t("notRun")}</span>;
  }
  return <>{formatCount(row.incidents, locale)}</>;
}

function Change({
  current,
  proposed,
  locale,
}: {
  current: number | null;
  proposed: number | null;
  locale: FormatLocale;
}) {
  const t = useTranslations("noise.result");
  if (current === null || proposed === null) return <span className="muted">—</span>;
  const delta = proposed - current;
  if (delta === 0) return <span className="muted">{t("noChange")}</span>;
  return <>{`${delta > 0 ? "+" : "−"}${formatCount(Math.abs(delta), locale)}`}</>;
}

function Comparison({ preview }: { preview: NoisePreview }) {
  const t = useTranslations("noise.result");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const { label } = useEnumLabels();
  const current = preview.current;
  const proposed = preview.proposed;
  if (!current || !proposed) return null;
  const types = [
    ...new Set([...current.by_detector, ...proposed.by_detector].map((d) => d.anomaly_type)),
  ];
  const find = (rows: NoisePreviewDetector[], type: string) => rows.find((d) => d.anomaly_type === type);
  return (
    <TableScroll label={t("tableLabel")}>
      <table className="table-dense noise-table noise-compare">
        <caption className="sr-only">{t("tableCaption")}</caption>
        <thead>
          <tr>
            <th scope="col">{t("detector")}</th>
            <th scope="col" className="num">
              {t("current")}
              <span className="cell-sub">{t("currentSub")}</span>
            </th>
            <th scope="col" className="num">
              {t("proposed")}
              <span className="cell-sub">{t("proposedSub")}</span>
            </th>
            <th scope="col" className="num">
              {t("change")}
              <span className="cell-sub">{t("changeSub")}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {types.map((type) => {
            const c = find(current.by_detector, type);
            const p = find(proposed.by_detector, type);
            return (
              <tr key={type}>
                <th scope="row">{label("anomalyType", type)}</th>
                <td className="num">
                  <Cell row={c} locale={locale} />
                </td>
                <td className="num">
                  <Cell row={p} locale={locale} />
                </td>
                <td className="num">
                  <Change current={c?.incidents ?? null} proposed={p?.incidents ?? null} locale={locale} />
                </td>
              </tr>
            );
          })}
          <tr className="noise-total-row">
            <th scope="row">{t("total")}</th>
            <td className="num">{formatCount(current.total_incidents, locale)}</td>
            <td className="num">{formatCount(proposed.total_incidents, locale)}</td>
            <td className="num">
              <Change current={current.total_incidents} proposed={proposed.total_incidents} locale={locale} />
            </td>
          </tr>
        </tbody>
      </table>
    </TableScroll>
  );
}

export function NoisePreviewResult({
  preview,
  stale,
  storedCriticality,
  onUseSevenDays,
}: {
  preview: NoisePreview;
  storedCriticality?: string;
  stale: boolean;
  onUseSevenDays?: () => void;
}) {
  const t = useTranslations("noise.result");
  const locale: FormatLocale = useLocale() === "tr" ? "tr" : "en";
  const { label } = useEnumLabels();
  const eff = preview.effective;
  const days = preview.window.days;
  const covered = preview.window.covered_days;
  const counted = preview.state === "COMPLETE" || preview.state === "PARTIAL";
  // The backend repeats a direction-disabled detector in `not_replayable`; its row already says so.
  const notReplayable = preview.not_replayable.filter(
    (n) => n.reason.code !== "noise.preview.direction_disabled",
  );
  return (
    <section
      className={stale ? "noise-result stale" : "noise-result"}
      aria-labelledby="noise-result-title"
      data-state={preview.state}
    >
      <h3 id="noise-result-title" className="noise-result-title">
        {t("title")}
      </h3>
      {stale && (
        <p className="banner warn" role="status">
          {t("stale")}
        </p>
      )}
      {counted && preview.window.first_bucket && preview.window.last_bucket ? (
        <p className="noise-result-line">
          {t.rich("coverage", {
            days,
            covered: formatCount(covered, locale),
            first: () => <UtcTime value={preview.window.first_bucket} />,
            last: () => <UtcTime value={preview.window.last_bucket} />,
          })}
        </p>
      ) : counted ? (
        <p className="noise-result-line">{t("coverageNone", { days })}</p>
      ) : null}
      {counted && preview.baseline.baseline_version !== null && preview.baseline.computed_at && (
        <p className="noise-result-line muted">
          {t.rich("baseline", {
            version: preview.baseline.baseline_version,
            time: () => <UtcTime value={preview.baseline.computed_at} />,
          })}
        </p>
      )}
      {preview.state === "PARTIAL" && (
        <div className="banner warn" role="status">
          <p>
            <strong>{t("partialTitle")}</strong>{" "}
            {preview.reason?.text ?? t("partialText", { covered: formatCount(covered, locale), days })}{" "}
            {t("partialNote")}
          </p>
        </div>
      )}
      {preview.state === "INSUFFICIENT_DATA" && (
        <div className="notice" role="status">
          <p>
            <strong>{t("insufficient.title")}</strong>
          </p>
          <p>{preview.reason?.text ?? t("insufficient.fallback")}</p>
          <p>{t("insufficient.text")}</p>
        </div>
      )}
      {preview.state === "TOO_LARGE" && (
        <div className="notice" role="status">
          <p>
            <strong>{t("tooLarge.title")}</strong>
          </p>
          <p>{preview.reason?.text}</p>
          <p>{t("tooLarge.text")}</p>
          {onUseSevenDays && days !== 7 && (
            <p>
              <button type="button" className="action secondary" onClick={onUseSevenDays}>
                {t("tooLarge.useSeven")}
              </button>
            </p>
          )}
        </div>
      )}
      {counted && eff && eff.lab_mode && storedCriticality !== undefined && eff.criticality !== storedCriticality && (
        <p className="noise-result-line">{t("criticalityLab")}</p>
      )}
      {counted && eff && !eff.lab_mode && storedCriticality !== undefined && eff.criticality !== storedCriticality && (
        <p className="noise-result-line">
          {t("criticalityReplayed", {
            used: label("severity", eff.criticality),
            stored: label("severity", storedCriticality),
          })}
        </p>
      )}
      {counted && <Comparison preview={preview} />}
      {counted && <p className="noise-footnote">{t("explain")}</p>}
      {preview.recorded && (
        <p className="noise-result-line noise-recorded">
          {t("recorded", {
            incidents: preview.recorded.incidents,
            opened: formatCount(preview.recorded.opened, locale),
            suppressed: formatCount(preview.recorded.suppressed, locale),
          })}
        </p>
      )}
      {counted && preview.not_judged_buckets !== null && preview.not_judged_buckets > 0 && (
        <p className="noise-result-line">{t("unjudged", { count: preview.not_judged_buckets })}</p>
      )}
      {counted && preview.maintenance_buckets !== null && preview.maintenance_buckets > 0 && (
        <p className="noise-result-line">{t("maintenanceBuckets", { count: preview.maintenance_buckets })}</p>
      )}
      {preview.warnings.map((w) => (
        <p key={w.code + w.text} className="banner warn" role="status">
          {w.text}
        </p>
      ))}
      {notReplayable.length > 0 && (
        <div className="noise-not-replayed">
          <h4>{t("notReplayableTitle")}</h4>
          <p className="noise-footnote">{t("notReplayableNote")}</p>
          <ul>
            {notReplayable.map((n) => (
              <li key={n.anomaly_type}>
                <strong>{label("anomalyType", n.anomaly_type)}</strong> {n.reason.text}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
