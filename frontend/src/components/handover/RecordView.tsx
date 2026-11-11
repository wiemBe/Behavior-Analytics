// One handover record, read-only: author and time, the note, Watch, the other frozen open rows,
// what the author acted on, and a line for every source that was not read cleanly.

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";

import { formatAgo } from "@/lib/home/format";
import { UtcTime, utcTimeText } from "@/components/ui/UtcTime";
import type { AgeWords } from "@/lib/time";
import type { HandoverRecord } from "@/lib/api";
import { formatCount, type FormatLocale } from "@/lib/format";
import { cutoff, sourceLines, splitItems } from "@/lib/handover";

import { ActedOn } from "./ActedOn";
import { ItemsTable } from "./ItemsTable";
import { SourceLines } from "./SourceLines";

import "./handover.css";

export function RecordView({ record, latest }: { record: HandoverRecord; latest: boolean }) {
  const t = useTranslations("handover.record");
  const format = useTranslations("format");
  const locale = (useLocale() === "tr" ? "tr" : "en") as FormatLocale;
  const body = record.body;
  const { watch, others } = splitItems(body);
  const lines = sourceLines(body);
  const cut = cutoff(body);
  const ago = formatAgo(record.written_at, Date.now(), format as AgeWords);
  const actedSource = body.acted_on_sources;
  const actedUnread = actedSource.length === 0 || actedSource.some((src) => src.state !== "OK");
  const openUnread = body.sources.some((src) => src.state === "FAILED" || src.state === "FORBIDDEN");
  const count = (n: number) => formatCount(n, locale);

  return (
    <article className="ho-record" aria-label={latest ? t("latest") : t("this")}>
      <div className="ho-meta">
        <p className="ho-meta-line">
          <span className="ho-meta-label">{latest ? t("latest") : t("this")}</span>{" "}
          <UtcTime value={record.written_at} age={ago ?? undefined} />
          <span className="ho-author">{t("by", { author: record.author })}</span>
        </p>
        <p className="ho-window muted">
          {t("window", { from: utcTimeText(body.window_started_at), to: utcTimeText(record.written_at) })}
        </p>
      </div>

      <section className="ho-section" aria-label={t("noteHeading")}>
        <h2 className="ho-h">{t("noteHeading")}</h2>
        <p className="ho-note">{record.note}</p>
      </section>

      {watch.length > 0 && (
        <section className="ho-section" aria-label={t("watchHeading")}>
          <h2 className="ho-h">
            {t("watchHeading")} <span className="ho-count num">{t("watchCount", { count: count(watch.length) })}</span>
          </h2>
          <ItemsTable items={watch} label={t("watchHeading")} />
        </section>
      )}

      <section className="ho-section" aria-label={t("openHeading")}>
        <h2 className="ho-h">
          {t("openHeading")}{" "}
          <span className="ho-count num">
            {cut.kind === "exact"
              ? t("openCount", { shown: count(cut.shown), total: count(cut.total) })
              : cut.kind === "atLeast"
                ? t("openCountAtLeast", { shown: count(cut.shown), total: count(cut.total) })
                : openUnread
                  ? null
                  : t("openShown", { count: count(body.items.length) })}
          </span>
          {cut.kind !== "none" && (
            <Link className="ho-more" href="/queue">
              {t("queueLink")}
            </Link>
          )}
        </h2>
        {others.length > 0 ? (
          <ItemsTable items={others} label={t("table.label")} />
        ) : watch.length > 0 && !openUnread ? null : (
          <p className="notice ho-empty" role="status">
            {openUnread ? t("openEmpty") : t("openEmptyClean")}
          </p>
        )}
      </section>

      <section className="ho-section" aria-label={t("actedHeading", { author: record.author })}>
        <h2 className="ho-h">{t("actedHeading", { author: record.author })}</h2>
        {body.acted_on.length > 0 ? (
          <ActedOn rows={body.acted_on} />
        ) : (
          <p className="notice ho-empty" role="status">
            {actedUnread ? t("actedUnread") : t("actedEmpty", { author: record.author })}
          </p>
        )}
      </section>

      {body.watch_dropped.length > 0 && (
        <p className="banner warn" role="status">
          {t("dropped", { keys: body.watch_dropped.map((k) => `${t(`droppedKind.${k.kind}`)} ${k.id}`).join(", ") })}
        </p>
      )}

      <SourceLines lines={lines} />
    </article>
  );
}
