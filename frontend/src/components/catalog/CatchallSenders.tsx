"use client";

// Catch-all senders (UR.5.1): which unknown senders feeding the catch-all need review? The
// newest unreviewed ones, five at most. The 24 h count is a lower bound named by stored top
// lists; a sender no list named has "unknown", which is not zero.

import { useApiResource } from "@/hooks/useApiResource";
import { homeReads } from "@/lib/api";
import { WidgetFrame, WidgetQualifiers } from "@/components/home/WidgetFrame";
import { When } from "@/components/home/WidgetParts";
import { TableScroll } from "@/components/ui/TableScroll";
import { EVENTS_24H_NOTE, formatEvents24h } from "@/app/fleet/catchall/catchall";

import { useExpanded } from "@/widgets/runtime";

import { newestSenders } from "./derive";

import "./catalog.css";

const SENDERS_READ = 200;

export function CatchallSenders() {
  const expanded = useExpanded();
  const read = homeReads.newCatchallSenders(SENDERS_READ);
  const state = useApiResource(read.key, read.fetch);
  const badge = state.status === "ok" ? `${state.data.total.toLocaleString("en-US")} new` : undefined;

  return (
    <WidgetFrame
      id="catchall-senders"
      title="Catch-all senders to review"
      windowText="new"
      badge={badge}
      link={{ href: "/fleet/catchall", label: "Review" }}
      state={state}
      what="the catch-all senders"
      permission="read:fleet"
    >
      {({ data }) => {
        if (data.items.length === 0 && data.total === 0) {
          return (
            <p className="tw-state">
              No unreviewed senders. Every sender seen feeding the catch-all sources has been
              reviewed or dismissed.
            </p>
          );
        }
        const { rows, qualifiers } = newestSenders(data.items, data.total, expanded ? Infinity : undefined);
        return (
          <>
            <TableScroll label="Newest unreviewed catch-all senders">
              <table className="tw-table cw-table">
                <colgroup>
                  <col />
                  <col className="tw-col-time" />
                  <col className="tw-col-eps" />
                </colgroup>
                <thead>
                  <tr>
                    <th scope="col">Sender</th>
                    <th scope="col">First seen</th>
                    <th scope="col" className="num" title={EVENTS_24H_NOTE}>
                      Events, 24 h
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
                          <span className="muted" title={EVENTS_24H_NOTE}>
                            unknown
                          </span>
                        ) : (
                          <>≥ {formatEvents24h(r.events_24h)}</>
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
                Showing the newest {rows.length} of {data.total.toLocaleString("en-US")}.
              </p>
            ) : null}
          </>
        );
      }}
    </WidgetFrame>
  );
}
