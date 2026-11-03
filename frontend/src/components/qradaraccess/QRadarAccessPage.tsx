"use client";

// "Which QRadar APIs can this site's token not reach, what does that switch off here, and what
// should I ask the QRadar administrator for?" One section per console: its verdict first, then the
// APIs with the refused ones on top. "Check now" queues the check in the worker and the page reads
// the stored result until it ends. The page sends nothing to QRadar itself.

import { useTranslations } from "next-intl";
import { useEffect, useState } from "react";

import { LoadingState } from "@/components/ui/RouteState";
import { UtcTime } from "@/components/ui/UtcTime";
import type { AccessConsole } from "@/lib/api";
import { formatDuration, relativeAge } from "@/lib/time";

import { anyRunning, POLL_LIMIT_SECONDS, POLL_SECONDS, sharedAsk, syncWord, verdictOf, verdictTone } from "./access";
import { Ask, EntryTable } from "./EntryTable";
import { useQRadarAccess, type CheckView } from "./useQRadarAccess";

import "./qradaraccess.css";

export function QRadarAccessPage() {
  const t = useTranslations("qradarAccess");
  const state = useQRadarAccess();
  const { part } = state;
  const [now, setNow] = useState(() => new Date());
  // The relative ages follow the clock, not only the reads.
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(id);
  }, []);
  useEffect(() => {
    if (part.status === "ok") setNow(new Date());
  }, [part]);

  if (part.status === "loading") return <LoadingState what={t("page.loadingWhat")} />;
  if (part.status === "forbidden") {
    return (
      <div className="notice" role="alert">
        <p>
          <strong>{t("page.forbidden.heading")}</strong>
        </p>
        <p>{t.rich("page.forbidden.body", { perm: (chunks) => <code className="perm">{chunks}</code> })}</p>
      </div>
    );
  }
  if (part.status === "error") return <ReadError part={part} onRetry={state.reload} />;

  const { consoles, check_interval_seconds: interval } = part.data;
  const running = anyRunning(consoles);
  return (
    <div className="qa-page">
      {consoles.length === 0 ? (
        <div className="notice" role="status">
          <p>
            <strong>{t("page.noConsole.heading")}</strong>
          </p>
          <p>{t("page.noConsole.body")}</p>
        </div>
      ) : (
        <>
          <CheckNow state={state} running={running} consoles={consoles} />
          {state.refreshFailed ? (
            <p className="banner warn" role="status">
              {t("error.refreshFailed")}
            </p>
          ) : null}
          {consoles.map((console) => (
            <ConsoleSection key={console.instance_id} console={console} interval={interval} now={now} />
          ))}
        </>
      )}
    </div>
  );
}

