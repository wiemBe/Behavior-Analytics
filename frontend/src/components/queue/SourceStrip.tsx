// One line per source of the queue: its state worded, and for a state that is not OK what it means
// and what to do. Not a row of cards: a status list, each name linking to the route that owns the
// data. A source that did not load has no count, and none is drawn in its place.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import type { QueueKind, QueueSource } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import { formatAge } from "@/lib/time";

export const SOURCE_ROUTES: Record<QueueKind, string> = {
  anomaly: "/anomalies",
  alert: "/alerts",
  offense: "/offenses",
  source_review: "/fleet/sources",
  catchall_sender: "/fleet/catchall",
};

function ageSince(from: string | null, asOf: string): string | null {
  if (!from) return null;
  const seconds = (Date.parse(asOf) - Date.parse(from)) / 1000;
  return Number.isFinite(seconds) && seconds >= 0 ? formatAge(seconds) : null;
}

export function SourceStrip({
  sources,
  asOf,
  totalExact,
}: {
  sources: QueueSource[];
  asOf: string;
  totalExact: boolean;
}) {
  const t = useTranslations("queue.sources");
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;

  function detail(s: QueueSource): { text: string; tone: "" | "crit" | "warn" } {
    switch (s.state) {
      case "FAILED":
        return { text: t("failed"), tone: "crit" };
      case "FORBIDDEN":
        return {
          text: s.permission ? t("forbidden", { permission: s.permission }) : t("forbiddenUnknown"),
          tone: "warn",
        };
      case "NOT_COLLECTED": {
        const key = s.reason_code === "NO_CONSOLE" || s.reason_code === "CONSOLE_UNCOLLECTED" ? s.reason_code : "default";
        return { text: t(`notCollected.${key}`), tone: "warn" };
      }
      case "STALE": {
        const age = ageSince(s.as_of, asOf);
        if (!s.as_of || !age) return { text: t("staleNoTime"), tone: "warn" };
        const key = s.reason_code === "COLLECTION_STALE" || s.reason_code === "COLLECTION_FAILING" ? s.reason_code : "default";
        return { text: t(`stale.${key}`, { age, time: utcTimeText(s.as_of) }), tone: "warn" };
      }
      case "PARTIAL": {
        const code = s.reason_code;
        const text =
          code === "OWNER_UNMAPPED"
            ? t("partial.OWNER_UNMAPPED")
            : code === "ALERT_LINK_FAILED"
              ? t("partial.ALERT_LINK_FAILED")
              : code === "ALERTS_NOT_READABLE"
                ? t("partial.ALERTS_NOT_READABLE")
                : t("partial.default");
        return { text, tone: "warn" };
      }
      default:
        if (s.reason_code === "NOT_APPLICABLE_TO_FILTER") return { text: t("notApplicable"), tone: "" };
        return { text: "", tone: "" };
    }
  }

  return (
    <section className="queue-sources" aria-label={t("label")}>
      <h2 className="queue-sources-title">{t("heading")}</h2>
      <ul className="queue-source-list">
        {sources.map((s) => {
          const name = t(`kind.${s.kind}`);
          const d = detail(s);
          const notApplicable = s.state === "OK" && s.reason_code === "NOT_APPLICABLE_TO_FILTER";
          const counted = s.count !== null && !notApplicable && s.state !== "FAILED" && s.state !== "FORBIDDEN";
          return (
            <li key={s.kind} className={`queue-source${d.tone ? ` queue-source-${d.tone}` : ""}`}>
              <Link href={SOURCE_ROUTES[s.kind]} className="queue-source-name" aria-label={t("open", { name })}>
                {name}
              </Link>
              {counted && s.count !== null && (
                <span className="queue-source-count num">
                  {t("waiting", { count: formatCount(s.count, locale) })}
                  {s.new_count !== null && s.new_count > 0 && (
                    <>
                      {" · "}
                      {t("newCount", { count: formatCount(s.new_count, locale) })}
                    </>
                  )}
                </span>
              )}
              {d.text && <span className="queue-source-detail">{d.text}</span>}
            </li>
          );
        })}
      </ul>
      <p className="queue-sources-asof muted">
        {t("loaded")} <UtcTime value={asOf} />
        {!totalExact && <> · {t("lowerBound")}</>}
      </p>
    </section>
  );
}
