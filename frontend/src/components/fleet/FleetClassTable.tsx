// One row per class of log source: how much it sent in the window, what that
// should have been, and how much of the window was actually measured.
//
// Every figure that can be missing is written so that missing reads as missing:
// "Not measured" for a class with no complete bucket, an em dash and its reason
// for an expectation with no baseline. Completeness sits in the row beside the
// figures it qualifies, not in a footnote.

import { useTranslations } from "next-intl";
import Link from "next/link";

import "@/components/ui/controls.css";

import { TableScroll } from "@/components/ui/TableScroll";
import type { FleetClassSummary } from "@/lib/api";
import {
  classLabel,
  completenessInfo,
  expectedInfo,
  formatEpsValue,
  formatEventCount,
  hasSources,
  openIncidentList,
} from "@/lib/fleet";
import { plainCount, useFleetText } from "@/lib/fleetText";
import { formatCount } from "@/lib/format";
import { unitGap } from "@/i18n/measures";

function Figure({ value, unit, why }: { value: string | null; unit?: string; why: string | null }) {
  const text = useFleetText();
  if (value === null) {
    return (
      <>
        <span className="muted">{text.say("fleet.notMeasured")}</span>
        {why && <span className="cell-sub">{why}</span>}
      </>
    );
  }
  return (
    <>
      {value}
      {unit && (
        <span className="unit">
          {unitGap(text.locale)}
          {unit}
        </span>
      )}
    </>
  );
}

function ClassRows({ c }: { c: FleetClassSummary }) {
  const t = useTranslations("fleet.classTable");
  const text = useFleetText();
  const { locale } = text;
  const eps = useTranslations("units")("eps");
  const completeness = completenessInfo(c.buckets, text);
  const expected = expectedInfo(c, text);
  const events = formatEventCount(c.events, locale);
  const mean = formatEpsValue(c.mean_eps, locale);
  const incidents = openIncidentList(c, text);
  const noComplete = completeness.state === "none" ? completeness.text : t("noCompleteBucket");
  const hasTop = c.top_sources.length > 0;

  return (
    <>
      <tr className={hasTop ? "fleet-main" : undefined}>
        <th scope="row" className="cell-class">
          <Link href={`/fleet/sources?class=${c.source_class}`}>
            {classLabel(c.source_class, text)}
          </Link>
          <span className="cell-sub">
            {hasSources(c)
              ? t("enabledDisabled", {
                  enabled: plainCount(c.sources.enabled, locale),
                  disabled: plainCount(c.sources.disabled, locale),
                })
              : t("noSources")}
          </span>
        </th>
        <td className="num">
          <Figure value={events} why={noComplete} />
          {events !== null && completeness.state === "incomplete" && (
            <span className="cell-sub">{t("completeOnly")}</span>
          )}
        </td>
        <td className="num">
          <Figure value={mean} unit={eps} why={null} />
        </td>
        <td className="num">
          {expected.value === null ? (
            <>
              <span className="muted" aria-label={t("noExpectation")}>
                —
              </span>
              <span className="cell-sub">{expected.note}</span>
            </>
          ) : (
            <>
              {expected.value}
              <span className="unit">
                {unitGap(locale)}
                {eps}
              </span>
              <span className="cell-sub">{expected.note}</span>
              {expected.partial && <span className="cell-sub">{t("coversPart")}</span>}
            </>
          )}
        </td>
        <td className="cell-completeness">
          {completeness.state === "incomplete" && (
            <span className="pill pill-quiet warn">{t("incomplete")}</span>
          )}
          <span className={completeness.state === "none" ? "muted" : undefined}>
            {completeness.text}
          </span>
          {completeness.detail && <span className="cell-sub">{completeness.detail}</span>}
          {c.buckets.complete_from_history > 0 && (
            <span className="cell-sub">
              {t("historyBuckets", {
                from: formatCount(c.buckets.complete_from_history, locale),
                complete: formatCount(c.buckets.complete, locale),
              })}
            </span>
          )}
        </td>
        <td>
          {incidents.length === 0 ? (
            <span className="muted">{t("noneOpen")}</span>
          ) : (
            <ul className="plain-list">
              {incidents.map(([label, n]) => (
                <li key={label}>
                  {label} <span className="num">{plainCount(n, locale)}</span>
                </li>
              ))}
            </ul>
          )}
        </td>
      </tr>
      {hasTop && (
        <tr className="fleet-top">
          <td colSpan={6}>
            <span className="fleet-top-label">{t("topSources")}</span>
            <ul className="fleet-top-list">
              {c.top_sources.map((s) => (
                <li key={s.id}>
                  <Link href={`/behavior/sources/${s.id}`}>{s.name}</Link>{" "}
                  <span className="num">{formatCount(s.events, locale)}</span>
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}

export function FleetClassTable({
  classes,
  windowName,
}: {
  classes: FleetClassSummary[];
  windowName: string;
}) {
  const t = useTranslations("fleet.classTable");
  return (
    <TableScroll label={t("label")}>
      <table className="fleet-table">
        <caption className="sr-only">{t("caption", { window: windowName })}</caption>
        <thead>
          <tr>
            <th scope="col">{t("class")}</th>
            <th scope="col" className="num">
              {t("events")}
            </th>
            <th scope="col" className="num">
              {t("meanEps")}
            </th>
            <th scope="col" className="num">
              {t("expectedEps")}
            </th>
            <th scope="col">{t("completeness")}</th>
            <th scope="col">{t("incidents")}</th>
          </tr>
        </thead>
        <tbody>
          {classes.map((c) => (
            <ClassRows key={c.source_class} c={c} />
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}
