"use client";

// The only page a `must_change_password` account may use, and the same form
// when someone opens it on purpose. The confirmation is checked here. Every
// refusal the backend sends is shown as sent.

import { useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { changePassword } from "./client";
import { ReasonList } from "./ReasonList";
import { errorTexts } from "./reasons";
import { SignOutButton } from "./SignOutButton";
import { useSession } from "./session";
import styles from "./auth.module.css";
import { destinationAfterPasswordChange } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator } from "@/i18n/textTranslator";

export function PasswordChange({
  required = false,
  embedded = false,
}: {
  required?: boolean;
  embedded?: boolean;
}) {
  const t = useTranslations("password");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const session = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [lines, setLines] = useState<string[] | null>(null);
  const [done, setDone] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (next !== confirm) {
      setDone(false);
      setLines([t("mismatch")]);
      return;
    }
    setPending(true);
    setLines(null);
    setDone(false);
    try {
      await changePassword(current, next);
    } catch (err) {
      setPending(false);
      setLines(errorTexts(err, say, locale));
      return;
    }
    setCurrent("");
    setNext("");
    setConfirm("");
    try {
      await session.reload();
    } catch {
      setPending(false);
      setLines([t("changedReloadFailed")]);
      return;
    }
    setPending(false);
    setDone(true);
    const here = `${window.location.pathname}${window.location.search}`;
    router.replace(destinationAfterPasswordChange(params.get("next"), here));
  }

  return (
    <div className={styles.column}>
      {embedded ? (
        <p className={styles.sectionNote}>{t("localDescription")}</p>
      ) : (
        <PageHeader
          title={t("title")}
          description={required ? t("requiredDescription") : t("localDescription")}
          actions={required ? <SignOutButton /> : null}
        />
      )}
      <form
        className={styles.form}
        aria-label={t("form")}
        onSubmit={(event) => void onSubmit(event)}
        aria-busy={pending}
      >
        <label className={styles.field}>
          <span className={styles.label}>{t("current")}</span>
          <input
            className={styles.control}
            type="password"
            name="current-password"
            autoComplete="current-password"
            value={current}
            disabled={pending}
            onChange={(event) => setCurrent(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>{t("new")}</span>
          <input
            className={styles.control}
            type="password"
            name="new-password"
            autoComplete="new-password"
            value={next}
            disabled={pending}
            onChange={(event) => setNext(event.target.value)}
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>{t("confirm")}</span>
          <input
            className={styles.control}
            type="password"
            name="confirm-password"
            autoComplete="new-password"
            value={confirm}
            disabled={pending}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </label>
        <div className={styles.actions}>
          <button type="submit" className={`action ${styles.submit}`} disabled={pending}>
            {pending ? t("changing") : t("change")}
          </button>
        </div>
      </form>
      {done ? <ReasonList tone="ok" lines={[t("changed")]} /> : null}
      {lines ? <ReasonList tone="warn" heading={t("notChanged")} lines={lines} /> : null}
    </div>
  );
}
