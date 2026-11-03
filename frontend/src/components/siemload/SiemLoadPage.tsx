"use client";

// The SIEM load page's body: how much are we loading QRadar now, and how is QRadar doing. One read
// of `/admin/ariel-load/current` feeds the top of the page and the in-flight table; the per-caller
// summary reads its own window. The top bar's Refresh re-reads both (the shell's refresh tick, via
// `useApiResource`); a failed refresh keeps the last answer and says so.

import { useTranslations } from "next-intl";
import { useState } from "react";

import { useApiResource } from "@/hooks/useApiResource";
import { useReportDataAge } from "@/components/shell/AutoRefresh";
import { LoadingState } from "@/components/ui/RouteState";
import { UtcTime } from "@/components/ui/UtcTime";
import { arielLoadApi } from "@/lib/api";

import { CallerSummary } from "./CallerSummary";
import { InFlightTable } from "./InFlightTable";
import { NowBand } from "./NowBand";
import { QRadarDetail } from "./QRadarDetail";
import { QRadarPanel } from "./QRadarPanel";
import { DEFAULT_TREND, type SummaryRangeId, type TrendRangeId } from "./siemFormat";

import "./siemload.css";

const CURRENT = "/admin/ariel-load/current";

export function SiemLoadPage({
  range,
  trend = DEFAULT_TREND,
}: {
  range: SummaryRangeId;
  trend?: TrendRangeId;
}) {
  const t = useTranslations("siemLoad.page");
  const state = useTranslations("siemLoad.state");
  const common = useTranslations("common");
  const errors = useTranslations("errors");
  // A new key re-reads after a failure; the key is only the request's identity.
  const [attempt, setAttempt] = useState(0);
  const resource = useApiResource(attempt === 0 ? CURRENT : `${CURRENT}#${attempt}`, (signal) =>
    arielLoadApi.current(signal),
  );
  useReportDataAge(resource.status === "ok" ? resource.data.as_of : null);

  if (resource.status === "forbidden") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{state("couldNotRead")}</strong>
        </p>
        <p>{state.rich("forbidden", { perm: (chunks) => <code className="perm">{chunks}</code> })}</p>
      </div>
    );
  }
  if (resource.status === "error") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{state("couldNotRead")}</strong>
        </p>
        <p>
          {resource.httpStatus === 429
            ? resource.retryAfter == null
              ? errors("rateLimited")
              : errors("rateLimitedAfter", { seconds: resource.retryAfter })
            : resource.httpStatus === 401
              ? errors("sessionExpired")
              : resource.httpStatus === null
                ? state("noAnswer")
                : state("answeredWith", { status: resource.httpStatus })}
        </p>
        <p>{state("advice")}</p>
        <p>
          <button type="button" className="action secondary" onClick={() => setAttempt((n) => n + 1)}>
            {common("tryAgain")}
          </button>
        </p>
      </div>
    );
  }

  if (resource.status !== "ok") {
    // The summary has its own read, so it starts with the first one instead of waiting for it.
    return (
      <div className="sl-page">
        <LoadingState what={t("loadingWhat")} />
        <CallerSummary range={range} trend={trend} />
      </div>
    );
  }

  const current = resource.data;
  const failed = resource.refreshFailed;
  const failedStatus =
    failed === null || failed.httpStatus === null
      ? ""
      : failed.httpStatus === 429
        ? ` (${
            failed.retryAfter == null
              ? state("refreshRateLimited")
              : state("refreshRateLimitedAfter", { seconds: failed.retryAfter })
          })`
        : ` (HTTP ${failed.httpStatus})`;

  return (
    <div className="sl-page">
      {failed ? (
        <p className="sl-refresh-note" role="status">
          {state.rich("refreshFailed", {
            status: failedStatus,
            time: () => <UtcTime value={current.as_of} />,
          })}
        </p>
      ) : null}
      <div className="sl-top">
        <NowBand current={current} />
        <QRadarPanel part={current.qradar} />
      </div>
      {current.qradar?.available && current.qradar.consoles.length > 0 ? (
        <QRadarDetail consoles={current.qradar.consoles} range={range} trend={trend} />
      ) : null}
      <InFlightTable part={current.in_flight} qradar={current.qradar} />
      <CallerSummary range={range} trend={trend} />
    </div>
  );
}
