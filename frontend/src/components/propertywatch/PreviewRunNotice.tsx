"use client";

// Where a queued preview stands (SCALE-2h3). The preview is a worker task: the editor shows what
// the poll last read, one box per state. Times are the server's, shown in UTC.

import { UtcTime } from "@/components/ui/UtcTime";
import { renderBackendMessage } from "@/i18n/backendMessage";

import type { PwI18n } from "./pwText";

export type PreviewRun =
  | { kind: "idle" }
  | { kind: "queued" | "running"; requestedAt: string | null; readFailed: boolean }
  | {
      kind: "failed";
      reason: string | null;
      code: string | null;
      params: Record<string, unknown> | null;
    }
  | { kind: "expired" }
  | { kind: "busy"; since: string | null }
  | { kind: "forbidden" }
  | { kind: "notFound" };

export const PREVIEW_POLL_MS = 5000;

export function PreviewRunNotice({ run, i18n }: { run: PreviewRun; i18n: PwI18n }) {
  const t = i18n.t;
  if (run.kind === "idle") return null;
  if (run.kind === "queued" || run.kind === "running") {
    return (
      <div className="banner warn pw-preview-run" role="status" aria-busy="true">
        <div>
          <p>
            {t(run.kind === "queued" ? "propertyWatch.previewRun.queuedLead" : "propertyWatch.previewRun.runningLead")}{" "}
            {run.requestedAt ? <UtcTime value={run.requestedAt} /> : null}
            {run.requestedAt ? ". " : " "}
            {t(run.kind === "queued" ? "propertyWatch.previewRun.queuedTail" : "propertyWatch.previewRun.runningTail")}
          </p>
          {run.readFailed && <p>{t("propertyWatch.previewRun.readFailed")}</p>}
        </div>
      </div>
    );
  }
  if (run.kind === "failed") {
    const reason = run.reason
      ? renderBackendMessage({ text: run.reason, code: run.code, params: run.params }, i18n.locale, i18n.t)
      : t("propertyWatch.previewRun.failedFallback");
    return (
      <div className="banner crit pw-preview-run" role="alert">
        <p>
          <strong>{t("propertyWatch.previewRun.failedTitle")}</strong> {reason}.{" "}
          {t("propertyWatch.previewRun.failedAction")}
        </p>
      </div>
    );
  }
  if (run.kind === "expired") {
    return (
      <div className="notice pw-preview-run" role="status">
        <p><strong>{t("propertyWatch.previewRun.expiredTitle")}</strong></p>
        <p>{t("propertyWatch.previewRun.expiredBody")}</p>
      </div>
    );
  }
  if (run.kind === "busy") {
    return (
      <div className="banner warn pw-preview-run" role="alert">
        <p>
          {run.since ? (
            <>
              {t("propertyWatch.previewRun.busyLead")} <UtcTime value={run.since} />.
            </>
          ) : (
            t("propertyWatch.previewRun.busyAnonymous")
          )}{" "}
          {t("propertyWatch.previewRun.busyTail")}
        </p>
      </div>
    );
  }
  const forbidden = run.kind === "forbidden";
  return (
    <div className="banner warn pw-preview-run" role="alert">
      <div>
        <p><strong>{t(forbidden ? "propertyWatch.previewRun.forbiddenTitle" : "propertyWatch.previewRun.notFoundTitle")}</strong></p>
        <p>{t(forbidden ? "propertyWatch.previewRun.forbiddenBody" : "propertyWatch.previewRun.notFoundBody")}</p>
      </div>
    </div>
  );
}
