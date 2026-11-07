"use client";

// The daily digest: when it is due, what the last run did, and whether anyone received it.
//
// A run that has not happened is "no run yet", never a count of zero. A run that could not send
// says how many incidents were recorded and not e-mailed, and where to fix it. A failed run words
// its delivery code with the sentences the e-mail admin page already uses.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { outcomeName, outcomeSentence } from "@/components/mailadmin/mail";
import { TableScroll } from "@/components/ui/TableScroll";
import { UtcTime } from "@/components/ui/UtcTime";
import type { DigestRun, DigestRunState, DigestStatus } from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";

import "@/components/ui/controls.css";

const hourText = (hour: number) => `${String(hour).padStart(2, "0")}:00`;

function statePill(state: DigestRunState): string {
  return state === "SUCCESS" ? "pill ok" : state === "EMPTY" ? "pill" : state === "FAILED" ? "pill crit" : "pill warn";
}

export function DigestPanel({
  digest,
  note,
}: {
  digest: DigestStatus | null;
  /** Why the status is missing, when it is. */
  note: string | null;
}) {
  const t = useTranslations("alertPolicy.digest");
  const say = asTextTranslator(useTranslations());

  if (digest === null) {
    return (
      <div className="notice digest" role="alert">
        <p>
          <strong>{t("unreadTitle")}</strong>
        </p>
        <p>{note}</p>
      </div>
    );
  }

  const hour = hourText(digest.digest_hour_utc);
  const run = digest.last_run;
  const link = (href: string, label: string) => (
    <Link href={href}>{label}</Link>
  );

  function sentence(r: DigestRun) {
    if (r.state === "FAILED") {
      const code = r.error_code ?? "";
      const reason = outcomeSentence({ code: `email.test.${code}`, message: "" }, say);
      const name = outcomeName(`email.test.${code}`);
      const nextKey = name ? `mailAdmin.test.next.${name}` : null;
      const next = nextKey && say.has?.(nextKey) ? say(nextKey) : null;
      return (
        <>
          <p>{t("say.FAILED", { reason, items: r.item_count })}</p>
          {next && <p className="field-hint">{next}</p>}
          <p>{link("/admin/email", t("linkFailed"))}</p>
        </>
      );
    }
    const text = t(`say.${r.state}`, { items: r.item_count, recipients: r.recipient_count });
    return (
      <>
        <p>{text}</p>
        {r.state === "NOT_CONFIGURED" && <p>{link("/admin/email", t("linkSetup"))}</p>}
        {r.state === "NO_RECIPIENTS" && <p>{link("/admin/email#recipients", t("linkRecipients"))}</p>}
      </>
    );
  }

  const warnNow =
    run && run.state !== "NOT_CONFIGURED" && !digest.email_configured
      ? t("unconfiguredNow")
      : run &&
          run.state !== "NO_RECIPIENTS" &&
          digest.email_configured &&
          digest.recipient_count === 0
        ? t("noRecipientsNow")
        : null;

  return (
    <div className="digest" aria-label={t("label")}>
      <dl className="digest-facts">
        <div>
          <dt>{t("due")}</dt>
          <dd>{t("dueValue", { hour })}</dd>
        </div>
        <div>
          <dt>{t("recipients")}</dt>
          <dd>{t("recipientsValue", { count: digest.recipient_count })}</dd>
        </div>
        {run && (
          <>
            <div>
              <dt>{t("lastRun")}</dt>
              <dd>
                <span className={statePill(run.state)}>{t(`state.${run.state}`)}</span>
                <span className="cell-sub">{t("digestDate", { date: run.digest_date })}</span>
              </dd>
            </div>
            <div>
              <dt>{t("items")}</dt>
              <dd>{t("itemsValue", { count: run.item_count })}</dd>
            </div>
            <div>
              <dt>{t("sent")}</dt>
              <dd>
                {run.sent_at ? (
                  <UtcTime value={run.sent_at} variant="compact" />
                ) : (
                  <span className="muted">—</span>
                )}
              </dd>
            </div>
          </>
        )}
      </dl>
      <div className="digest-say">
        {run ? sentence(run) : <p>{t("noRun", { hour })}</p>}
        {warnNow && <p className="field-hint">{warnNow}</p>}
      </div>

    </div>
  );
}

/** The last run of each console, when there is more than one. */
export function DigestConsoles({ digest }: { digest: DigestStatus | null }) {
  const t = useTranslations("alertPolicy.digest");
  if (digest === null || digest.instances.length <= 1) return null;
  return (
    <section aria-labelledby="policy-digest-consoles">
      <h2 className="section-title" id="policy-digest-consoles">
        {t("consoles")}
      </h2>
        <TableScroll label={t("consolesLabel")}>
          <table className="fleet-table">
            <caption className="sr-only">{t("consolesLabel")}</caption>
            <thead>
              <tr>
                <th scope="col">{t("console")}</th>
                <th scope="col">{t("date")}</th>
                <th scope="col">{t("outcome")}</th>
                <th scope="col" className="num">
                  {t("items")}
                </th>
                <th scope="col" className="num">
                  {t("recipientsCol")}
                </th>
                <th scope="col">{t("sentCol")}</th>
              </tr>
            </thead>
            <tbody>
              {digest.instances.map((i) => (
                <tr key={i.instance_id}>
                  <th scope="row">{i.instance_name ?? i.instance_id.slice(0, 8)}</th>
                  {i.last_run ? (
                    <>
                      <td className="nowrap">{i.last_run.digest_date}</td>
                      <td>
                        <span className={statePill(i.last_run.state)}>{t(`state.${i.last_run.state}`)}</span>
                      </td>
                      <td className="num">{i.last_run.item_count}</td>
                      <td className="num">{i.last_run.recipient_count}</td>
                      <td>
                        {i.last_run.sent_at ? (
                          <UtcTime value={i.last_run.sent_at} variant="compact" />
                        ) : (
                          <span className="muted">—</span>
                        )}
                      </td>
                    </>
                  ) : (
                    <td colSpan={5} className="muted">
                      {t("neverRun")}
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </TableScroll>
    </section>
  );
}
