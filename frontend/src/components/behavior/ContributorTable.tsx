"use client";

// One dimension's contributor analysis.
//
// Five columns by default — value, baseline, anomaly, delta, contribution —
// which are the ones that answer "what changed and by how much". The other
// six the API returns (percent delta, both shares, both ranks, cardinality
// ratio) are one keystroke away per row rather than permanently on screen:
// they matter when interrogating a specific value, not when scanning a list.
//
// The critical behaviour is what happens when a dimension is *not* AVAILABLE.
// The panel still renders, with an explicit statement that the field was never
// checked. Hiding it would leave an analyst to conclude from its absence that
// the dimension was examined and found clean — the exact inversion of the
// truth.

import { useTranslations } from "next-intl";
import { Fragment, useState } from "react";

import { TableScroll } from "@/components/ui/TableScroll";
import type { ExplanationDimension } from "@/lib/api";
import {
  contributorsAreShowable,
  dimensionLabel,
  dimensionMeaningIn,
  dimensionTone,
  formatCount,
  formatDelta,
  formatPercentDelta,
  formatRatio,
  formatShare,
  newAndDisappearedAreDeterminate,
} from "@/lib/behavior";
import { ARROW, availabilityCode } from "@/lib/behaviorI18n";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { useBackendMessage } from "@/i18n/backendMessage";

