// The Definition tab's generated query text (design §G.13.3): what the collector would send for
// this watch, and what it recorded for the newest bucket. Read-only text in a collapsed
// `CodePanel`; nothing here is an input and nothing is ever sent back (AGENTS.md §2 rule 4).
//
// The text may contain a pinned value (D-22) and is attacker-controlled: `CodePanel` renders it
// as text. A failed read, an unresolvable scope and a watch that has not run each say so; none
// of them is an empty panel.

import { CodePanel } from "@/components/ui/CodePanel";
import { useTranslations } from "next-intl";
import type { PwAqlQuery, WatchAql } from "@/lib/api";
import { UtcTime } from "@/components/ui/UtcTime";
import { useBehaviorI18n } from "@/lib/useBehaviorI18n";

import { pwSentence } from "./pwReason";


/** The outcome of the AQL read: the text, or the HTTP status that refused it (0: no answer). */
export type AqlRead = { kind: "ok"; aql: WatchAql } | { kind: "error"; status: number };

function Queries({ prefix, queries }: { prefix: string; queries: PwAqlQuery[] }) {
  const t = useTranslations("propertyWatch.aql");
  const common = useTranslations("propertyWatch.common");
  return (
    <div className="pw-query-list">
      {queries.map((q) => (
        <CodePanel
          key={q.search}
          label={`${prefix} · ${t(`search.${q.search}`)}`}
          code={q.text}
          meta={common("readOnly")}
        />
      ))}
    </div>
  );
}

export function WatchAqlSection({ read }: { read: AqlRead | null }) {
  const t = useTranslations("propertyWatch.aql");
  const common = useTranslations("propertyWatch.common");
  const i18n = useBehaviorI18n();
  if (read === null || read.kind === "error") {
    const forbidden = read !== null && read.status === 403;
    return (
      <div className="notice" role="alert">
        <p>
          <strong>
            {forbidden
              ? t("forbiddenTitle")
              : t("failedTitle")}
          </strong>
        </p>
        <p>
          {forbidden
            ? t("forbiddenBody")
            : t("failedBody")}
        </p>
      </div>
    );
  }
  const { aql } = read;
  const last = aql.last_bucket;
  return (
    <div className="stack-6">
      <p className="subtitle">{t("note")}</p>

      <div>
        <h3 className="pw-subtitle">{t("currentDefinition")}</h3>
        {aql.current_unavailable ? (
          <div className="notice" role="status">
            <p>
              <strong>{t("currentUnavailableTitle")}</strong>
            </p>
            <p>
              {pwSentence(
                aql.current_unavailable,
                { code: aql.current_unavailable_code, params: aql.current_unavailable_params },
                i18n,
              )}
            </p>
          </div>
        ) : aql.current.length === 0 ? (
          <p className="notice" role="status">
            {t("currentEmpty")}
          </p>
        ) : (
          <>
            <p className="subtitle">
              {aql.window_start && aql.window_end
                ? t.rich("currentWindow", {
                    start: () => <UtcTime value={aql.window_start as string} />,
                    end: () => <UtcTime value={aql.window_end as string} />,
                    version: aql.watch_version,
                  })
                : t("currentWindowFallback", {
                    window: t("windowUnrecorded"),
                    version: aql.watch_version,
                  })}
            </p>
            <Queries prefix={common("current")} queries={aql.current} />
          </>
        )}
      </div>

      <div>
        <h3 className="pw-subtitle">{t("lastCollected")}</h3>
        {last === null ? (
          <p className="notice" role="status">
            {t("lastEmpty")}
          </p>
        ) : (
          <>
            <p className="subtitle">
              {t.rich("lastSummary", {
                bucket: () => <UtcTime value={last.bucket_start} />,
                collected: () => <UtcTime value={last.collected_at} />,
                version: last.watch_version,
                changed:
                  last.watch_version !== aql.watch_version
                    ? t("versionChanged", { version: aql.watch_version })
                    : "",
              })}
            </p>
            {last.queries.length === 0 ? (
              <p className="notice" role="status">
                {t("lastQueriesEmpty")}
              </p>
            ) : (
              <Queries prefix={common("lastBucket")} queries={last.queries} />
            )}
          </>
        )}
      </div>
    </div>
  );
}
