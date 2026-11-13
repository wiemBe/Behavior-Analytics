"use client";

// A part of the page that could not be read: what failed, what the backend answered, and what the
// administrator can do. The other part stays as it is.

import { useTranslations } from "next-intl";

export function PartError({
  what,
  httpStatus,
  retryAfter,
  onRetry,
}: {
  /** A noun phrase: "the routing rules". */
  what: string;
  httpStatus: number | null;
  retryAfter: number | null;
  onRetry: () => void;
}) {
  const t = useTranslations("notifyRouting.error");
  const errors = useTranslations("errors");
  const answer =
    httpStatus === 429
      ? retryAfter === null
        ? errors("rateLimited")
        : errors("rateLimitedAfter", { seconds: retryAfter })
      : httpStatus === 401
        ? errors("sessionExpired")
        : httpStatus === null
          ? t("noAnswer")
          : t("answeredWith", { status: httpStatus });
  return (
    <div className="notice" role="alert">
      <p>
        <strong>{t("couldNotRead", { what })}</strong>
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
