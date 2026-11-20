"use client";

// "What does this product collect and alert on, and what does each family cost?" One row per
// family, with its state first. The page reads the switches in the browser and re-reads them
// after every change: what it shows is what the API holds, never what the click assumed.

import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

import { holds } from "@/components/auth/access";
import { useSession } from "@/components/auth/session";
import { LoadingState } from "@/components/ui/RouteState";
import { TableScroll } from "@/components/ui/TableScroll";
import { ApiError, featuresApi, type FeatureOverview } from "@/lib/api";
import { groupProblems } from "@/lib/features";

import { FamilyRow } from "./FamilyRow";

import "@/components/ui/controls.css";
import "./features.css";

type Part =
  | { status: "loading" }
  | { status: "ok"; data: FeatureOverview }
  | { status: "forbidden" }
  | { status: "error"; httpStatus: number | null; retryAfter: number | null };

function Perm({ chunks }: { chunks: React.ReactNode }) {
  return <code className="perm">{chunks}</code>;
}

export function FeaturesPage() {
  const t = useTranslations("features");
  const session = useSession();
  const [part, setPart] = useState<Part>({ status: "loading" });
  const [now, setNow] = useState(() => new Date());
  const granted = session.status === "ready" && session.me ? session.me.permissions : [];
  const canWrite = holds(granted, "admin:features");

  const read = useCallback(async (signal?: AbortSignal): Promise<boolean> => {
    try {
      const data = await featuresApi.get(signal);
      if (signal?.aborted) return true;
      setPart({ status: "ok", data });
      setNow(new Date());
      return true;
    } catch (err) {
      if (signal?.aborted) return true;
      if (err instanceof ApiError && err.status === 403) setPart({ status: "forbidden" });
      else {
        // A failed refresh keeps the answer already on screen.
        setPart((prev) =>
          prev.status === "ok"
            ? prev
            : {
                status: "error",
                httpStatus: err instanceof ApiError ? err.status : null,
                retryAfter: err instanceof ApiError ? err.retryAfter : null,
              },
        );
      }
      return false;
    }
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    void read(controller.signal);
    return () => controller.abort();
  }, [read]);

  if (part.status === "loading") return <LoadingState what={t("page.loadingWhat")} />;
  if (part.status === "forbidden") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("page.forbidden.heading")}</strong>
        </p>
        <p>{t.rich("page.forbidden.body", { perm: (chunks) => <Perm chunks={chunks} /> })}</p>
      </div>
    );
  }
  if (part.status === "error") return <ReadFailed part={part} onRetry={() => void read()} />;

  const { data } = part;
  if (data.reason) {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("page.unreadable.heading")}</strong>
        </p>
        <p>{t("page.unreadable.body")}</p>
      </div>
    );
  }
  const { byFamily, other } = groupProblems(data.settings_problems);
  const noConsole = data.families.length > 0 && data.families.every((f) => f.estimate.enabled_consoles === 0);
  return (
    <div className="ft-page">
      {noConsole && (
        <p className="banner" role="status">
          <span>
            <strong>{t("page.noConsole.heading")}</strong> {t("page.noConsole.body")}
          </span>
        </p>
      )}
      {!canWrite && session.status === "ready" && (
        <p className="banner" role="status">
          <span>{t.rich("page.readOnly", { perm: (chunks) => <Perm chunks={chunks} /> })}</span>
        </p>
      )}
      {other.length > 0 && (
        <details className="banner warn ft-conflict">
          <summary>{t("page.settings")}</summary>
          {other.map((text) => (
            <code key={text} className="ft-problem">
              {text}
            </code>
          ))}
        </details>
      )}
      <TableScroll label={t("table.label")}>
        <table className="table-dense ft-table">
          <caption className="sr-only">{t("table.caption")}</caption>
          <thead>
            <tr>
              <th scope="col">{t("table.family")}</th>
              <th scope="col">{t("table.state")}</th>
              <th scope="col" className="num">{t("table.load")}</th>
              <th scope="col">{t("table.needs")}</th>
              <th scope="col" className="num">{t("table.lastRun")}</th>
            </tr>
          </thead>
          <tbody>
            {data.families.map((row) => (
              <FamilyRow
                key={row.family}
                row={row}
                now={now}
                canWrite={canWrite}
                problems={byFamily[row.family] ?? []}
                onChanged={() => read()}
              />
            ))}
          </tbody>
        </table>
      </TableScroll>
    </div>
  );
}

function ReadFailed({
  part,
  onRetry,
}: {
  part: { httpStatus: number | null; retryAfter: number | null };
  onRetry: () => void;
}) {
  const t = useTranslations("features.page.readFailed");
  const errors = useTranslations("errors");
  const answer =
    part.httpStatus === 429
      ? part.retryAfter === null
        ? errors("rateLimited")
        : errors("rateLimitedAfter", { seconds: part.retryAfter })
      : part.httpStatus === 401
        ? errors("sessionExpired")
        : part.httpStatus === null
          ? t("noAnswer")
          : t("answeredWith", { status: part.httpStatus });
  return (
    <div className="notice" role="alert">
      <p>
        <strong>{t("heading")}</strong>
      </p>
      <p>{answer}</p>
      <p>{t("advice")}</p>
      <p>
        <button type="button" className="action secondary" onClick={onRetry}>
          {t("tryAgain")}
        </button>
      </p>
    </div>
  );
}
