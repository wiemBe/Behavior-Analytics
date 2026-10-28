"use client";

// Sign-in. A 401 is the backend's sentence and nothing about which field was
// wrong. A 503 names the directory and says a local account still works. A 429
// says to wait. The password is never written to web storage.

import { useRouter, useSearchParams } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { useState, type FormEvent } from "react";

import { login } from "@/components/auth/client";
import { ApiVersionLine } from "@/components/auth/ApiVersionLine";
import { ReasonList } from "@/components/auth/ReasonList";
import { errorTexts } from "@/components/auth/reasons";
import { useSession } from "@/components/auth/session";
import styles from "@/components/auth/auth.module.css";
import { ApiError, destinationAfterLogin } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import type { Locale } from "@/i18n/locale";
import { asTextTranslator, type TextTranslator } from "@/i18n/textTranslator";

type Problem = { heading?: string; lines: string[]; alert?: boolean };

export function LoginForm() {
  const t = useTranslations("login");
  const say = asTextTranslator(useTranslations());
  const locale = useLocale() as Locale;
  const common = useTranslations("common");
  const session = useSession();
  const router = useRouter();
  const params = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState<Problem | null>(null);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setProblem(null);
    let result;
    try {
      result = await login(username, password);
    } catch (err) {
      setPending(false);
      setProblem(loginProblem(err, t, say, locale));
      return;
    }
    try {
      await session.reload();
    } catch {
      setPending(false);
      setProblem({ lines: [t("signedInReadFailed")] });
      return;
    }
    setPending(false);
    router.replace(destinationAfterLogin(params.get("next"), result.must_change_password));
  }

  const refused = problem != null && problem.alert;

  return (
    <div className={styles.column}>
      <p className={styles.product}>{common("product")}</p>
      <PageHeader title={t("title")} description={t("description")} />
      <form
        className={styles.form}
        aria-label={t("form")}
        onSubmit={(event) => void onSubmit(event)}
        aria-busy={pending}
      >
        <label className={styles.field}>
          <span className={styles.label}>{t("username")}</span>
          <input
            className={styles.control}
            name="username"
            autoComplete="username"
            value={username}
            disabled={pending}
            onChange={(event) => setUsername(event.target.value)}
            required
          />
        </label>
        <label className={styles.field}>
          <span className={styles.label}>{t("password")}</span>
          <input
            className={styles.control}
            type="password"
            name="password"
            autoComplete="current-password"
            value={password}
            disabled={pending}
            onChange={(event) => setPassword(event.target.value)}
            required
          />
        </label>
        {refused ? (
          <p className="banner crit" role="alert">
            {problem.lines.join(" ")}
          </p>
        ) : null}
        <button type="submit" className={`action ${styles.submit}`} disabled={pending}>
          {pending ? t("signingIn") : t("title")}
        </button>
        {problem && !refused ? (
          <ReasonList tone="warn" heading={problem.heading} lines={problem.lines} />
        ) : null}
      </form>
      <ApiVersionLine className={styles.hint} />
    </div>
  );
}

function loginProblem(
  err: unknown,
  t: ReturnType<typeof useTranslations<"login">>,
  say: TextTranslator,
  locale: Locale,
): Problem {
  const lines = errorTexts(err, say, locale);
  if (err instanceof ApiError && err.status === 401) return { lines, alert: true };
  if (err instanceof ApiError && err.status === 503) {
    return {
      heading: t("directoryFailed"),
      lines,
    };
  }
  if (err instanceof ApiError && err.status === 429) {
    return { heading: t("wait"), lines };
  }
  return { lines };
}
