"use client";

// The first screen: how mail leaves this site. Read from `/admin/email/settings`, which carries
// the transport as `.env` sets it and never the password. The page cannot change any of it.

import Link from "next/link";
import { useTranslations } from "next-intl";

import { LoadingState } from "@/components/ui/RouteState";
import type { EmailSettings } from "@/lib/api";
import { asTextTranslator } from "@/i18n/textTranslator";

import { outcomeSentence } from "./mail";
import { PartError } from "./PartError";
import type { Part } from "./usePart";

const GUIDE = "/guide/alert-email";

export function RelaySection({ part, onRetry }: { part: Part<EmailSettings>; onRetry: () => void }) {
  const t = useTranslations("mailAdmin.relay");
  return (
    <section className="mail-section" aria-labelledby="mail-relay-h">
      <h2 className="mail-h" id="mail-relay-h">
        {t("heading")}
      </h2>
      {part.status === "loading" ? <LoadingState what={t("loadingWhat")} /> : null}
      {part.status === "error" ? (
        <PartError
          what={t("what")}
          httpStatus={part.httpStatus}
          retryAfter={part.retryAfter}
          onRetry={onRetry}
        />
      ) : null}
      {part.status === "ok" ? <RelayFacts settings={part.data} /> : null}
    </section>
  );
}

function RelayFacts({ settings }: { settings: EmailSettings }) {
  const t = useTranslations("mailAdmin.relay");
  const root = asTextTranslator(useTranslations());
  const insecure = settings.configured && settings.security === "none" && settings.username_set;
  const endpoint = `${settings.host}:${settings.port}`;
  const from = settings.from_name
    ? `${settings.from_name} <${settings.from_address}>`
    : settings.from_address;
  return (
    <>
      {!settings.configured ? (
        <div className="banner warn" role="alert">
          <div>
            <p>
              <strong>{t("notConfigured.heading")}</strong>
            </p>
            <p>
              {t.rich("notConfigured.body", {
                code: (chunks) => <code>{chunks}</code>,
                guide: (chunks) => <Link href={GUIDE}>{chunks}</Link>,
              })}
            </p>
          </div>
        </div>
      ) : null}
      {insecure ? (
        <div className="banner crit" role="alert">
          <div>
            <p>
              <strong>{t("insecure.heading")}</strong>
            </p>
            <p>
              {outcomeSentence(
                { code: "email.test.insecure_login", message: "" },
                root,
              )}
            </p>
          </div>
        </div>
      ) : null}
      <dl className="mail-facts">
        <div>
          <dt>{t("fact.status")}</dt>
          <dd>
            <span className={settings.configured ? "pill" : "pill warn"}>
              {settings.configured ? t("status.configured") : t("status.notConfigured")}
            </span>
            {settings.configured ? <span className="cell-sub">{t("status.note")}</span> : null}
          </dd>
        </div>
        <div>
          <dt>{t("fact.relay")}</dt>
          <dd>
            {settings.host ? (
              <code className="mail-mono">{endpoint}</code>
            ) : (
              <span className="muted">{t("unset")}</span>
            )}
          </dd>
        </div>
        <div>
          <dt>{t("fact.security")}</dt>
          <dd>
            {t(`security.${settings.security}`)}
            <span className="cell-sub">
              {settings.security_source === "SMTP_SECURITY"
                ? t("securitySource.explicit")
                : t("securitySource.derived")}
            </span>
          </dd>
        </div>
        <div>
          <dt>{t("fact.from")}</dt>
          <dd>
            <span className="mail-mono">{from}</span>
          </dd>
        </div>
        <div>
          <dt>{t("fact.login")}</dt>
          <dd>{settings.username_set ? t("credentials.set") : t("credentials.none")}</dd>
        </div>
        <div>
          <dt>{t("fact.caFile")}</dt>
          <dd>{settings.ca_file_set ? t("caFile.set") : t("caFile.none")}</dd>
        </div>
      </dl>
      <p className="mail-hint">
        {t.rich("envNote", {
          code: (chunks) => <code>{chunks}</code>,
          guide: (chunks) => <Link href={GUIDE}>{chunks}</Link>,
        })}
      </p>
    </>
  );
}