export function ContributorTable({ dimension }: { dimension: ExplanationDimension }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const t = useTranslations("anomalies.contributors");
  const i18n = useBehaviorI18n();
  const backendMessage = useBackendMessage();
  const { locale } = i18n;

  const label = dimensionLabel(dimension.dimension, i18n);
  const checked = contributorsAreShowable(dimension.availability);
  // Truncation makes the counts an artifact of the cap rather than a finding,
  // so they are withheld even though the contributor rows are shown.
  const countsDeterminate = newAndDisappearedAreDeterminate(dimension.availability);

  const lead = `${availabilityCode(i18n, dimension.availability)}.`;

  const countCell = (value: number | null) =>
    countsDeterminate ? (
      formatCount(value, locale)
    ) : (
      <span className="muted">{t("indeterminate")}</span>
    );

  return (
    <section aria-label={t("panel", { label })} className="dimension-panel">
      <div className="row dimension-panel-head">
        <h4>{label}</h4>
        <span className={`pill ${dimensionTone(dimension.availability)}`}>
          {availabilityCode(i18n, dimension.availability)}
        </span>
      </div>
      <p className="subtitle">{dimensionMeaningIn(dimension.availability, i18n)}</p>

      {dimension.detail && (
        <p className="subtitle">
          {/* Backend-sanitized: a reason, never a stack trace. */}
          <strong>{t("reason")}</strong>{" "}
          {backendMessage({
            text: dimension.detail,
            code: dimension.detail_code,
            params: dimension.detail_params,
          })}
        </p>
      )}

      {/* Compact metadata. Under a cap the cardinality and new/disappeared
          figures are artifacts of the limit rather than observations, so they
          are named indeterminate rather than shown as the backend's zeroes. */}
      <dl className="dimension-meta">
        <div>
          <dt>{t("baselineCardinality")}</dt>
          <dd className="num">{countCell(dimension.baseline_distinct_count)}</dd>
        </div>
        <div>
          <dt>{t("anomalyCardinality")}</dt>
          <dd className="num">{countCell(dimension.anomaly_distinct_count)}</dd>
        </div>
        <div>
          <dt>{t("concentration")}</dt>
          <dd className="num">
            {formatShare(dimension.baseline_top_share, locale)} {ARROW}{" "}
            {formatShare(dimension.anomaly_top_share, locale)}
          </dd>
        </div>
        <div>
          <dt>{t("newValues")}</dt>
          <dd className="num">{countCell(dimension.new_value_count)}</dd>
        </div>
        <div>
          <dt>{t("disappearedValues")}</dt>
          <dd className="num">{countCell(dimension.disappeared_value_count)}</dd>
        </div>
      </dl>

      {!checked ? (
        // No empty table: an unavailable or failed dimension has nothing to
        // show, and an empty table would read as "checked, nothing found".
        <p className={`banner ${dimension.availability === "FAILED" ? "crit" : ""}`}>
          <span>
            <strong>{lead}</strong>{" "}
            {dimension.availability === "FAILED" ? t("queryFailed") : t("notPopulated")}
          </span>
        </p>
      ) : dimension.contributors.length === 0 ? (
        <div className="notice">{t("noneStandOut")}</div>
      ) : (
        <>
          {dimension.truncated && (
            <p className="dimension-truncation">{t("truncated")}</p>
          )}
          {/* Distinct from the section's own label: two nested regions with
              the same accessible name are indistinguishable when navigating
              landmarks. */}
          <TableScroll label={t("rows", { label })}>
            <table>
              <caption className="sr-only">
                {t("caption", { label })}
              </caption>
              <thead>
                <tr>
                  <th scope="col">{t("value")}</th>
                  <th scope="col" className="num">
                    {t("baselineEvents")}
                  </th>
                  <th scope="col" className="num">
                    {t("anomalyEvents")}
                  </th>
                  <th scope="col" className="num">
                    {t("deltaEvents")}
                  </th>
                  <th scope="col" className="num">
                    {t("contribution")}
                  </th>
                  <th scope="col">
                    <span className="sr-only">{t("rowDetail")}</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {dimension.contributors.map((c) => {
                  const key = `${c.dimension}:${c.value}`;
                  const open = expanded === key;
                  const name = c.label ?? c.value;
                  return (
                    <Fragment key={key}>
                      <tr>
                        <td>
                          {/* Values come from QRadar. Rendered as text, never
                              as markup. */}
                          {c.label ? (
                            <>
                              {c.label} <span className="muted">({c.value})</span>
                            </>
                          ) : (
                            c.value
                          )}{" "}
                          {c.is_new && <span className="pill pill-quiet warn">{t("new")}</span>}
                          {c.is_disappeared && (
                            <span className="pill pill-quiet warn">{t("disappeared")}</span>
                          )}
                        </td>
                        <td className="num">{formatCount(c.baseline_count, locale)}</td>
                        <td className="num">{formatCount(c.anomaly_count, locale)}</td>
                        <td className="num">{formatDelta(c.absolute_delta, locale)}</td>
                        <td className="num">
                          {/* Magnitude: the share is signed on a drop, and
                              "-95.9%" is not how a reduction is described. */}
                          {formatShare(
                            c.contribution_share == null
                              ? null
                              : Math.abs(c.contribution_share),
                            locale,
                          )}
                        </td>
                        <td>
                          <button
                            type="button"
                            className="row-toggle"
                            aria-expanded={open}
                            aria-label={t(open ? "hideDetail" : "showDetail", { name })}
                            onClick={() => setExpanded(open ? null : key)}
                          >
                            {open ? t("less") : t("more")}
                          </button>
                        </td>
                      </tr>
                      {open && (
                        <tr className="row-detail">
                          <td colSpan={6}>
                            <dl className="dimension-meta">
                              <div>
                                <dt>{t("percentDelta")}</dt>
                                {/* null for a new value: there is no baseline
                                    to be a percentage of, and "0%" would
                                    assert no change. */}
                                <dd className="num">
                                  {formatPercentDelta(c.percent_delta, locale)}
                                </dd>
                              </div>
                              <div>
                                <dt>{t("baselineShare")}</dt>
                                <dd className="num">{formatShare(c.baseline_share, locale)}</dd>
                              </div>
                              <div>
                                <dt>{t("anomalyShare")}</dt>
                                <dd className="num">{formatShare(c.anomaly_share, locale)}</dd>
                              </div>
                              <div>
                                <dt>{t("rank")}</dt>
                                <dd className="num">
                                  {c.baseline_rank ?? "—"} {ARROW} {c.anomaly_rank ?? "—"}
                                </dd>
                              </div>
                              <div>
                                <dt>{t("cardinalityRatio")}</dt>
                                <dd className="num">
                                  {formatRatio(dimension.cardinality_ratio, locale)}
                                </dd>
                              </div>
                            </dl>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </TableScroll>
        </>
      )}
    </section>
  );
}
