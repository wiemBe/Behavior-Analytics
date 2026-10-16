"use client";

// The only page a `must_change_password` account may use, and the same form
// when someone opens it on purpose. The confirmation is checked here. Every
// refusal the backend sends is shown as sent.

import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";

import { changePassword } from "./client";
import { ReasonList } from "./ReasonList";
import { errorTexts } from "./reasons";
import { SignOutButton } from "./SignOutButton";
import { useSession } from "./session";
import styles from "./auth.module.css";
import { destinationAfterPasswordChange } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";

export function PasswordChange({ required = false }: { required?: boolean }) {
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
      setLines(["The new password and the confirmation do not match."]);
      return;
    }
    setPending(true);
    setLines(null);
    setDone(false);
    try {
      await changePassword(current, next);
    } catch (err) {
      setPending(false);
      setLines(errorTexts(err));
      return;
    }
    setCurrent("");
    setNext("");
    setConfirm("");
    try {
      await session.reload();
    } catch {
      setPending(false);
      setLines(["The password is changed, but the account could not be read again. Try again."]);
      return;
    }
    setPending(false);
    setDone(true);
    const here = `${window.location.pathname}${window.location.search}`;
    router.replace(destinationAfterPasswordChange(params.get("next"), here));
  }

  return (
    <div className={styles.panel}>
      <PageHeader
        title="Change password"
        description={
          required
            ? "This account cannot continue until the password is changed."
            : "A local account changes its password here."
        }
        actions={required ? <SignOutButton /> : null}
      />
      <form
        className={styles.form}
        aria-label="Change password"
        onSubmit={(event) => void onSubmit(event)}
        aria-busy={pending}
      >
        <label className={styles.field}>
          <span className={styles.label}>Current password</span>
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
          <span className={styles.label}>New password</span>
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
          <span className={styles.label}>Confirm new password</span>
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
          <button type="submit" className="action" disabled={pending}>
            {pending ? "Changing password" : "Change password"}
          </button>
        </div>
      </form>
      {done ? <ReasonList tone="ok" lines={["The password is changed."]} /> : null}
      {lines ? <ReasonList tone="warn" heading="The password was not changed." lines={lines} /> : null}
    </div>
  );
}
