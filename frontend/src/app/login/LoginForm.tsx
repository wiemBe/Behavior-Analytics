"use client";

// Sign-in. A 401 is the backend's sentence and nothing about which field was
// wrong. A 503 names the directory and says a local account still works. A 429
// says to wait. The password is never written to web storage.

import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { login } from "@/components/auth/client";
import { ApiVersionLine } from "@/components/auth/ApiVersionLine";
import { ReasonList } from "@/components/auth/ReasonList";
import { errorTexts } from "@/components/auth/reasons";
import { useSession } from "@/components/auth/session";
import styles from "@/components/auth/auth.module.css";
import { ApiError, destinationAfterLogin } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";

type Problem = { heading?: string; lines: string[]; alert?: boolean };

export function LoginForm() {
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
      setProblem(loginProblem(err));
      return;
    }
    try {
      await session.reload();
    } catch {
      setPending(false);
      setProblem({ lines: ["Signed in, but the account could not be read. Try again."] });
      return;
    }
    setPending(false);
    router.replace(destinationAfterLogin(params.get("next"), result.must_change_password));
  }

  const refused = problem != null && problem.alert;

  return (
    <div className={styles.column}>
      <p className={styles.product}>Behavior Analytics</p>
      <PageHeader title="Sign in" description="A local account or a directory account." />
      <form
        className={styles.form}
        aria-label="Sign in"
        onSubmit={(event) => void onSubmit(event)}
        aria-busy={pending}
      >
        <label className={styles.field}>
          <span className={styles.label}>Username</span>
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
          <span className={styles.label}>Password</span>
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
          {pending ? "Signing in" : "Sign in"}
        </button>
        {problem && !refused ? (
          <ReasonList tone="warn" heading={problem.heading} lines={problem.lines} />
        ) : null}
      </form>
      <ApiVersionLine className={styles.hint} />
    </div>
  );
}

function loginProblem(err: unknown): Problem {
  const lines = errorTexts(err);
  if (err instanceof ApiError && err.status === 401) return { lines, alert: true };
  if (err instanceof ApiError && err.status === 503) {
    return {
      heading: "The directory could not be reached. Local accounts still work.",
      lines,
    };
  }
  if (err instanceof ApiError && err.status === 429) {
    return { heading: "Wait, then try again.", lines };
  }
  return { lines };
}
