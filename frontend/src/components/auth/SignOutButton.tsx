"use client";

// Ends a real session. The development principal never renders this control.

import { useState } from "react";

import { useSession } from "./session";
import styles from "./auth.module.css";

export function SignOutButton() {
  const session = useSession();
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function onSignOut() {
    setPending(true);
    setProblem(null);
    const message = await session.signOut();
    setPending(false);
    if (message) setProblem(message);
  }

  return (
    <>
      <button
        type="button"
        className={`action secondary ${styles.signOut}`}
        onClick={() => void onSignOut()}
        disabled={pending}
      >
        {pending ? "Signing out" : "Sign out"}
      </button>
      {problem ? (
        <span role="alert" className={styles.name}>
          {problem}
        </span>
      ) : null}
    </>
  );
}
