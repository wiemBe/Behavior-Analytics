"use client";

// Catch-all senders (UR.5.1): which unknown senders feeding the catch-all need review? The
// newest unreviewed ones, five at most. The 24 h count is a lower bound named by stored top
// lists; a sender no list named has "unknown", which is not zero.

import { useTranslations } from "next-intl";

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { plainCount, useFleetText } from "@/lib/fleetText";
import { formatCount } from "@/lib/format";
import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { When } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";
import { formatEvents24h } from "@/app/fleet/catchall/catchall";

import { useExpanded } from "@/widgets/runtime";

import { newestSenders } from "./derive";

import "./catalog.css";

const SENDERS_READ = 200;

/** A lower bound; the sign is the same in both languages. */
const AT_LEAST = "≥";

export function CatchallSenders() {
  const t = useTranslations("catalog.catchall");
  const text = useFleetText();
  const note = text.say("catchall.events24hNote");
  const expanded = useExpanded();
  const read = homeReads.newCatchallSenders(SENDERS_READ);
  const state = useApiResource(read.key, read.fetch);
  const badge =
    state.status === "ok"
      ? t("badge", { count: formatCount(state.data.total, text.locale) })
      : undefined;

  return (
    <WidgetFrame
      id="catchall-senders"
      title={t("title")}
      windowText={t("windowText")}
      badge={badge}
      link={{ href: "/fleet/catchall", label: t("link") }}
      state={state}
      what={t("what")}
      permission="read:fleet"
    >
      {({ data }) => {
        if (data.items.length === 0 && data.total === 0) {
          return (
            <p className="tw-state">{t("empty")}</p>
          );
        }
        const { rows, qualifiers } = newestSenders(data.items, data.total, expanded ? Infinity : undefined);
        return (
          <>
            <TableScroll label={t("label")}>
              <table className="tw-table cw-table" aria-label={t("label")}>
                <colgroup>
                  <col />
                  <col className="tw-col-time" />
                  <col className="tw-col-eps" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">{t("sender")}</th>
                    <th scope="col">{t("firstSeen")}</th>
                    <th scope="col" className="num" title={note}>
                      {t("events")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td className="tw-name">
                        <span title={r.identifier}>{r.identifier}</span>
                        <span className="tw-sub" title={r.catchall_log_source_name}>
                          {r.catchall_log_source_name}
                        </span>
                      </td>
                      <td>
                        <When iso={r.first_seen_at} />
                      </td>
                      <td className="num">
                        {r.events_24h === null ? (
                          <span className="muted" title={note}>
                            {text.say("catchall.unknown")}
                          </span>
                        ) : (
                          <>{AT_LEAST} {formatEvents24h(r.events_24h, text)}</>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableScroll>
            <WidgetQualifiers qualifiers={qualifiers} />
            {expanded || data.total > rows.length ? (
              <p className="tw-note">
                {t("showing", {
                  shown: plainCount(rows.length, text.locale),
                  total: formatCount(data.total, text.locale),
                })}
              </p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
