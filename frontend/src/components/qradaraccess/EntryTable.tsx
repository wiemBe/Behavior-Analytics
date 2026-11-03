"use client";

// One console's APIs, refused first. A status is a word with its tone, never a blank: an entry
// that was never checked says so. What to ask the QRadar administrator for sits with the refused
// entry, worded as IBM's hint unless the API says IBM states the capability itself.

import { useTranslations } from "next-intl";

import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { AccessEntry } from "@/lib/api";
import { relativeAge } from "@/lib/time";

import { Fragment } from "react";

import { askKind, changedFrom, isRefused, orderEntries, sharedAsk, syncWord, toneOf } from "./access";

export function EntryTable({
  consoleName,
  entries,
  now,
}: {
  consoleName: string;
  entries: readonly AccessEntry[];
  now: Date;
}) {
  const t = useTranslations("qradarAccess.table");
  const label = useTranslations("qradarAccess.console")("tableLabel", { console: consoleName });
  const hoisted = sharedAsk(entries) !== null;
  return (
    <TableScroll label={label}>
      <table className="qa-table">
        <caption className="sr-only">{label}</caption>
        <thead>
          <tr>
            <th scope="col">{t("api")}</th>
            <th scope="col">{t("status")}</th>
            <th scope="col" className="num">
              {t("http")}
            </th>
            <th scope="col">{t("feeds")}</th>
            <th scope="col">{t("checked")}</th>
          </tr>
        </thead>
        <tbody>
          {orderEntries(entries).map((entry) => (
            <EntryRow key={entry.id} entry={entry} now={now} hoisted={hoisted} />
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function EntryRow({ entry, now, hoisted }: { entry: AccessEntry; now: Date; hoisted: boolean }) {
  const t = useTranslations("qradarAccess");
  const was = changedFrom(entry);
  const refused = isRefused(entry);
  const tone = toneOf(entry.status);
  const detailed = entry.covers.length > 0 || entry.sources.length > 0;
  return (
    <tr className={refused ? "qa-row-refused" : undefined}>
      <td className="cell-name">
        <span className="qa-title">{entry.title}</span>
        <CallPath method={entry.method} path={entry.path} />
        {detailed ? (
          <details className="qa-details">
            <summary>{t("entry.details")}</summary>
            {entry.covers.length > 0 ? (
              <>
                <p className="qa-sub">{t("entry.covers")}</p>
                <ul className="qa-list">
                  {entry.covers.map((call) => (
                    <li key={`${call.method} ${call.path}`}>
                      <CallPath method={call.method} path={call.path} />
                      <span>{call.reason}</span>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {entry.sources.length > 0 ? (
              <>
                <p className="qa-sub">{t("entry.sources")}</p>
                <ul className="qa-list">
                  {entry.sources.map((href) => (
                    <li key={href}>
                      <a href={href} target="_blank" rel="noreferrer">
                        {href}
                      </a>
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
          </details>
        ) : null}
      </td>
      <td>
        <span className={`pill ${tone}`.trim()}>
          {entry.status === null ? t("status.never") : t(`status.${entry.status}`)}
        </span>
        {was ? <span className="cell-sub">{t(`was.${was}`)}</span> : null}
      </td>
      <td className="num">
        {entry.http_status === null ? <span className="muted">—</span> : entry.http_status}
      </td>
      <td>
        <span>{entry.feeds}</span>
        {entry.used_by.length > 0 ? (
          <span className="cell-sub">{t("entry.switchesOff", { pages: entry.used_by.join(", ") })}</span>
        ) : null}
        {entry.syncs_started.length > 0 ? (
          <span className="cell-sub">
            {t("entry.syncsStarted", { syncs: entry.syncs_started.map(syncWord).join(", ") })}
          </span>
        ) : null}
        {refused && !hoisted ? <Ask entry={entry} /> : null}
      </td>
      <td className="cell-when">
        <UtcTime
          value={entry.checked_at}
          variant="compact"
          wrapZone
          age={relativeAge(entry.checked_at, now)}
          ageClassName="cell-sub"
        />
      </td>
    </tr>
  );
}

/** A call path breaks only after a "/", never inside a segment. */
function CallPath({ method, path }: { method: string; path: string }) {
  return (
    <code className="qa-call">
      {method}{" "}
      {path.split("/").map((segment, index) => (
        <Fragment key={index}>
          {index > 0 ? (
            <>
              /<wbr />
            </>
          ) : null}
          {segment}
        </Fragment>
      ))}
    </code>
  );
}

/**
 * What to ask for. `capability_hint` is IBM's role table for the same data: a hint, said as one.
 * Only an entry the API marks `capability_verified` shows `required_capability` as a plain fact.
 * A 404 gets its own sentence and nothing else.
 */
export function Ask({ entry, heading = true }: { entry: AccessEntry; heading?: boolean }) {
  const t = useTranslations("qradarAccess.ask");
  const kind = askKind(entry);
  return (
    <div className="qa-ask">
      {heading ? <p className="qa-sub">{t("heading")}</p> : null}
      {kind === "verified" ? (
        <p>{t("verified", { capability: entry.required_capability as string })}</p>
      ) : kind === "unauthorized" ? (
        <p>{t("unauthorized")}</p>
      ) : kind === "notFound" ? (
        <p>{t("notFound")}</p>
      ) : kind === "hint" ? (
        <p>{t("hint", { hint: entry.capability_hint as string })}</p>
      ) : (
        <p>{t("none")}</p>
      )}
    </div>
  );
}
