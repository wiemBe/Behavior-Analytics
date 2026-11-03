"use client";

// Does it reach them: one real message through the relay, built from what the editor holds now.
// The outcome is the coded sentence the API returns, worded here with what to do next. The relay's
// own reply text never reaches the browser, so none is shown.

import { useTranslations } from "next-intl";
import { useId, useState, type FormEvent } from "react";

import { asTextTranslator } from "@/i18n/textTranslator";

import { outcomeName, outcomeSentence } from "./mail";
import type { TestView } from "./useTemplateWorkspace";

export function TestSend({
  configured,
  limit,
  view,
  ready,
  onSend,
}: {
  /**
   * Whether the relay is set in `.env`. False disables the send, with the reason beside it. Null is
   * "not read yet" (or unreadable): the send stays off rather than guess that a relay exists.
   */
  configured: boolean | null;
  /** `test_rate_limit` from the settings, for example `5/minute`; null while they are unread. */
  limit: string | null;
  view: TestView;
  /** The editor holds a draft that can be sent. */
  ready: boolean;
  onSend: (to: string) => void;
}) {
  const t = useTranslations("mailAdmin.test");
  const root = asTextTranslator(useTranslations());
  const headingId = useId();
  const toId = useId();
  const reasonId = useId();
  const [to, setTo] = useState("");
  const sending = view.kind === "sending";
  const blocked = configured !== true;
  const disabled = blocked || sending || !ready || to.trim() === "";

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!disabled) onSend(to.trim());
  }

  return (
    <section className="mail-section" aria-labelledby={headingId}>
      <h2 className="mail-h" id={headingId}>
        {t("heading")}
      </h2>
      <p className="mail-hint">{limit ? t("note", { limit }) : t("noteNoLimit")}</p>
      <form className="mail-send" onSubmit={submit} aria-label={t("heading")}>
        <div className="mail-field mail-field-inline">
          <label htmlFor={toId}>{t("to")}</label>
          <input
            id={toId}
            value={to}
            inputMode="email"
            autoComplete="off"
            spellCheck={false}
            disabled={blocked}
            aria-describedby={blocked ? reasonId : undefined}
            onChange={(event) => setTo(event.target.value)}
          />
        </div>
        <button
          type="submit"
          className="action"
          disabled={disabled}
          aria-describedby={blocked ? reasonId : undefined}
        >
          {sending ? t("sending") : t("send")}
        </button>
      </form>
      {blocked ? (
        <p className="mail-hint mail-reason" id={reasonId}>
          {configured === null
            ? t("settingsUnread")
            : t.rich("notConfigured", { code: (chunks) => <code>{chunks}</code> })}
        </p>
      ) : null}

      {view.kind === "outcome" && view.ok ? (
        <div className="banner ok" role="status">
          <div>
            <p>
              <strong>{t("acceptedHeading")}</strong>
            </p>
            <p>{outcomeSentence(view, root)}</p>
            <p>{t("next.sent")}</p>
          </div>
        </div>
      ) : null}
      {view.kind === "outcome" && !view.ok ? (
        <div className="banner crit" role="alert">
          <div>
            <p>
              <strong>{t("failedHeading")}</strong>
            </p>
            <p>{outcomeSentence(view, root)}</p>
            <NextStep code={view.code} />
            <p className="mail-code-line">{t("code", { code: view.code })}</p>
          </div>
        </div>
      ) : null}
      {view.kind === "limited" ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("limitedHeading")}</strong>
            </p>
            <p>
              {limit ? t("limitedBody", { limit }) : t("limitedBodyNoLimit")}{" "}
              {view.retryAfter === null
                ? t("retryLater")
                : t("retryAfter", { seconds: view.retryAfter })}
            </p>
          </div>
        </div>
      ) : null}
      {view.kind === "failed" ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("notSent")}</strong>
            </p>
            <ul className="mail-lines">
              {view.lines.map((line, index) => (
                <li key={`${index}-${line}`}>{line}</li>
              ))}
            </ul>
          </div>
        </div>
      ) : null}
    </section>
  );
}

/** What to do after a failure the page knows; a code it does not know has no step to offer. */
function NextStep({ code }: { code: string }) {
  const t = useTranslations("mailAdmin.test.next");
  const name = outcomeName(code);
  if (name === null || name === "sent") return null;
  return <p>{t(name)}</p>;
}
