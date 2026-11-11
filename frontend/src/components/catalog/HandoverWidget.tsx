"use client";

// Latest handover (SOC-14b): who wrote the newest shift record, when, the start of the note and
// how many rows it watches. It is a record, not a collected measure: it has no cadence, so it has
// no stale state. An absent record says so; a frozen source that was not read cleanly says the
// snapshot is incomplete, never a smaller count.

import { useTranslations } from "next-intl";

import { WidgetFrame } from "@/components/home/WidgetFrame";
import { When } from "@/components/home/WidgetParts";
import { useApiResource } from "@/hooks/useApiResource";
import { asTextTranslator } from "@/i18n/textTranslator";
import { handoverRead } from "@/lib/api";
import { snapshotIncomplete, sourceLines, splitItems } from "@/lib/handover";

import "./catalog.css";

/** The note's first characters; the full text is on the page the widget links to. */
const EXCERPT = 240;

export function HandoverWidget() {
  const t = useTranslations("handover.widget");
  const sources = useTranslations("handover.source");
  const q = useTranslations("queue.sources");
  const say = asTextTranslator(useTranslations());
  const read = handoverRead.latest();
  const state = useApiResource(read.key, read.fetch);

  return (
    <WidgetFrame
      id="latest-handover"
      title={t("title")}
      link={{ href: "/handover", label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:anomalies"
    >
      {({ data }) => {
        const record = data.handover;
        if (record === null) return <p className="tw-state">{t("empty")}</p>;
        const note =
          record.note.length > EXCERPT ? `${record.note.slice(0, EXCERPT).trimEnd()}…` : record.note;
        const watched = splitItems(record.body).watch.length;
        const lines = sourceLines(record.body);
        const names = [
          ...new Set(
            lines.map((l) =>
            l.scope === "open"
              ? say.has?.(`queue.sources.kind.${l.source.kind}`)
                ? q(`kind.${l.source.kind as "anomaly"}`)
                : l.source.kind
              : sources(l.source.kind === "alert" ? "kind.alert" : "kind.triage"),
            ),
          ),
        ].join(", ");
        return (
          <div className="ho-widget" aria-label={t("label")}>
            <p className="ho-widget-head">
              <strong>{record.author}</strong> <When iso={record.written_at} />
            </p>
            <p className="ho-widget-note">{note}</p>
            <p className="tw-note">{watched > 0 ? t("watch", { count: watched }) : t("watchNone")}</p>
            {snapshotIncomplete(record.body) && (
              <p className="tw-note warn" title={t("incompleteTitle", { sources: names })}>
                {t("incomplete", { sources: names })}
              </p>
            )}
          </div>
        );
      }}
    </WidgetFrame>
  );
}
