"use client";

// Earlier handovers, newest first, paged by "Show earlier". Each row opens its record at
// /handover?id=…; the note is its first 240 characters.

import Link from "next/link";
import { useEffect, useState } from "react";
import { useLocale, useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import { asTextTranslator } from "@/i18n/textTranslator";
import { handoverApi, type HandoverSummary } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import { EARLIER_PAGE } from "@/lib/handover";

import { handoverLoadReason } from "./loadError";

export function EarlierList({ currentId }: { currentId: string | null }) {
  const t = useTranslations("handover.earlier");
  const say = asTextTranslator(useTranslations());
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;
  const [rows, setRows] = useState<HandoverSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [reason, setReason] = useState("");
  const [nonce, setNonce] = useState(0);
  const [more, setMore] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setStatus((cur) => (cur === "ready" ? cur : "loading"));
    handoverApi
      .list(EARLIER_PAGE, rows.length)
      .then((page) => {
        if (cancelled) return;
        setRows((cur) => (rows.length === 0 ? page.items : [...cur, ...page.items]));
        setTotal(page.total);
        setStatus("ready");
        setMore(false);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        setReason(handoverLoadReason(err, say));
        setStatus("error");
        setMore(false);
      });
    return () => {
      cancelled = true;
    };
    // `nonce` is the retry and the "Show earlier" press; the offset it reads is rows.length.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nonce]);

  // The record on screen is not "earlier" than itself.
  const shown = rows.filter((row) => row.id !== currentId);

  return (
    <section className="ho-section ho-earlier" aria-label={t("label")}>
      <h2 className="ho-h">{t("heading")}</h2>
      {status === "error" ? (
        <div className="notice" role="alert">
          <p>
            <strong>{t("failed")}</strong>
          </p>
          <p>{reason}</p>
          <p>
            <button type="button" className="action secondary" onClick={() => setNonce((n) => n + 1)}>
              {say("handover.retry")}
            </button>
          </p>
        </div>
      ) : status === "loading" && rows.length === 0 ? (
        <p className="notice" role="status" aria-live="polite">
          {t("loading")}
        </p>
      ) : shown.length === 0 && rows.length >= total ? (
        <p className="notice" role="status">
          {t("empty")}
        </p>
      ) : (
        <>
          <TableScroll label={t("label")}>
            <table className="table-dense ho-table" aria-label={t("label")}>
              <thead>
                <tr>
                  <th scope="col">{t("written")}</th>
                  <th scope="col">{t("author")}</th>
                  <th scope="col">{t("note")}</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((row) => (
                  <tr key={row.id}>
                    <td className="ho-waiting">
                      <Link href={`/handover?id=${encodeURIComponent(row.id)}`}>
                        <UtcTime value={row.written_at} variant="compact" />
                      </Link>
                    </td>
                    <td>{row.author}</td>
                    <td className="ho-excerpt">
                      {row.note_excerpt}
                      {row.note_length > row.note_excerpt.length && (
                        <span className="ho-sub muted">
                          {t("length", { count: formatCount(row.note_length, locale) })}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableScroll>
          {rows.length < total && (
            <p className="ho-pager">
              <button
                type="button"
                className="action secondary"
                disabled={more}
                onClick={() => {
                  setMore(true);
                  setNonce((n) => n + 1);
                }}
              >
                {t("more")}
              </button>{" "}
              <span className="muted num">
                {t("moreOf", { shown: formatCount(rows.length, locale), total: formatCount(total, locale) })}
              </span>
            </p>
          )}
        </>
      )}
    </section>
  );
}