function ReadError({
  part,
  onRetry,
}: {
  part: { httpStatus: number | null; retryAfter: number | null };
  onRetry: () => void;
}) {
  const t = useTranslations("qradarAccess.error");
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
        <strong>{t("couldNotRead")}</strong>
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

function CheckNow({
  state,
  running,
  consoles,
}: {
  state: ReturnType<typeof useQRadarAccess>;
  running: boolean;
  consoles: readonly AccessConsole[];
}) {
  const t = useTranslations("qradarAccess.check");
  const posting = state.view.kind === "posting";
  const busy = posting || running;
  const timedOut = state.timedOut && running;
  return (
    <section className="qa-check" aria-labelledby="qa-check-h">
      <h2 className="qa-h" id="qa-check-h">
        {t("heading")}
      </h2>
      <p className="qa-hint">{t("note")}</p>
      <div className="qa-check-row">
        <button type="button" className="action" disabled={busy} onClick={state.checkNow}>
          {posting ? t("queuing") : t("button")}
        </button>
      </div>
      <div className="qa-check-state" role="status" aria-live="polite">
        <ViewSentences view={state.view} />
        {running && !timedOut ? <p>{t("running", { seconds: POLL_SECONDS })}</p> : null}
        {timedOut ? <p>{t("timedOut", { minutes: POLL_LIMIT_SECONDS / 60 })}</p> : null}
        {state.finished && !running ? <Ended consoles={consoles} /> : null}
      </div>
    </section>
  );
}

function ViewSentences({ view }: { view: CheckView }) {
  const t = useTranslations("qradarAccess.check");
  switch (view.kind) {
    case "answered":
      return (
        <>
          {view.started.length > 0 ? <p>{t("started", { consoles: view.started.join(", ") })}</p> : null}
          {view.alreadyRunning.length > 0 ? (
            <p>{t("alreadyRunning", { consoles: view.alreadyRunning.join(", ") })}</p>
          ) : null}
          {view.failed.length > 0 ? (
            <p className="qa-bad">{t("failed", { consoles: view.failed.join(", ") })}</p>
          ) : null}
        </>
      );
    case "limited":
      return (
        <p className="qa-bad">
          {view.retryAfter === null
            ? t("limitedLater")
            : t("limitedAfter", { seconds: view.retryAfter })}
        </p>
      );
    case "forbidden":
      return (
        <p className="qa-bad">
          {t.rich("forbidden", { perm: (chunks) => <code className="perm">{chunks}</code> })}
        </p>
      );
    case "error":
      return (
        <p className="qa-bad">
          {view.httpStatus === null ? t("errorNoAnswer") : t("error", { status: view.httpStatus })}
        </p>
      );
    default:
      return null;
  }
}

/** After a check this page started has ended: the syncs it started, per console, or none. */
function Ended({ consoles }: { consoles: readonly AccessConsole[] }) {
  const t = useTranslations("qradarAccess.check");
  return (
    <div>
      <p>{t("ended")}</p>
      <ul className="qa-lines">
        {consoles
          .filter((console) => console.applicable)
          .map((console) => (
            <li key={console.instance_id}>
              <strong>{console.instance_name}</strong>{" "}
              {console.syncs_started.length > 0
                ? t("syncsStarted", { syncs: console.syncs_started.map(syncWord).join(", ") })
                : t("noSyncs")}
            </li>
          ))}
      </ul>
    </div>
  );
}

function ConsoleSection({
  console,
  interval,
  now,
}: {
  console: AccessConsole;
  interval: number;
  now: Date;
}) {
  const t = useTranslations("qradarAccess");
  const verdict = verdictOf(console);
  const tone = verdictTone(verdict.kind);
  const headingId = `qa-${console.instance_id}`;
  const names = verdict.titles.join(", ");
  const shared = sharedAsk(console.entries);
  return (
    <section className="qa-console" aria-labelledby={headingId}>
      <div className="qa-head">
        <h2 className="qa-h" id={headingId}>
          {console.instance_name}
        </h2>
        {console.applicable && console.check_running ? (
          <span className="pill">{t("console.running")}</span>
        ) : null}
      </div>
      {verdict.kind === "notApplicable" ? (
        <div className="banner">
          <p>{t("console.notApplicable")}</p>
        </div>
      ) : (
        <>
          <div className={`banner ${tone}`.trim()} role={tone === "" ? "status" : "alert"}>
            <div>
              <p>
                <strong>
                  {verdict.kind === "allOk" || verdict.kind === "notChecked" || verdict.kind === "unauthorized"
                    ? t(`verdict.${verdict.kind}`)
                    : t(`verdict.${verdict.kind}`, { count: verdict.count, names })}
                </strong>
              </p>
              <p className="qa-when">
                {console.last_checked_at ? (
                  <>
                    {t("console.lastChecked")}{" "}
                    <UtcTime value={console.last_checked_at} age={relativeAge(console.last_checked_at, now)} />
                    {". "}
                  </>
                ) : (
                  <>{t("console.neverChecked")} </>
                )}
                {t("console.interval", { interval: formatDuration(interval) })}
              </p>
              {shared ? <Ask entry={shared} /> : null}
              {console.syncs_started.length > 0 ? (
                <p className="qa-when">
                  {t("console.latestSyncs", { syncs: console.syncs_started.map(syncWord).join(", ") })}
                </p>
              ) : null}
            </div>
          </div>
          <EntryTable consoleName={console.instance_name} entries={console.entries} now={now} />
        </>
      )}
    </section>
  );
}
