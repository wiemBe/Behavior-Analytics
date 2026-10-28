// Every recorded lifecycle transition, as a vertical timeline.
//
// Nothing is filtered. "This incident flapped CANDIDATE/NORMAL six times
// before opening" is a fact about detector tuning, and a view showing only the
// transitions leading to the current state hides exactly the evidence that the
// thresholds need adjusting. Holds caused by incomplete data are shown too.
//
// The nine-column table this replaces spent four of those columns on the same
// value repeated down every row — actor and policy version — so those move
// into a per-transition disclosure where they are still one interaction away.

import { useTranslations } from "next-intl";

import { Disclosure } from "@/components/ui/Disclosure";
import { useBackendMessage } from "@/i18n/backendMessage";
import { useEnumLabels } from "@/i18n/enumLabels";
import type { AnomalyTransition } from "@/lib/api";
import { ARROW, versionLabel } from "@/lib/behaviorI18n";
import { formatMetric, stateTone } from "@/lib/behavior";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";
import { UtcTime } from "@/components/ui/UtcTime";


export function LifecycleTimeline({
  transitions,
  policyVersion,
  stateLabel,
}: {
  transitions: AnomalyTransition[];
  policyVersion: number | null;
  /** How a state is worded. Defaults to the state's label; WAF pages pass a word. */
  stateLabel?: (state: string) => string;
}) {
  const t = useTranslations("anomalies.lifecycle");
  const { locale } = useBehaviorI18n();
  const { label } = useEnumLabels();
  const backendMessage = useBackendMessage();
  const wording = stateLabel ?? ((state: string) => label("anomalyState", state));
  if (transitions.length === 0) {
    return <div className="notice">{t("empty")}</div>;
  }

  return (
    <ol className="lifecycle">
      {transitions.map((tr, i) => (
        <li key={`${tr.occurred_at}-${tr.to_state}-${i}`} className="lifecycle-item">
          <div className="lifecycle-marker" aria-hidden="true" />
          <div className="lifecycle-body">
            <div className="row lifecycle-states">
              {/* The first transition has no previous state. That is an
                  origin, not an unknown, and is labelled as such. */}
              {tr.from_state ? (
                <span className={`pill pill-quiet ${stateTone(tr.from_state)}`}>
                  {wording(tr.from_state)}
                </span>
              ) : (
                <span className="muted">{t("initial")}</span>
              )}
              <span className="muted" aria-hidden="true">
                {ARROW}
              </span>
              <span className={`pill ${stateTone(tr.to_state)}`}>{wording(tr.to_state)}</span>
              <UtcTime value={tr.occurred_at} className="muted num" />
            </div>

            <p className="lifecycle-reason">
              {tr.reason ? (
                backendMessage({ text: tr.reason, code: tr.reason_code, params: tr.reason_params })
              ) : (
                <span className="muted">{t("noReason")}</span>
              )}
            </p>

            <Disclosure summary={t("detail")}>
              <dl className="dimension-meta">
                <div>
                  <dt>{t("bucket")}</dt>
                  <dd className="num">{<UtcTime value={tr.bucket_start} />}</dd>
                </div>
                <div>
                  <dt>{t("observed")}</dt>
                  <dd className="num">{formatMetric(tr.observed_value, 2, locale)}</dd>
                </div>
                <div>
                  <dt>{t("expected")}</dt>
                  <dd className="num">{formatMetric(tr.expected_value, 2, locale)}</dd>
                </div>
                <div>
                  <dt>{t("actor")}</dt>
                  <dd>{tr.actor}</dd>
                </div>
                <div>
                  <dt>{t("policyVersion")}</dt>
                  <dd className="num">
                    {policyVersion != null ? versionLabel(policyVersion) : "—"}
                  </dd>
                </div>
              </dl>
            </Disclosure>
          </div>
        </li>
      ))}
    </ol>
  );
}
