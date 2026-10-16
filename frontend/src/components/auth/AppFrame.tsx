"use client";

// The shell, except where the account is not allowed to see it. Sign-in has
// no navigation. A password that must change, and an account with no
// permission, replace the page they asked for.

import { Suspense, type ReactNode } from "react";
import { usePathname } from "next/navigation";

import { hasAnyAccess, visibleNav } from "./access";
import { NoRole } from "./NoRole";
import { PasswordChange } from "./PasswordChange";
import { useSession } from "./session";
import styles from "./auth.module.css";
import { AppNav } from "@/components/shell/AppNav";
import { TopBar } from "@/components/shell/TopBar";
import { NAV } from "@/lib/nav";

function onLoginPath(pathname: string): boolean {
  return pathname === "/login" || pathname.startsWith("/login/");
}

export function AppFrame({ children }: { children: ReactNode }) {
  const pathname = usePathname() ?? "/";
  const session = useSession();

  if (onLoginPath(pathname)) {
    return <div className={styles.gate}>{children}</div>;
  }

  if (session.status === "ready" && session.me?.must_change_password) {
    return (
      <div className={styles.gate}>
        <Suspense fallback={<p className="notice" role="status">Loading the password form…</p>}>
          <PasswordChange required />
        </Suspense>
      </div>
    );
  }

  if (session.status === "ready" && session.me && !hasAnyAccess(session.me)) {
    return (
      <div className={styles.gate}>
        <NoRole me={session.me} />
      </div>
    );
  }

  if (session.status === "anonymous") {
    return (
      <div className={styles.gate}>
        <p className="notice" role="status">
          Sign-in is required.
        </p>
      </div>
    );
  }

  if (session.status === "error") {
    return (
      <div className={styles.gate}>
        <div className="notice" role="alert">
          <p>
            <strong>The session could not be read.</strong>
          </p>
          <p>
            Nothing is shown until the account can be confirmed.
            {session.error ? ` ${session.error}` : ""} Try again. If it keeps failing, the
            backend may be unreachable.
          </p>
          <p>
            <button type="button" className="action secondary" onClick={() => void session.reload()}>
              Try again
            </button>
          </p>
        </div>
      </div>
    );
  }

  const granted = session.status === "ready" && session.me ? session.me.permissions : [];

  return (
    <>
      <header className="shell-head">
        <a href="/behavior" className="brand plain">
          <span className="brand-name">Behavior Analytics</span>
          <span className="brand-sub">QRadar Anomaly &amp; Investigation</span>
        </a>
        <TopBar />
      </header>
      <div className="layout">
        <aside className="sidebar">
          <AppNav groups={visibleNav(NAV, granted)} />
        </aside>
        <main className="main" id="content">
          <div className="page">{children}</div>
        </main>
      </div>
    </>
  );
}
