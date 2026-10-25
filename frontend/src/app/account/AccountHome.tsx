"use client";

// Account settings. A password-gated account still sees only the required
// password form; all other accounts can store an interface language.

import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { PasswordChange } from "@/components/auth/PasswordChange";
import { useSession } from "@/components/auth/session";
import styles from "@/components/auth/auth.module.css";
import { writeLocaleCookie, type Locale } from "@/i18n/locale";
import { ApiError, updateUiLanguage } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { LoadingState } from "@/components/ui/RouteState";
import { ChangeStatus, type ChangeOutcome } from "@/components/ui/ChangeStatus";

type AccountOutcome =
  | { kind: "idle" }
  | { kind: "pending" }
  | { kind: "ok"; message: "saved" }
  | { kind: "notice"; message: "browserOnly" }
  | { kind: "error"; message: "saveFailed" | "cookieFailed" };

export function AccountHome() {
  const session = useSession();
  const locale = useLocale() as Locale;
  const t = useTranslations("account");
  const common = useTranslations("common");
  const router = useRouter();
  const [choice, setChoice] = useState<Locale>(locale);
  const [saved, setSaved] = useState<Locale>(locale);
  const [outcome, setOutcome] = useState<AccountOutcome>({ kind: "idle" });

  useEffect(() => {
    setChoice(locale);
    setSaved(locale);
  }, [locale]);

  if (session.status !== "ready" || session.me == null) {
    return <LoadingState what={t("loading")} />;
  }
  if (session.me.must_change_password) return <PasswordChange required />;

  async function saveLanguage(event: FormEvent) {
    event.preventDefault();
    setOutcome({ kind: "pending" });
    try {
      await updateUiLanguage(choice);
    } catch (error) {
      if (error instanceof ApiError && error.status === 409) {
        if (!writeLocaleCookie(choice)) {
          setChoice(saved);
          setOutcome({ kind: "error", message: "cookieFailed" });
          return;
        }
        setSaved(choice);
        setOutcome({ kind: "notice", message: "browserOnly" });
        router.refresh();
        return;
      }
      setChoice(saved);
      setOutcome({ kind: "error", message: "saveFailed" });
      return;
    }
    if (!writeLocaleCookie(choice)) {
      setChoice(saved);
      setOutcome({ kind: "error", message: "cookieFailed" });
      return;
    }
    setSaved(choice);
    setOutcome({ kind: "ok", message: "saved" });
    router.refresh();
  }

  const pending = outcome.kind === "pending";
  const changeOutcome: ChangeOutcome =
    outcome.kind === "ok" || outcome.kind === "notice" || outcome.kind === "error"
      ? { kind: outcome.kind, text: t(outcome.message) }
      : outcome;
  const passwordText =
    session.me.auth_source === "LDAP" ? t("directoryPassword") : t("noPassword");

  return (
    <div className={styles.accountSettings}>
      <PageHeader title={t("title")} />
      <section className={styles.settingsSection}>
        <h2>{t("language")}</h2>
        <p className={styles.sectionNote}>{t("languageDescription")}</p>
        <form className={styles.form} onSubmit={(event) => void saveLanguage(event)}>
          <fieldset className={styles.checks} disabled={pending}>
            <legend className={styles.legend}>{t("languageGroup")}</legend>
            <label className={styles.check}>
              <input
                type="radio"
                name="ui-language"
                value="en"
                checked={choice === "en"}
                onChange={() => {
                  setChoice("en");
                  setOutcome({ kind: "idle" });
                }}
              />
              <span lang="en">{t("english")}</span>
            </label>
            <label className={styles.check} lang="tr">
              <input
                type="radio"
                name="ui-language"
                value="tr"
                checked={choice === "tr"}
                onChange={() => {
                  setChoice("tr");
                  setOutcome({ kind: "idle" });
                }}
              />
              <span>{t("turkish")}</span>
            </label>
          </fieldset>
          <div className={styles.actions}>
            <button
              type="submit"
              className="action"
              disabled={pending || choice === saved}
            >
              {pending ? common("saving") : common("save")}
            </button>
          </div>
          <ChangeStatus outcome={changeOutcome} />
        </form>
      </section>
      <section className={styles.settingsSection}>
        <h2>{t("password")}</h2>
        {session.me.auth_source === "LOCAL" ? (
          <PasswordChange embedded />
        ) : (
          <>
            <p>{passwordText}</p>
          </>
        )}
      </section>
    </div>
  );
}
