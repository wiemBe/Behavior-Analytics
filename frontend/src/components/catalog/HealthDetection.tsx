"use client";

// Detection health (UR.5.1): is detection coverage holding up? Technique coverage leads, the
// rule verdicts follow. Each read stands on its own: a refused or failed one leaves its half at
// an em dash with the reason. Coverage with no evaluated technique is "not evaluated", never
// "0 % covered".

import Link from "next/link";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { healthTone } from "@/lib/health";
import { NOT_MEASURED } from "@/lib/behavior";
import { secondary, WidgetFrame, WidgetLoading } from "@/components/home/WidgetFrame";
import { Unmeasured } from "@/components/home/WidgetParts";
import { formatCount } from "@/lib/format";

import { detectionView, ruleHealthView } from "./derive";

import "./catalog.css";

// One case for a verdict word: the coverage page's, upper case with the underscore read as a space.
const word = (s: string) => s.replace(/_/g, " ");
const n = (v: number) => formatCount(v);

export function HealthDetection() {
  const coverageRead = homeReads.coverageSummary();
  const rulesRead = homeReads.ruleHealthSummary();
  const coverage = useApiResource(coverageRead.key, coverageRead.fetch);
  const rules = useApiResource(rulesRead.key, rulesRead.fetch);

  return (
    <WidgetFrame
      id="health-detection"
      title="Detection health"
      link={{ href: "/coverage", label: "Coverage" }}
      state={coverage}
      what="the detection coverage"
      permission="read:coverage"
    >
      {({ data }) => {
        const r = secondary(rules);
        if (r.pending) return <WidgetLoading what="the rule health" />;
        const view = detectionView(data);
        const rv = r.data ? ruleHealthView(r.data) : null;
        return (
          <>
            {view.coveredPercent === null ? (
              <p className="tw-state">
                <strong>Coverage has not been evaluated.</strong> No technique is mapped yet, so
                this is not a finding of zero coverage.
              </p>
            ) : (
              <>
                <p className="cw-figure num">
                  {view.coveredPercent.toFixed(1)} %
                  <span className="cw-unit">
                    {n(view.covered)} of {n(view.total)} techniques covered
                  </span>
                </p>
                {view.others.length > 0 ? (
                  <p className="tw-note">
                    Other statuses:{" "}
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
              <caption className="sr-only">Rule health by verdict</caption>
              <colgroup>
                <col />
                <col className="tw-col-count" />
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">Rule verdict</th>
                  <th scope="col" className="num">
                    Rules
                  </th>
                </tr>
              </thead>
              <tbody>
                {rv === null ? (
                  <tr>
                    <td>All rules</td>
                    <td className="num">
                      <Unmeasured reason={`Rule health is unavailable: ${r.gap}`} />
                    </td>
                  </tr>
                ) : rv.total === 0 ? (
                  <tr>
                    <td>All rules</td>
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
                Rule health is unavailable ({r.gap}).{" "}
                <Link className="tw-link" href="/rules">
                  Rules
                </Link>
              </p>
            ) : rv.total === 0 ? (
              <p className="tw-note">No rule has been synced, so no rule verdict exists.</p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
