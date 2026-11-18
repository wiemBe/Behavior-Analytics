// The last log-source inventory pass of one console, in one line (D-82), the rest in a <details>.
//
// Used by the Fleet "Sync now" area and by Admin → QRadar access, so both say the same thing. A
// partial pass is said as partial with the page that failed: its pages were stored, nothing was
// marked missing. A failed pass says what failed and what the analyst can do.

import { useTranslations } from "next-intl";

import { UtcTime } from "@/components/ui/UtcTime";
import type { FleetSyncPass } from "@/lib/api";
import { formatExactDuration } from "@/lib/time";

import "./fleet-sources.css";

const KINDS = ["timeout", "auth", "rate_limited", "upstream", "network", "malformed"] as const;
type Kind = (typeof KINDS)[number];

function kindOf(raw: string | null): Kind | "unknown" {
  return (KINDS as readonly string[]).includes(raw ?? "") ? (raw as Kind) : "unknown";
}

export function InventoryPassLine({
  pass,
  running = false,
  consoleName = null,
}: {
  pass: FleetSyncPass | null;
  running?: boolean;
  /** Named only when more than one console is listed. */
  consoleName?: string | null;
}) {
  const t = useTranslations("fleet.sourceList.sync.pass");
  const prefix = consoleName ? `${consoleName}: ` : "";
  if (pass === null) {
    return (
      <span className="fs-pass">
        {prefix}
        {running ? t("running") : t("never")}
      </span>
    );
  }
  const kind = kindOf(pass.failure_kind);
  const where = pass.failed_page !== null ? t("onPage", { page: pass.failed_page }) : "";
  const reason = `${t(`kind.${kind}`)}${where}`;
  let line;
  if (pass.status === "COMPLETE") {
    line = (
      <>
        {t("complete")} <UtcTime value={pass.finished_at} variant="compact" />
        {" · "}
        {t("sources", { count: pass.seen })}
      </>
    );
  } else if (pass.status === "PARTIAL") {
    line = <>{t("partial", { reason, count: pass.seen })}</>;
  } else {
    line = <>{t("failed", { reason })}</>;
  }
  return (
    <span className="fs-pass">
      {prefix}
      {running ? `${t("running")} ` : ""}
      {line}
      <details className="fs-pass-details">
        <summary>{t("details")}</summary>
        <dl className="fs-pass-grid">
          <dt>{t("labelStatus")}</dt>
          <dd>{t(`status.${pass.status}`)}</dd>
          <dt>{t("labelFinished")}</dt>
          <dd>
            <UtcTime value={pass.finished_at} />
          </dd>
          <dt>{t("labelPages")}</dt>
          <dd>{t("pages", { done: pass.pages_done, failed: pass.pages_failed })}</dd>
          <dt>{t("labelSeen")}</dt>
          <dd>{pass.seen.toLocaleString("en-US")}</dd>
          <dt>{t("labelCreated")}</dt>
          <dd>{pass.created.toLocaleString("en-US")}</dd>
          <dt>{t("labelUpdated")}</dt>
          <dd>{pass.updated.toLocaleString("en-US")}</dd>
          <dt>{t("labelElapsed")}</dt>
          <dd>{pass.elapsed_ms === null ? "—" : formatExactDuration(pass.elapsed_ms / 1000)}</dd>
          <dt>{t("labelFailure")}</dt>
          <dd>{pass.failure_kind === null ? "—" : `${t(`kind.${kind}`)}${where}`}</dd>
          {pass.failure_kind !== null && (
            <>
              <dt>{t("labelAction")}</dt>
              <dd>{t(`action.${kind}`)}</dd>
            </>
          )}
        </dl>
      </details>
    </span>
  );
}
