"use client";

// The banner on an alert or anomaly that is already quiet (SOC-17d): until when, by whom, what was
// recorded meanwhile, and the alerts that broke through. It has its own loading, error and
// forbidden states and draws nothing when no quiet period holds the condition. "End now" asks
// first. An incident is always recorded; the copy never says otherwise.

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";

import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import { sevTone, suppressionApi, type SuppressionFor } from "@/lib/api";
import { enumLabel, type EnumTranslator } from "@/i18n/enumLabels";
import { asTextTranslator } from "@/i18n/textTranslator";
import { detectorGroup, spanDays, suppressFailure, type ReadFailure } from "@/lib/suppression";

import "./suppression.css";

export type BannerState = { kind: "loading" } | { kind: "ready"; data: SuppressionFor } | ReadFailure;

export function SuppressionBanner({
  state,
  onRetry,
  onEnded,
}: {
  state: BannerState;
  onRetry: () => void;
  /** After the quiet period was ended (or found gone): read the state again. */
  onEnded: (message: string) => Promise<void> | void;
}) {
  const t = useTranslations("suppression");
  const say = asTextTranslator(useTranslations());
  const enums = useTranslations("enums") as unknown as EnumTranslator;
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);
  const [outcome, setOutcome] = useState<ChangeOutcome>({ kind: "idle" });
  const endRef = useRef<HTMLButtonElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const restore = useRef(false);

  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
    else if (restore.current) {
      restore.current = false;
      endRef.current?.focus();
    }
  }, [confirming]);

  if (state.kind === "loading") {
    return (
      <p className="quiet-loading" role="status">
        {t("banner.loading")}
      </p>
    );
  }
  if (state.kind === "forbidden") {
    return (
      <div className="banner warn quiet-banner" role="status">
        <div className="quiet-body">
          <p>
            <strong>{t("banner.forbiddenTitle")}</strong>
          </p>
          <p>
            {state.permission
              ? t("banner.forbiddenHelp", { permission: state.permission })
              : t("banner.forbiddenHelpBoth")}
          </p>
        </div>
      </div>
    );
  }
  if (state.kind === "error") {
    return (
      <div className="banner crit quiet-banner" role="alert">
        <div className="quiet-body">
          <p>
            <strong>{t("banner.failedTitle")}</strong>
          </p>
          <p>{t("banner.failedHelp", { reason: state.text })}</p>
          <div className="actions">
            <button type="button" className="action secondary" onClick={onRetry}>
              {t("banner.retry")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  const { data } = state;
  const row = data.suppression;
  if (row === null || row.id === null) return null;
  const id = row.id;

  const days = spanDays(row);
  const detector =
    row.detector_type === null
      ? null
      : enumLabel(enums, detectorGroup(data.family), row.detector_type);
  const scope = detector ?? t("scope.everyDetectorOf", { name: row.subject_name ?? t("scope.removedSource") });

  async function end() {
    if (pending) return;
    setPending(true);
    setOutcome({ kind: "pending" });
    try {
      await suppressionApi.end(id);
      setConfirming(false);
      await onEnded(t("banner.ended"));
    } catch (err) {
      const failure = suppressFailure(err, say);
      if (failure.gone) {
        setConfirming(false);
        await onEnded(t("banner.endGone"));
      } else {
        setOutcome({ kind: failure.kind, text: failure.text });
      }
    } finally {
      setPending(false);
    }
  }

  return (
    <section className="banner quiet-banner" aria-label={t("heading")}>
      <div className="quiet-body">
        <p className="quiet-headline">
          <strong>
            {t.rich(row.owner ? "banner.headline" : "banner.headlineNoOwner", {
              when: utcTimeText(row.ends_at, "full"),
              until: (chunks) => (
                <UtcTime value={row.ends_at}>
                  {chunks}
                </UtcTime>
              ),
              owner: row.owner ?? "",
              days: days ?? 0,
            })}
          </strong>{" "}
          <span className="quiet-recorded">{t("banner.recorded", { count: data.hit_count })}</span>
        </p>
        <dl className="quiet-facts">
          <div>
            <dt>{t("banner.kind")}</dt>
            <dd>
              {t(`kind.${row.kind}` as "kind.ANALYST")}
              {row.reference && <> · {t("banner.reference", { reference: row.reference })}</>}
            </dd>
          </div>
          <div>
            <dt>{t("banner.scope")}</dt>
            <dd>{scope}</dd>
          </div>
          <div className="quiet-reason">
            <dt>{t("banner.reason")}</dt>
            <dd>{row.reason ?? "—"}</dd>
          </div>
        </dl>
        <p className="quiet-note">{t("banner.recordedNote")}</p>

        <div className="quiet-breakthrough">
          <p className="quiet-sub">{t("banner.breakthroughTitle")}</p>
          {data.breakthrough_alerts.length === 0 ? (
            <p className="muted">{t("banner.breakthroughNone")}</p>
          ) : (
            <ul>
              {data.breakthrough_alerts.map((a) => (
                <li key={a.id}>
                  <Link href={`/alerts/${a.id}`}>{a.title}</Link>{" "}
                  <span className={`pill ${sevTone(a.severity.toUpperCase())}`}>
                    {enumLabel(enums, "severityPill", a.severity.toUpperCase())}
                  </span>{" "}
                  <span className="muted">
                    {t("banner.breakthroughOpened")} <UtcTime value={a.opened_at} /> ·{" "}
                    {t("banner.breakthroughSeen", { count: a.occurrence_count })}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {!row.read_only && !confirming && (
          <div className="actions">
            <button
              ref={endRef}
              type="button"
              className="action secondary"
              onClick={() => {
                setOutcome({ kind: "idle" });
                setConfirming(true);
              }}
            >
              {t("banner.end")}
            </button>
          </div>
        )}
        {confirming && (
          <div className="rule-form quiet-confirm" role="group" aria-label={t("banner.endTitle")}>
            <div className="impact">
              <p className="impact-title">{t("banner.endTitle")}</p>
              <ul className="impact-lines">
                <li>{t("banner.endLines", { scope })}</li>
              </ul>
            </div>
            <div className="actions">
              <button
                ref={confirmRef}
                type="button"
                className="action"
                disabled={pending}
                onClick={() => void end()}
              >
                {pending ? t("banner.ending") : t("banner.endConfirm")}
              </button>
              <button
                type="button"
                className="action secondary"
                disabled={pending}
                onClick={() => {
                  restore.current = true;
                  setConfirming(false);
                  setOutcome({ kind: "idle" });
                }}
              >
                {t("banner.endKeep")}
              </button>
            </div>
          </div>
        )}
        <ChangeStatus outcome={outcome} />
      </div>
    </section>
  );
}
