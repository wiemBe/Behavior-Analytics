"use client";

// Detection health (UR.5.1): is detection coverage holding up? Technique coverage leads, the
// rule verdicts follow. Each read stands on its own: a refused or failed one leaves its half at
// an em dash with the reason. Coverage with no evaluated technique is "not evaluated", never
// "0 % covered".

import { useTranslations } from "next-intl";
import Link from "next/link";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";
import { statusLabel } from "@/lib/coverageText";
import { useFleetText } from "@/lib/fleetText";
import { healthTone } from "@/lib/health";
import { NOT_MEASURED } from "@/lib/behavior";
import { secondary, WidgetFrame, WidgetLoading } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { formatCount, formatPercent } from "@/lib/format";

import { detectionView, ruleHealthView } from "./derive";

import "./catalog.css";

export function HealthDetection() {
  const t = useTranslations("catalog.detection");
  const text = useFleetText();
  const say = asTextTranslator(useTranslations());
  const n = (v: number) => formatCount(v, text.locale);
  // One case for a verdict word: the coverage page's (`coverage.status`).
  const word = (s: string) => statusLabel(say, s);
  const coverageRead = homeReads.coverageSummary();
  const rulesRead = homeReads.ruleHealthSummary();
  const coverage = useApiResource(coverageRead.key, coverageRead.fetch);
  const rules = useApiResource(rulesRead.key, rulesRead.fetch);

  return (
    <WidgetFrame
      id="health-detection"
      title={t("title")}
      link={{ href: "/coverage", label: t("link") }}
      state={coverage}
      what={t("what")}
      permission="read:coverage"
    >
      {({ data }) => {
        const r = secondary(rules, say);
        if (r.pending) return <WidgetLoading what={t("ruleHealthWhat")} />;
        const view = detectionView(data);
        const rv = r.data ? ruleHealthView(r.data) : null;
        return (
          <>
            {view.coveredPercent === null ? (
              <p className="tw-state">
                {t.rich("notEvaluated", { b: (chunks) => <strong>{chunks}</strong> })}
              </p>
            ) : (
              <>
                <p className="cw-figure num">
                  {formatPercent(view.coveredPercent / 100, null, 1, text.locale)}
                  <span className="cw-unit">
                    {t("covered", { covered: n(view.covered), total: n(view.total) })}
                  </span>
                </p>
                {view.others.length > 0 ? (
                  <p className="tw-note">
                    {t("others")}{" "}
                    {view.others.map((o, i) => (
                      <span key={o.status} className="num">
                        {i > 0 ? " · " : ""}
                        {word(o.status)} {n(o.count)}
                      </span>
                    ))}
                  </p>
                ) : null}
              </>
            )}
            <table className="tw-table cw-table">
              <caption className="sr-only">{t("caption")}</caption>
              <colgroup>
                <col />
                <col className="tw-col-count" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">{t("verdict")}</th>
                  <th scope="col" className="num">
                    {t("rules")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {rv === null ? (
                  <tr>
                    <td>{t("allRules")}</td>
                    <td className="num">
                      <Unmeasured reason={t("unavailable", { gap: r.gap ?? "" })} />
                    </td>
                  </tr>
                ) : rv.total === 0 ? (
                  <tr>
                    <td>{t("allRules")}</td>
                    <td className="num">{NOT_MEASURED}</td>
                  </tr>
                ) : (
                  <>
                    <tr>
                      <td>
                        <span className={`tw-state-word ${healthTone("HEALTHY")}`}>{word("HEALTHY")}</span>
                      </td>
                      <td className="num">{n(rv.healthy)}</td>
                    </tr>
                    {rv.others.map((o) => (
                      <tr key={o.status}>
                        <td>
                          <span className={`tw-state-word ${healthTone(o.status)}`}>
                            {word(o.status)}
                          </span>
                        </td>
                        <td className="num">{n(o.count)}</td>
                      </tr>
                    ))}
                  </>
                )}
              </tbody>
            </table>
            {rv === null ? (
              <p className="tw-note">
                {t.rich("unavailableNote", {
                  gap: r.gap ?? "",
                  link: (chunks) => (
                    <Link className="tw-link" href="/rules">
                      {chunks}
                    </Link>
                  ),
                })}
              </p>
            ) : rv.total === 0 ? (
              <p className="tw-note">{t("noRules")}</p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
