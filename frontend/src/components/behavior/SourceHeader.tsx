// The source's identity, in one block.
//
// The name appears once, as the page's <h1> — the previous header repeated it
// in the title and then again as the first thing in a card row. Behaviour
// state is the dominant badge; monitoring and maintenance are real but
// subordinate, and render quietly beside it.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { useEnumLabels } from "@/i18n/enumLabels";
import type { LogSourceDetail, SourceBehavior } from "@/lib/api";
import { stateTone } from "@/lib/behavior";
import { evaluationOf, windowIsPartial } from "@/lib/evaluationFault";
import { UtcTime } from "@/components/ui/UtcTime";
import { GuideLink } from "@/components/guide/GuideLink";
import { guideFor } from "@/lib/guideRoutes";

export function SourceHeader({
  behavior,
  meta,
}: {
  behavior: SourceBehavior;
  /** Inventory metadata. Null when that call failed; the page still renders. */
  meta: LogSourceDetail | null;
}) {
  const t = useTranslations("behavior.header");
  const { statePill, severityPill } = useEnumLabels();
  const evaluation = evaluationOf(behavior);
  const partial = windowIsPartial(behavior);
  return (
    <header className="page-header incident-header">
      <div>
        <p className="incident-back">
          <Link href="/behavior/sources">{t("allSources")}</Link>
        </p>

        <h1 className="page-title page-title-long">{behavior.name}</h1>

        <div className="row incident-meta">
          <span className={`pill pill-strong ${stateTone(behavior.state)}`}>
            {statePill(behavior.state)}
          </span>
          {evaluation?.judged_through && (
            <span className="muted">
              {t.rich("judgedThrough", {
                time: () => <UtcTime value={evaluation.judged_through} variant="compact" />,
              })}
            </span>
          )}
          {partial && (
            <span className="pill pill-quiet warn">{t("partial")}</span>
          )}

          {/* Secondary identity, so an analyst can match this against QRadar. */}
          {meta?.qradar_id != null && (
            <span className="muted">{t("qradarId", { id: meta.qradar_id })}</span>
          )}
          {meta?.type_name && <span className="muted">{meta.type_name}</span>}
          <span className="muted">{t("criticality", { value: severityPill(behavior.criticality) })}</span>

          {/* Both of these change how the numbers below should be read, so
              they are shown whenever they are not the default. */}
          {meta?.monitoring_enabled === false && (
            <span className="pill pill-quiet warn">{t("monitoringDisabled")}</span>
          )}
          {meta?.maintenance_mode && (
            <span className="pill pill-quiet warn">{t("maintenanceMode")}</span>
          )}
        </div>
      </div>

      <div className="page-header-actions">
        <Link
          href={`/log-sources/${behavior.log_source_id}`}
          className="action secondary button-link"
        >
          {t("inventoryDetail")}
        </Link>
        <GuideLink section={guideFor("/behavior/sources/[id]")} />
      </div>
    </header>
  );
}
