"use client";

// The cadence table: one row per search family.
//
// Each row changes its own cadence with a PUT and reports the outcome on the
// row. Permissions are never judged here: the API answers, and a 403 reverts
// the select and says which permission is missing.

import { useTranslations } from "next-intl";
import { useState } from "react";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { TableScroll } from "@/components/ui/TableScroll";
import { api, type SearchCadenceItem } from "@/lib/api";
import { plainCount } from "@/lib/figureText";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import {
  cadenceLabel,
  changeFailure,
  familyInfo,
  formatBucket,
  outcomeInfo,
  worstCaseDelay,
} from "@/lib/searchCadence";
import { UtcTime } from "@/components/ui/UtcTime";

import "@/components/ui/controls.css";

function Outcome({ value }: { value: string }) {
  const info = outcomeInfo(value, useBehaviorI18n());
  if (info === null) {
    // An outcome this build does not know is shown as recorded, not guessed at.
    return (
      <span className="cell-sub">
        <code>{value}</code>
      </span>
    );
  }
  return (
    <span className="cell-sub">
      <span className={`pill pill-quiet ${info.tone === "neutral" ? "" : info.tone}`}>
        {info.label}
      </span>
    </span>
  );
}

function CadenceRow({ initial }: { initial: SearchCadenceItem }) {
  const t = useTranslations("cadence.table");
  const i18n = useBehaviorI18n();
  const [item, setItem] = useState(initial);
  const [pending, setPending] = useState<number | null>(null);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });

  const info = familyInfo(item.family, i18n);
  const delay = worstCaseDelay(
    item.cadence_seconds,
    item.bucket_seconds,
    item.settle_seconds,
    i18n,
  );
  const lastRun = item.last_run;

  async function change(value: string) {
    const next = Number(value);
    // Each change is an audited write; a second one must wait for the first.
    if (pending !== null || next === item.cadence_seconds) return;
    setPending(next);
    setOutcome({ kind: "pending" });
    try {
      const updated = await api.setSearchCadence(item.family, next);
      setItem(updated);
      const time = () => <UtcTime value={updated.updated_at} />;
      setOutcome({
        kind: "ok",
        text: updated.updated_by
          ? t.rich("savedBy", { user: updated.updated_by, time })
          : t.rich("saved", { time }),
      });
    } catch (err) {
      setOutcome(
        changeFailure(err, t("forbidden"), i18n),
      );
    } finally {
      setPending(null);
    }
  }

  const hasStatus = outcome.kind !== "idle";

  return (
    <>
      <tr className={hasStatus ? "has-status" : undefined}>
        <td className="cell-family">
          {info.name}
          {info.scope && <span className="cell-sub">{info.scope}</span>}
        </td>
        <td className="cadence-cell">
          <select
            className="select-control"
            aria-label={t("cadenceFor", { name: info.name })}
            value={pending ?? item.cadence_seconds}
            disabled={pending !== null}
            aria-busy={pending !== null}
            onChange={(e) => void change(e.target.value)}
          >
            {item.allowed_cadences_seconds.map((s) => (
              <option key={s} value={s}>
                {cadenceLabel(s, i18n)}
              </option>
            ))}
          </select>
        </td>
        <td className="num">
          {item.bucket_seconds == null ? (
            <span className="muted" title={t("noBucketsTitle")}>
              {formatBucket(null)}
            </span>
          ) : (
            formatBucket(item.bucket_seconds, i18n)
          )}
        </td>
        <td className="num">
          <span className="nowrap">{delay.text}</span>
        </td>
        <td className="num">
          {lastRun ? (
            <>
              <UtcTime value={lastRun.at} variant="compact" className="nowrap" />
              {lastRun.outcome && <Outcome value={lastRun.outcome} />}
            </>
          ) : (
            <span className="muted">{t("notRun")}</span>
          )}
        </td>
        <td className="num">
          <UtcTime value={item.next_dispatch_at} variant="compact" className="nowrap" />
        </td>
        <td className="num">
          {lastRun === null ? (
            <span className="muted">{t("noRunYet")}</span>
          ) : lastRun.searches_sent === null ? (
            <span className="muted" title={t("notCountedTitle")}>
              {t("notCounted")}
            </span>
          ) : (
            plainCount(lastRun.searches_sent, i18n.locale)
          )}
        </td>
      </tr>
      {hasStatus && (
        <tr className="status-row">
          <td colSpan={7}>
            <ChangeStatus outcome={outcome} />
          </td>
        </tr>
      )}
    </>
  );
}

export function SearchCadenceTable({ items }: { items: SearchCadenceItem[] }) {
  const t = useTranslations("cadence.table");
  return (
    <>
      <TableScroll label={t("label")}>
        <table className="table-dense">
          <caption className="sr-only">{t("caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("family")}</th>
              <th scope="col">{t("cadence")}</th>
              <th scope="col" className="num">
                {t("bucket")}
                <span className="cell-sub">{t("noBuckets")}</span>
              </th>
              <th scope="col" className="num">
                {t("worstCase")}
                <span className="cell-sub">{t("formula")}</span>
              </th>
              <th scope="col" className="num">
                {t("lastRun")}
              </th>
              <th scope="col" className="num">
                {t("nextRun")}
              </th>
              <th scope="col" className="num">
                {t("searchesSent")}
              </th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <CadenceRow key={item.family} initial={item} />
            ))}
          </tbody>
        </table>
      </TableScroll>

      <ul className="table-notes" aria-label={t("notesLabel")}>
        <li>{t("noteCadence")}</li>
        <li>{t("noteExplanation")}</li>
        <li>{t("noteMissing")}</li>
      </ul>
    </>
  );
}
